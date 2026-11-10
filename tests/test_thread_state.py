import pickle
import threading

from goblintools.thread_state import ThreadLocalAttribute, ThreadLocalStateMixin


class _Holder(ThreadLocalStateMixin):
    report = ThreadLocalAttribute(lambda: None)
    failures = ThreadLocalAttribute(dict)

    def __init__(self):
        self.name = "holder"
        self.report = "constructor"


def _run_in_thread(func):
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("value", func()))
    thread.start()
    thread.join()
    return result["value"]


def test_each_thread_starts_from_the_default():
    holder = _Holder()

    assert holder.report == "constructor"
    assert _run_in_thread(lambda: holder.report) is None
    assert _run_in_thread(lambda: holder.failures) == {}


def test_default_is_kept_after_the_first_read():
    holder = _Holder()

    holder.failures[3] = "throttled"

    assert holder.failures == {3: "throttled"}


def test_threads_do_not_see_each_other_values():
    holder = _Holder()
    barrier = threading.Barrier(2)
    seen = {}

    def worker(name):
        holder.report = name
        holder.failures[0] = name
        barrier.wait()
        seen[name] = (holder.report, dict(holder.failures))

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen == {"a": ("a", {0: "a"}), "b": ("b", {0: "b"})}


def test_pickle_round_trip_drops_the_thread_state():
    holder = _Holder()
    holder.failures[1] = "rasterize"

    restored = pickle.loads(pickle.dumps(holder))

    assert restored.name == "holder"
    assert restored.report is None
    assert restored.failures == {}


def test_class_access_returns_the_descriptor():
    assert isinstance(_Holder.report, ThreadLocalAttribute)
