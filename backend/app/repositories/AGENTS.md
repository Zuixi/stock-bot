# stock data

数据来源：
- TuShare Pro API（唯一数据源）

A股股票具体信息，通过 TuShare `stock_basic` / `stock_company` 获取并入库。
日线行情通过 TuShare `daily` API 按交易日期批量获取。
