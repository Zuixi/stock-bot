"""内容抽取（content_items → content_metrics）。

纯解析函数不连库；需要数据库的抽取任务（如定期报告财务指标）在本包内实现，
统一通过 ``app.repositories.content_repo`` 落库。
"""
