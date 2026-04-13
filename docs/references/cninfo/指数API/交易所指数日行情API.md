交易所指数日行情

API接口名称: p_index2905

URL接口名称: http://webapi.cninfo.com.cn/api/index/p_index2905

请求方式方法: get,post

最大记录数: 20000

输入参数:

| 英文名称 | 中文名称 | 类型 | 是否必填 | 说明 |
| --- | --- | --- | --- | --- |
| scode | 指数代码 | string | 否 | 传单个指数； 可为空 --，为空时返回传入的截止日期当天所有交易所指数行情数据（不判断是否交易日）； |
| sdate | 查询开始日期 | string | 否 |  |
| edate | 查询截止日期 | string | 是 | 不可为空 |
| market | 市场 | string | 否 | 上交所、深交所 可为空，单选、不能多选 |
| format | 结果集格式 | string | 否 | 设置结果返回的格式，可选的有xml、json、csv、dbf |
| @column | 结果列选择 | string | 否 | 选择结果集中所需要的字段，多列用逗号分隔，如@column=a,b |
| @limit | 结果条数限制 | int | 否 | 设置结果返回的条数 |
| @orderby | 结果集排序 | string | 否 | 设置结果集的格式，如 @orderby=id:desc @orderby=id:asc |

输出参数:

| 英文名称 | 中文名称 | 类型 | 单位 | 说明 |
| --- | --- | --- | --- | --- |
| TRADEDATE | 交易日期 | varchar |  |  |
| INDEXCODE | 指数代码 | varchar |  |  |
| INDEXNAME | 指数名称 | varchar |  |  |
| F001V | 交易所 | varchar |  |  |
| F002V | 指数英文名称 | varchar |  |  |
| F003N | 开市指数 | decimal |  |  |
| F004N | 最高指数 | decimal |  |  |
| F005N | 最低指数 | decimal |  |  |
| F006N | 最近指数 | decimal |  |  |
| F007N | 昨日收市指数 | decimal |  |  |
| F008N | 成交数量 | bigint |  | 单位：股 |
| F009N | 成交笔数 | bigint |  |  |
| F010N | 成交金额 | decimal |  | 单位：元 |
| MEMO | 备注 | varchar |  |  |