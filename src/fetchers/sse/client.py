"""SSE commonQuery.do JSONP client."""

import json
import random
import re
import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.models.config import SseConfig


class SseApiError(Exception):
    """SSE API error."""

    def __init__(self, message: str, response_text: str | None = None):
        super().__init__(message)
        self.response_text = response_text


class SseCommonQueryClient:
    """Client for SSE commonQuery.do JSONP API.

    Handles JSONP parsing, rate limiting, and retries.
    """

    def __init__(self, config: SseConfig):
        self.config = config
        self._client: httpx.Client | None = None
        self._last_request_time: float = 0.0

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

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "SseCommonQueryClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _generate_callback_name(self) -> str:
        """Generate a random JSONP callback name."""
        rand_num = random.randint(10000000, 99999999)
        return f"{self.config.jsonp.callback_prefix}{rand_num}"

    def _parse_jsonp(self, text: str, callback_name: str) -> dict[str, Any]:
        """Parse JSONP response and extract JSON payload.

        Args:
            text: Raw JSONP response text
            callback_name: Expected callback function name

        Returns:
            Parsed JSON object

        Raises:
            SseApiError: If parsing fails or response indicates error
        """
        text = text.strip()

        # Check for common error responses
        if "System Error" in text or "系统繁忙" in text:
            raise SseApiError("SSE API returned System Error", text[:500])

        if text.startswith("<!") or text.startswith("<html"):
            raise SseApiError("SSE API returned HTML error page", text[:500])

        # Try to extract JSON from JSONP wrapper
        # Pattern: callbackName({...}) or callbackName({...});
        pattern = rf"^{re.escape(callback_name)}\s*\(\s*(.*)\s*\);?\s*$"
        match = re.match(pattern, text, re.DOTALL)

        if not match:
            # Try more lenient pattern (any callback name)
            lenient_pattern = r"^\w+\s*\(\s*(.*)\s*\);?\s*$"
            match = re.match(lenient_pattern, text, re.DOTALL)

        if not match:
            raise SseApiError(f"Failed to parse JSONP response", text[:500])

        json_str = match.group(1)

        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            raise SseApiError(f"Failed to parse JSON: {e}", json_str[:500]) from e

    def _rate_limit(self) -> None:
        """Apply rate limiting between requests."""
        if self.config.rate_limit.requests_per_second <= 0:
            return

        min_interval = 1.0 / self.config.rate_limit.requests_per_second
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
            response = client.get(self.config.endpoint, params=params)
            response.raise_for_status()
            return response

        return _do_request()

    def query_page(self, page_no: int) -> dict[str, Any]:
        """Query a single page of stock data.

        Args:
            page_no: Page number (1-indexed)

        Returns:
            Parsed JSON response containing pageHelp.data and metadata
        """
        callback_name = self._generate_callback_name()
        timestamp = int(time.time() * 1000)

        # Build query parameters
        params: dict[str, Any] = {
            self.config.jsonp.param_name: callback_name,
            "_": timestamp,
        }

        # Add fixed query params
        params.update(self.config.query)

        # Add filter params
        params.update(self.config.filters)

        # Add pagination params
        params.update({
            "pageHelp.pageNo": page_no,
            "pageHelp.pageSize": self.config.pagination.page_size,
            "pageHelp.beginPage": page_no,
            "pageHelp.endPage": page_no,
            "pageHelp.cacheSize": self.config.pagination.cache_size,
        })

        response = self._make_request(params)
        data = self._parse_jsonp(response.text, callback_name)

        # Validate response structure
        if "pageHelp" not in data:
            raise SseApiError("Response missing 'pageHelp' field", str(data)[:500])

        return data

    def get_page_data(self, page_no: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Get stock records and pagination info for a page.

        Args:
            page_no: Page number (1-indexed)

        Returns:
            Tuple of (records list, page_help metadata dict)
        """
        response = self.query_page(page_no)
        page_help = response.get("pageHelp", {})
        records = page_help.get("data", [])

        if records is None:
            records = []

        return records, page_help
