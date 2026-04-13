# 巨潮资讯 CNINFO WebAPI 接口文档

> 平台地址：<https://webapi.cninfo.com.cn/#/apiDoc>  
> 数据范围：沪深北港等交易所上市公司行情与财务数据  
> 技术支持：szsi_apidata@szse.cn / 0755-81902345（交易日 8:30-11:30，13:30-17:00）

原始 API 规格文档位于 `docs/references/cninfo/` 目录。

---

## 1. 认证方式

### 1.1 mcode 认证（所有接口均需要）

所有接口均须在 HTTP 请求头中携带 `mcode` 参数。`mcode` 为当前**秒级 Unix 时间戳**经自定义
Base64 算法编码后的结果，有效期约 5 分钟，每次请求须实时生成。

**JS 原始算法（`missjson` 函数）**：

```javascript
// 调用方式：missjson(String(Math.floor(Date.now() / 1000)))
// Date.now() 返回毫秒，除 1000 得秒
function missjson(input) {
    var keyStr = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=";
    // ... 对字符串字节逐三字节做自定义 Base64 编码 ...
}
```

**Python 等价实现**（见 [`app/core/providers/cninfo_client.py`](../app/core/providers/cninfo_client.py)）：

```python
import time

def generate_mcode() -> str:
    # Python time.time() 已是秒，不需要再除 1000
    ts = str(int(time.time()))
    KEY = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
    # ... 与 JS missjson 等价的字节编码逻辑 ...
    return encoded
```

### 1.2 access_token（付费接口）

付费接口需在请求参数中加入 `access_token`，在平台注册充值后获得。  
计费标准：**0.1 元/次**。  
通过环境变量 `CNINFO_TOKEN` 或 `CnInfoClient(token=...)` 传入。

---

## 2. 指数 API（`/api/index/`）

> 原始规格：`docs/references/cninfo/指数API/`

### 2.1 `GET /api/index/p_index2905` — 交易所指数日行情

> 规格文档：[交易所指数日行情API.md](../../docs/references/cninfo/指数API/交易所指数日行情API.md)

**接口地址**：`http://webapi.cninfo.com.cn/api/index/p_index2905`  
**最大记录数**：20,000

**请求参数**：

| 参数名   | 类型   | 必填 | 说明 |
|----------|--------|------|------|
| edate    | string | 是   | 查询截止日期，格式 `YYYY-MM-DD` |
| scode    | string | 否   | 指数代码（如 `000300`）；为空时返回该日所有交易所指数 |
| sdate    | string | 否   | 查询开始日期，用于范围查询 |
| market   | string | 否   | `上交所` 或 `深交所`，单选 |
| format   | string | 否   | 返回格式：`json`（推荐）、`xml`、`csv`、`dbf` |

**响应字段**：

| 字段        | 含义           | 类型    | 单位 |
|-------------|----------------|---------|------|
| TRADEDATE   | 交易日期       | varchar |      |
| INDEXCODE   | 指数代码       | varchar |      |
| INDEXNAME   | 指数名称       | varchar |      |
| F001V       | 交易所         | varchar |      |
| F002V       | 指数英文名称   | varchar |      |
| F003N       | 开市指数       | decimal |      |
| F004N       | 最高指数       | decimal |      |
| F005N       | 最低指数       | decimal |      |
| **F006N**   | **最近指数（收盘）** | decimal |   |
| **F007N**   | **昨日收市指数**    | decimal |   |
| F008N       | 成交数量       | bigint  | 股   |
| F009N       | 成交笔数       | bigint  |      |
| F010N       | 成交金额       | decimal | 元   |
| MEMO        | 备注           | varchar |      |

**系统字段映射**（`_parse_index_record`）：
- `F006N` → `close`
- `F007N` → `prev_close`；`change = F006N - F007N`；`changePercent = change / F007N * 100`

**目标指数代码**（`market_service.py` `_TARGET_INDEX_CODES`）：

| 代码   | 名称     | 交易所 |
|--------|----------|--------|
| 000001 | 上证指数 | 上交所 |
| 000016 | 上证50   | 上交所 |
| 000300 | 沪深300  | 上交所 |
| 399001 | 深证成指 | 深交所 |
| 399006 | 创业板指 | 深交所 |
| 899050 | 北证50   | 北交所 |

