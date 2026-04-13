获取股票数据服务，负责获取三大交易所数据，数据清洗和存储。

数据来源：
- [CNINFO 巨潮资讯 WebAPI](https://webapi.cninfo.com.cn/#/apiDoc)

接口说明文档：
- [docs/cninfo_api.md](../../docs/cninfo_api.md)

主要服务模块：
- `stock_service.py`：股票池列表、分类、搜索（数据源：PostgreSQL + Redis 缓存）
- `quote_service.py`：个股 K 线与最新行情（数据源：PostgreSQL `daily_quotes` 表，由 QuotesWorker 通过 CNINFO `p_sysapi1015` 写入）
- `market_service.py`：大盘指数、涨跌分布、板块行情、资金流（数据源：AKShare 异步拉取，CNINFO 付费接口预留）
- `universe_ingest.py`：股票池爬取与入库（数据源：各交易所爬虫 + AKShare 备用）
