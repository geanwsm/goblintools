#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import logging
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3
from botocore.config import Config as BotoConfig

from goblintools import OCRConfig
from goblintools.ocr_parser import OCRProcessor

MODES = {
    "adaptive": ({"mode": "adaptive", "total_max_attempts": 1}, None),
    "no-limiter": ({"total_max_attempts": 1}, None),
    "old": ({"total_max_attempts": 1}, 3.5),
}


class Quota:
    def __init__(self, rate: float):
        self.rate = rate
        self.tokens = rate
        self.last = time.monotonic()
        self.lock = threading.Lock()
        self.accepted = 0
        self.throttled = 0

    def take(self) -> bool:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.rate, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens >= 1:
                self.tokens -= 1
                self.accepted += 1
                return True
            self.throttled += 1
            return False


def make_handler(quota: Quota, latency: float):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if quota.take():
                time.sleep(latency)
                status, body = 200, {"Blocks": [{"BlockType": "LINE", "Text": "ok"}]}
            else:
                status, body = 400, {"__type": "ThrottlingException", "message": "Rate exceeded"}
            payload = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/x-amz-json-1.1")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    return Handler


def run_mode(mode: str, args) -> dict:
    retries, forced_wait = MODES[mode]
    quota = Quota(args.quota)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(quota, args.latency))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    endpoint = f"http://127.0.0.1:{server.server_address[1]}"
    max_wait = forced_wait if forced_wait is not None else args.max_wait
    results = {"ok": 0, "dropped": 0}
    lock = threading.Lock()

    def worker():
        processor = OCRProcessor(OCRConfig(
            use_aws=True, aws_access_key="test", aws_secret_key="test", textract_max_wait_s=max_wait,
        ))
        processor._textract_client = boto3.client(
            "textract",
            endpoint_url=endpoint,
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
            config=BotoConfig(retries=retries, connect_timeout=10, read_timeout=60, max_pool_connections=4),
        )
        for _ in range(args.pages):
            try:
                text = processor._textract_detect(b"page")
            except Exception:
                text = ""
            with lock:
                results["ok" if text else "dropped"] += 1

    started = time.monotonic()
    threads = [threading.Thread(target=worker) for _ in range(args.clients)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started
    server.shutdown()
    total_requests = quota.accepted + quota.throttled
    return {
        "mode": mode,
        "max_wait_s": max_wait,
        "pages_ok": results["ok"],
        "pages_dropped": results["dropped"],
        "requests": total_requests,
        "throttled_pct": round(100 * quota.throttled / total_requests, 1) if total_requests else 0.0,
        "served_per_s": round(quota.accepted / elapsed, 1) if elapsed else 0.0,
        "elapsed_s": round(elapsed, 1),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Local Textract throttling simulation: a stub DetectDocumentText endpoint with a token-bucket "
            "quota serves N concurrent clients, comparing the adaptive client-side limiter with no limiter "
            "and with the previous retry policy (no limiter, ~3.5 s per page). No AWS calls are made."
        )
    )
    parser.add_argument("--quota", type=float, default=25.0, help="requests per second the stub accepts")
    parser.add_argument("--clients", type=int, default=60, help="concurrent clients (indexer tasks)")
    parser.add_argument("--pages", type=int, default=20, help="pages per client")
    parser.add_argument("--latency", type=float, default=0.2, help="seconds per accepted request")
    parser.add_argument("--max-wait", type=float, default=30.0, help="textract_max_wait_s for adaptive and no-limiter")
    parser.add_argument("--modes", default="adaptive,no-limiter,old")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    rows = [run_mode(mode.strip(), args) for mode in args.modes.split(",") if mode.strip()]
    columns = list(rows[0])
    print(" | ".join(columns))
    for row in rows:
        print(" | ".join(str(row[c]) for c in columns))
    return 0


if __name__ == "__main__":
    sys.exit(main())
