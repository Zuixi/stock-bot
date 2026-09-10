# Benchmark 实施计划（Tier 1 / Tier 2）

> 目标：让性能回归成为**可量化、可门禁、可追踪**的工程约束，而不是"上线后才发现变慢"。设计参考业内最佳实践（pytest-benchmark / CodSpeed / ASV / k6 / Google CI 性能回归做法）。

## 设计原则（参考业内最佳实践）

1. **确定性优先**：CPU 基准用固定尺寸的合成输入，不用活库/活 API 响应，保证两次运行可比。
2. **统计严谨**：足够 warmup + min-rounds，禁用 GC 噪声，固定随机种子；报告 P50/P95 而非均值。
3. **相对基线，不用绝对阈值**：机器差异大，门禁按"相对上次退化 X%"触发，不写死绝对 ms。
4. **噪声免疫**：共享 CI runner 的 wall-clock 不可信。硬门禁优先用**指令数 / callgrind 式确定性测量**（CodSpeed 路线），或固定到同一硬件的 dedicated runner。
5. **跨提交趋势**：单次 PR 对比易抖动，长期用 change-point detection（E-divisive）或 ASV 历史曲线判退化（Google 的做法）。
6. **隔离**：benchmark 不混进常规单测；用独立 marker，CI/pre-commit 显式排除。
7. **分级门禁**：纯计算域=硬门禁（确定性高）；API 域=软门禁/信息性（环境抖动大）。

## Tier 1 —— 纯计算域基准（硬门禁）

**对象**（仓库中无 DB 的 CPU 热点纯函数）：
- 规则引擎周期判定（阶段复苏 / 左侧信号）
- 指标 rollup（日度 → 月度派生计算）
- 多频指标"最新值"仲裁（registry 频率过滤 + 双频跨月共存）
- 源优先级裁决（source 名 → registry 匹配）
- 单位 / 行情字段归一化 mapper

**工具链**：
- 主选 `pytest-benchmark`（Python 微基准事实标准），`--benchmark-save`/`--benchmark-compare` 管理基线，JSON 输出可入 CI artifact。
- 进阶（Phase B）：`pytest-codspeed`（基于 Valgrind/callgrind 的指令数测量），**确定性高、对 CI 机器噪声免疫**，作为硬门禁首选；可保留 pytest-benchmark 作 wall-clock 趋势对照。

**产物结构**：
```
backend/
├── pyproject.toml                 # [project.optional-dependencies] bench = ["pytest-benchmark"]
├── tests/
│   ├── conftest.py                # 注册 marker: bench, perf
│   └── benchmarks/
│       ├── test_rule_engine_bench.py
│       ├── test_rollup_bench.py
│       └── test_normalization_bench.py
benchmarks/
├── baseline.json                  # Tier 1 基线（pytest-benchmark 输出）
└── asv.json                       # (Phase B) ASV 历史/趋势
scripts/
└── bench.sh                       # 跑 CPU 套件 + 存基线 + 对比门禁
```

**门禁接入**：
- pre-commit：**不加**（会拖慢提交，收益低）。
- CI PR：新增 `bench-cpu` job，硬门禁——跑 `uv run pytest -m bench --benchmark-save=current`，与 `benchmarks/baseline.json` 对比，退化 > 10% 即红；Phase B 切换为 CodSpeed 指令数门禁。
- 必须修正：现 pre-commit 测试钩子 `-m "not e2e"` 会把 bench 测试也收进来 → 改为 `-m "not e2e and not bench"`，CI test job 同理。

## Tier 2 —— API / 查询延迟基准（软门禁）

**对象**：个股列表/详情（enriched）、申万行业树、财务时序、估值历史等关键端点（对应 best-practices 里 LATERAL 150x 教训）。

**工具链**：
- `k6`（业界主流 HTTP 负载基准，原生 thresholds + JSON summary，CI 友好）。备选 `wrk2`/`vegeta`（更轻）。
- 后端依赖：复用 CI 已有的 postgres/redis service + seed fixture（或 docker-compose smoke 栈）。

