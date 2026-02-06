
"""SZSE listed company JSONP client.

This client mirrors the SSE one but targets the SZSE
"""

import json
import random
import re
import time
from pathlib import Path
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config import load_config
from src.models.config import SzseConfig


class szseApiError(Exception):
    """Custom exception for SZSE API errors."""

    def __init__(self, message: str, response_text: str | None = None):
        super().__init__(message)
        self.response_text = response_text


class szseApiClient:
    """Minimal client for https://www.szse.cn/market/product/stock/list/

    It issues the same GET request as the exchange web page,
    with query string parameters and a JSON response.
    """

    # Cookie storage path
    COOKIE_CACHE_PATH = Path("data/szse_cookies.json")

    def __init__(
        self,
        config: SzseConfig | None = None,
        *,
        timeout: float | None = None,
        max_requests_per_second: float | None = None,
        cookies: dict[str, str] | None = None,
        auto_fetch_cookies: bool = True,
    ):
        self.config = config or SzseConfig()
        self._client: httpx.Client | None = None
        self._last_request_time: float = 0.0
        self._auto_fetch_cookies = auto_fetch_cookies
        self._fetched_cookies: dict[str, str] | None = None

        if timeout is not None:
            self.config.timeout = timeout
        if max_requests_per_second is not None:
            self.config.rate_limit.requests_per_second = max_requests_per_second
        if cookies is not None:
            self.config.cookies = cookies
            self._auto_fetch_cookies = False  # User provided cookies, skip auto-fetch

    @classmethod
    def load_cached_cookies(cls) -> dict[str, str]:
        """Load cookies from cache file if exists."""
        if cls.COOKIE_CACHE_PATH.exists():
            try:
                return json.loads(cls.COOKIE_CACHE_PATH.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    @classmethod
    def save_cookies(cls, cookies: dict[str, str]) -> None:
        """Save cookies to cache file."""
        cls.COOKIE_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cls.COOKIE_CACHE_PATH.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _ensure_cookies(self) -> None:
        """Ensure we have valid cookies, fetching from homepage if needed."""
        # If cookies already configured, use them
        if self.config.cookies:
            return

        # Try to load from cache
        cached = self.loaded_cookies()
        if cached:
            self.config.cookies = cached
            return

        # Auto-fetch cookies from homepage
        if self._auto_fetch_cookies:
            self._fetch_cookies_from_homepage()

    @classmethod
    def loaded_cookies(cls) -> dict[str, str]:
        """Load cookies from cache file if exists (class method for external use)."""
        return cls.load_cached_cookies()

    def _fetch_cookies_from_homepage(self) -> None:
        """Fetch cookies by visiting the SZSE homepage.

        Note: SZSE may require JavaScript to set cookies. This method
        attempts to get cookies via HTTP, but if none are returned,
        manual cookie extraction from browser may be required.
        """
        print("Attempting to fetch cookies from SZSE homepage...")

        client = httpx.Client(
            timeout=self.config.timeout,
            follow_redirects=True,
        )

        try:
            # Visit the stock list page to get initial cookies
            homepage_url = "https://www.szse.cn/market/product/stock/list/"
            resp = client.get(homepage_url)
            resp.raise_for_status()

            # Extract cookies from the response
            cookies: dict[str, str] = {}
            for cookie in client.cookies:
                cookies[cookie.name] = cookie.value

            if cookies:
                self.config.cookies = cookies
                self._fetched_cookies = cookies
                # Save to cache
                self.save_cookies(cookies)
                print(f"Obtained {len(cookies)} cookies from SZSE")
            else:
                # Try making a request to the API endpoint to see if it sets cookies
                client.get(
                    "https://www.szse.cn/api/report/ShowReport",
                    params={
                        "SHOWTYPE": "JSON",
                        "CATALOGID": "1110",
                        "TABKEY": "tab1",
                        "PAGENO": "1",
                    },
                )
                # Check again for cookies after API request
                for cookie in client.cookies:
                    if cookie.name not in cookies:
                        cookies[cookie.name] = cookie.value

                if cookies:
                    self.config.cookies = cookies
                    self._fetched_cookies = cookies
                    self.save_cookies(cookies)
                    print(f"Obtained {len(cookies)} cookies after API request")
                else:
                    print("Warning: SZSE did not return HTTP cookies.")
                    print("Note: SZSE may require browser-based authentication.")
                    print("Please manually extract cookies from browser DevTools:")
                    print("  1. Open browser DevTools (F12)")
                    print("  2. Go to Network tab")
                    print("  3. Visit https://www.szse.cn/market/product/stock/list/")
                    print("  4. Copy the Cookie header value")
                    print("  5. Add to szse.yaml cookies section")

        finally:
            client.close()

    def _get_client(self) -> httpx.Client:
        """Create an httpx client with basic headers."""
        if self._client is None:
            # Ensure we have cookies
            self._ensure_cookies()

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

        The SZSE site uses names like
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
            raise szseApiError("Failed to parse BSE JSONP response", text[:500])

        body = match.group(1)
        try:
            return json.loads(body)
        except json.JSONDecodeError as exc:
            raise szseApiError(f"Invalid JSON from SZSE: {exc}", body[:500]) from exc

    def _make_request(self, page_no: int) -> httpx.Response:
        """Issue the GET request that SZSE expects."""

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

            # 与浏览器抓包一致的 query params
            params = {
                "SHOWTYPE": "JSON",
                "CATALOGID": "1110",
                "TABKEY": "tab1",  # SZSE uses tab1 for listed companies
                "PAGENO": str(page_no),
                "random": str(random.random()),
            }

            resp = client.get(self.config.endpoint, params=params)
            resp.raise_for_status()
            return resp

        return _do_request()

    def query_page(self, page_no: int) -> list[dict[str, Any]]:
        """Query a specific page from the SZSE API.

        This builds the same GET request as the browser and
        returns the decoded JSON dict for that page.
        """

        response = self._make_request(page_no)

        # Check if response is HTML (indicates missing cookies or API error)
        if "text/html" in response.headers.get("content-type", "").lower():
            raise szseApiError(
                "SZSE API returned HTML instead of JSON. "
                "This usually means cookies are missing or expired. "
                "Please update szse.yaml with valid cookies from browser DevTools.",
                response.text[:500],
            )

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise szseApiError(f"Invalid JSON from SZSE: {exc}", response.text[:500]) from exc

        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise szseApiError("SZSE response is not a list of objects", str(type(payload)))

        return payload

    def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "SzseConfig":
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
    """Quick manual test for the SZSE client.

    Usage:
        python -m src.fetchers.szse.client [page_no]

    Options:
        --clear-cache    Clear cached cookies and exit
        --fetch-cookies  Force re-fetch cookies from homepage
    """

    import sys

    # Parse arguments
    args = sys.argv[1:]
    clear_cache = "--clear-cache" in args
    force_fetch = "--fetch-cookies" in args

    # Remove flags from args
    args = [a for a in args if not a.startswith("--")]

    if clear_cache:
        if szseApiClient.COOKIE_CACHE_PATH.exists():
            szseApiClient.COOKIE_CACHE_PATH.unlink()
            print("Cleared cached cookies")
        else:
            print("No cached cookies to clear")
        return 0

    if force_fetch:
        client = szseApiClient(auto_fetch_cookies=True)
        client._fetch_cookies_from_homepage()
        if client._fetched_cookies:
            print(f"Cookies saved to: {szseApiClient.COOKIE_CACHE_PATH}")
            return 0
        else:
            print("Failed to fetch cookies")
            return 1

    # Get page number
    page_no: int = 1
    if args:
        try:
            page_no = int(args[0])
        except ValueError:
            print(f"Invalid page number: {args[0]}")
            return 1
    else:
        input_page = input(f"Enter page number to query (default {page_no}): ").strip()
        if input_page:
            try:
                page_no = int(input_page)
            except ValueError:
                print("invalid page number")
                return 1

    # Try to load config if available, otherwise use defaults
    config = None
    try:
        config_data = load_config("szse")
        config = SzseConfig.from_yaml(config_data)
        print("Loaded config from szse.yaml")
    except FileNotFoundError:
        print("No szse.yaml found, using default config")

    with szseApiClient(config=config) as client:
        data = client.query_page(page_no)

    if not isinstance(data, (dict, list)):
        print("response is not JSON object or list")
        return 1

    output_dir = Path("data") / "szse"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"page_{page_no}.json"
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
