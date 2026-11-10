"""Retry utility for transient failures."""
import random
import time
import functools
from typing import Callable, Optional, Tuple, Type, TypeVar

T = TypeVar("T")


def retry_with_backoff(
    max_retries: int = 3,
    backoff_factor: float = 2.0,
    initial_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
):
    """Decorator that retries a function on transient failures with exponential backoff."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt == max_retries:
                        raise
                    delay = min(initial_delay * (backoff_factor ** attempt), max_delay)
                    time.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


def retry_until_deadline(
    func: Callable[[], T],
    max_wait_s: float,
    base_delay: float,
    max_delay: float,
    exceptions: Tuple[Type[Exception], ...],
    sleep: Optional[Callable[[float], None]] = None,
    clock: Optional[Callable[[], float]] = None,
    rng: Optional[Callable[[], float]] = None,
) -> T:
    sleep = sleep or time.sleep
    clock = clock or time.monotonic
    rng = rng or random.random
    budget = max(0.0, float(max_wait_s))
    start = clock()
    attempt = 0
    while True:
        try:
            return func()
        except exceptions:
            delay = min(max_delay, base_delay * (2 ** min(attempt, 30))) * rng()
            if budget <= 0 or clock() - start + delay > budget:
                raise
            sleep(delay)
            attempt += 1