**门禁接入**：
- CI：新增 `bench-api` job，**信息性**（`continue-on-error` 或 `::warning::`）——打 P95、rows、错误率，与基线对比；退化 >30% 警告，>2x 或硬错误才标红。
- 可选 nightly（main 定时）攒趋势曲线。
- 阈值示例（k6 thresholds）：`http_req_duration p(95)<Xms`、`http_req_failed<1%`；基线 X 由首次 seed 实测标定。

## 分阶段落地（tracer-bullet）

- **Phase A（最小可用）**：Tier 1 三个纯计算基准文件 + pytest-benchmark + baseline.json + CI `bench-cpu` 硬门禁 + pre-commit/CI test 钩子 marker 排除修正 + best-practices「基准纪律」条目。**交付：纯计算回归能被 PR 自动卡住。**
- **Phase B（加固）**：切 CodSpeed 指令数测量做硬门禁（消除机器噪声）+ ASV 趋势 + change-point detection（ruptures/E-divisive）替代单基线对比 + 扩 CPU 套件。
- **Phase C（Tier 2）**：k6 HTTP API 基准 against postgres seed + thresholds + 信息性 CI job + 可选 nightly。

## 风险与缓解

- **CI runner 抖动**：wall-clock 硬门禁会假阳性 → Phase B 用 CodSpeed 指令数；过渡期用宽容差（10-15%）+ 多次取中位。
- **基线漂移**：合成数据尺寸/seed 一旦改，基线失效 → 把尺寸/seed 写进测试常量并注释，基线随代码走、入仓库。
- **过度门禁**：信息性 job 不该卡合并 → bench-api 用 `continue-on-error`。
- **marker 泄漏**：bench/perf 混进默认单测拖慢提交 → conftest 注册 marker，所有 `-m "not e2e"` 处补 `and not bench and not perf`。

## 验收标准

- `uv run pytest -m bench` 在 ~秒级跑完，输出可复现（同尺寸同 seed 方差 < 5%）。
- CI PR 能在"故意注入一个 O(n²) 退化"时被 `bench-cpu` 卡红。
- `bench-api` 能在 docker-compose 栈上打出 P95 并产出可对比 JSON。
- 默认 `pytest`/`pre-commit` 跑单测时不触发 bench/perf（时间无明显增加）。
- best-practices 新增「基准纪律」一条，AGENTS 自检门禁引用基准脚本。

## 决策记录（2026-09-09）

1. **Tier 2 本轮不做**：Phase A 只落地 Tier 1（纯计算硬门禁）；Tier 2（k6 API 软门禁）留 Phase C。
2. **硬门禁首选 pytest-benchmark**（wall-clock + median 相对退化 + 宽容差 12%）；CodSpeed 指令数留 Phase B 切换。
3. **ASV 暂不引入**（Phase B 再评估）。
4. **偏差一**：pytest-benchmark 放 **dev extra** 而非独立 bench extra——pytest 收集 `tests/benchmarks/` 目录需要包在场（收集先于 marker 过滤），放 dev 免去为一个小包维护两套 CI sync；测试文件内 `pytest.importorskip` 兜底未装环境。
5. **偏差二（CI 实测修正）**：wall-clock 基线绑定硬件——本机跑的 baseline.json 在 CI runner 上全员慢 37-49%，跨机器对比必假红。CI 门禁改为**同 runner A/B**（先 checkout base commit 跑一遍存临时基线，再 checkout head 对比）；入库 `benchmarks/baseline.json` 降级为本地开发参考。首次引入基准的 PR 经 `--allow-added` 放行（base 侧本就没有新基准）。另两处实测坑：Windows 创建的 .py 无执行位，CI 直接执行报 126（改经 `uv run python` 调用）；>10ms/次的大样本基准单次抖动 7-8%，样本缩到 1-5ms 量级后 <1.2%。
