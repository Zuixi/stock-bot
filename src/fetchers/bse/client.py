
# urlt = {type: 'post',
# url: '/nqxxController/nqxxCnzq.do',
# dataType: 'jsonp', 
# data:t = {type: 'post', url: '/nqxxController/nqxxCnzq.do', dataType: 'jsonp', data:


import time
import random
import re
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)


class bseApiError(Exception):
    """Custom exception for BSE API errors."""
    def __init__(self, message: str, response_text: str | None = None):
        super().__init__(message)
        self.response_text = response_text

class bseApiClient:
    def __init__(self):
        self._client: httpx.Client | None = None # 3.10 联合类型
        self.baseUri: str = "https://www.bse.cn/nqxxController/nqxxCnzq.do?callback="
        self._last_request_time: float = 0.0

    def _generate_callback_name(self) -> str:
        """Generate a unique JSONP callback function name."""
        return generate_expando()

    def _get_client(self) -> httpx.Client:
        """Get or create HTTP client."""
        if self._client is None:
            headers = dict(self.config.headers)
            cookie_header = self.config.build_cookie_header()
            if cookie_header:
                headers["Cookie"] = cookie_header

            self._client = httpx.Client(
                timeout=self.config.timeout,
                headers=headers,
                follow_redirects=True,
            )
        return self._client
    
    def _generate_expando(self) -> str:
        # 时间戳 + 随机数
        raw = str(time.time()) + str(random.random())
        # 去掉非数字字符
        digits = re.sub(r"\D", "", raw)

        guid = int(time.time() * 1000) # 当前时间戳的毫秒数
        return "jQuery" + digits+ "_" + str(guid)

    def query_page(self, page_no: int) -> dict[str, Any]:
        """Query a specific page from the BSE API."""

        pass

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "bseApiClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _rate_limit() -> None:
        """Enforce rate limiting based on config."""
        if self.config.rate_limit.max_requests_per_second <= 0:
            return

        min_interval = 1.0 / self.config.rate_limit.max_requests_per_second
        elapsed = time.time() - self._last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)


    def _make_request(self, params: dict[str, Any]) -> httpx.Response:
        """Make HTTP request with retry logic."""
        client = self._get_client()

        @retry(
            retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
            stop=stop_after_attempt(self.config.retry.max_attempts),
            wait=wait_exponential(
                multiplier=self.config.retry.backoff_multiplier,
                min=self.config.retry.initial_delay,
            ),
            reraise=True,
        )
        def _do_request() -> httpx.Response:
            self._rate_limit()
            self._last_request_time = time.time()
            # post request with json data
            payload = {
                
            }
            response = client.get(self.config.endpoint, params=params)
            response.raise_for_status()
            return response

        return _do_request()
