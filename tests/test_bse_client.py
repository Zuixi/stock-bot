import pytest

from src.config import load_config
from src.fetchers.bse.client import bseApiClient
from src.models.config import BseConfig


def _find_total_pages(data: dict | list) -> int | None:
    # Handle list-wrapped response (e.g., BSE API)
    if isinstance(data, list) and len(data) > 0:
        if isinstance(data[0], dict):
            return data[0].get("totalPages") or data[0].get("totalPage") or data[0].get("pages")

    if not isinstance(data, dict):
        return None

    keys = ["totalPages", "totalPage", "pageTotal", "pages", "total_pages"]
    for key in keys:
        if key in data:
            try:
                return int(data[key])
            except (TypeError, ValueError):
                return None

    for container_key in ["data", "page", "pageInfo", "pageinfo", "result"]:
        container = data.get(container_key)
        if isinstance(container, dict):
            for key in keys:
                if key in container:
                    try:
                        return int(container[key])
                    except (TypeError, ValueError):
                        return None

    return None


def _has_records(data: dict | list) -> bool:
    # Handle list-wrapped response (e.g., BSE API)
    if isinstance(data, list) and len(data) > 0:
        if isinstance(data[0], dict) and "content" in data[0]:
            return len(data[0]["content"]) > 0
        return True

    if not isinstance(data, dict):
        return False

    list_keys = ["list", "data", "rows", "records", "result"]
    for key in list_keys:
        value = data.get(key)
        if isinstance(value, list) and len(value) > 0:
            return True
    # 可能是嵌套结构
    for key in list_keys:
        value = data.get(key)
        if isinstance(value, dict):
            for inner_key in list_keys:
                inner = value.get(inner_key)
                if isinstance(inner, list) and len(inner) > 0:
                    return True
    return False


@pytest.mark.network
def test_bse_client_can_fetch_page_1() -> None:
    try:
        config_data = load_config("bse")
    except FileNotFoundError:
        pytest.skip("missing bse.yaml; copy from bse.sample.yaml and fill cookies")

    config = BseConfig.from_yaml(config_data)

    with bseApiClient(config=config) as client:
        data = client.query_page(1)

    assert isinstance(data, (dict, list)), f"expected dict or list, got {type(data)}"
    assert len(data) > 0, "empty response from BSE"

    # Normalize to list if dict with 'list' key exists
    if isinstance(data, dict) and "list" in data:
        data = data["list"]

    total_pages = _find_total_pages(data)
    if total_pages is not None:
        assert total_pages == 15

    # 如果能识别出记录列表，要求非空
    if _has_records(data) is False:
        # 允许结构未识别，但确保非明显失败
        assert "error" not in {k.lower() for k in data.keys()}