---

### 2.2 `GET /api/index/p_swindex` — 申万指数行情

> 规格文档：[申万指数行情.md](../../docs/references/cninfo/指数API/申万指数行情.md)

**接口地址**：`http://webapi.cninfo.com.cn/api/index/p_swindex`

参数与响应结构与 `p_index2905` 相同，但返回**申万行业指数**（用于 SW 行业分析与 M2 特征工程）。

| 参数名  | 必填 | 说明 |
|---------|------|------|
| edate   | 是   | 查询截止日期 |
| scode   | 否   | 申万指数代码；为空返回当日所有申万指数 |
| sdate   | 否   | 开始日期 |

---

### 2.3 `GET /api/index/p_index2911` — 交易所指数基本信息

> 规格文档：[交易所指数基本信息.md](../../docs/references/cninfo/指数API/交易所指数基本信息.md)

**接口地址**：`http://webapi.cninfo.com.cn/api/index/p_index2911`

返回指数 ID、名称、代码、类型、发布机构等基础元数据，用于构建指数目录。

---

## 3. 股票行情 API（`/api/sysapi/`）

### 3.1 `POST /api/sysapi/p_sysapi1015` — 个股日行情

> ⚠️ 注意：此接口自 2024 年起需要付费 `token` 才能访问（error code 451）。

**接口地址**：`http://webapi.cninfo.com.cn/api/sysapi/p_sysapi1015`  
**请求方式**：POST（`application/x-www-form-urlencoded`）

**请求参数**：

| 参数名 | 必填 | 说明 |
|--------|------|------|
| tdate  | 是   | 交易日期，格式 `YYYYMMDD` |
| scode  | 是   | 股票代码（如 `600519`） |
| token  | 是*  | 付费 token（无 token 时 resultcode=451） |

**响应字段映射**：

| 字段   | 含义   | 映射到 `DailyQuote` |
|--------|--------|---------------------|
| ZQDM   | 证券代码 | —（用于匹配 stock_id）|
| TDATE  | 交易日期 | `trade_date`       |
| OPRICE | 开盘价   | `open`             |
| HPRICE | 最高价   | `high`             |
| LPRICE | 最低价   | `low`              |
| CPRICE | 收盘价   | `close`            |
| CJSL   | 成交数量 | `volume`           |
| CJJE   | 成交金额 | `amount`           |

---

## 4. 数据源降级策略

| 数据类型     | Tier 1（CNINFO，需 token）          | Tier 2（AKShare，免费）               | Tier 3（静态 Mock）  |
|--------------|-------------------------------------|---------------------------------------|----------------------|
| 市场指数     | `p_index2905`                       | `ak.stock_zh_index_spot_sina()`       | `MARKET_INDICES`     |
| 申万行业指数 | `p_swindex`                         | —                                     | —                    |
| 个股日行情   | `p_sysapi1015`（需 token）          | `ak.stock_zh_a_hist()`                | —                    |
| 板块行情     | 待接入                              | `ak.stock_board_industry_name_em()`   | `MARKET_SECTORS`     |
| 板块资金流   | 待接入                              | `ak.stock_fund_flow_industry()`       | `MARKET_CAPITAL_FLOW`|

> ⚠️ 测试发现：`p_index2905` 和 `p_swindex` 同样返回 error 451，需要注册并配置 `CNINFO_TOKEN`。
> 未配置 token 时，市场指数自动降级至 Tier 2（AKShare）。

---

## 5. 相关代码位置

| 模块               | 路径 |
|--------------------|------|
| CNINFO 客户端      | [`app/core/providers/cninfo_client.py`](../app/core/providers/cninfo_client.py) |
| 行情 Worker        | [`app/workers/quotes_worker.py`](../app/workers/quotes_worker.py) |
| 大盘数据服务       | [`app/services/market_service.py`](../app/services/market_service.py) |
| 大盘 API 路由      | [`app/api/v1/market.py`](../app/api/v1/market.py) |
| 原始 API 规格      | [`docs/references/cninfo/`](../../docs/references/cninfo/) |
