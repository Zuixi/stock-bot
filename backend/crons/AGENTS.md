# crons

独立的爬虫脚本目录，与 `app/scheduler`（APScheduler 定时任务容器）职责区分：
- `app/scheduler`：随服务容器运行的定时采集任务（每日增量）
- `app/workers`：RabbitMQ Worker，手动触发的回补任务
- 本目录 `crons/`：按需单独运行的一次性/专项爬虫脚本，如 `crons/SSE`（上交所指数基础信息爬取入库）

新增定时采集优先放 `app/scheduler` 并在 `runner.py` 注册 CronTrigger；本目录仅用于不适合同定时框架的一次性脚本。

IMPORTANT:
- AFTER EACH TASK COMPLETION, SUMMARIZE USEFUL EXPERIENCES AND ADD TO [THIS DOCUMENT](../../docs/references/best-practices.md)
