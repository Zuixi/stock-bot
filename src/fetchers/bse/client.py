
"""BSE listed company JSONP client.

This client mirrors the SSE one but targets the BSE
"""

import json
from pathlib import Path
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

from src.models.config import BseConfig
from src.config import load_config


class bseApiError(Exception):
    """Custom exception for BSE API errors."""

    def __init__(self, message: str, response_text: str | None = None):
        super().__init__(message)
        self.response_text = response_text


class bseApiClient:
    """Minimal client for https://www.bse.cn/nqxxController/nqxxCnzq.do.

    It issues the same POST request as the exchange web page,
    with a JSONP callback in the query string and form-data
    payload in the body.
    """

    def __init__(
        self,
        config: BseConfig | None = None,
        *,
        timeout: float | None = None,
        max_requests_per_second: float | None = None,
        cookies: dict[str, str] | None = None,
    ):
        self.config = config or BseConfig()
        self._client: httpx.Client | None = None
        self._last_request_time: float = 0.0

        if timeout is not None:
            self.config.timeout = timeout
        if max_requests_per_second is not None:
            self.config.rate_limit.requests_per_second = max_requests_per_second
        if cookies is not None:
            self.config.cookies = cookies

    def _get_client(self) -> httpx.Client:
        """Create an httpx client with basic headers."""
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

    def _generate_callback_name(self) -> str:
        """Generate a unique JSONP callback function name.

        The BSE site uses names like
        jQuery371014360643062038658_1770042569355
        """

        raw = str(time.time()) + str(random.random())
        digits = re.sub(r"\D", "", raw)
        guid = int(time.time() * 1000)
        return f"jQuery{digits}_{guid}"

    def _rate_limit(self) -> None:
        """Simple client-side rate limiting."""
        if self.config.rate_limit.requests_per_second <= 0:
            return

        min_interval = 1.0 / self.config.rate_limit.requests_per_second
        elapsed = time.time() - self._last_request_time
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    def _parse_jsonp(self, text: str, callback_name: str) -> dict[str, Any]:
        """Strip JSONP wrapper and return JSON payload."""

        text = text.strip()
        pattern = rf"^{re.escape(callback_name)}\s*\(\s*(.*)\s*\);?\s*$"
        match = re.match(pattern, text, re.DOTALL)
        if not match:
            # 退一步：只要是 callback( ... ) 就接受
            generic = r"^\w+\s*\(\s*(.*)\s*\);?\s*$"
            match = re.match(generic, text, re.DOTALL)
        if not match:
            raise bseApiError("Failed to parse BSE JSONP response", text[:500])

        body = match.group(1)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise bseApiError(f"Invalid JSON from BSE: {exc}", body[:500]) from exc

    def _make_request(self, page_no: int, callback_name: str) -> httpx.Response:
        """Issue the POST request that BSE expects."""

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

            params = {"callback": callback_name}

            # 与浏览器抓包一致的 form-data
            data = {
                "page": str(page_no),
                "typejb": "T",
                "xxfcbj[]": "2",
                "xxzqdm": "",
                "sortfield": "xxzqdm",
                "sorttype": "asc",
            }

            resp = client.post(self.config.endpoint, params=params, data=data)
            resp.raise_for_status()
            return resp

        return _do_request()

    def query_page(self, page_no: int) -> dict[str, Any]:
        """Query a specific page from the BSE API.

        This builds the same POST + JSONP request as the
        browser and returns the decoded JSON dict for that
        page.
        """

        callback_name = self._generate_callback_name()
        response = self._make_request(page_no, callback_name)
        return self._parse_jsonp(response.text, callback_name)

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "bseApiClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def _extract_first_record(data: Any) -> dict[str, Any] | None:
    """Best-effort extraction of the first record from a BSE response."""
    if isinstance(data, list):
        if not data:
            return None
        first = data[0]
        if isinstance(first, dict):
            for key in ["content", "list", "data", "rows", "records", "result"]:
                value = first.get(key)
                if isinstance(value, list) and value:
                    return value[0]
            return first
        return None

    if isinstance(data, dict):
        list_keys = ["list", "data", "rows", "records", "result", "content"]
        for key in list_keys:
            value = data.get(key)
            if isinstance(value, list) and value:
                return value[0]
        for key in list_keys:
            value = data.get(key)
            if isinstance(value, dict):
                for inner_key in list_keys:
                    inner = value.get(inner_key)
                    if isinstance(inner, list) and inner:
                        return inner[0]
    return None


def main() -> int:
    """Quick manual test for the BSE client.

    Requires bse.yaml in src/config (copy from bse.sample.yaml).
    """
    try:
        config_data = load_config("bse")
    except FileNotFoundError:
        print("missing bse.yaml; copy from bse.sample.yaml and fill cookies")
        return 2

    config = BseConfig.from_yaml(config_data)

    with bseApiClient(config=config) as client:
        data = client.query_page(1)

    if not isinstance(data, (dict, list)):
        print("response is not JSON object or list")
        return 1

    output_dir = Path("data") / "bse"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "page_1.json"
    output_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"saved response to {output_path}")
    print("response is JSON")
    first_record = _extract_first_record(data)
    if first_record is None:
        print("no records found in response")
        return 1

    print("first record:", first_record)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
