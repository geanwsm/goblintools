import os
import logging
from typing import Dict, List, Optional, Sequence

import boto3
import cv2
import numpy as np
from botocore.exceptions import ClientError
from pathlib import Path
from pdf2image import convert_from_path
import pytesseract
import multiprocessing
from scipy.ndimage import rotate
from goblintools.config import OCRConfig
from goblintools.retry import retry_with_backoff
from goblintools.log_policy import log_warning

logger = logging.getLogger(__name__)

# DetectDocumentText (synchronous) rejects documents above 10 MB.
TEXTRACT_SYNC_MAX_BYTES = 10 * 1024 * 1024

# Error codes worth another attempt; anything else (AccessDenied, invalid image,
# unsupported document) will fail the same way every time.
_TEXTRACT_TRANSIENT_CODES = frozenset({
    "ThrottlingException",
    "ProvisionedThroughputExceededException",
    "InternalServerError",
    "ServiceUnavailableException",
    "LimitExceededException",
})


class TextractTransientError(Exception):
    """Transient Textract failure (throttling / 5xx), eligible for retry."""


def _encode_for_textract(image, max_bytes: int = TEXTRACT_SYNC_MAX_BYTES) -> Optional[bytes]:
    """JPEG-encode a page for DetectDocumentText under the synchronous size limit.

    Steps quality down, then scale, until the payload fits. The first attempt
    (full scale, quality 95) is OpenCV's default encoding, so pages that already
    fit are sent exactly as before. Returns None when nothing fits.
    """
    frame_source = np.asarray(image)
    for scale in (1.0, 0.75, 0.5):
        if scale == 1.0:
            frame = frame_source
        else:
            frame = cv2.resize(frame_source, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        for quality in (95, 80, 60):
            ok, encoded = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if ok and encoded.nbytes <= max_bytes:
                return encoded.tobytes()
    return None


class OCRProcessor:
    def __init__(self, config: OCRConfig):
        self.config = config
        self._textract_client = None
        has_keys = bool(config.aws_access_key and config.aws_secret_key)
        # Fall back to local OCR when use_aws=True but no credential source was chosen
        if config.use_aws and not has_keys and not config.use_default_aws_credentials:
            log_warning(
                logger,
                "AWS credentials not found; falling back to local Tesseract OCR. "
                "Provide aws_access_key and aws_secret_key in OCRConfig, or set "
                "use_default_aws_credentials=True to use the boto3 credential chain "
                "(e.g. an ECS task role), to use AWS Textract.",
            )
            self._use_aws_effective = False
        else:
            self._use_aws_effective = config.use_aws

    @property
    def use_aws(self) -> bool:
        """Whether AWS Textract is effectively being used (False when credentials missing)."""
        return self._use_aws_effective

    @property
    def textract_client(self):
        """Lazy-loaded AWS Textract client.

        Explicit keys (plus the session token, required for temporary credentials)
        win; otherwise boto3 resolves credentials through its default chain.
        """
        if self._textract_client is None and self._use_aws_effective:
            kwargs = {'region_name': self.config.aws_region}
            if self.config.aws_access_key and self.config.aws_secret_key:
                kwargs.update(
                    aws_access_key_id=self.config.aws_access_key,
                    aws_secret_access_key=self.config.aws_secret_key,
                    aws_session_token=self.config.aws_session_token,
                )
            try:
                self._textract_client = boto3.client('textract', **kwargs)
            except Exception as e:
                # Type only: the message of a client-construction error may echo arguments.
                logger.error(f"Failed to initialize AWS Textract client: {type(e).__name__}")
                raise
        return self._textract_client

    def _process_page_aws(self, image):
        img_bytes = _encode_for_textract(image)
        if img_bytes is None:
            log_warning(
                logger,
                "Page image exceeds the Textract synchronous limit even after downscaling; page skipped.",
            )
            return ""
        try:
            return self._textract_detect(img_bytes)
        except TextractTransientError as e:
            log_warning(logger, f"AWS Textract unavailable after retries ({e}); page skipped.")
            return ""

    @retry_with_backoff(max_retries=3, initial_delay=0.5, exceptions=(TextractTransientError,))
    def _textract_detect(self, img_bytes: bytes) -> str:
        try:
            response = self.textract_client.detect_document_text(Document={'Bytes': img_bytes})
        except ClientError as e:
            code = e.response.get('Error', {}).get('Code', '')
            if code in _TEXTRACT_TRANSIENT_CODES:
                raise TextractTransientError(code) from e
            log_warning(logger, f"AWS Textract rejected the page ({code or 'unknown error'}).")
            return ""
        except Exception as e:
            logger.error(f"Error during AWS Textract processing: {type(e).__name__}")
            return ""
        return '\n'.join(
            item['Text']
            for item in response.get('Blocks', [])
            if item.get('BlockType') == 'LINE' and item.get('Text')
        )

    def extract_text_from_image(self, image) -> str:
        """OCR one in-memory image (PIL image or ndarray) with the configured engine."""
        try:
            if self._use_aws_effective:
                return (self._process_page_aws(np.asarray(image)) or "").strip()
            return (self._process_page_local(image) or "").strip()
        except Exception as e:
            logger.error(f"Error during image OCR: {type(e).__name__}")
            return ""

    @retry_with_backoff(max_retries=3, exceptions=(Exception,))
    def _process_page_local(self, image):
        image = np.array(image)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        def determine_score(arr, angle):
            data = rotate(arr, angle, reshape=False, order=0)
            histogram = np.sum(data, axis=1, dtype=float)
            return np.sum((histogram[1:] - histogram[:-1]) ** 2, dtype=float)

        delta = 1
        limit = 5
        scores = [determine_score(thresh, angle) for angle in range(-limit, limit + delta, delta)]
        best_angle = (np.argmax(scores) - limit) * delta

        (h, w) = image.shape[:2]
        center = (w // 2, h // 2)
        M = cv2.getRotationMatrix2D(center, best_angle, 1.0)
        corrected = cv2.warpAffine(image, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

        return pytesseract.image_to_string(corrected, lang=self.config.tesseract_lang)

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        try:
            images = convert_from_path(pdf_path)
        except Exception as e:
            logger.exception(f"Error converting PDF to images: {e}")
            return ""

        if self._use_aws_effective:
            extracted_texts = []
            for image in images:
                text = self._process_page_aws(np.array(image))
                extracted_texts.append(text if text else "")
            return ' '.join(extracted_texts).strip()
        else:
            n_workers = min(multiprocessing.cpu_count(), len(images))
            with multiprocessing.Pool(processes=n_workers) as pool:
                extracted_text = pool.map(self._process_page_local, images)
            return ' '.join(extracted_text).strip()
    
    def extract_text_from_pdf_by_pages(self, pdf_path: str) -> List[str]:
        """Extract text from PDF returning a list of pages"""
        try:
            images = convert_from_path(pdf_path)
        except Exception as e:
            logger.exception(f"Error converting PDF to images: {e}")
            return []

        if self._use_aws_effective:
            extracted_texts = []
            for image in images:
                text = self._process_page_aws(np.array(image))
                extracted_texts.append(text if text else "")
            return extracted_texts
        else:
            n_workers = min(multiprocessing.cpu_count(), len(images))
            with multiprocessing.Pool(processes=n_workers) as pool:
                extracted_text = pool.map(self._process_page_local, images)
            return [text if text else "" for text in extracted_text]

    def extract_text_from_pdf_page_indices(
        self, pdf_path: str, page_indices: Sequence[int]
    ) -> Dict[int, str]:
        """OCR only selected 0-based pages (e.g. PyPDF failures). Skips invalid indices."""
        out: Dict[int, str] = {}
        for idx in page_indices:
            if idx < 0:
                continue
            try:
                images = convert_from_path(
                    pdf_path, first_page=idx + 1, last_page=idx + 1
                )
            except Exception as e:
                logger.warning("OCR skip page %s of %s: %s", idx, pdf_path, e)
                continue
            if not images:
                continue
            image = images[0]
            if self._use_aws_effective:
                text = self._process_page_aws(np.array(image))
            else:
                text = self._process_page_local(image)
            out[idx] = (text or "").strip()
        return out
