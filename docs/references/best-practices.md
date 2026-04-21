# Best Practices

项目开发过程中沉淀的经验教训。

---

- 外部数据源爬取服务应设计为独立的 scheduler 容器进程，通过 APScheduler 管理定时任务，避免耦合到 API 主进程中影响请求处理性能；反爬策略（UA 轮换、cookie 持久化、随机 jitter、指数退避）应在服务层统一封装，而非散落在各调用点。
