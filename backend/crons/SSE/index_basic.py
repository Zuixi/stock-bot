"""
SSE Index Real-time Data Scraper
上海证券交易所指数实时数据采集爬虫

功能：
- 从 SSE 官方 JSONP 接口获取多个股票指数的实时行情
- 9:30-15:00 每 10 分钟采集一次（含随机抖动）
- 反爬策略：UA 轮换、请求头伪造、指数退避重试
- 数据以 CSV 格式按天存储
"""

import csv
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

# ============================================================
# 常量定义
# ============================================================

BASE_URL = "https://yunhq.sse.com.cn:32042/v1/csip/list/self/"
INDEX_CODES = (
    "000001_000002_000003_000009_000010_000016_000017_000020"
    "_000043_000044_000045_000046_000047_000090_000132_000133"
    "_000155_000680_000681_000688_000698_000699_950580"
)
SELECT_FIELDS = "prev_close,open,high,low,last,chg_rate,code,name"
JQUERY_VERSION = "3.7.1"

# 采集时间段
TRADE_START = (9, 30)   # 9:30
TRADE_END = (15, 0)     # 15:00
INTERVAL_MIN = 10       # 每 10 分钟
JITTER_SEC = 30         # 随机抖动 ±30 秒
MAX_RETRIES = 3         # 最大重试次数

# 数据输出
DATA_DIR = Path(__file__).parent / "data" / "sse_indices"

# 日志
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

# User-Agent 池 —— 覆盖 Chrome / Edge / Firefox 主流版本
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

# CSV 列定义
CSV_COLUMNS = ["collect_time", "code", "name", "prev_close", "open", "high", "low", "last", "chg_rate"]


# ============================================================
# callback 参数生成（jQuery expando 复现）
# ============================================================

def generate_callback() -> str:
    """
    模拟 jQuery expando 生成 callback 参数名。

    jQuery 源码逻辑：
        expando = "jQuery" + ( version + Math.random() ).replace( /\D/g, "" )

    即：版本号字符串 + random() 返回的浮点数字符串，拼接后去除所有非数字字符。
    示例：
        "3.7.1" + "0.5569336422301754" → 去除非数字 → "37105569336422301754"
        最终 callback = "jQuery37105569336422301754"
    """
    rand_str = str(random.random())  # e.g. "0.5569336422301754"
    combined = JQUERY_VERSION + rand_str  # e.g. "3.7.10.5569336422301754"
    digits_only = re.sub(r"\D", "", combined)  # e.g. "37105569336422301754"
    return f"jQuery{digits_only}"


# ============================================================
# JSONP 响应解析
# ============================================================

def parse_jsonp(text: str) -> dict:
    """
    从 JSONP 响应中提取 JSON 数据。

    响应格式示例：
        jQuery37105569336422301754_1776754720000({"list":[...],"date":...})

    提取括号内的 JSON 部分并解析。
    """
    # 匹配 callback(JSON) 格式
    # callback 可能是 jQuery37108864502359718892 或 jQuery37108864502359718892_1776754720000
    match = re.search(r"jQuery[\d_]+\((.+)\)$", text, re.DOTALL)
    if not match:
        raise ValueError(f"无法解析 JSONP 响应，格式不匹配: {text[:200]}")

    json_str = match.group(1)
    return json.loads(json_str)


# ============================================================
# 数据采集
# ============================================================

def build_headers() -> dict:
    """构造完整的浏览器请求头，模拟真实浏览器访问。"""
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": "https://yunhq.sse.com.cn/",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }


def fetch_index_data() -> list[dict]:
    """
    请求 SSE 指数接口，返回解析后的指数数据列表。

    Returns:
        list[dict]: 每个元素为一个指数的行情数据
    """
    callback = generate_callback()
    timestamp_ms = int(time.time() * 1000)

    params = {
        "callback": callback,
        "select": SELECT_FIELDS,
        "_": timestamp_ms,
    }

    url = BASE_URL + INDEX_CODES

    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"请求 SSE 指数数据 (第 {attempt}/{MAX_RETRIES} 次)...")
            resp = requests.get(url, params=params, headers=build_headers(), timeout=10)
            resp.raise_for_status()

            data = parse_jsonp(resp.text)
            # SSE 返回结构: {"date": "20260421", "time": "093009", "list": [[...], ...]}
            raw_list = data.get("list", [])
            if not raw_list:
                logger.warning("接口返回数据为空（可能非交易时间或市场休市）")
                return []

            # 将列表数据映射为字典
            # select 字段顺序: prev_close, open, high, low, last, chg_rate, code, name
            field_names = ["prev_close", "open", "high", "low", "last", "chg_rate", "code", "name"]
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            records = []
            for item in raw_list:
                if len(item) < len(field_names):
                    logger.warning(f"数据字段不足，跳过: {item}")
                    continue
                record = {"collect_time": now_str}
                for i, field in enumerate(field_names):
                    record[field] = item[i]
                records.append(record)

            logger.info(f"成功获取 {len(records)} 条指数数据")
            return records

        except (requests.RequestException, ValueError, json.JSONDecodeError) as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                wait = 2 ** attempt  # 指数退避: 2s, 4s
                logger.warning(f"请求失败: {e}，{wait}s 后重试...")
                time.sleep(wait)
            else:
                logger.error(f"请求失败，已达最大重试次数: {e}")

    raise RuntimeError(f"数据采集失败，已重试 {MAX_RETRIES} 次") from last_exc


