获取股票数据服务，负责获取三大交易所数据，数据清洗和存储。

数据来源：
- [TuShare Pro API](https://tushare.pro/)（唯一数据源：stock_basic / stock_company / daily / index_daily / daily_basic / index_classify 等）

接口说明文档：
- [TuShare API 参考](../../../docs/references/tushare/index.md)

主要服务模块：
- `stock_service.py`：股票池列表、分类、搜索（数据源：PostgreSQL + Redis 缓存）
- `quote_service.py`：个股 K 线与最新行情（数据源：PostgreSQL `daily_quotes` 表，由 TuShare `daily` API 写入）
- `market_service.py`：大盘指数、涨跌分布、板块行情、资金流（数据源：TuShare `index_daily` + DB 聚合计算）
- `tushare_ingest.py`：TuShare 数据获取、原始数据备份、清洗入库
- `data_saver.py`：原始 API 响应 JSONL 持久化到 data/ 目录（防丢失备份）
- `data_init.py`：首次启动检测空库，后台自动拉取初始数据（股票池 + 近30个交易日日线）
- `universe_ingest.py`：工具函数（exchange 规范化、日期解析等）
