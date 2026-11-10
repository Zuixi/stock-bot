"""内容采集（公告/新闻 → content_items + content_entities）。

纯函数优先：解析逻辑不连库，落库由 ``app.repositories.content_repo`` 负责。
"""
