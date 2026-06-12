"""Polling helpers for e2e."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception_type,
    retry_if_result,
    stop_after_delay,
    wait_exponential,
    wait_fixed,
)

from tests.e2e.log import clip_log_body, log

from .constants import POLL_INTERVAL

T = TypeVar("T")

def _diag(message: str) -> None:
    log.debug("mailflow_diag", detail=message)


def mailflow_diag_block(title: str, body: str, *, max_chars: int = 20000) -> None:
    """Многострочный дамп в stderr для анализа mailflow (IMAP bridge / notmuch / systemd)."""
    truncated = body if len(body) <= max_chars else body[:max_chars] + "\n... [mailflow_diag_block truncated] ...\n"
    log.debug(
        "mailflow_diag_block",
        title=title,
        body=clip_log_body(truncated, max_len=max_chars),
    )


def mailflow_log_phase(message: str) -> None:
    """Короткая метка фазы mailflow-теста (время относительно фикстуры — в сообщении).

    Logged at INFO so the per-phase call-point timeline (``+Xs`` deltas) surfaces at the default level
    for end-to-end chain tracing, not buried in DEBUG.
    """
    log.info("mailflow_phase", phase=message)


# Surface slow-but-passing checks at INFO so the timing of each check point in the chain is visible
# (a fast check stays at DEBUG to avoid noise); a timeout is always surfaced (hidden bug — E2E.md §5).
_POLL_SLOW_INFO_SEC = 3.0


def _poll_completed(desc: str, t_start: float, attempts: int) -> None:
    elapsed = time.monotonic() - t_start
    if elapsed >= _POLL_SLOW_INFO_SEC:
        log.info("poll_done", desc=desc, elapsed_s=round(elapsed, 1), attempts=attempts)
    else:
        _diag(f"poll done: {desc} (+{elapsed:.1f}s, {attempts} attempts)")


def _poll_timed_out(desc: str, t_start: float, attempts: int, timeout: float) -> None:
    elapsed = time.monotonic() - t_start
    log.info(
        "poll_timeout", desc=desc, elapsed_s=round(elapsed, 1), attempts=attempts, timeout_s=timeout
    )


def poll_until(
    fn: Callable[[], T | None],
    *,
    timeout: float,
    interval: float = POLL_INTERVAL,
    desc: str = "condition",
) -> T:
    """Fixed-interval poll backed by tenacity. Returns first non-None result from *fn*."""
    _diag(f"poll start: {desc} (timeout={timeout}s)")
    t_start = time.monotonic()
    attempts = 0
    report_at = t_start + min(10.0, max(3.0, float(timeout) / 4.0))

    def _before_sleep(retry_state: Any) -> None:
        nonlocal report_at, attempts
        attempts = retry_state.attempt_number
        now = time.monotonic()
        if now >= report_at:
            _diag(f"poll progress: {desc} (attempt #{retry_state.attempt_number}, +{now - t_start:.1f}s)")
            report_at = now + min(10.0, max(3.0, float(timeout) / 4.0))

    try:
        result = Retrying(
            retry=retry_if_result(lambda r: r is None) | retry_if_exception_type(Exception),
            stop=stop_after_delay(timeout),
            wait=wait_fixed(interval),
            before_sleep=_before_sleep,
        )(fn)
    except RetryError as e:
        last = e.last_attempt.exception() if e.last_attempt.failed else None
        _poll_timed_out(desc, t_start, attempts, timeout)
        msg = f"timeout waiting for {desc} ({timeout}s, waited {time.monotonic() - t_start:.1f}s, {attempts} attempts)"
        if last:
            msg += f": {last!r}"
        raise TimeoutError(msg) from last
    _poll_completed(desc, t_start, attempts)
    return result  # type: ignore[return-value]


def poll_until_backoff(
    fn: Callable[[], T | None],
    *,
    timeout: float,
    desc: str = "condition",
    progress_extra: Callable[[], str] | None = None,
) -> T:
    """Exponential-backoff poll backed by tenacity. Returns first non-None result from *fn*."""
    _diag(f"poll(backoff) start: {desc} (timeout={timeout}s)")
    t_start = time.monotonic()
    attempts = 0
    report_at = t_start + min(10.0, max(3.0, float(timeout) / 4.0))

    def _before_sleep(retry_state: Any) -> None:
        nonlocal report_at, attempts
        attempts = retry_state.attempt_number
        now = time.monotonic()
        if now >= report_at:
            extra = ""
            if progress_extra is not None:
                try:
                    extra = f" | {progress_extra()}"
                except Exception as pe:
                    extra = f" | (progress_extra failed: {pe!r})"
            _diag(
                f"poll(backoff) progress: {desc}{extra} "
                f"(attempt #{retry_state.attempt_number}, +{now - t_start:.1f}s)"
            )
            report_at = now + min(10.0, max(3.0, float(timeout) / 4.0))

    try:
        result = Retrying(
            retry=retry_if_result(lambda r: r is None) | retry_if_exception_type(Exception),
            stop=stop_after_delay(timeout),
            wait=wait_exponential(multiplier=0.25, min=0.5, max=5),
            before_sleep=_before_sleep,
        )(fn)
    except RetryError as e:
        last = e.last_attempt.exception() if e.last_attempt.failed else None
        _poll_timed_out(desc, t_start, attempts, timeout)
        msg = f"timeout waiting for {desc} ({timeout}s, waited {time.monotonic() - t_start:.1f}s, {attempts} attempts)"
        if last:
            msg += f": {last!r}"
        raise TimeoutError(msg) from last
    _poll_completed(desc, t_start, attempts)
    return result  # type: ignore[return-value]