# ============================================================
# 数据存储
# ============================================================

def save_to_csv(records: list[dict], date_str: str | None = None) -> Path:
    """
    将采集数据追加写入当天的 CSV 文件。

    Args:
        records: 指数数据列表
        date_str: 日期字符串，默认使用当天日期

    Returns:
        Path: 写入的 CSV 文件路径
    """
    if not records:
        logger.info("无数据需要保存")
        return Path("")

    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = DATA_DIR / f"sse_indices_{date_str}.csv"

    file_exists = csv_path.exists() and csv_path.stat().st_size > 0

    with open(csv_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if not file_exists:
            writer.writeheader()
        writer.writerows(records)

    logger.info(f"数据已追加写入: {csv_path} ({len(records)} 条)")
    return csv_path


# ============================================================
# 调度逻辑
# ============================================================

def is_workday(dt: datetime | None = None) -> bool:
    """判断是否为工作日（周一=0 ... 周日=6）。"""
    if dt is None:
        dt = datetime.now()
    return dt.weekday() < 5


def get_trade_start_today() -> datetime:
    """获取今天的交易开始时间。"""
    now = datetime.now()
    return now.replace(hour=TRADE_START[0], minute=TRADE_START[1], second=0, microsecond=0)


def get_trade_end_today() -> datetime:
    """获取今天的交易结束时间。"""
    now = datetime.now()
    return now.replace(hour=TRADE_END[0], minute=TRADE_END[1], second=0, microsecond=0)


def calculate_next_collect_times() -> list[datetime]:
    """
    计算今天所有采集时间点（基于绝对时间 + 随机抖动）。

    基准时间点：9:30, 9:40, 9:50, ..., 14:50, 15:00
    每个时间点加 ±JITTER_SEC 的随机偏移。
    """
    base = get_trade_start_today()
    end = get_trade_end_today()
    times = []

    t = base
    while t <= end:
        # 添加 ±JITTER_SEC 的随机偏移
        jitter = random.randint(-JITTER_SEC, JITTER_SEC)
        adjusted = t + timedelta(seconds=jitter)
        # 确保偏移后不超过交易时间段
        if adjusted < base:
            adjusted = base
        if adjusted > end:
            adjusted = end
        times.append(adjusted)
        t += timedelta(minutes=INTERVAL_MIN)

    return times


def run_once() -> None:
    """执行一次完整采集流程：获取数据 → 保存 CSV。"""
    try:
        records = fetch_index_data()
        if records:
            save_to_csv(records)
        else:
            logger.info("本次采集无数据（可能非交易时间）")
    except Exception as e:
        logger.error(f"采集流程异常: {e}", exc_info=True)


def run_scheduled() -> None:
    """
    进入定时调度模式：在 9:30-15:00 之间每 10 分钟采集一次。

    如果当前时间已过 9:30 但还没到 15:00，会从下一个采集点开始。
    如果当前时间已过 15:00，直接退出。
    """
    if not is_workday():
        logger.info("今天不是工作日，跳过采集")
        return

    now = datetime.now()
    trade_start = get_trade_start_today()
    trade_end = get_trade_end_today()

    if now >= trade_end:
        logger.info("已过交易时间 (15:00)，今日采集结束")
        return

    # 如果还没到 9:30，等待到 9:30
    if now < trade_start:
        wait_sec = (trade_start - now).total_seconds()
        logger.info(f"等待交易开始，还需 {wait_sec:.0f} 秒...")
        time.sleep(wait_sec)

    # 计算所有采集时间点
    collect_times = calculate_next_collect_times()
    logger.info(f"今日计划采集 {len(collect_times)} 次")

    for i, target_time in enumerate(collect_times):
        now = datetime.now()
        if now > trade_end:
            logger.info("已过交易时间，采集结束")
            break

        # 等待到目标时间
        if now < target_time:
            wait_sec = (target_time - now).total_seconds()
            logger.info(f"下次采集: {target_time.strftime('%H:%M:%S')}（{wait_sec:.0f}s 后）")
            time.sleep(wait_sec)

        logger.info(f"--- 第 {i + 1}/{len(collect_times)} 次采集 [{datetime.now().strftime('%H:%M:%S')}] ---")
        run_once()

    logger.info("今日所有采集任务完成")


# ============================================================
# 入口
# ============================================================

def main():
    """主入口：支持命令行参数选择运行模式。"""
    if len(sys.argv) > 1:
        mode = sys.argv[1]
    else:
        mode = "scheduled"

    if mode == "once":
        # 单次采集模式：立即采集一次
        logger.info("=== 单次采集模式 ===")
        run_once()
    elif mode == "scheduled":
        # 定时调度模式
        logger.info("=== 定时调度模式 ===")
        run_scheduled()
    else:
        print(f"用法: python {sys.argv[0]} [once|scheduled]")
        print("  once      - 立即采集一次")
        print("  scheduled - 定时调度（默认）")
        sys.exit(1)


if __name__ == "__main__":
    main()
