"""Base class for wrapping synchronous third-party SDKs with throttling + retry.

DRY: TuShareClient 与 AkShareClient 共用此基类——限流、重试、asyncio.to_thread
包装逻辑只实现一次；子类仅声明请求间隔/重试次数并按需覆写错误处理与结果规整。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class RateLimitedSyncProvider:
    """Serialize sync SDK calls through a throttle window with retry on failure.

    Throttle + call share one critical section so concurrent threads cannot
    bypass the global request interval (same semantics as the original
    TuShareClient implementation this base was extracted from).
    """

    request_interval: float = 0.5  # seconds between requests
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0

    def __init__(self) -> None:
        self._last_request_time: float = 0.0
        self._throttle_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Core invoke loop
    # ------------------------------------------------------------------

    def invoke_sync(self, api_name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Run ``fn(*args, **kwargs)`` with throttle + retry; never returns None
        unless the wrapped call does — see :meth:`normalize_result`."""
        for attempt in range(1, self.max_retries + 1):
            try:
                with self._throttle_lock:
                    elapsed = time.monotonic() - self._last_request_time
                    if elapsed < self.request_interval:
                        time.sleep(self.request_interval - elapsed)
                    self._last_request_time = time.monotonic()
                    result = fn(*args, **kwargs)
                return self.normalize_result(result)
            except Exception as exc:
                # Hook may re-raise (e.g. permission errors are not retryable).
                self.handle_error(api_name, exc)
                logger.warning(
                    "%s %s attempt %d/%d failed: %s",
                    type(self).__name__, api_name, attempt, self.max_retries, exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_backoff_seconds)
        raise RuntimeError(
            f"{type(self).__name__} API '{api_name}' failed after {self.max_retries} retries"
        )

    async def invoke_async(
        self, api_name: str, fn: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        return await asyncio.to_thread(self.invoke_sync, api_name, fn, *args, **kwargs)

    # ------------------------------------------------------------------
    # Hooks for subclasses
    # ------------------------------------------------------------------

    def handle_error(self, api_name: str, exc: Exception) -> None:
        """Inspect an exception before retry. Re-raise to abort retrying."""

    def normalize_result(self, result: Any) -> Any:
        """Map a successful (possibly None) result to the caller-facing value."""
        return result
