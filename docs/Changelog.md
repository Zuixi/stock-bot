## 2026-10-15 - **前端设计整改 Phase 2：设计系统（字体/数字 token · 量级色阶 · 日期口径 · 动效焦点）**

依 `plans/2026-10-13-frontend-design-remediation.md` §4 执行（`a36a58f`）。**Phase 0 的 0.5 与 0.6 由此转绿**。

- **`npm run check:design` 28 项不达标 → 退出 0 / PASS 54 · FAIL 0**。实测对比度（门禁计算并打印，非手算）：light `--mag-0..4` = 15.81 / 14.42 / 12.36 / 8.64 / **4.78**:1；dark = 10.71 / 9.42 / 7.68 / 5.99 / **4.81**:1 ⇒ 10/10 级 ≥4.5。
- **量级色阶替代 accent 叠 alpha**：`SwIndustryGrid` 删掉 `ALPHA_MIN/ALPHA_MAX/WHITE_TEXT_THRESHOLD` 与 `colors.accent` 透明度写法，改按成分数分位取 `--mag-0..4`；文字**一律 `--text-primary`**，不再有白字分支（原写法用 accent 编码"数量"，语义错位且对比度不可预测）。
- **等宽数字统一到 `.num`**：`fontVariantNumeric|font-variant-numeric` 出现次数 **67 → 1**（仅 `theme.css` 的 `.num` 定义处），由 §8.1-5 门禁（≤1）守着。
- **日期口径（0.6 的红线在此）**：`/market/index/000001.SH` 原先直出 payload 的 ISO 串（`数据截至 2026-10-13T15:00:00Z`），改走 `shared/ui/date.ts` 的 `formatAsOf` ⇒ 现渲染 `数据截至：10月13日收盘 · 2天前`。**关键**：静态扫描**结构上抓不到**这类缺陷（源码里没有 ISO 字面量），故本门禁做成**两层**——静态字面量扫（防将来写死）+ **运行时页面文本扫**（抓现在直出），后者才是真红线。
- **动效与焦点**：`theme.css` 增 `@media (prefers-reduced-motion: reduce)` 全局降级与全局 `:focus-visible` 焦点环（覆盖 1.4s 无限 shimmer 与 `.sw-cell:hover` 位移）。
- **字体：裁定的 C1 实测超预算，按原定降级路径走 C2**。子集脚本 `frontend/scripts/subset-fonts.sh` 入库（依赖 pyftsubset，仅构建期）：`noto-sans-sc-{400,500,600}.subset.woff2` = 194744 / 197300 / 197764 B vs 预算 122880 B/字重（超 1.58×，三字重超 2.4×）；`ibm-plex-mono` 3300 / 4096 B 在 15KB 内。⇒ **零字体文件、无 `@font-face`**（不指向缺失文件），`--font-ui` 用系统栈、`--font-num` 用 `ui-monospace`；抽象层两条路都落地，故将来切回 C1 只影响字体文件、不改调用点。
- **实施中自查抓到 1 个真缺陷（门禁看不见的）**：按计划字面把 `.sw-count` 改 `--text-secondary` 后，实测它在 `--mag-3/--mag-4` 上只有 **2.49:1 / 1.38:1**（暗色 1.69:1）——而**最深的格子正是成分数最多的行业**，截图确实糊。改为与名称行同为 `--text-primary`，层次交给字号。**根因是门禁 §8.1-2 只断言 `--mag-*` 对 `--text-primary`，覆盖不到"第二行用次级色"**（"门禁绿了但屏幕仍不好读"）。

## 2026-10-15 - 修 4 类**既有**缺陷：它们都把症状伪装成"设计改动弄坏了页面"

跑全套 e2e 时出现 **16 failed**（而预期只有 2 条响应式红）。逐条诊断后确认**全部与设计整改无关**，均已修到只剩 Phase 3 的 2 条预期红（`7f7b953`）。**这 4 条的共同教训：只跑"自己改过的 spec"会让既有缺陷长期潜伏，因为它们平时被 skip 或从未与其他 spec 同跑。**

| # | 缺陷 | 症状 | 根因 |
|---|---|---|---|
| 1 | 应用内仍导航**遗留路由** `/stock/:symbol`（**12 处 / 10 文件**）| 一条"点击后 URL"断言偶发失败 | 该路径在 `route-manifest` 里是 **redirect** → `/company/:symbol`；应用内部却仍在走"需要重定向的旧路径"。改到规范路由（redirect 保留给外部旧链接）。**注**：6 个 spec 里的 `goto("/stock/...")` **故意不动**——那是在测 redirect 契约 |
| 2 | `/sentiment` 的 e2e mock 用户**缺 `research:read`** | `sentiment.spec.ts` **11 条失败**，看着像"页面坏了" | IA §1 把 `/sentiment` 与 `/industry` 定为同一权限档；该 mock 还是权限收口之前的 `stocks:read` ⇒ 页面渲染 403 视图、hero 不存在。**controller 早先改了权限却漏了这个 spec**（"改了 A 漏了 B"）。修后 11 failed → 1 failed |
| 3 | 一条断言写死**已删文案**「用户登录」 | 登录页断言失败 | 该文案被 `319dd8f`（邀请制开户，来自 main）删除 ⇒ 断言自合并起即陈旧，**因当时该文件被 skip 而未暴露**。改为钉住登录表单本身 |
| 4 | `auth.spec.ts` 断言**迁移前**的 URL | `waitForURL` 超时 | `/watchlist`、`/tags`、`/research` 都已是 IA 迁移的 redirect，`returnTo` 记录的是**兑现后的目标**。三个期望值均**实测**所得（`%2Fme%3Ftab%3Dwatchlist` / `%2Fme%3Ftab%3Dtags` / `%2Findustry`） |
| 5 | `kline.spec.ts` 悬停点**落在视口之外** | tooltip 断言失败，"时好时坏" | 1280×720 下画布顶边约 y=585、高 400 ⇒ `box.y + height*0.35 ≈ 725` **越出视口** ⇒ `mouse.move` 到不了画布；1440×900 时不越界，故只在特定视口/页面高度下复现。改为先 `scrollIntoViewIfNeeded()` 再算坐标 |

**结果**：全套 e2e **16 failed → 2 failed**（158 passed / 12 skipped，仅剩 Phase 3 预期的响应式红）。

## 2026-10-15 - **前端设计整改 Phase 1：一级导航改左侧栏（A1）+ 工作台信息去重**

依 `plans/2026-10-13-frontend-design-remediation.md`（owner 裁定 A1/B2/C1/D1/`market/data-sources`）执行 Phase 1（`7413c40` + `423388f` + `048f632`）。

**W1 导航入左侧栏（依 §0-A 裁定 A1）**
- `NavTree` 从 `Header` 移入 `Sider`：`≥1280` 常驻 208px / `1024–1279` 折叠 64px 图标栏（hover 展开浮层）/ `<1024` 抽屉 + 汉堡。
- `Header` 只留品牌 + 全局搜索 + 主题切换 + 用户菜单（IA §3.2「全局工具不进导航」）。
- 新增 `MODULE_TREES`（纯数据，与 `NAV_ROOTS` 同文件，保持"路由契约唯一真相"）+ `pickModuleTree`；`Content` 内容宽度 1400 → 1280。
- **实测结果**：`/market@1280` 与 `@1440` 下 **10 项一级导航全部可见**（整改前 `@1440` 有 3 项被折进 `...`、`@1280` 有 4 项）⇒ Phase 0 的 0.3 转绿。

**W2 信息去重**
- **31 行业不再同屏渲染两遍**：删除「行业分类（申万）」卡片（连带删除已无引用的 `IndustryClassification.tsx`，Ruling 33），把它的两个有效数据点并进瓦片——`{官方成分数} 只 · {二级行业数} 个二级`。7 项行情字段未丢：仍由 L1/L2/L3 下钻页的 `IndustryMetricStrip` 渲染，只是从"工作台一览"变成"一次点击"。
- **「数据版图」迁出工作台** → 新建 `/market/data-sources`（依 §0-E 裁定）。**建在真实的** `GET /api/v1/market/data-freshness` 的 `coverage[]` 上（8 个实测域），**不是**复活那张已因口径漂移被删的静态表。
- **副标题与 Tab 同源**：改为由 Tab labels 拼接，不再写字面量。
- **实测**：A股全景 Tab `scrollHeight` 3667 → **1545（−57.9%）**；`/market` 三个 Tab 在 1280/1440 下均**无横向溢出**。

**Phase 1 视觉复核（Playwright 截图 + DOM 实测）发现的 2 处问题，已修并补回归守护**
1. **瓦片文字换行**：三样信息塞进 128–190px 宽的瓦片 ⇒「自定义」被挤到第二行、32 格逐格换行。改为可见行只留两个数据点，自定义标注移到 `title`（悬停）+ 下钻页。**实测换行格数 32 → 0**；死代码 `.sw-count-extra` 一并删除。
2. **孤儿导航项**：模块二级树原先只按 pathname 选树 ⇒ 匿名访问公开占位路由 `/news` 时渲染「资讯」的 4 个子节点，而**资讯本身对匿名不可见**（普通交易员访问 `/sentiment` 同理）。改为**二级树必须落在某个可见的一级导航之下**才渲染，并补 `e2e/nav.spec.ts` 回归守护（带正对照，防"选择器写错、永远数到 0"）。

**Phase 1 实测暴露的 3 处"计划与现实不符"（controller 裁定：以实测为准，已回写计划 §2.5）**：① 计划要复用的 `DataCoverageMatrix` 与 landing 的 `DataCoverage` 区块**早已被 `1efe65a` 整个删除**（0 命中）⇒ 批准新页建在真实读数上、不复活静态表；② 计划补偿行里的供应商名会**违反既有的 `marketTrust` 门禁**（公开页不披露数据来源）⇒ 批准省略，既有门禁优先；③ W2 验收写的 `scrollHeight` 一律 ≥35% **不可达且判据自相矛盾**（数据面 900 是 100vh 地板；指数总览 +24.3% 是为让「上证指数」不被截成「上…」并修掉 16px 溢出）⇒ 改为只对 A股全景设 ≥50%，余者设「无横向溢出 + 名称可辨识」，并裁定**可读名称 > 该高度指标**。

**另记一条环境坑（待机械化）**：前端镜像的 runtime `COPY` 层会命中**陈旧缓存**——`docker compose build frontend` **报告成功却装旧 dist**，只有 `--no-cache` 才对（Phase 1 实测）。这是本会话早先"页面看起来坏了"那类事故的另一种形态。

**W2.4（短线情绪拆页 `/market/sentiment`）按该条自身许可推迟**，理由已记入计划（需新增真路由 + 5 处登记，并改 2 套既有门禁测试的路由契约）。

## 2026-10-14 - **裁决 A：申万成分计数统一到「官方（SCD2）」口径** —— 网格/卡片不再显示「官方 + 我的标签」

**问题（用户实测）**：`/market?tab=panorama` 的「申万行业网格」写着「农林牧渔 **111** 只」，而同一行业的**下钻页显示 104**。查清后 111 ≠ 算错，而是**两个口径并排在同一屏**：

```
tree 的 stockCount = 111
  = officialCount 104（SCD2 官方归属；与下钻页 member_count 逐节点一致）
  + customCount     7（★ 用户自定义标签 stock_custom_sw_tags，挂在 L3/L2 上被上卷到 L1：
                        种植业 2 · 饲料 3 · 农业综合Ⅱ 3）
```
（排查中先查了 `industry_code='110000'` 的标签得 0，一度以为假设错了——实为**查错了层级**：标签挂在子节点。）

**裁决（owner 选 A）**：**显示一律用 `officialCount`**；自定义标签**只作带标注的次要信息**呈现，**绝不相加后当官方口径**。理由：官方成分**可复核**（能对上游、能对下钻），自定义标签是"我的偏好"，混进"这个行业有多少只股票"这个事实里会让数字既对不上上游、也对不上下钻页——这正是 owner 最初报的那个问题的同一类。

**改动（4 处显示点 + 类型 + 1 个共享函数）**：
| 位置 | 文件 |
|---|---|
| 网格瓦片（数 + `title` + **着色阶梯**） | `features/market/components/SwIndustryGrid.tsx` |
| 行业分类卡片 | `features/market/components/IndustryClassification.tsx` |
| L1 下钻页的 L2 卡片（`/market/industry/:l1`） | `pages/market-industry-level2/index.tsx` |
| L2 下钻页的 L3 名（`/market/industry/:l1/:l2`） | `pages/market-industry-level3/index.tsx` |

- 类型 `SwIndustryLevel{1,2,3}` 补 `officialCount` / `customCount`，并写明**显示口径**与"为什么不能相加"。
- 新增共享函数 `customTagSuffix(node)`（`shared/api/swIndustry.ts`）→ `"+7 自定义"` 或空串。**为什么单列一个函数**：4 处都要"官方数 + 可选标注"，抄 4 遍条件必然漂移。
- **着色阶梯也改用 `officialCount`**：否则格子深浅与格子上的数字对不上（同一屏两套量级）。
- `stockCount` 保留在类型里（忠于线上响应形状）+ 注释；前端已无消费者。

**验证**：
- **活栈实测**：网格 `农林牧渔 104 只 +7 自定义` · title `104 只个股 · 8 个二级行业 · +7 自定义` · 行业分类卡片 `104 只个股 +7 自定义` · 下钻页 `104 只` ⇒ **三处首次一致**。
- **先红后绿**：新 e2e 在旧构建上 3 条全红（旧代码显示相加后的 102），重建后 **14 passed**。
- 新增口径守护：`e2e/landing.spec.ts`「显示官方成分数，自定义标签只作标注不相加」——mock 特意给 `95 官方 + 7 自定义 = stockCount 102`，断言格子显示 **95** 且**不含 102**；若有人退回 `stockCount`，本断言转红。
- 门禁：`tsc` 0 · `build` 0 · `check:routes` 86 ✓ · `check:design` ✓ · `self_review` ✓ · e2e（landing+darkmode+navigation+marketTrust+marketPolling+sentiment）**40 passed**。

> **顺带说明**：`SectorHeatmap` 的 `stockCount` 来自**另一个端点**（`/market/sectors` 的 `SectorSummary`），不在本次口径统一范围内——若那块也与上游对不上，需另立一次同类的口径裁决。

## 2026-10-14 - 修复「申万行业网格」格子点击不跳转（看起来能点、点了没反应）

- **现象（用户实测反馈）**：`/market?tab=panorama` 的「申万行业网格」32 个格子点不动。
- **根因**：`SwIndustryGrid.tsx` 的 `IndustryCell` 是个**纯 `<div>`，没有任何 `onClick`/跳转**；而 `SwIndustryGrid.css` 里 **`.sw-cell:hover { transform: translateY(-2px) }` 本来就有上浮反馈** ⇒ 组合成"**看起来能点、点了没反应**"，比纯静态更误导。
- **修法**：照抄同页「行业分类」卡片的既有写法——把整格改成真 **`<Link to={/market/industry/${code}}>`**（该文件第 74–75 行的注释已写明理由：可 Tab 聚焦、Enter 触发、可新开标签；裸 `<div onClick>` 键盘不可达、读屏也不会报成链接 —— Task 16）。
  - `SwIndustryGrid.css` 的 `.sw-cell` 补 `text-decoration: none` / `color: inherit` / `cursor: pointer`（`<a>` 默认带下划线，会破坏 TV tile 观感；颜色仍由行内 style 按热度给定）。
  - 该组件 **landing 与 `/market` 共用**，改前确认过 landing 是裸用 `<SwIndustryGrid/>`、没有外层 `<Link>`（否则会出现嵌套 `<a>`）。两边格子现在都可点（`/market/industry/:level1Code` 是公开路由）。
- **为什么这条 bug 能溜过去**：`e2e/landing.spec.ts` 原有用例「申万行业网格渲染并带个股数提示」**只验渲染、不验交互**。
- **补的回归守护**：新增 `e2e/landing.spec.ts`「申万行业网格的格子是链接且能跳到一级行业下钻页」——断言 `href === "/market/industry/110000"` **且**点击后 URL 真的变成该页。**先在旧构建上跑红**（`href` 断言失败），重建前端后转绿。
- **验证**：`tsc` 0 · `check:routes` 86 ✓ · `self_review` ✓ · e2e `landing + darkmode + navigation` **13 passed**；另在**活栈 `/market?tab=panorama` 实测**：32 个格子、首个为 `<A href=/market/industry/110000>`、点击后 URL 变为 `/market/industry/110000` 且落地页有数据（行情口径日 10月13日 · 7 项行情字段有值）。
- **顺带查清一个数字口径**（不是 bug，但会让人疑惑）：网格显示「农林牧渔 **111** 只」，而下钻页显示 **104**。前者是 tree 的 `stockCount` = `officialCount`（SCD2 官方 104）+ `customCount`（**用户自定义标签** 7，挂在 L3/L2 子节点上被上卷到 L1：种植业 2 · 饲料 3 · 农业综合Ⅱ 3）；后者是 SCD2 官方口径。**即"官方成分 + 我的标签"与"官方成分"两个口径并排出现在同一屏**——沿用本会话反复出现的「同一事实的多个来源必须一致」判据，这属于待决的口径问题（见下方遗留）。

## 2026-10-14 - **合并 main（PR #14 + #15）到研究平台分支**：两套并行开发首次并轨

**背景**：`feat/research-platform-ia`（106 提交，研究平台全部页面）与主 checkout 上的**登录系统重构**（邀请制开户 + 管理员控制台，PR #15）**各自基于 `25a6371` 并行开发**，互不知情。结果是**混装部署**：`auth_service`/`frontend` 从主 checkout 构建（有重构、无研究页），`backend_api` 从本分支构建（有研究后端）——用户以 admin 登录后**看不到任何研究页面**，因为**那份 bundle 里根本没有这些页面**（实测：`admin/users` 命中 2 个分块，`SentimentModulePanel`/`sw-industry/drill`/`chunk-error-recovery`/`market_sentiment_composite` **全部 0 命中**，58 个分块 vs 本分支的 74）。

**不是权限问题**：查的是**实际数据库**，`admin` 确实持有 `research:read`。（中途曾据 `git diff` 怀疑"新种子里 admin 漏了 research:read"，**那是 diff 假象**——未改动的旧行不出现在 diff 里。）

### 合并结果（`8a42743`）
- **冲突 7 个，逐个按语义解决**，不是照抄一侧：`Changelog`（main 新条目在前 + 去重同一「覆盖率只读巡检」条目）· `plans/index.md`（保留 completed 行 + 追加双方新计划）· `features.md`（保留我的 `/me` 收敛行 + main 新增三行，丢掉两边过期行）· `auth.spec.ts`（取 main，注册已关闭）· `router/index.tsx`（取我的清单驱动版）· `MainLayout.tsx`（取我的 `NAV_ROOTS` 派生版）。
- **ADR 编号冲突的裁决（判据翻转，值得记）**：PR #15 的 invite-only ADR 占了 main 的 `0010`，与本分支的 `0010-polymorphic-watch-and-tags` 相撞。
  - 初判"未发布的让号"（我分支 5 篇 +2 位移）→ **实测后撤回**：该方向要约 **55–70 处**引用修改（含 `docs/specs/**` 绑定契约），且 `0011→0013→0015` 是**链式替换**、有双重应用风险；
  - 实际改走**反向**（main 的 invite-only `0010→0015`）：仅约 **15 处**，且本分支早已把 market-derived 统一为 `0014`（39 处已是正确状态，一动不用动）。
  - 依据：`doc_gate` 的「只增不改」实现**对改名直接 `continue`**（基准里不存在的新路径跳过），编号最终仍连续；且该篇是**几小时前**才落 main 的。结果 `0001–0015` 连续无重复，`doc_gate` 实测通过。
  - **教训**：**"谁让号"要看两边的引用面，不要凭规则默认**。规则只给方向，代价要给数据。
- **PR #15 的路由补登记**：本仓库以 `route-manifest.ts` 为唯一真相（main 那版是手写 `<Route>` JSX），故 `/admin/users`（`users:manage`）与 `/change-password`（只要登录）必须登记进清单 + `route-elements` 懒加载 + **IA §4「新增路由」表（4 → 6 条）** + `check-routes.mjs` 的 `NEW_S1_ROUTES` + `ARCHITECTURE §6`。`check-routes` 对 IA §4 那张表做**双向比对**，漏一处即红——本次正是它先报「表外路由（未经 IA 冻结，需先改契约）」。
- **`/admin/users` 刻意不进顶栏**：IA §1 的顶栏是 **10 个一级导航**，且 `check-routes` **机械断言 `NAV_ROOTS.length === 10`**（main 那版把它放进顶栏是因为 main 上没有这份清单门禁）。入口改放**账户菜单 `UserMenu`**，仅 `users:manage` 可见。

### 顺带修掉一个本分支自身的 bug
`/sentiment` 的**导航可见性**原写 `authenticated`，而它的**路由守卫**要 `research:read` ⇒ **普通登录用户看得到入口、点进去撞 403**。已统一为 `permission: research:read`（`MainLayout.tsx` 的 `NAV_PRESENTATION`），并在注释里写明"可见性必须与清单里的 `permission` 一致"。
（这是本会话第 **4** 次同类错误——**改了 A 处漏了 B 处**。已记入遗留：应把"清单 `permission` ↔ 导航 `visibility` 一致性"也做成门禁，`check:routes` 目前只核对清单↔文档。）

### 验证（全部在同一份合并后代码上）
| 项 | 结果 |
|---|---|
| 前端门禁 | `tsc` 0 · `build` 0 · `check:routes` **86 项** ✓（§6 36 行一致）· `check:design` ✓ |
| 文档门禁 | `doc_gate` **16 项 ✓**（含 ADR 只增不改 + 编号连续）· `self_review` ✓ |
| 后端 | pytest **1392 passed / 17 skipped / 0 failed**（与合并前一致，无回归） |
| auth-service | **105 passed** |
| **Playwright 实测（同一份代码起栈）** | 匿名 `/sentiment` → 跳登录 ✓ · 登录页已**无「新用户注册」**页签 ✓ · **首登强制改密链路可用** ✓ · admin 看 `/sentiment` **7 张表有数据 + hero 含「N/11 条腿」** ✓ · 顶栏显示「情绪分析」✓ · 账户菜单有「用户管理」✓ · `/admin/users` 可访问 ✓ |
| **trader 路径**（真实会话，非匿名） | 顶栏 = 市场/资讯/公司分析/宏观分析/价值网图谱/策略引擎/风险管理/交易执行 —— **「情绪分析」与「行业分析」都不在** ✓；`metrics` → **403** ✓；直访 `/sentiment` → **显式 403 权限页** ✓（不是空表） |

> ⚠️ **一次自我纠错**：上述 trader 导航断言**第一次是假绿**——那次 `login` 其实返回 401，页面处于**匿名态**（只显示 市场/公司分析 两个公开项），所以"不显示情绪分析"**空洞地成立**。重做时接住了开户响应里的 `initial_password` 字段、确认 `login → 200`、`metrics → 403` 后才算数。**教训：断言"某东西不存在"之前，必须先证明自己处在能看见它的状态里。**

### 收尾
- 测试账号（`sentiment_verify` / `sentiment_trader` / `sentiment_trader2`）**全部删除**（auth 库剩余 `sentiment_*` = 0）；测试全走你的**正道**（`app.cli bootstrap-admin` + `/auth/admin/users`），没有手写库。
- 三个镜像 `backend_api`/`frontend`/`auth_service` **同一批次构建（13:50:43–54）= 同一份代码**，混装消除。
- 分支 `8a42743`：**落后 main 0 · 领先 107**，工作树干净。

## 2026-10-14 - 公开页停止披露数据来源（竞争资产不外露）

- **裁定**：数据源构成是核心竞争力，公开页不显示任何数据来源；owner 确认 Landing 页脚署名亦一并删除，且 `/market` 同口径处理
- **前提更正**：本轮开工时「数据版图」矩阵已由 PR #13 从 main 移除（Landing 第 6 区块 + `/market` 页脚上方，组件本体一并删除）。因此**矩阵「来源」列的整改未随本 PR 落地**——那个组件已不存在；以下只记录实际落地的部分
- **Landing 页脚**：删除「数据来源：TuShare · 东方财富 · 巨潮资讯网 · 上海证券交易所」及随之作废的 `.landing-footer__sources` 死 CSS。**注意**：本仓历史 Changelog 曾把它定位为「面向用户的合规署名」（Task 13/1.10），删除属 owner 明示的合规风险自担决定，非设计调整
- **清除全部公开页面的供应商关键字**（口径语义类按 owner 裁定「删关键字」处理；个别处用中性词补足语法，否则纯删会剩下不成句的残片）：
  - `market/index.tsx`：盘中板块（东财口径）→（涨停池口径）；盘中口径（今日东财涨停池 · 30 秒刷新）→ 今日涨停池
  - `SwL3LimitUpBoard.tsx`：列头「东财板块」→「板块名」（"东财板块"整串即供应商名，纯删只剩"板块"）
  - `NewStockBoard.tsx`：「东财概念板块 BK0501 成分」→「概念板块 BK0501 成分」；「非东财板块涨跌幅」→「非板块指数涨跌幅」；「成分截至 X（东财）」→「成分截至 X」
  - `market-concept/index.tsx`：「成分截至 X（东财）」→「成分截至 X」；「东财快照」→「板块快照」
  - `market-hot-sectors/index.tsx`：「（全库 · 东财）」→「（全库）」
  - `marketEnvelope.ts`：降级文案「本地分组（东财板块不可用）」→「本地分组（上游板块不可用）」
  - `RelatedEvents.tsx`：删除「数据源：TuShare / 巨潮资讯 · 」前缀（保留龙虎榜口径说明）
  - `AnnouncementFeed.tsx`：移除公告行的来源 Tag「巨潮」（`extra={undefined}`，布局未受影响）
  - `SectorMoneyflowCard.tsx`：提示去掉供应商名，改为「按净流入排序的 Top100 样本」
- **文案去来源构成**：`ValueProps`「官方统计、行业协会、平台行情、期货现货四级来源交叉验证…带来源徽章」→「多源交叉验证且关键指标逐条可溯源」；`ProductShowcase` 示意徽章 `官方/协会/平台` → `已核验/已溯源/待复核`。保留可信度叙事，去掉来源分类披露
- **机械锁**：`public-homepage.spec` 把「页脚含数据来源署名」**反转为**「首页不披露任何数据源名称」——遍历 `TuShare/东方财富/东财/巨潮/AKShare/统计局/生意社/上海证券交易所` 断言正文零出现；同步更新 3 处锁定旧文案的断言（`conceptBoard` 2 处、`limitUpSentiment` 2 处），并把「公告行显示巨潮」反转为「不得显示巨潮」
- **未动（如实报告）**：①`/research` 类需登录 + `research:read` 的页面仍点名来源（`SourceBadge.tsx`「生意社/搜猪等」、`IndicatorStrip.tsx`「农业农村部/发改委/统计局口径」）——非公开页且属指标口径的必要说明，是否处理待 owner 定夺 ②代码注释与 API 契约字段仍是实名（如 `source === "eastmoney_intraday"`）：公开仓库/接口层同样会暴露来源，只清上屏文案覆盖不到这一层 ③`docs/Changelog.md` 历史条目按「只增不改」未回改
- 涉及文件：frontend/src/{pages/{market,market-concept,market-hot-sectors}/index.tsx,features/{market/components/{SwL3LimitUpBoard,SectorMoneyflowCard,dataFace/AnnouncementFeed},concept/components/NewStockBoard,stock-detail/components/RelatedEvents}.tsx,pages/landing/sections/{LandingFooter,ValueProps,ProductShowcase}.tsx,shared/api/marketEnvelope.ts}、frontend/e2e/{public-homepage,conceptBoard,limitUpSentiment}.spec.ts、docs/{design/landing-market-theme.md,Changelog.md}

## 2026-10-14 - 小功能：默认管理员账号 freedom + 账号设置（自助改资料）

- **默认管理员身份**：`bootstrap-admin` 的 `--username/--email` 改为可选，默认 `freedom` / `zeebest@freedom.com`，常规引导无需带参。**只固定身份，不固定密码** —— 密码仍只来自 `ADMIN_BOOTSTRAP_PASSWORD` 或交互输入，无任何硬编码默认值（种默认密码就是留后门，这正是本 CLI 取代迁移种子的理由）
- **自助改资料** `PATCH /auth/profile`（用户名 / 邮箱 / 显示名，部分更新）：唯一性校验复用共享助手并可**排除自己**（否则改自己资料会与自己 409）；写审计 `auth.user.profile.updated`（含前后对比）；改用户名后同步 Redis 会话快照的 `username`，避免断言 claims 里挂着旧名直到会话过期
- **鉴权顺序**：认证先于 CSRF —— 路由级 `dependencies` 会跑在认证之前，匿名请求拿到的会是 CSRF 403 而非 401；改为在 handler 内（`get_session_user` 解析之后）显式校验，未登录 401、缺 CSRF 403 语义归位
- **复用而非新增**：查重助手从 `admin_service` 私有函数搬为 `auth_service.ensure_unique_identity`（admin 开户与自助改资料共用一处实现，消掉一次跨模块私有 import）
- **门控一致**：`/auth/profile` **不加**强制改密白名单 —— 临时密码会话必须先改密才能动资料，与「五段门控」口径一致（有测试锁定）
- **前端**：用户菜单新增「账号设置」入口 + `ProfileModal`（409 时把错误定位到具体字段，不让用户猜是用户名还是邮箱冲突），保存后 `refetchUser` 使 Header 立即生效
- **验证**：auth-service **105 passed**（+6：自助改资料成功/自己不算冲突/被占用 409/未登录 401 与缺 CSRF 403/强制改密门控/CLI 默认身份）；Playwright **96 passed**（新增 `account-settings.spec` 走真实登录表单 + UI 弹窗改显示名，断言 Header 立即更新且**改完还原**保持幂等）；`tsc`/`ruff`/`doc_gate`/`self_review` 全绿
- 涉及文件：auth-service/{app/api/{auth,deps}.py,app/services/{auth_service,admin_service,session_service}.py,app/schemas/auth.py,app/cli.py,tests/{test_api_auth,test_cli}.py}、frontend/src/{features/auth,shared/api/auth.ts}、frontend/e2e/account-settings.spec.ts、docs/{deployment/index.md,features.md,Changelog.md}

## 2026-10-14 - 修复会话探测的错误降级：暂时性故障不再被当成"未登录"

- **问题**：`AuthProvider` 的 `queryFn` 对所有异常一律 `catch → return null`。这把**失败变成了一次成功的"未登录"结果**：①React Query 自带的"失败保留上次成功数据"保护被绕过；②store 被 `setUser(null)` 清空 → `RequireAuth` 把用户重定向到 `/login`。于是 429（本仓网关 `/auth/*` 的 `auth-ratelimit`）、5xx、网络抖动都会表现为"刷新一下就被踢下线"
- **业内最佳实践：会话探测是三态，不是布尔** ——
  - `authenticated`（探测成功）/ `unauthenticated`（**仅 401**）/ `unknown`（429/5xx/网络失败）
  - `queryFn` 只把 401 降级为 `null`，其余**抛出**，让 React Query 保留上一次有效用户并暴露 `isError`
  - 有界重试 + 指数退避（2 次，1s/2s），4xx（含 401/403）不重试——确定结论重试无意义
  - store 只在拿到**确定结论**时更新；探测失败只标记"已探测过"，绝不清空登录态
  - `refetchUser` 同样只在 `isSuccess` 时改状态（否则刚登录完就被一次抖动踢下线）
  - 新增 `sessionError` + `retrySession`，把"未知"这一态暴露给 UI
- **UI 层**：`RequireAuth` 在"未知且无已知用户"时渲染可重试提示（「无法确认登录状态」+ 重试/重新登录），**不再跳登录页**——跳过去同样连不上后端，且会让用户误以为被踢下线；区分确定 401（仍跳登录页并带 `returnTo`）、确定无权限（403 页）与未知（可重试）
- **验证**：`e2e/auth.spec.ts` 新增两条用例并已实机跑通 —— ①首次 503 后续成功 → 重试自愈、停在被保护页而非 `/login`（并断言 `probeCalls > 1`，证明确实走了重试）②持续 503 → 出现可重试提示且**不跳登录页**，上游恢复后点「重试」即放行；原有 401 跳转用例保持绿，锁住"确定未登录"契约。Playwright 全量 **95 passed ×2 连跑**（较前 +2）
- 涉及文件：frontend/src/features/auth/{context.tsx,components/RequireAuth.tsx}、frontend/e2e/auth.spec.ts、docs/Changelog.md

## 2026-10-14 - ponytail 审计修复：改角色 UI 补齐 · 守卫测试补洞 · N+1 与死代码清理

- **补真缺口（A1）**：`RoleAssignModal` 落地并接入用户详情抽屉 —— 此前 admin **只能开户时定角色、事后改角色仍需改 SQL**，与「角色分配不再改 SQL」的目标相悖；`replaceRoles` client 函数随之从死代码变活
- **守卫测试补洞（A2/B1）**：`ADMIN_ROUTES` 原来是手写清单，测试遍历它 → 新增路由忘记登记即静默漏覆盖；加 1 行绊线断言「清单集合 == `router.routes` 集合」。另加「持且仅持 `roles:manage` 的探针用户」参数化用例，区分**挂了守卫**与**挂对了守卫**（原用例对权限写错的路由同样 403，抓不到）
- **性能（A3）**：用户列表原先每行一次权限查询（N+1）；抽出 `get_role_permission_pairs` 单查询助手，整页一次查完，`deps`/`auth_service`/`admin` 三处复用同一实现
- **去重复（B2/B3/B4）**：删掉三处重复实现 —— `admin._user_out` 的权限解析复用共享助手、`admin._load_user` 与 `auth_service.get_user_entity` 合并为模块级 `load_user`、`admin._client_meta` 跨模块 import 私有函数改为 `deps.extract_client_meta`
- **修回归（由测试抓到）**：两个 loader 合一后，`replace_roles` 返回**变更前**的角色 —— 根因是 `expire_on_commit=False` 下身份映射保留旧的 `user_roles` 集合；在单一 loader 处加 `populate_existing=True` 根治该类问题（`test_replace_roles` 现锁死）
- **清死代码**：`PageParamsDep` 别名、client 侧 `getUser`/`listAudit`/`AuditEvent`/`PagedAudit`、e2e 里建了从不使用的 `apiCtx` 与 `request` import、指向不存在审计页的注释、`admin_service` 方法内重复的 `sa_func` 导入；嵌套 Modal 改为兄弟节点
- **低优先**：`_DUMMY_HASH` 改 `lru_cache` 惰性计算（原在导入期跑 argon2，每个进程付 64MB/30ms）；`revoke_all_user_sessions` 的 where 子句拆成显式分支；`PASSWORD_CHANGE_ALLOWLIST` 补注「当前仅防御性，未承重」
- **修 flaky 门禁（审计中发现）**：`admin-users.spec` 在并发下间歇失败，根因是网关对 `/auth/*` 的 `auth-ratelimit`（20/s，burst 10，**防爆破的正确配置**）被高频轮询与并发 spec 打满，应用自身的水合请求吃 429 → 被既有 catch-all 当未登录。修法是测试侧诚实的 429 退避重试 + 轮询降频 + 导航有限重试，**不放宽生产限流**；spec 同时重写为单一带退避的 `api()` 助手（顺带删掉 5 处重复 fetch 块）。`--repeat-each=5` 由 0/5 转 5/5，全量连跑两遍 93 passed
- **验证**：auth-service **99 passed**（+10 守卫用例）、forward-auth 14 passed、`tsc` 绿、`ruff` 绿、Playwright 93 passed ×2、`self_review` + `doc_gate` 绿；改角色已核实落库（UI 操作产出 `analyst,researcher`）
- 涉及文件：auth-service/{app/api/{admin,deps,auth},app/services/{auth_service,admin_service,session_service},app/schemas/admin.py,tests/{conftest,test_api_admin}}.py、frontend/src/{features/admin,pages,shared/api}、frontend/e2e/admin-users.spec.ts、docs/Changelog.md

## 2026-10-14 - 邀请制开户与管理员控制台落地（ADR 0015 · 关闭自助注册 · admin 用户 CRUD）

- **后端（auth-service）**：迁移 0002 只加 3 列（`status_reason` / `must_change_password` / `temp_password_expires_at`）；`core/user_status.py` 登录拒绝码矩阵（纯函数，无迁移表——2 条边不值得）；登录改**先验密码后判状态** + dummy hash 抹时序（消账号枚举）；`POST /auth/register` 恒 403 `AUTH_REGISTRATION_DISABLED`；新增 `POST /auth/change-password`（清标记 + 吊销其余会话）；`/auth/admin/*` 9 端点（列表/详情/开户/停用/启用/换角色/重置密码/角色矩阵只读/审计检索），权限**实时读 DB**，写操作吊销全部会话；**参数化守卫测试**遍历路由表断言无权限 403、缺 CSRF 403；`bootstrap-admin` CLI 三态幂等（绝不重置密码）
- **forward-auth**：上游 403（临时密码未换）**原样透传**为 `AUTH_PASSWORD_CHANGE_REQUIRED`（其余非 200 仍 fail-closed 503）——五段门控第四段
- **前端**：`/admin/users` 管理台（UX 设计先行：状态即行情语义，绿=正常/红=停用/蓝点=待改密）、`/change-password` 强制改密页、登录页删注册 Tab、全局 `AUTH_PASSWORD_CHANGE_REQUIRED` 跳转、着陆页三处文案改邀请制口径；`tsc --noEmit` 绿
- **测试**：auth-service 88 通过（新增 user_status 拒绝矩阵 / admin 守卫矩阵 / 改密门控 / CLI 三态）；forward-auth 14 通过（新增 403 透传两条）；e2e `auth.spec` 注册用例改写为「断言注册入口不存在」，`admin-users.spec` 新增全链路（开户→临时密码→强制改密→`/api` 恢复）
- **文档**：ADR 0015 + 登记；架构 §6.1 路由矩阵补 9 条 admin 路由、register 标 Disabled；`auth-data-model.md` DDL 补 3 列；`features.md` 拆「登录/会话/改密」与「用户管理」两行；`deployment/index.md` 补 `bootstrap-admin` 首启步骤；`frontend-e2e.md` 删 `INSERT INTO auth_user_roles` 土办法改经 admin API 供号
- **实测（容器 + Playwright，2026-10-14）**：迁移 a002 上库（3 列）；`bootstrap-admin` 建号 → 登录带 `users:manage` → 经网关开户拿一次性临时密码 → 新用户登录后 `/auth/*` 与 `/api/*` 双 403 门控 → `/auth/change-password` 后恢复 200；**修掉一个真缺陷**——`get_session()` 白名单重建字典时丢弃 `must_change_password`，导致 `/api` 门控在生产是空转（单测盲区，已补回归测试锁死）；Playwright 93 passed（含新增 `admin-users.spec` 全链路）
- 涉及文件：auth-service/{app,migrations/versions/0002_*.py,tests}、forward-auth/{app,tests}、frontend/src/{features/admin,pages/{admin-users,change-password,login},shared/api,app,features/auth}、frontend/e2e/{auth,admin-users,landing,public-homepage}.spec.ts、docs/{decisions/0009,architecture,deployment,testing,features}.md、plans/

## 2026-10-14 - 计划登记：邀请制开户与管理员控制台（关闭自助注册 · admin 用户 CRUD）

- **背景**：角色分配目前只能改 SQL，而 RBAC 的 `users:manage` / `roles:manage` / `audit:read` 三个权限**已定义并已授予 admin 却零端点**，`RoleOut`/`PermissionOut`/`UserRoleUpdateRequest`/`AuditEventOut` 四个 Schema 也已写好但无人引用，架构文档 §6.1 更早已把 `/auth/admin/users|roles|audit` 写成契约——即"实现落后于契约"。[ADR 0008](../docs/decisions/0008-research-read-requires-permission.md) 的「已知遗留」正是此事
- **实测硬事实（写入计划 Why now 表，逐条带落点与行号）**：`POST /auth/register` 建号即 `status="active"` 且默认角色硬编码 `trader`（`auth_service.py:117`/`:85`）；**全仓没有改密端点**，邀请制下管理员交付的初始密码永久有效；auth-service 自身**没有权限依赖**，而 `/auth/*` 在 Traefik 上不过 forward-auth（`docker-compose.yml:193`），admin 端点必须自判权限与 CSRF；**首个 admin 无引导方式**（迁移 0001 无种子、`grep bootstrap` 无命中）——这正是"只能改 SQL"的根因；登录**先判状态后验密码**（`auth_service.py:222` 早于 `:240`）导致账号状态可被枚举；变更角色后旧会话不失效而 forward-auth 断言缓存 TTL 仅 25s（`forward-auth/app/config.py:17`）；着陆页「注册只增加个性化能力」主叙事被 `public-homepage.spec.ts:314-333` 断言锁定；`test_api_auth.py`(5 处) 与 `test_api_internal.py`(1 处) 把注册当建号 fixture
- **owner 六项裁定**：邀请制/管理员预开户 · 开户时必须指定角色 · `/auth/admin/roles` 只读 · 不硬删只软停用 · 本轮就关闭注册并改造着陆页 · 服务端临时密码 + 强制首登改密
- **产出**：`plans/2026-10-14-invite-only-provisioning-and-admin-console.md` + `plans/index.md` 登记（`status: draft`）
- **计划结构**：§0 六项裁定 → §1 数据契约（**迁移只加 4 列、0 新表、0 约束变更** + `core/user_status.py` 状态机纯函数 + 测试建号 helper 前置）→ §2 关闭注册 + 登录门控改为**先验密码后判状态**（消除账号枚举，含假 hash 时序对齐）→ §3 临时密码与强制改密的**五段门控**（redis 会话 → auth-service 白名单 → 断言签发拒绝 → forward-auth 403 透传 → 前端全局跳转）→ §4 十条 admin 端点 + 会话吊销 + 三条硬保护 → §5 `bootstrap-admin` CLI 消灭首个 admin 的 SQL 依赖 → §6 前端控制台与着陆页改造 → §7 文档收口
- **可机械化的门禁（本计划新增）**：参数化遍历 admin 路由表，断言**每条路由无权限会话下 403、每条写路由缺 CSRF 时 403**（新增端点忘挂守卫立刻红）；断言 `POST /auth/register` 恒 403（防入口被悄悄打开）；forward-auth「上游 403 原样透传 / 上游 500 仍 503」；`bootstrap-admin` 三态幂等（空库建号 / 已存在只补角色 / 已是 admin 零变更）
- **边界**：不改业务库与 backend 的读接口；`/auth/*` 不走 forward-auth 故鉴权自判；**不改断言 claims 契约**（改由 `POST /internal/principal/assertion` 拒绝签发实现）；不做邀请码、邮件、SSO、组织租户、角色-权限在线编辑、硬删除、审计导出
- 涉及文件：plans/2026-10-14-invite-only-provisioning-and-admin-console.md、plans/index.md、docs/Changelog.md

## 2026-10-13 - 计划登记：前端设计整改（导航形态 · 信息去重 · 设计 token · 响应式）

- **背景**：对运行中的两套前端（`:3000` 的 `.worktrees/research-platform-ia` 10 导航新 IA、`:80` 的 docker 5 导航旧版）做全站设计评审，在 1440/1280/390 三档实测取证。结论不是"丑"，而是**默认值堆叠 + 同一信息出现多次 + 移动端硬伤**
- **实测硬事实（写入计划 Why now 表，逐条带落点与行号）**：一级导航 1280 折叠 4 项 / **1440 折叠 3 项**（`MainLayout.tsx`）；`MODULE_TREE` 恒为空数组致"三层布局"只有两层（同文件 `:69`）；数据版图约 800px 被钉在 `/market` 全部 5 个 Tab 之下（`market/index.tsx:312`）；31 个申万行业用 `SwIndustryGrid`(:244) 与 `IndustryClassification`(:251) 同屏渲染两遍；瓦片白字对 accent 叠白实测 **2.10:1（alpha 0.5）～3.81:1（0.85）**，低于 AA 4.5:1 且 6/31 瓦片命中（`SwIndustryGrid.tsx:8-17`）；`/market/global` 390px 视口 `scrollWidth=749`；移动端指数卡名称截成 `上证…`、代码 `CN 000001` 逐字竖排；日期四种写法（含 `2026-10-12T15:00:00Z` 直出）；品牌字标 `Stock Bot` / `StockBot` 并存；全站 0 处 `prefers-reduced-motion`、`.css` 仅 1 处 `:focus-visible`。**除导航溢出与左树为空两项外，其余在 `main` 上同样成立**（已逐项核对 `market/index.tsx`、`SwIndustryGrid.tsx`、`landing/index.tsx`）
- **产出**：`plans/2026-10-13-frontend-design-remediation.md` + `plans/index.md` 登记（`status: draft`）
- **计划结构**：§0 五项 owner 待裁定（导航形态 A1/A2/A3 · 移动端承诺等级 · 字体自托管预算 · 首页定位是否推翻既有"免登录行情台"拍板 · 数据版图新页位置）→ Phase 0 基线冻结与**先红后绿门禁**（五条断言先全部失败，证明抓的是真问题）→ Phase 1 W1 导航与布局表达 + W2 信息去重 → Phase 2 W3 设计系统（`--font-ui`/`--font-num` 分离 · 中性量级色阶 `--mag-0..4` 取代 accent 编码数量 · 日期单一入口 · 动效降级 · 焦点环）→ Phase 3 W4 四档断点 → Phase 4 W5 文案与打磨（信号历史 diff 化 · landing 收敛 · DataRow 多列）
- **可机械化的门禁（本计划新增）**：`check-design-tokens.mjs` 扩展为 5 组断言（`--mag-*` 三块一致性 + 逐级对比度 ≥4.5 + 字体 token 与 `theme.ts` 一致 + `reduced-motion`/`focus-visible` 存在 + `fontVariantNumeric` 出现次数 ≤1）；新增 `check-no-raw-iso.mjs`（禁止 ISO 日期直出）；新增 4 个 Playwright spec（尺寸/导航/工作页布局/占位页路线图）——**不采用像素快照**（实盘数据会漂，只锚结构、尺寸与文本存在性）
- **边界**：不改后端、不改 K 线/ECharts 主题、不引入新依赖、不重画 10 个模块的真页面、不改 IA 的 10 导航构成与权限三态；与 `2026-10-12-investment-research-platform-ia.md` 的关系是本计划只做其**前端表达层**，W1 依赖 IA Wave 0「IA 与路由表冻结」先落
- 涉及文件：plans/2026-10-13-frontend-design-remediation.md、plans/index.md、docs/Changelog.md
## 2026-10-13 - 短线情绪页纵向预算回收：三个长区块限制默认行数（页高 −42%）

- **背景（用户反馈「布局混乱」+ 实测）**：`/market?tab=sentiment` 页高 **5723px（≈5.7 屏）**，其中 **76% 的纵向预算**花在三个次级区块上——申万三级最高板铺全量 **2778px**、昨日涨停今日表现 `pagination={false}` 全量 45 行 **1956px**、次新股情绪 20 行/页 **1171px**；两个核心区块（情绪温度计 502px + 连板梯队 919px）被挤到首屏之外
- **改动（纯限制 + 复用既有折叠模式，无新组件/无新数据）**：
  - `SwL3LimitUpBoard`：默认只铺热门前 **10** 个行业（行已按 `板高 desc → 家数 desc` 排序，头部即结论），其余走新增的 `.sw3-toggle`「展开全部 N 个行业 / 收起」——与 `LimitUpLadder` 档内 `展开全部 N 家` 同一交互与视觉语言
  - `YesterdayLimitUp`：`pagination={false}` → `pageSize: 10`（`hideOnSinglePage` 保留：样本 ≤10 时不出现分页器）
  - `NewStockBoard`：`pageSize: 20` → `10`
- **效果（Playwright 实测，同视口）**：页高 **5723 → 3344px（−42%）**；「展开全部 49 个行业」可点开（10 → 49 行）、可收起；阈值下不渲染折叠按钮
- **回归证据**：全量 e2e **同一 dev server 前后对照 = 97 passed / 10 skipped / 0 failed**（10 skipped 全是 `marketDataFace.spec.ts` 按数据缺失优雅跳过的既有用例）；`tsc --noEmit` ✓、`npm run build` ✓、`check:design` ✓
- 涉及文件：frontend/src/features/market/components/{SwL3LimitUpBoard.tsx,SwL3LimitUpBoard.css,YesterdayLimitUp.tsx}、frontend/src/features/concept/components/NewStockBoard.tsx、docs/Changelog.md
- 未做（有意）：情绪温度计压缩、左右分栏重排、题材维度——按 ponytail 评审先只回收纵向预算，看到效果再决定
## 2026-10-13 - **`/sentiment` 情绪分析页落地**（S1.8–S1.13 的读出台）+ 综合指数 hero 端点 + 守卫与数据需求对齐

### 一、为什么做这一页（补一个真实缺口）
IA 文档把「情绪分析」列为 **Wave 1**，而 Wave 1 只交付了**数据层**（S1.8–S1.13 落库 + 端点），**页面始终是 5 行 `return null` 占位**。更关键的是：**spec 的验收判据明确要求 UI**，而我此前把它们按"文档可查"自证合格了（等于自己把判据改宽）：
`S1.8`「定义在 UI 可查」· `S1.9`「换手率口径在 UI 可查」· `S1.10`「升贴水负值时 **UI 明示**」· `S1.11`「PE 口径在 UI 可查」· `S1.12`「频率与滞后在 UI 明示」「下界标注对使用者可见」· `S1.13`「**UI 必须明示** 当日由 N/11 条腿合成 + 生效权重」。本页把这些**逐条**兑现。

### 二、新增公开端点 `GET /api/v1/market/sentiment/composite`（`6d96f76`）
- 返回 `{as_of, value, window, sample_count, percentile, insufficient_history, legs, leg_count, total_legs, effective_weight, min_effective_weight, caliber_version}`；放在 `market_data.py` 的 `/sentiment/*` 家族（与 `calendar`/`trends`/`intraday` 同为**匿名可读**），**不**改受保护的 `/api/v1/metrics/*`。
- **分位由后端算**（沿用仓库纪律：分位一律来自后端、必须带样本窗口、样本不足**只显示「样本不足」绝不给数字**）。定义：尾窗 1260 个非 NULL 样本含当日，`round(100 × count(v ≤ today) / n, 2)`，nearest-rank 无插值。控制器**独立复核**（SQL 重算）：`today=32.8157 n=1260 count_le=134 pct=10.63` —— 与端点**完全一致**。
- **刻意不改** `percentile_from_series` / `PERCENTILE_FIELDS`（`limit_up_service.py:779`）：它是**拍板过**的机制（Ruling 4 / 拍板 R6，只 3 个涨停字段），且按 `_PERCENTILE_VALIDITY` 硬编码字段名、返回的是**分位表**而非"今天处于第几分位"。无需求支撑地改它 = 纯风险；在情绪侧写同定义的极小函数并留 `ponytail:` 注明 **S2.2 该统一**。

### 三、`/sentiment` 真页面（`f8a7ecd`）
- **不是好看仪表盘，是可复核读数台**：每格数字都能追到**口径**与**新鲜度**。
- **常驻左索引 + 单滚动列**（10 个模块是同一件事的 10 个侧面，**需要同时比较**，故不用 Tabs）；按读者提问顺序分组：结果 / 市场内部 / 价格之外 / 参与者。
- **Hero（全页唯一卡片）**：综合指数值 + **0–100 分位轨道**（标出 10/90 极值带）+ **「本日由 3/11 条腿合成 · 生效权重 0.23（下限 0.20）」**（Ruling 66 对"最新值很薄"的可见性要求）+ 参与腿与分位窗口/样本数。
- **1 张模块表 + 1 个渲染器**（`moduleTable.ts` + `SentimentModulePanel`），不是 10 个手写组件；无图默认（数字不需要图）。
- **8 条口径内联注**（换手率自由流通口径 / 基差符号=期货−现货即贴水 / QVIX 滞后 / PE 为 CSI300 / 公募基金仓位是第三方**周度估算且口径不可复算** / `new_fund_share` 部分为**下界** / 综合指数权重与方法 / 缺失一律 `None` + `missing_reason`），原文取自 `metric-dictionary` 与 `data-source`，**未自造口径文案**。
- **2 个未建模块就地说明**（⑧ 散户舆情 = `S1.14` 标 superseded；⑩ 情绪预警与预判 = 依赖未建的 S2.2），**不留空图**。
- **分位只在后端提供处显示**：实测页面上分位带自己的窗口「P25 23.81% / P50 29.21% / P75 35.16% · 近 240 交易日分位（2025-10-15 至 2026-10-12）」。
- **新增 2 个情绪极值 token**（`--sent-extreme-cold` `#8b7cff`/`#5b4bd6`、`--sent-extreme-hot` `#ffb020`/`#a35f00`）。选紫/琥珀而非红/绿，是因为 `--up`/`--down` **已表示涨跌方向**，情绪"热"若也用红会与"股价上涨"混淆（读数台上是危险歧义）；且这两个色**恰好对应 S1.13 参数化规则的 `pct>0.90` / `pct<0.10` 两条阈值**。**已把这两个名字加进 `check-design-tokens.mjs` 的对比度名单**（原为死数组 `["up","down"]`），实测全过：cold 6.14:1/5.83:1、hot 5.01:1/4.76:1。

### 四、⚠️ 落地时实测抓到并修掉的一个真实缺陷：**守卫与数据需求不一致**
- **现象**：`/sentiment` 初版只标 `requiresAuth`（任何登录用户可进），但页面消费的 `/api/v1/metrics/*` **要求 `research:read`**——实测匿名 **401**、**普通交易员 403**。结果是：**普通登录用户能进页面，却读不到任何模块数据**（只剩一张诚实的"读面未取到…不显示 0"空表）。
- **修法**：把守卫改成与同为研究层的 `/industry` **同一个** `permission: "research:read"`（1 行，沿用既有机制，不新增 scope）。依据是 **IA 文档 §1 的「分区」列本就写着情绪分析 = 研究（只读）**。
- **实测确认修好**：研究者（`research:read`）→ `/api/v1/metrics/latest` **200**，页面 **7 张表 · 真实数值 · 空表告警消失 · console errors 0**（上涨家数 3414家 / 下跌家数 2145家 / 站上20日均线 38.72% / 炸板率 21.74%，均带「数据截至 10月9日 · 日频 · 口径 s1.8-v2」）；退回纯 trader → 呈现 **403「抱歉，您缺少必要的操作权限。」**（而**不是**一张看起来坏掉的空表）。
- **文档同步**：IA §1 权限列「待定」→ `research:read`；`ARCHITECTURE.md §6` 鉴权列同步（`check:routes` 会**双向**核对这两处，改漏即红——本次就是它先报的 `✘ /sentiment：鉴权列缺 research:read`）。

### 五、验证
- 前端：`tsc` 0 · `build` 0 · `check:routes` ✓（§6 34 行一致 · 84 项）· `check:design` ✓（含新 token 对比度）· `e2e/sentiment.spec.ts` **11 passed**。
- 后端：pytest **1380 → 1392 passed / 17 skipped / 0 failed**（+12）· ruff / `ruff format --check`（198 文件）/ `mypy app`（197 文件）绿 · `self_review.sh` ✓。
- **真实登录态端到端实测**（此前从未跑过，是本页最大的验证缺口）：用临时账号走真实注册/登录（CSRF 取自 `/auth/csrf`）、按 `docs/testing/frontend-e2e.md` 的既有做法挂 `专业投研员` 角色验证有数据路径、再撤角色验证 403 路径，**验证后已把临时账号与角色全部删除**（auth 库回到原 3 个账号）。
- **运维提醒**：若你的账号是「普通交易员」，则 `/industry`（早已上线）与本页会同样返回 403 —— 研究层模块需要 `专业投研员`/`分析师`/`系统管理员` 角色（现有 `stocks@example.com` 是系统管理员）。

### 六、流程教训（第三次同类，已记）
我在写 brief 时**凭记忆**写了「S1.13 写 `extra.legs`」，实际写的是 **`extra.participants`** —— 实现 agent **没盲从 brief，而是去读了真实数据**并纠正。这是本会话**第三次**"凭记忆写 brief / 凭记忆列清单"出错（前两次：并行协议里凭记忆列禁用文件、对 `head -12` 截断下结论）。**规则：brief 里任何事实性断言（字段名、文件路径、常量值）必须当场查证，不得凭记忆写出。**

## 2026-10-13 - **申万行业判据统一到 SCD2** + 前端旧分块 404 恢复通道 + SCD2 数据回填 + 数据卷锚点地雷拆除

### 一、申万行业树与下钻端点**逐节点对齐**（`5de5101`）
- **缺陷**：同一张卡片渲染的两个接口对同一事实给出不同答案——
  `GET /market/sw-industry/tree` 走**旧扁平表** `sw_industry_members`（陈旧快照），而 `GET /market/sw-industry/drill/levels/*` 走 SCD2。实测 **29/31 个一级行业数字不一致（94%）**，合计 **旧树 5587 vs SCD2 5211（多算 376 只）**：农林牧渔 107 vs 104、基础化工 **450 vs 410**、钢铁 53 vs 43、电子 524 vs 493。用户在「申万行业网格」「行业分类」卡片上看到的是**偏多的错数**，而同一屏下钻给的才是对的。
- **根因**：S1.4 已确立「**SCD2 归属是唯一真相：所有成分解析都走它**」（`sw_drilldown_service.py:8`），但 `get_sw_industry_tree` **没有跟着迁过来**。
- **修复**：官方成员源切到 `sw_industry_member_periods` 且 `is_current`，**按节点自身层级聚合（不做 L3 上卷）**、**不 join `Stock`**（与下钻 `_EXACT_MEMBERS_SQL` 同语义）；层级仍来自 `SwIndustryClass`，**用户自定义标签 `StockCustomSwTag` 行为保持不变**。每个节点新增 `officialCount` + `customCount`，`stockCount = officialCount + customCount`。
- **顺带修掉两个隐藏问题**：① 缓存 key **硬编码** `market:sw-tree` 导致 `set_sw_tags` 的失效逻辑会在改 key 后**静默失效** → 改为常量 `SW_TREE_CACHE_KEY = "market:sw-tree:v2"`（读写与失效共用），并有测试在 v1 key 放哨兵值断言其**不会被返回** ② 「其他 OTHER」桶原先带 `level == 3` 过滤，使 **261 个直挂 L1/L2 的符号被重复计数**（74 → 63）。
- **验收**：**511/511 个真实节点（L1 31 + L2 134 + L3 346）差异 = 0**，`sum|diff|` L1/L2/L3 全 0，节点集双向一致；`OTHER`（63）无下钻对应物，`officialCount = 0`，如实标注而非隐藏。控制器**独立复跑对账脚本**并额外抽查活栈 L2/L3（51 节点）确认 0 差异。
- 门禁：pytest **1373 → 1380 passed / 17 skipped / 0 failed**；mypy app ✓；ruff/format ✓；`self_review.sh` ✓。**无现有测试断言过旧数字**（即没有测试在守护这个 bug）。

### 二、前端「重新部署后旧分块 404」恢复通道（`004aa90`）
- **缺陷**：Vite 哈希分块在**每次前端重新部署**后改名。**部署前就开着的标签页**内存里是旧 bundle，导航到尚未加载的路由时按**旧哈希**请求分块 → **404** → 懒加载组件不挂载 → **面板静默空白 / "Failed to fetch"**，用户无从得知也无人自愈。本会话实测到 `404 /assets/index-iB5Al3gl.js`（时间戳在重建之后），而当前 `index.html` 引用的是 `index-DKExqQSR.js`；**旧哈希在当前镜像里 0 命中**。同时确认 nginx 缓存头本身**正确**（`index.html` → `no-cache`；`/assets/*` → `public, immutable`）、**无 Service Worker**、后端与数据库全程健康——**纯粹是缺客户端恢复通道**。
- **修复**：`frontend/src/app/chunk-error-recovery.ts`（新）监听 `vite:preloadError`（并 `preventDefault`）+ `unhandledrejection` 兜底（该路径**必须先过动态 import 报错文案匹配**，以免把后端 5xx 误判成版本更新）；在 `main.tsx` **首行 import**，模块求值期即注册，**早于 React 挂载与任何懒加载分块请求**。决策逻辑是**纯函数** `shouldReload`/`markReloaded`（`sessionStorage['stockbot:chunk-reloaded']` 守卫，**不可能成环**）：首次失败 → 「页面版本已更新，正在刷新…」+ **本会话刷新一次**；同会话再次失败 → 常驻「页面版本仍是旧版，请手动刷新（Ctrl+F5 / Cmd+Shift+R）」**不再刷新**；另有 `handling` 防重入。零硬编码颜色。
- **验证**：`tsc` exit 0 · `build` exit 0 · `check:design` ✓ · `check:routes` ✓ · **新增 `frontend/e2e/chunkRecovery.spec.ts` 6 passed**（自动刷新 / 提示文案 / 防重入 / 二次失败转常驻 / `unhandledrejection` 兜底 / **噪声不误伤**）+ `navigation` 3 passed。控制器在**容器产物**上复跑：**9 passed**。
- 注：本仓库**无前端单测运行器**（无 vitest/jest），故守卫由 Playwright 真浏览器驱动验证。

### 三、SCD2 成分归属回填（环境修复，非代码）
- `sw_industry_member_periods` 在此环境**从未被灌过数据**（0 行，`sw_member_ingest_runs` = 0 行 = 无任何运行记录），而 S1.4 规定所有成分解析都走它 → 下钻页显示「分类归属口径日 无数据」「成分覆盖 0 指数」、7 项行情字段全 `--`、并**显式弹出**「分类归属尚无 SCD2 数据…请先运行 `backend/scripts/ingest_sw_member_periods.py`」（这个**显式失败是好设计**：它明说"成分一律为空，不代表当日无成分"，没有拿 0 冒充真相）。
- **已执行**：`511/511 指数成功 · 新增 23,534 行 · as_of=2026-10-13 · fully_ingested=True · failed=0`；覆盖 L1 31/L2 134/L3 346；**431/511 个行业**拿到成员，**80 个上游 0 行**（UI 显示为「0 行 80」）。
- **遗留（部署项）**：该 ingest **未注册进调度**，不会自愈——新环境部署必须跑一次（或登记周期任务）。另 `stock_features` 为空（`features.compute` 队列从未被触发过），属同类"代码在、数据未灌"缺口。

### 四、数据卷锚点地雷拆除（环境修复）
- **事故复盘**：`docker-compose.yml` 的 `volumes.postgres_data.name: stock_bot_wt_p7_postgres_data` 与**本机真实数据卷** `stock-bot_postgres_data`（5.0G，created 2026-05-09）**不一致** → 控制器跑 `docker compose up` 时 postgres 被重建到**此前不存在的空卷**，表现为"数据全丢"（数据其实一直在旧卷，只是没挂上）。
- **修复**：把数据**迁进锚定卷**（tar 跨卷拷贝，源卷 `:ro` 只读、目标卷先清空、owner/权限/mtime 保留），随后改用**标准 compose（不带任何 override）** 启动。校验：`postgres` 挂载 **`stock_bot_wt_p7_postgres_data`**、`alembic = b6a1f0c2d7e4`、`industry_metrics = 44,812`、`daily_quotes = 4,433,523`、`scd2_periods = 23,534`。**此后标准 `docker compose up` 即正确**。
- **旧卷 `stock-bot_postgres_data` 保留未删**（回滚保险）。

### 五、控制器流程教训（该记）
1. **"代码验证通过" ≠ "环境可用"**：S1.4 当时有 10 条机器记录与真实对拍，却**没把 SCD2 灌进环境**，页面全空。验收标准必须包含"**跑完之后这个页面能看**"。
2. **验收必须验"同一事实的所有来源是否一致"**，不能只验"每个接口各自对不对"——本次 tree/drill 打架就是漏了这一条。
3. **跑会重建基础设施的 compose 命令前，必须先核对"配置声明的卷名"与"实际有数据的卷名"是否一致**（`docker volume ls` + `inspect` 看 created/labels）。

## 2026-10-12 - 移除「数据版图」静态矩阵（市场页底部 + landing 第 6 区块）

- **背景（用户反馈）**：`DataCoverageMatrix` 是 11 行**手写常量**的静态宣传矩阵，挂在 `/market` 的 Tabs **下方**，所以 5 个 Tab 里下滑都会重复看到同一张表；且它与真实数据可用性长期漂移——北向上游停更后靠手改常量把该行标成「规划中」，逐域上线状态全靠人工维护
- **改动**：删除组件本体 `features/market/components/DataCoverageMatrix.{tsx,css}` 与 barrel 导出、`/market` 页脚上方那份（`withStats={false}`）、landing 第 6 区块（`landing/sections/DataCoverage.tsx` 及其 import/挂载）；同步删除两处断言它的 e2e（`marketTrust.spec.ts` 的「数据版图把北向一行标为规划中…」、`landing.spec.ts` 的「数据覆盖矩阵含核心数据域与免费徽章」），修正 `marketPolling.spec.ts` 与 `landing.css` 里的过时注释，设计文档 `docs/design/landing-market-theme.md` §5 改为「已下线」并保留表格作历史存档（去掉对已删 `COVERAGE_ROWS` 的同源引用）
- **替代入口**：数据新鲜度的真实来源是**后端** `GET /market/data-freshness` 的 `coverage`（逐域 `have_days`/`complete_days`/`cover_pct`，由库内实况算出）；前端不再用手写常量对外宣称"已上线/免费"
- 涉及文件：frontend/src/features/market/components/{DataCoverageMatrix.tsx,DataCoverageMatrix.css,index.ts}、frontend/src/pages/market/index.tsx、frontend/src/pages/landing/{index.tsx,landing.css,sections/DataCoverage.tsx}、frontend/e2e/{marketTrust,landing,marketPolling}.spec.ts、docs/design/landing-market-theme.md、docs/Changelog.md

## 2026-10-12 - S1.13 市场情绪综合指数（11 条日频腿滚动分位加权合成；**S2.2 的前置**）

- **交付**（`697a138`，6 文件）：**`market_sentiment_composite`**（S2.2 期望的确切键名）落 `industry_metrics(industry_key='market')`，口径 `s1.13-v1`；**无新表、无新迁移**（单头 `b6a1f0c2d7e4`）
- **控制器查库实测**：**4002 行 / 2708 有值**（**≥1260** ✓）；行跨度 `2010-04-16..2026-10-12`（**= 最长成分 `if_basis`**）、有值跨度 `2015-08-13..2026-10-12`；缺失 **1294 行全为 `insufficient_participants`**；PIT `count(*) = published_at = caliber_version = 4002`
- **PIT 的深度验证（做得深）**：`published_at::date = period` 为 4002/4002——因成分**都是当日观测**，PIT 由**时点**而非日偏移保证；并实测 **`published_at < 当日最晚成分` 的行数 = 0 ⇒ 无前视**
- **可复算性（判据 2）**：以**独立** nearest-rank 实现**从成分行**重算（**不重读 `extra`**），4 个日期逐位一致（`2019-10-27` 51.1338 · `2026-09-10` 40.3612（10/11 腿、按 /0.88 归一化）· `2015-08-13` 36.9615 · `2026-10-12` 32.8157）；容差 = `Numeric(18,4)` 量化（≤5e-5）+ 4 位取整后严格相等；**破坏验证：跳过归一化 或 分位四舍五入到 6 位 → 4 个测试转红** ⇒ 断言非空转
- **命名**：只叫「市场情绪综合指数」；产品面（`frontend/src/`）**0 命中**；判据最终形式为 `grep -rn "恐贪\|fear.*greed" frontend/src/ docs/ --exclude-dir=specs --exclude=Changelog.md` 无命中（**Ruling 65/66**：契约层**陈述**禁令、历史层**记录**禁令都必然命中，排除二者；否则"记录禁令"本身即违规，逻辑自相矛盾）
- **spec 依实测修正（Ruling 66）**：① 判据 1「与成分**等长**」对窗口极不齐的成分**不可能同时成立** → 落地口径为**行日历 = 各成分交易日并集**（行跨度等于最长成分、不短于任何成分），**有值跨度另行如实报告**，且**每个有值行的 `extra` 记录当日实际参与成分**（可复算前提）② **S2.2 必须用 `range>=6y`（或 `all`）**——`range=5y` 只回 **1214** 个有值日（<1260），会把 `window=1260` 的分位算在不完整窗口上 ③ **最新值的"薄"必须对使用者可见**：实测 `2026-10-12` **仅 3/11 条腿**（生效权重 0.23，下限 0.20），而 `2026-10-09` 有 10 条——因不同腿最新日不同（`daily_quotes` 锚定的腿止于 10-09、QVIX 止于 09-30）。**保留下限 0.20**（提高会把最近若干日整片判 NULL，恰在用户最关注时），**但 UI 须明示"当日由 N/11 条腿合成 + 生效权重"**，且下限取值理由须在字典写明
- 门禁：pytest **1347 → 1373 passed / 17 skipped / 0 failed**（+26 = 19 纯 + 7 DB）；ruff/format/mypy/self_review/doc_gate/`check_metric_dictionary`(48 行 MISMATCH 0) 全绿；测试零库名字面量

## 2026-10-12 - Ruling 64/65 收尾：`freq` 改实测周度 + 下界可见 + **「registry 元数据 vs 落库元数据不一致 ⇒ 序列静默 0 点」升级为机械检查** + S1.13 判据修正

- **`freq` 改实测值**（`53bfe07`）：`public_fund_equity_position` `quarterly` → **`weekly`**，共改 **19 处**（registry `MetricDef.freq`、服务 `FREQS`、registry 描述（删「季报口径」，改为「低频参考值」+「频率：`weekly`（周度）——按产地实测登记」）、`extra.frequency_note`/`publish_lag_note`、字典 §4.2、`data-source.md` 附.10 四处、provider/探针注释、4 处测试断言）；前端 `FREQ_LABELS` 本就有 `weekly→周度`，无需改
  - **导入期断言未放宽**（只加注释），并**新增单测 `test_service_freq_matches_registry_freq`** 防"服务 ↔ registry freq 漂移"
  - 控制器核验：表内 `freq=weekly`（455 行，pit/cal 各 455）、`new_fund_share` `monthly`（45/45/45）；`alembic heads` 单头 `b6a1f0c2d7e4`
- **下界可见（Ruling 64）**：registry 描述新增面向使用者的逐字警示（「⚠️ **下界口径（使用者必读）**：…本值 = **「有值成员之和」即该月真实规模的下界**…**读的人不得把部分成员的和当成全月完整总额**」），字典 §4.2 新增 🔴 条目；**并修掉一处与实现相反的旧文案**（原写"有任一基金为空→整月 NULL"，与实际实现不符）。控制器核验：`extra.is_lower_bound` 行数 **42/45** ✓；`source_tier='calc'`、口径不可复算、背景-only/不进信号链路、`published_at` 为推算值（列为未验证余项）**均保留未动**
- **★ 一类新缺陷被识别并机械覆盖（Ruling 64 收尾）**：`freq` 是**落库列**且**序列读面按 registry 的 `freq` 过滤** → 改 registry 后，dev 库 455 行旧标签会使 `get_series` **静默返回 0 点**。这与 S1.10 发现的「registry `sources` 与落库 `source` 不一致 ⇒ 序列静默 0 点」**是同一类**：**registry 元数据与落库元数据不一致时，读面静默返空**。
  - 处置：实现方对 dev 库做了一次**幂等定向元数据重标**（`UPDATE … SET freq='weekly' … AND freq='quarterly'` → 455 行，复测 `get_series` = **455 点**）；控制器实测确认（`public_fund_equity_position` 455 点、`new_fund_share` 45 点）
  - **⚠️ 其它环境（含生产）若已按 `quarterly` 落过行，需要同一次重标**——首选**重跑幂等回补脚本**（`scripts/backfill_investor_behavior.py`），上游不可达时用等价的定向重标。**必须在部署文档留痕**（归入终审 triage 的部署项）
  - **裁定**：该类"**元数据漂移 ⇒ 静默返空**"**必须机械化**（AGENTS.md 第 5 条：能机械检测的不得只写文档）——要求**新增检查：对所有已落库的 `metric_key`，断言其落库 `freq`/`source` 与 registry 声明一致**（S1.10 已为 `source` 加过单测，本项把它扩到 `freq` 并覆盖全量 key）。排入下一批
- **S1.13 判据修正（Ruling 65）**：原判据 `grep -rn "恐贪\|fear.*greed" frontend/src/ docs/` **无命中**按字面**不可满足**——实测 3 处命中**全在 `docs/specs/`**，即**契约层自己陈述该禁令**（`S1-market-sentiment.md:305` 禁令本身、`:313` 原判据本身、`README.md:197` 不作项清单）。**判据意图是禁止产品命名，不是禁止契约陈述禁令** → 改为 `--exclude-dir=specs`，且 `docs/specs/` 内的命中**只允许是禁令陈述本身**；并补明「**自建 composite 允许进信号链路**（S2.2 的示例规则正是消费它），被禁的是**第三方机构编制**的情绪指数」
- 门禁：pytest **1345 → 1347 passed / 17 skipped / 0 failed**（+2）；ruff/format/mypy/`check_metric_dictionary`/`test_db_isolation_guard`/self_review/doc_gate/tsc 全绿；测试**零库名字面量**

## 2026-10-12 - S1.12 投资者行为（新成立基金份额 + 公募基金仓位；**开户数判不可得并从产品中移除**）

- **交付**（`076d3dd`，9 文件）：
  - **`new_fund_share`** 45 行/45 有值（月频，2023-01…2026-09）
  - **`public_fund_equity_position`** 455 行/455 有值（2017-12-04…2026-09-30）
  - 两者口径 `s1.12-v1`；**无新表、零新迁移**（单头 `b6a1f0c2d7e4`）
- **★ 开户数裁决 = `不可得`，按 spec 从产品中移除**（不是占位空图、不是估算值）：东财 `stock_account_statistics_em` 可调但**最新月停在 2023-08**（101 行；**直连东财 API 实测 `count=101/pages=1` ⇒ 101 即全量，非分页截断**）；中国结算月报是唯一权威产地而 AKShare 未封装 ⇒ **无第二源**。处置：**连 `metric_key` 都不注册**（market scope 39 个 key 里没有它；读面对该键 404，有真库用例）+ 结论落 `INVESTOR_BEHAVIOR_DROPPED_ITEMS` + 字典专段 + 对 4 个占位键名写死**导入期断言**防将来误加
- **PIT**：两 key 均 `count(*) == count(published_at) == count(caliber_version)`（45/45/45、455/455/455）；**并额外验证了否定性质——`published_at::date = period` 的行数 = 0/0 ⇒ 确认未静默回退 `period`**
- **spec 判据机械化**：频率与滞后已落 registry `MetricDef.freq` + 前端 `FREQ_LABELS` 徽章 + 字典 4 列表，并**用导入期断言强制描述里同时含「频率」「滞后」**（把文档纪律升级为门禁，符合 AGENTS.md 第 5 条）；公募基金仓位**不进信号链路**由 `grep` 可验
- **spec 依实测修正（Ruling 64）**：spec 原写公募基金仓位为「**季报**、滞后 ~15 工作日」，但**实测产地并非官方季报**——乐咕乐股 `fund_stock_position_lg` 是第三方**周度观测/估算**（相邻观测间隔 6–8 天占 **90.3%**），且上游 JSON **只有 `date/close/position`**（无报告期/发布日期/编制方法）⇒「股票投资占基金总资产比」**无法从产地复算**。spec 已改为描述真实来源；`source_tier='calc'`（第三方编制，**非官方**）、`freq` 按实测、`published_at` 为**推算值并列为未验证余项**；「仅背景展示、不进信号链路」的硬约束保留
  - 本条与上文「成交额冒充净买入」是**同一类错误**（**机器可读的标签说了假话**）——不能只靠散文披露兜底，故 `freq` 必须等于实测频率
- **下界口径须对使用者可见（Ruling 64）**：`new_fund_share` 的 42/45 月是**下界**（字段级缺值 ≈4.8%，均匀散布），实现方选「有值成员之和 + `extra.is_lower_bound=true`」而非「整月判 NULL」（后者实测只剩 3 个月，差 15 倍）——控制器**接受该口径判断**，但补明：机读标记是**必要非充分**条件，registry 描述与字典须以**面向使用者**的措辞写明"下界（有值成员之和）"。**A/C 双计不除以 2 是对的**（同一基金的独立份额类别，各自募集份额相加即总募集）；2019–2022 该产地无数据已登记
- 门禁：pytest **1306 → 1345 passed / 14 → 17 skipped**（+42 = 39 pass + 3 条件 skip，逐项对账吻合）；ruff/format/mypy/`check_metric_dictionary`(47 行 MISMATCH 0)/self_review/doc_gate 全绿；测试**零库名字面量**，`test_db_isolation_guard` 通过

## 2026-10-12 - **北向成交额口径重标**（关闭上文那处静默数据缺陷）+ 覆盖矩阵口径更正

- **交付**（`b32e778`，23 文件 +1108/−83）：`northbound_daily` 增加**行级 `amount_kind`**（`net_buy` | `turnover`，**NOT NULL + CHECK**，按 2024-08-19 断点回填）+ `net_amount` **改名** `amount`（**数值一个不改**）；断点常量的**唯一活来源** = `app/services/northbound_caliber.py`；**每个 DTO 点带 `amount_kind`**，信封给窗口口径（`net_buy` / `turnover` / 跨断点 `mixed` / 无行 `None`）
  - **为什么落列而不只在读面派生**：口径是**行的属性**——落列后**任何直接读表者**（SQL 查询、报表、后续新服务）也**无法再把成交额当净买入**；只改 DTO 会让那些读者继续错标
  - **单头**：`alembic heads` → `b6a1f0c2d7e4`（唯一）；up/down + 跨断点回填在**测试库真跑过**（原始输出在报告）
  - **控制器已在 dev 库执行 `alembic upgrade head`**（`84476cb7b838 → b6a1f0c2d7e4`）：列由 `['id','net_amount','source','trade_date']` 变为 `['amount','amount_kind'(NOT NULL),…]`；**42 行全部正确回填为 `amount_kind='turnover'`**，范围 `[207941.62, 351740.65]`——与控制器先前独立测得的"42 行全是断点后成交量口径值"**完全一致**
- **UI 真实性（dev 库真数据，days=30 窗口 14 行）**：
  - **改前**：`盘后净流入 · 亿元` / `当日 +29.67亿` / `近30日累计 362.55亿` ← **把 14 天的成交额加起来冒充净流入**
  - **改后**：`盘后成交额 · 亿元` / `当日成交额 29.67亿`（**无 + 号**）/ `近30日累计成交额 362.55亿`；跨断点 `mixed` → `—` + 「不与净买入相加」；**成交额不着涨跌色**、零轴只在净买入口径画
  - **既有能力一行未删**（表 / ingest / `northbound_daily_job` / 读端点 / 前端卡片全保留）——符合 Ruling 30
- **spec 判据的机械验证**：`grep -rn "北向\|northbound" backend/app/ frontend/src/` 共 **120 处**，**由脚本机械校验"全覆盖 + 无重复归类"**：
  - A 断点前净买入历史（显式标注）= **13**
  - B 成交额口径（命名已体现）= **107**（B1 明写成交额/口径 30；B2 口径中立管道、值由行级 `amount_kind` 标注 77）
  - **第三类（把成交额冒充净买入）= 0** ✓（控制器独立抽查确认：唯一邻近「净流入」的行是 `market_data_service.py:1659` 的**纠正性文档**"⚠️ 这不是'北向净流入'序列"）
  - 本轮还抓出并修掉**最后一处活代码错标**：`scheduler/jobs.py` 的 `northbound_daily_job` docstring 原写「北向资金每日净流入」
- **过程诚实（值得记）**：该 agent 的前一个提交 `9267185` **误把并行 S1.12 agent 追加的「附.10」带进自己的提交**——它**主动发现、amend 掉、把他人字节原样写回工作区（未暂存）留给对方自行提交**，并机械核对"本提交里对方文件数 = 0"。这是**跨 agent 污染的正确处理方式**
- 门禁：pytest **1291 → 1306 passed / 14 skipped / 0 failed**（+15）；ruff/format/mypy/tsc/build/check:design/check:routes/doc_gate 全绿；新 DB 用例**只取 `TEST_DATABASE_URL`**（未设即 skip、全程 rollback），`test_db_isolation_guard.py` 通过
- **覆盖矩阵（遗留，控制器裁定为延后）**：该行的原判据"全表 0 行"已不成立（现 42 行），但**转「已上线」是公开覆盖口径**且被两条 e2e（`landing.spec.ts` / `marketTrust.spec.ts`）与落地文档统计带 10→11 钉着 → 本轮**只把内容列由「净流入」改为「成交额（2024-08-19 起）」并把过期判据显式标出**，`planned` 状态未动。**控制器裁定：转「已上线」需同步 e2e 与文档计数，归入终审 triage**（内容已诚实，非数据正确性问题）

## 2026-10-12 - S1.7 资金流四件套（ETF 份额变化 + 南向净买入）+ 一处**同列换口径**的静默数据缺陷被查明

- **交付**（`9cbf416` Step-0 · `84a32e3` 四件套 + 陆股通持仓口径 + Ruling 61 读面 · `04ad704` 单测）：
  - **`etf_share_change`** 916 行（2023-01-03..2026-10-12 = **3.77 年**，远超 spec 的 ≥1 年，915 有值/1 NULL）
  - **`southbound_net_buy`** 674 行日频（673 有值/1 NULL）
  - **两融不重做**：`margin_financing_balance`/`margin_short_balance` 由 S1.9 已落，本批**只引用**（spec 契约表自己写着"已有（S1.9）"）
  - **`industrial_capital_net_change` 维持"待接"**（依赖未完成的 S3.2 公告采集），**0 行、无假数据** ✓（spec 原话即"不得用假数据填充"）
  - **陆股通持仓（季度）**：只落口径与滞后说明（`hk_hold` 覆盖不规则：`20240329` 0 行、`20260930` 只回港股通行）；**`market/foreign` 经核实"从未建过"**，页面待后续批次
- **PIT**：逐 key `count(*) == count(published_at) == count(caliber_version)`（916/916/916、674/674/674），`published_at` 为当日北京 21:00 带时区，`as_of_source` 恒 `published_at`（不回退 `period`）
- **★ 查明一处静默数据缺陷：同一列中途换口径**
  - **北向**：`moneyflow_hsgt.north_money` 在 **2024-08-19** 换义——断点前 148 日含 **83 个负值**（`[-13056.64, 22448.99]`，百万元）＝**净买入**；断点后 502 日**零负值零零值**（`[77584.24, 510101.29]`）＝**成交额**。**独立产地东财「当日成交净买额」断点后 502/502 全空** → **净买入口径确实已取消**。故 `northbound_daily` 现存 42 行（全在断点后、负值 0）**不是死数据，而是"成交量被错标成净流入"**；读面与前端卡片正把它当「北向净流入」、甚至**求和成「近30日累计」**——那个累计值**没有意义**
  - **南向**：`south_money` 是**历史累计净买额（亿元）**而非日频净买入，且**该列在 2023-11-24 也换过口径**（此前 114 日为"日频/百万元"且 37 日为负；此后为"累计/亿元"且无一日 ≤0）。实现方以**一阶差分**得日频，并与东财独立源对拍：累计 **650/650**、差分 vs 东财当日净买额 **649/649 逐值相等**；只落断点后 + **口径回退守卫**（断点后若出现 ≤0 或不合理小值 → **响亮失败**，即"上游把口径改回去"的信号）+ 丢弃断点前旧口径行（注释指出**混算会得到二阶差分**）
- **spec 依实机证据修正**（`259d406`，控制器行使裁定权）：背景改为「**每日净买入确实取消，但每日成交额仍披露、同列不同义**」；验收判据由「grep 不得出现每日净买入口径字段名」改为**语义判据**——凡承载断点后 `north_money` 的字段名/DTO/文案**必须体现「成交额」口径**，每处 grep 命中须归类为「断点前净买入历史（显式标注）」或「成交额口径（命名已体现）」，**不得有第三类**
- **上游陷阱（三条同族）**：`fund_share` **单次 2000 行且截"最近"** → 必须逐 `ts_code` 取（宽窗会静默缺头）；`hk_hold` **4200 行上限**；`stk_holdertrade` **3000 行上限**
- **ETF 篮子口径**：6 只按**规模（份额 × 收盘价）**选族内代表（只比份额会选中低净值行业主题 ETF；benchmark 文本匹配会漏 `510050`/`510500`，已打印原文举证）；上游脏行 `159915.SZ` 有 **2026-01-17 周六行** → 落 `NULL + partial_basket_coverage`，**不拿 1/6 成员冒充全篮**
- 门禁：pytest **1260 → 1291 passed / 14 skipped / 0 failed**（+31 = 27 纯 + 4 PG）；ruff/format/mypy/self_review/doc_gate/`check_metric_dictionary`（45 行 MISMATCH 0）全绿；**无新表**（alembic 单头）；新测试只用 `TEST_DATABASE_URL`（无硬编码库名）
- **Ruling 61 已落地**：`MetricLatestOut` + `MetricHistoryPointOut` 增加 `missing_reason` + `coverage`；样例 `qvix_50etf` → `source_not_updated`、`etf_share_change` 2026-01-17 → `partial_basket_coverage`(0.166667) —— **"源侧滞后"与"真缺数"从此可区分**
- **遗留**：北向的**跨面改名/重标**（字段名/DTO/前端文案体现成交额 + 去掉无意义的"近30日累计"）由 `f29980a9` 处理；`stk_holdertrade` **不采用**（spec 要求原料来自公告，公告面才提供 `publisher_tier` 与正文可回溯）；待接 key 骨架的 `missing_reason` 仍为 null（需 registry 级 `pending_reason`，跨批次）

## 2026-10-12 - S1.10 波动率与衍生品（QVIX 期权隐含波动率 + 股指期货升贴水 + IC/IM 贴水率）

- **交付**（`1b64ec7` Step-0 · `bc390bf` 指标落库 · `1875e3a` 修读面）：**5 条 QVIX 中国波指**（50ETF 2833 天 / 300ETF 1646 / 500ETF 981 / 创业板ETF 981 / 科创50ETF 811）· **4 个品种基差**（IF 4002 天自 2010-04-16、IC/IH 2791、IM 1021）· **IC/IM 贴水率**（`基差/现货×100`，与基差**同号不翻转**、不年化，年化值落 `extra.annualized_pct`）→ 全部落 `industry_metrics(industry_key='market')`，**无新表无迁移无平行读路**
- **复用优先的关键一步**：spec 要的"期权隐含波动率"**AKShare 已自带 5 条 QVIX 序列**（本机 1.18.94 实测），故**直接取序列，未自己实现 Black-Scholes 反解**；股指期货走 TuShare `fut_basic`/`fut_daily`/`fut_mapping`（S0.3 附.5 已证明可用，本任务补进 client）
- **Step-0 实测**（追加为 `docs/design/data-source.md` **附.8**）：两源**全部 verified、无 blocked**；`fut_daily` **单次 2000 行上限实测复现 → 必须分片**；`fut_mapping` **逐日对拍 0 不一致**（无前视、未复权）
- **符号约定写死并可机读**：**基差 = 期货 − 现货**（正=升水、负=贴水），`extra.sign_convention=futures_minus_spot` + registry 描述 + 字典三处一致；主值取**期货结算价**（`published_at` 16:00），收盘口径另落 `extra.basis_close` 供对拍（IF 2026-10-12：结算 −62.1798 vs 收盘 −54.3798，后者与 Step-0 探针 −54.38 逐位一致）——正是 spec「升贴水负值时 UI 明示贴水语义、避免正负号误读」的落地
- **PIT**：逐 key `count(*) == count(published_at) == count(caliber_version)`（控制器亲查确认），两档时点 15:00/16:00 Asia/Shanghai，口径 `s1.10-v1`；落库 21669 行（有值 21603 / NULL 66，**全部 `missing_reason=source_not_updated`，非 0**）
- **实现方发现并修掉一个真缺陷（惠及后续所有指标）**：registry 的 `sources` 登记与落库 `source` 不一致 → `get_series` 源白名单取不到源 → **`GET /metrics/series?metric_key=if_basis` 静默返回 0 点**（`latest` 读面正常故此前不可见）→ 已修 + 新增**机械守卫单测**（逐个 key 断言服务写入的 source 必在 registry sources 内）；控制器复现：`if_basis`/`ic_basis`/`qvix_50etf` 各 **242 点**、`source_mismatch=False`
- **口径诚实性**：QVIX 是**第三方（optbbs）编制**，官方 iVIX 自 **2018-02-22 由中证指数公司停发** → `source_tier` 记 **`highfreq` 而非 `official`**（未冒充官方）；且该静态 CSV **发布滞后 1–3 个交易日** → 相关日期落 NULL + `source_not_updated`（**不是 0**）；另有 3 行落在非交易日的上游行不落库但**计数告警**
- **未验证余项（记入附.8.4，不估算）**：中金所结算价的精确发布时刻（16:00 为保守取值）· QVIX 第三方口径未与官方/第二源对拍
- 门禁：pytest **1213 → 1260 passed**（12→14 skipped，+49 用例）；ruff/format/mypy/self_review/doc_gate/`check_metric_dictionary` 全绿；`alembic heads` 单头
- **遗留（Ruling 61）**：`missing_reason` 已落库但**读面不回传**（`MetricLatestOut`/`MetricHistoryPointOut` 无该字段）→ 前端分不清"源侧滞后"与"真缺数"；实现方**正确地未擅自改**（属 S1.2 读面变更）→ 已裁定要求扩展读面，排入下一批

## 2026-10-12 - **修复测试隔离缺陷（PIT 列每次 pytest 被 DROP）** + S1.9 交易活跃度与杠杆资金 + S1.11 估值与风险溢价

### 先记最重要的：一个会**破坏 dev 库数据**的测试隔离缺陷
- **症状**：`industry_metrics` 的 `published_at`/`caliber_version` 会在**每次完整 `pytest` 之后**变成 NULL，且 `count`/`created_at`/`xmin` **全不变**——看起来像"某次服务写入把它们抹了"，导致控制器一度误判实现方"谎报验证"（**Ruling 60 已撤回该指控**）
- **真因**：`backend/tests/test_intraday_snapshot_table.py` **硬编码 dev 库**（`localhost:5433/stock_bot`）并执行 `alembic downgrade d7c8b9a0e1f2`——**该版本低于 `f1a2b3c4d5e6_add_metric_pit_and_caliber`** → **DROP 掉这两列**，随后 `upgrade head` 把它们加回来（值 NULL）。`DROP/ADD COLUMN` **不重写行**，故所有行级指纹不变
- **修复**（`89c1fa0`）：该测试改用 `TEST_DATABASE_URL`（**刻意不回退 `DATABASE_URL`**——注释写明"那会在只设了 `DATABASE_URL` 且指向 dev 库时打到 dev 库"）+ 新增 **`backend/tests/test_db_isolation_guard.py`**（静态扫描"测试不得硬编码 dev 库" + 对两个跑 downgrade 的模块断言**解析后的 URL 不得是 dev 库**）
- **控制器决定性反证**：`pytest` 前 `market: n=15071 pit=15071 cal=15071` → 跑完 1213 passed → **仍为 15071/15071/15071**（不变）
- **教训**：① **在把 DB 状态当证据前，必须先确认测试套件不改写该库**——"状态稳定性"要先于"状态正确性"，这正是"亲自查库"这条缓解措施失效的原因（抹除发生在两次查询之间）② "测试硬编码共享 dev 库"应从"基线噪声"升级为"**可能造成数据破坏**"③ Task 8 已对 `test_industry_metric_pit_roundtrip.py` 做了破坏性迁移的 opt-in，但**同类的另一个文件被漏掉**——"修一个实例"不等于"修这一类"，现由隔离守卫机械覆盖全类

### S1.9 交易活跃度与杠杆资金 + S1.11 估值与风险溢价（`1a5feba` + 修复轮 `d0c5a4d`/`80ab747`）
- **Step-0 实机验证**（追加为 `docs/design/data-source.md` **附.7**，探针 `backend/scripts/spike_s19_s111_sources.py` 入库、空结果响亮失败）：
  - **融资融券** = `可得`（TuShare `margin`，日频，一行 = 一交易日 × 一交易所，实测 `SSE 1557/SZSE 1557/BSE 886`）；**单次 4000 行上限 → 不分片会把"≥3 年"做成"只有最近 8 年"且头部静默缺失**；金额单位实测为**元**、`rqyl` 为**股**
  - **指数换手率** = `受限`：7 个里 **5 个可直取**；**北证50(899050.BJ) 与科创综指(000680.SH) 返回 0 行且不报错** → 判 `需自建`（`index_weight` 月快照 × `daily_basic.turnover_rate_f`）；`turnover_rate_f`（自由流通）与 `turnover_rate`（总股本）同日不同值，**已在 registry 写明口径**
  - **10Y 国债** = TuShare `yc_cb` **blocked**（逐字：`抱歉，您没有接口(yc_cb)访问权限`）→ 改**中债官方** + 备源东财 `bond_zh_us_rate`（2002 起），**两源同日逐值相同（差 0.0000）**
- **落库**：13 个 spec 指标 + `pe_csi300` 落 `industry_metrics(industry_key='market')`，**不新建表、不新增迁移、无平行读路**；`erp_10y` 经修复轮**改锚到自身两个输入（CSI300 PE ∩ 10Y 国债）的可用性交集**，由 829 天（3.4 年）→ **1298 行 / 1260 有值 / 2021-07-26 起（5.2 年）**，满足 spec 的 ≥5 年；`idx_turnover_ratio_star` 仅 **244 天有值**（限制来自**上游 `index_weight` 快照起点 2025-09-30**，非指数成立日——`index_basic` 显示 base_date 2019-12-31、`index_daily` 1639 行；**保留科创综指不替换为科创50**），585 天标 `no_weight_snapshot`
- **修掉两个真 bug**：① 自建换手率的守卫把**空的成分集合**当成 0.000（并非键空间不匹配——实测交集 50/50 与 579/579），改为**先判定快照期**再取数 ② **缓存命中的 (key,day) 被回写成 `source_empty` NULL** → 重跑会摧毁全部自建值
- **PIT**：`published_at` 按产地分档（行情/IV 15:00 · 两融 18:00 · 国债 19:00 Asia/Shanghai），`caliber_version='s1.9s1.11-v2'`；控制器查库确认**每个 key 的 `n == published_at == caliber_version`**
- 门禁：pytest **1200 → 1213 passed / 12 skipped / 0 failed**；ruff/format/mypy/doc_gate/self_review 全绿；`check_metric_dictionary.py` 30 行 MISMATCH 0；alembic 单头
- **另补 S1.6 欠账**：`docs/design/metric-dictionary.md` 新增 **`## §4.7`** 榜级口径（`monthly_pct_chg`/`total_mv`/`dv_ratio`/`n_income_attr_p`/`roe`，含窗口、复权、样本谓词、期别语义、PIT 与"分母 == 排序谓词"）

## 2026-10-12 - S2.1 因子层（纯函数库 + registry 元数据，**不建表**）

- **交付**（`c2015e8`、`f5c8411`）：`backend/app/services/indicators.py`（新，463 行）——可复用**纯函数**因子库；`industry_registry.py` 扩展因子元数据（`factor_key → {名称, 单位, 输入指标, 窗口, 展示分组}`）；`docs/design/metric-dictionary.md` 登记
- **因子清单 14 个**：**10 个读时算**（纯函数、无 IO，Redis 缓存留在读面）+ **4 个物化**——后者**复用 `stock_features` 既有列**（`return_percentile`/`max_drawdown`/`trend_slope`/`annual_volatility`）→ **新增列 0、新增迁移 0**（`alembic heads` 仍单头 `84476cb7b838`）。长窗口因子落在既有 `stock_features.extra` JSONB
- **验收判据「不新增任何表」已核实**：迁移目录零改动、无 `factor_defs`/`create_table`（`grep` 计数 0）；spec 明令不建的 `factor_defs` 与因子库 UI **均未出现**（后者 Wave 5 才有消费方）
- **复用**：`cycle_engine.count_consecutive_negative` 以**再导出**方式复用（**恒等性由测试断言**，不是抄一份）；`percentile` 口径与既有两处 nearest-rank 实现（`limit_up_service.percentile_from_series`、`financial_service._percentile_rank`）对齐——那两处是 dict/shape-bound 且非滚动，故不可直接 import
- **纯函数纪律已验证**：138 个新测试在**数据库不可达**（端口 59999）时全绿 → 证明"无 DB、无 marker"；`indicators.py` 只 import `math`/`typing`
- **可复算性**：同一输入跑两次**误差恰为 0.0**；另有"用旧定义重算历史仍得原值"与"历史前缀不看未来"两条测试
- **物化写入路径**：新增纯函数 `stock_feature_row()` + 复用既有 `feature_repo.upsert_features()`（`uq_stock_features_key` 幂等）。**尚无写路径调用方**——spec 把消费方推迟到 Wave 5，故未自造 scheduler/worker 范围
- 门禁：pytest **980 → 1118 passed / 4 skipped / 0 failed**（+138 = **恰好等于新增测试数**，零回归）· ruff/format/mypy（190 files）✓ · `check_metric_dictionary.py` 16 行 **MISMATCH 0** · `bash scripts/bench.sh` **全部基准在 12% 阈值内**
- **并行开发的两个新坑（记入流程）**：① **pytest 必须显式导出 `DATABASE_URL`/`TEST_DATABASE_URL`（端口 5433）**——否则 `test_auth_guard.py` 会去连 5432 而失败，基线被误读为 979/1-failed ② **`scripts/self_review.sh` 在并行期间无法变绿**：它按分支改动集收集文件，会把**另一个 agent 的未提交 WIP** 拉进 ruff 面 → 并行期只能做**逐文件归因**（本任务已如此处理：唯一失败文件是对方的未跟踪测试，自己 5 个文件全过）

## 2026-10-12 - S1.8 市场宽度与动量（6 指标落 industry_metrics scope=market）+ 并行开发流程启用

- **并行开发流程（Ruling 53，用户批准 A+B）**：同一 worktree 内**两个 agent 并行**，靠"文件集不重叠 + 提交只用显式路径"保证安全；**分级评审**——碰数据正确性的走完整闭环，纯展示类只跑门禁即收口（**但每条新断言仍须配注入反例**，不降级）。首批并行（S1.5 前端 / S1.8 后端）**零冲突落地**，合并态 `pytest` 一次通过
- **S1.8 交付**（`643ec73`）：`backend/app/services/market_sentiment_service.py` 计算并落库 **8 个** `metric_key`（spec 的 6 个 + `market_advance_count`/`market_decline_count`，后者让"同源同值"可直接核验）到 **`industry_metrics` + `industry_key='market'`**——这正是 S1.2 当初泛化该键的用途（Ruling 16）。**不新建表、不新增迁移、不造平行读路**，经既有 `GET /api/v1/metrics/{latest,series}` 读取
- **复用**：4 个涨停类指标**只读 `market_sentiment_daily`**（零重算涨停事实）；`advance_decline_ratio` 复用市场模块**同一 loader**（`load_day_rows`）与**同一分桶谓词**（`_distribution_bucket`），并有标签集自检；唯一新算法是 `above_ma20_ratio`（单条窗口函数 SQL + `win=20` 完备性守卫，不足 20 交易日的股票从分子分母同时剔除）
- **回填实测**：**262 交易日 / 401 日历日**（2025-09-04→2026-10-09）· **2096 行** · missing=0 · 幂等（重跑仍 2096）。覆盖率：`above_ma20_ratio` 262/262；`advance_decline_ratio` **241/262**（21 天为 `partial_pct_chg`——`pct_chg` 覆盖不足 95% 门槛时不落数值）；涨停类 236–239/262（缺数标 `no_sentiment_day`/`source_null`，**NULL 而非 0**）。PIT：`published_at`=15:00 Asia/Shanghai、`as_of_source='published_at'`（无静默回退）、`caliber_version='s1.8-v2'`
- **口径冲突处置（Ruling 54）**：市场模块 `0~1%` 桶是 `0 <= pct_chg < 1`，**把平盘算进上涨**；而 `summarize_group.up_count` 是 `pct_chg > 0`。实测 2026-10-09 正桶合计 3414 vs 正号 3297，**差 117 只平盘**（负侧完全一致）。裁定：主口径**对齐市场模块已发布的 11 桶**（两模块对用户展示一致、可逐桶核验），**同时用 `extra.flat_in_positive_bucket`/`extra.sign_basis` 保留双口径**（不丢数据）；**缺陷在源侧**（桶边界把平盘当上涨，与"上涨"标准语义不符）→ 转后续任务在拥有 market 文件的批次修正
- **后续项**：`curated_count` 徽章分母现包含非行业的 `market` scope，而徽章标签是「行业分类（申万）」→ **须只数行业 scope**（Ruling 54 第 4 条）；`_distribution_bucket`/`_DISTRIBUTION_RANGES` 私有符号的公开化清理

## 2026-10-12 - S1.5 个股与板块热力图（treemap 首屏不再阻塞主线程）

- **交付**（`56b5ba8`）：`/market/heatmap` 占位页顶成真实现（占位路由 10→9）；`frontend/src/features/market/HeatmapTreemap.tsx` + `heatmapTree.ts`；后端**新建批量读路** `GET /api/v1/market/heatmap/treemap` —— 既有行情端点上限全是 `≤100/≤200/≤31`，**"≥3000 标的"没有现成读路**，这是本任务的技术核心
- **实测容量**：**5559 标的** · 载荷 **796,666 B**（≈778 KB）· 响应 **205 ms**；字段覆盖"面积=成交额或市值 / 颜色=涨跌幅 / 分组=申万或概念"，并**声明单位** `amount_unit`/`mv_unit`（避开单位陷阱）+ `as_of`/`as_of_quality`
- **性能（spec 硬判据：≥3000 标的首屏不得阻塞主线程）**：修复前实测 **最长 long task 189 ms、6–7 个 >100ms**（60fps 下一帧 16.7ms，189ms ≈ 掉 11 帧）→ 修复后 **最长 63 ms、零个 >100ms、`goto→canvas` 1139→389 ms**
- **根因是测量出来的，不是猜的**：旧的 800 叶"渐进着色"每帧重跑完整 `setOption`（`notMerge`），**把 1 次 190ms 重排复制了 7 次**（5559/800 ≈ 6.9，与实测 7 个 long task 精确吻合）；对照实验 `?paint=all` 仍 191ms、关动画仍 194ms → 代价 ∝ 叶节点数（CDP 剖析 `Model.getShallow` 148ms、`measureText` 39ms）。**分片与 Web Worker 都用数据分别否决**
- **解法：聚合替代分片**——渲染 **901 块**，其余 **4659 只聚合成带标签的「其他 N 只」块**（点击展开）；分组视图首帧 63/77/76 ms 全部 ≤100ms。既满足判据，也比"画出亚像素矩形"更符合 spec 的「高信息密度」
- **性能基线可复跑**：`frontend/scripts/measure-heatmap.mjs`（Playwright + `PerformanceObserver` 的 `longtask` 条目，这是「不得阻塞主线程」的正确测法），三次取中位
- **残留（已披露）**：严格「无 >50ms 任务」未达（残余 2 个 ~55/63ms，来自 React commit + ECharts 首帧；要压到 <50ms 需把可见块砍到 ~400，与「高信息密度」冲突，控制器判定不值得）；测量夹具**无法自动点击聚合块**（ECharts `getLayout()` 坐标与命中测试不重合），下钻改由确定性不变量覆盖

## 2026-10-12 - S1.4 行业分类钻取（申万）：SCD2 成分历史 + 三级下钻 7 项字段

- **第 0 步实机验证**（`docs/design/data-source.md` 附.6，10 条实机记录）：TuShare `index_member` **带 `in_date`/`out_date`**，31/31 一级行业可得、`in_date` 最早 `19901210`（可回溯到 1990 年）→ spec「任取历史交易日还原当时成分」的判据**原样成立，无需收窄**。并记录一个真陷阱：`index_classify` 的 `src='SW'` **静默返回 0 行**（正确值 `src='SW2021'` = 511 节点）
- **SCD2 建表**（迁移 `b4c5d6e7f8a9`）：**新建** `sw_industry_member_periods`（`effective_from` 必填 / `effective_to` 可空 / `is_current`）+ `sw_member_ingest_runs`，**既有 `sw_industry_members` 一列未改**——其唯一键 `(industry_code, stock_code)` 装不下多段区间，且整表 DELETE 会清空历史。回填实测：346 指数 / **7,624 行** / 0 失败，重跑新增 0（幂等）
- **三级下钻读面** `/api/v1/market/sw-industry/drill/*`：31 个一级 → 二级 → 三级 → 成分股，**7 项字段**（K线开/收/高/低 · 涨跌幅 · 换手率 · 成交量 · 量比 · 振幅 · **成交额**）全部**复用既有列**（`daily_quotes` + `daily_basic`），缺失一律 `None` 不填 0；**公开免登录**；前端末级成分股页（路由走 `route-manifest.ts` + `ARCHITECTURE.md` §6 各加一行）
- **纠正一处既有单位注释错误**：`daily_quotes.amount` 是 TuShare 原生**千元**（×1000 才是元），原注释写成"源为亿元，×1e8"
- **评审抓到并修复 1 处 Critical**：L1/L2 成分原先**靠"L3 上卷"**，而「祖级指数返回其下全部成分的并集」这一前提**经真实上游实测为假**（24/31 一级偏少，最多 −35%；`union(L2) ≠ L1`）→ 农林牧渔一级**成交额被低估 25.7%**（`23,743,561,160` 元 → 真值 **`29,851,841,770` 元**）。修法：读路**优先取节点自身层级**的成员、L3 上卷仅为兜底，响应以 `member_source` / `member_source_note` **显式标注来源而不声称等价**；回填改为三级全灌。**31 条 L1 逐条对账 `0/31 不一致；合计 5,211 = 5,211`**
- **配套的护栏**（同一评审驱动）：ingest 的 0 行响应 / `--limit` 截断 / 下界断言未过的运行一律 `fully_ingested=False` / `status='partial'`，**不再抬高分类归属口径日**；三条下界断言（逐层零行 / 环比跌幅 ≤20% / 零行比例 ≤30%）+ 读面 `scd2_coverage` + 前端徽章；破坏性迁移 round-trip 改为 **`SC_DESTRUCTIVE_MIGRATION_TESTS=1` 显式 opt-in**（默认 `pytest` 不再清空数据），运维顺序写入 `docs/deployment/index.md`
- **可复现性**：`backend/scripts/spike_sw_member_history.py` 补 `src` 枚举与 31 个 L1 全扫（空列表响亮失败）；`backend/scripts/reconcile_sw_member_counts.py` 入库（逐 L1 比对本地 own-level 当前成分 vs 上游，不一致非零退出）——`docs/design/data-source.md` 附.6.2 #11/#13 的证据**可由入库脚本复现**
- **测试**：+30（SCD2 契约、下钻读面、成员来源回归锁 `member_count==2` 旧实现返回 1、31/31 自建树断言不再依赖 CI 库恰好有 seed、detail 写入→解析 round-trip）；后端 **698 → 728 passed**
- 涉及文件：`backend/app/models/sw_industry.py`、`backend/app/migrations/versions/{b4c5d6e7f8a9,c5d6e7f8a9b0}_*.py`、`backend/app/{services,schemas,api/v1}`、`backend/scripts/{spike_sw_member_history,reconcile_sw_member_counts,ingest_sw_member_periods,gen_sw_index_mapping}.py`、`backend/tests/test_sw_*.py`、`backend/tests/test_industry_metric_pit_roundtrip.py`、`frontend/src/pages/market-industry-*`、`frontend/src/app/router/route-manifest.ts`、`frontend/scripts/check-routes.mjs`、`docs/design/data-source.md`、`docs/deployment/index.md`、`docs/architecture/frontend/ARCHITECTURE.md`

## 2026-10-12 - S1.3 全球市场与跨市场标的（含一次门禁撤回裁定）

- **段 A · 全球标的与 `/market/global`**（`99e6326`）：既有 **14 条 `ts_code` 一条未改**，新增 `NDX`（纳斯达克100）/`SOX`（费半）/`HXC`（金龙中国）/`HKTECH`（恒生科技）/`XIN9`（富时中国A50 期货）；**每条标的补齐五要素**（名称 / 数据源代码 / 交易时段 / 时区 / 币种），19 条**非空率 100%**，覆盖 CN8·HK2·JP1·KR1·US6·SG1；`/market/global` 按市场分组、**公开免登录**；A50 显式标注「新加坡 · CNY 夜盘」（与 A 股时段区分）
- **消歧修正**：`IXIC`（纳斯达克**综合**）与 `NDX`（纳斯达克**100**）是两个标的。原先二者共用同一东财 `em_secid` → 最新值必然相等 → `IXIC ≠ NDX` 验收判据会**假通过**；改为 `IXIC → 100.IXIC`，并加四层断言（登记表 / 读路径 / 端点 / 名称权威）。`100.IXIC` 的一手验证作为 open item 记入 `docs/design/data-source.md` 附.5.5 #6
- **段 B · 数据源可行性复核**（`3b7df9e`）：大宗商品 / 外汇 **20 条实机记录**，4 项三态结论（国内商品期货 可得 / 国际商品 受限 / 外汇 可得 / 落库形态 可得）+ 5 项 `blocked-on-verification`；页面只放「数据源待接入」占位，**不发明名录、不填假数据**。修正了两处控制器预判：TuShare **上游**有 `fx_daily`（4000 行）与 `fut_basic`/`fut_daily`（仅本仓 client 未实现）；恒生科技代码是 `HKTECH` 而非 `HSTECH`，且 `index_global` 经全量枚举确认**不含** NDX/SOX/HXC
- **四处展示面假 0 修正**：`IndicatorGrid`（涨跌幅）、`MarketMoneyflowCard` / `SectorMoneyflowCard`（资金流柱值与颜色）、`NorthboundCard`（近 30 日累计）——缺值不再折成 `0`，改为显式缺失表达；`DistributionChart` 的桶级 `?? 0` **经核实保留**（后端全量 11 桶起底，改 `—` 会把"0 家股票"说成"数据缺失"）
- **`DistributionChart` 卡片级空态**（`6053186`）：`items: []`（未解析判据日）或抓取失败时，原先仍打印「上涨 0 只 / 下跌 0 只」且无缺失提示；现分离**空态 / 错误态**（`DegradedNotice` 可与空数据区分）、`data-testid="distribution-empty"`，真实平盘日（11 桶全 0）仍正常渲染；tooltip 的 `total || 1` 兜底一并改为显式 `—`。**新增 Playwright 用例**（mock 空态 → 断言空态可见且 0 只文案 0 条、无 canvas），破坏验证 `✘ 1 failed`、整 spec **11 passed**
- **门禁改动（含一次撤回）**：`check-design-tokens.mjs` 扩展为解析 `src/` 下**每个** `var(--x)` 引用并对照定义（`.ts/.tsx` 走 **TypeScript 编译器 API**，`.css` 走文本），抓到真实死变量 `var(--flat)`；新增 `frontend/scripts/lib/{ast-scan,source-scan}.mjs` 共享扫描层。**撤回**本轮新建的 `check-no-fake-zero`（Ruling 47）：四轮后证明它在**当前仓库已有 ≥5 处真实漏判**（绿灯而漏判，属"假绿当常态"）、6 处结构性逃逸（"值会不会被渲染"是**数据流**问题，靠节点形状猜不出）、且会误红合法的空安全写法 `x.f && x.f.toFixed()`；其要防的具体缺陷已由 `?? null` + `—` 渲染直接修掉，真保证落在**数据层**（`price_source == "none"`，pytest 机械覆盖）。撤除后该类改为**直接修站点 + 沉入 `docs/references/best-practices/frontend.md`**（含"能否机械检测：不能"的答复）
- **影响文件**：`backend/app/services/market_data_service.py`、`backend/scripts/check_global_instruments.py`、`backend/tests/test_market_data_mapping.py`、`frontend/src/pages/{market-global,market-index}`、`frontend/src/features/market/components/*`、`frontend/src/app/router/{route-manifest.ts}`、`frontend/scripts/{check-design-tokens.mjs,lib/*}`、`docs/{design/data-source.md,architecture/frontend/ARCHITECTURE.md,references/best-practices/frontend.md,testing/index.md}`、`frontend/e2e/marketDataFace.spec.ts`
- **删除**：`frontend/scripts/check-no-fake-zero.mjs`（及其在 `package.json` / `ci.yml` / `self_review.sh` / 门禁矩阵的全部接线）

## 2026-10-12 - Wave 1 前两项（S1.1 导航骨架与路由迁移 · S1.2 指标表加 PIT 两列与统一读面）+ 前端当前态文档同步与路由门禁补洞

- **Task 5 · S1.1 导航骨架与路由迁移**（`1fb5fb4`→`3311bd0`，8 提交 / 3 轮修复）：顶栏 **10 个一级导航**（市场·资讯·行业分析·公司分析·宏观分析·情绪分析·价值网图谱·策略引擎·风险管理·交易执行）+ 数据驱动导航树原语 `frontend/src/features/nav/`（零业务常量，徽标语义不在代码里）+ 既有 **15 条路由全量迁移（8 redirect + 7 保留）** + 10 个「规划中」占位页 + `/me` 工作台（复用既有 `WatchlistTable` / 新抽 `TagsPanel` / `WatchlistPanel` / `TagsOverview`，**不重写自选与标签能力**）
- **路由清单化（Ruling 33）**：`frontend/src/app/router/route-manifest.ts` 成为路由唯一真相（纯数据：`kind` / `redirectTo` / `isPlaceholder` / `requiresAuth` / `permission` / `parent`），`index.tsx` 只**生成** `<Route>`；`route-elements.tsx` 承载懒加载注册表与鉴权包裹。**门禁不再解析 JSX 源码**
- **新增路由迁移门禁** `frontend/scripts/check-routes.mjs`（`npm run check:routes`）：旧路由声明 / redirect 去向逐字 / **保留不得被改成 redirect** / **占位集合契约（双向成员+基数）** / **无 redirect 指向占位页** / 权限口径 / IA §4 ↔ 清单双向比对 / **ARCHITECTURE §6 ↔ 清单双向比对**；已接入 CI `frontend-lint` 与 `self_review` **默认快检**
- **Task 5.5 · 前端当前态文档同步与门禁补洞**（`0b20ee3`→`b5133e2`）：`docs/architecture/frontend/ARCHITECTURE.md` §6 路由一览**按清单重写**（32 行一一对应，redirect 去向逐字），陈旧的「以 `app/router/index.tsx` 为准」指针改为 `route-manifest.ts`；§2 目录树修正门禁脚本挂层并补列 `check-routes.mjs`；`docs/design/ia-investment-research-platform.md` 两处同类陈旧指针一并改指。门禁补洞：计数改由断言集合派生、**占位集合契约化**、§6 重复行检测、§6 指针断言落到「为准」句（消除恒真假绿）
- **Task 6 · S1.2 指标表加 2 列 + 统一读面**（`f16163e`→`1b4c070`，5 提交 / 2 轮修复）：`industry_metrics` **只加 2 列**（`published_at timestamptz NULL` / `caliber_version text NULL`，迁移 `f1a2b3c4d5e6`，无 `create_table`，downgrade 对称）；模型 docstring 写明 `industry_key` 语义已泛化为通用 scope 键 + `ponytail:` 改名升级路径；registry 增口径版本登记位；新增 `GET /api/v1/metrics/latest` 与 `/series`（**复用**既有 service/repo 层与源优先级，未平行造栈；`metrics:batch` 刻意未新增——既有端点已覆盖）
- **PIT 正确性（本轮评审抓到的实质缺陷）**：① `as_of` 曾**静默回退** `period` ≡ 伪造 PIT 时点（违 ADR 0011「绝不静默回退」）→ 新增显式判别式 **`as_of_source: published_at|period`**（Ruling 34）② `ON CONFLICT DO UPDATE` 曾用 `excluded.*`，**重导入会抹掉已记录的 PIT/口径** → 改 `coalesce(...)` 并加靶向测试 ③ PIT 日期曾按 **UTC** 取整日 → 北京时间凌晨发布的数据出现**一天前视** → 改 `Asia/Shanghai`（Ruling 35）④ `range` 曾是**行数**而文档声称时间窗（`30d` 对月度指标返回 30 个月）→ 改真实时间窗（新 `repo.get_metric_window`，既有 `get_metric_history` 与猪智投调用方逐字节未动）
- **前端 `<MetricBadge>`**：四徽章组件（source / as_of / freq / 口径版本），chip 样式与配色**逐字复用** `SourceBadge`；`period` 回退用中性色 + 「口径」前缀 + 「规则/回测不得引用」提示，口径断点高亮；已接进既有 `IndicatorCard`
- **门禁修复（Task 4.5，计划外，依 AGENTS.md 自检第 5 条）**：`scripts/self_review.sh` 两个真实缺陷——路径采集取 porcelain 第 2 列导致**纯 `git mv` 的 `.py` 被静默跳过 lint**；format 失败分支的裸反引号被 shell 当重定向致脚本**以语法错崩掉并截断提示**
- **测试**：`industry_metrics` 相关新增 55 个用例（含真库 alembic up/down/up、生产 `batch_upsert` + 生产 reader 的 PIT 往返、重导入保留 PIT、真时间窗、真 HTTP 栈的 422 边界）；后端 **630 → 685 passed**，失败/错误始终仅在 `tests/test_intraday_snapshot_table.py`（该文件硬编码 dev 库 `stock_bot`，属已知环境项、非回归判据）
- 涉及文件：frontend/src/app/{router,layouts}、frontend/src/features/{nav,watchlist}、frontend/src/pages/**、frontend/scripts/check-routes.mjs、frontend/src/shared/{api,ui}、backend/app/{models,migrations/versions,services,repositories,schemas,api/v1}、backend/tests、backend/scripts/check_metric_dictionary.py、scripts/{self_review.sh,doc_gate.sh}、docs/architecture/frontend/ARCHITECTURE.md、docs/design/{ia-investment-research-platform,data-source,metric-dictionary}.md、docs/testing/index.md、docs/features.md、docs/authority.md、docs/index.md、docs/Changelog.md、plans/**

## 2026-10-12 - Wave 0 平台契约完成（S0.1–S0.4 + 门禁修复），投研工作台主线进入 Wave 1

- **执行方式**：worktree `.worktrees/research-platform-ia`（分支 `feat/research-platform-ia`，基线 `25a6371`）+ subagent-driven-development —— 每任务 = 派实现 subagent → review package → 任务评审 → 修复循环 → ledger 记账。**16 提交 / 4 修复轮 / 6 次评审**，无一条 finding 被静默丢弃
- **Task 1 · S0.1**（`dfedcc8`）：5 篇 ADR `0009-content-view-separation` / `0010-polymorphic-watch-and-tags` / `0011-point-in-time-data-contract`（含两条机械保证）/ `0012-no-automated-order-placement` / `0013-reuse-existing-signal-infrastructure`（含"加 2 列复用、不建 `metric_series`"附带裁定）+ `decisions/index.md` 登记
- **Task 2 · S0.2**（`1e29c6d`→`a687ffc`，5 提交 2 修复轮）：新建 `docs/design/ia-investment-research-platform.md` —— 10 导航总表（权限三态：公开 2 / `research:read` 1 / 待定 7）、子模块树逐条转录、顶栏工具（不含 `/calendar`）、**15 条既有路由全量迁移表（10 redirect + 5 保留）**、三层布局 + 左侧树数据驱动约束
- **Task 3 · S0.3**（`38aaf46`→`8928dbe`，1 修复轮）：`docs/design/data-source.md` 追加 7 项数据源 × 四栏三态结论（**5 核实 + 2 `blocked-on-verification`** 且各附「去哪查/查什么字段/判断标准」）+ 19 条实机调用记录 + 与计划 §8 的逐项一致性对照
- **Task 4 · S0.4**（`428eeb2`→`76f4053`，3 修复轮）：新建 `docs/design/metric-dictionary.md`（§4.1 两种徽标语义并**删除 `A股市场 18`** / §4.2 `metric_key` 命名 / §4.3 `publisher_tier` 与既有 `source_tier` 的区别 / §4.4 口径版本 / §4.5 五处模块边界 / §4.6 条件式兜底）+ 登记 `docs/authority.md`
- **Task 4.5 · 门禁修复（计划外，依 AGENTS.md 自检第 5 条）**（`8ca6e6f`）：`scripts/self_review.sh` 两个真实缺陷 —— ① 路径采集用 `git status --porcelain | awk '{print $2}'`，重命名的第 2 列是**旧路径**，导致**纯 `git mv` 的 `backend/**.py` 被静默跳过 lint**（Task 4 实测发生）；② `:67` 失败提示的裸反引号被 shell 当命令替换、`<files>` 被当重定向，**ruff 真失败时脚本以语法错崩掉并截断提示**。修法：采集改为 `git diff --name-only HEAD` + `git ls-files --others --exclude-standard` + 原 merge-base..HEAD（两条文档化不变量实测保留，b/c/d 场景新旧集合经 `comm` 双向比对逐字节相同）；提示改为 `\`` 转义（同 `doc_gate.sh` 房内写法）
- **事实更正（Ruling 19，由 Task 3 复核发现）**：此前主张的「陆股通持仓**绝大部分是内地资金借道香港**」**缺乏权威证据且可核量级相反**（兴业证券 2021-12 按港交所托管机构口径：中资托管 3.54% / 外资银行 78.8% / 外资券商 17.66%；证监会令第 200 号第十三条明文「沪股通和深股通投资者不包括内地投资者」）。已从 `docs/specs/S0-platform-contracts.md`、`docs/specs/S1-market-sentiment.md`、计划 §8.2 与 §11-B2 **共 4 处删除**，替换为三条均有官方出处的降级理由（名义持有人汇总账户 / 持仓主体不可拆分 / 季度低频率）；**结论与替代口径不变**（`可得但降级`、具名「陆股通持仓（季度）」、移入公司·股东股权）。Changelog 追加更正条目，既有条目未改
- **口径一致性收尾（Ruling 22 / 28）**：计划 §11-B1 去掉与 spec「不建通用裁定队列」冲突的旧措辞；§11-C 的 `source_tier=official` → `publisher_tier=official`；spec §S0.4 的 `已策展数` 改引字典唯一定义、§4.2 命名示例换成已注册的 `sow_inventory`
- **新增机械检查**：`backend/scripts/check_metric_dictionary.py` —— 比对字典 §4.2 样例表与 `industry_registry` 的 name/unit/freq，并对"已注册键误入提案表"做反查；已落入 `self_review`/pre-commit 的 `^backend/` ruff 面（**未接入执行面**，接入方案见 plans 遗留）
- **遗留**：13 条 minor/deferred（记于 `plans/2026-10-12-investment-research-platform-ia.md` §13 与 SDD ledger）。**含一条合并期待办**：本分支取 ADR 0009–0013，与 `feat/market-hotspot-persistence`（active、其 Task 3 声明要建 `0014-market-derived-fact-persistence.md`）在**合并时**编号碰撞，需人类定夺由谁改号（**已于合并时解决**：对方 0009 改号为 **0014**，本分支 0009–0013 不动，`doc_gate [2/13]` 回绿）
- 涉及文件：docs/decisions/0009–0013 与 index、docs/design/{ia-investment-research-platform,data-source,metric-dictionary}.md、docs/specs/{S0-platform-contracts,S1-market-sentiment}.md、docs/authority.md、docs/Changelog.md、plans/2026-10-12-investment-research-platform-ia.md、scripts/self_review.sh、backend/scripts/check_metric_dictionary.py

## 2026-10-12 - 核对脚本移入 backend/scripts 并补 name/反查检查（Ruling 27）+ spec §4.1/§4.2 口径对齐（Ruling 28）

- **背景**：Fix round 2 新增的核对脚本放在根 `scripts/` 下，**三层 lint 覆盖全无**（`scripts/self_review.sh` 的 `py_files()` 只认 `^backend/.*\.py$`；pre-commit 的 ruff hook 是 `files: ^backend/`；CI backend job 只跑 `ruff check app/ tests/`，唯一跑全目录的 job 是 auth-service），且 `ruff format --check` 未过
- **Ruling 27（脚本）**：
  - `git mv` 到 **`backend/scripts/check_metric_dictionary.py`**——**零新增门禁基建**即落入 `self_review` 与 pre-commit 的 ruff 面（实测 `self_review` 已打印 `✔ ruff check 通过 (scripts/check_metric_dictionary.py)`，`✔ ruff format --check 通过`）
  - 跑 `ruff format`，`check` / `format --check` 均通过
  - **补 name 列比对**（此前完全不查，故 Fix round 2 的 `hog_price` 名称那半无脚本保护）；**补反查断言**（已注册 key 若被错移进 5 列"新增提案"表 → 报出该 key 并非 0 退出）；补**覆盖面**报告（样例表 8 / registry 共 21，只报告不失败）
  - docstring 修正：原写"必须在 backend/ 下跑"**不成立**（脚本自己 `sys.path.insert`，cwd 无关）；用法改为 `uv run --project backend python backend/scripts/check_metric_dictionary.py`
  - **文档措辞降级**：`metric-dictionary.md` 的"可直接当门禁"→「**手动检查脚本（未接入门禁；接入方案见 deferred）**」，并写明它**不在** `self_review` / pre-commit / CI 的执行面内
  - **未改** CI / `self_review.sh` / `.pre-commit-config.yaml`：把 ruff 执行面扩到 `backend/scripts/` 是**独立变更**（该目录既有 **4 个未格式化文件**，扩面会让 CI 因无关原因变红）→ **deferred**
- **Ruling 28（spec 残留，因 §authority 新优先级"spec 优先于字典"而已升级为真冲突）**：
  - `docs/specs/S0-platform-contracts.md:144` 的 `已策展数` 定义由「已人工策展/**建成完整体验**的条目数」改为引用字典 §4.1 的**唯一定义**（已有策展产物：registry 指标定义 / 行业页 / 榜单配置），并注明 `16` 的**数值**仍是 owner 标注、待数据侧核对
  - 同文件 `:150` 把 `margin_financing_balance` 列为命名**示例** → 换成 registry 中真实存在的 `sow_inventory`，并明标 `margin_financing_balance` **属提案、尚未注册**（勿当既有 key 引用）
- **deferred（本轮发现，未修，已留证据）**：
  - ① CI 的 ruff 面从 `app/ tests/` 扩到含 `backend/scripts/`（扩面会暴露该目录 4 个既有未格式化文件）
  - ② `scripts/self_review.sh` 的 `py_files()` 只取 `git status --porcelain` 的**第 2 列**（`awk '{print $2}'`），而 rename 条目的第 2 列是**旧路径** → **纯 `git mv` 一个 .py 时该文件会被静默跳过 lint**（本轮实测："无 backend Python 改动，跳过"，提交后才恢复命中）；修法是取最后一个字段或 `--porcelain -z` 解析
  - ③ 同一脚本 `:58` 的 `fail "...（跑 \`uv run ruff format <files>\`）"` 里 `<files>` 被当作重定向 → 触发 `syntax error near unexpected token 'newline'`（仅在真失败时才现形；**未改**，属门禁脚本自身缺陷）
- 涉及文件：backend/scripts/check_metric_dictionary.py（移动+增强）、scripts/check_metric_dictionary.py（删除）、docs/design/metric-dictionary.md、docs/specs/S0-platform-contracts.md、docs/Changelog.md

## 2026-10-12 - 口径字典复核修正（Fix round 2）：样例表单位/频率对齐 registry、`caliber_version` 改回"待加"、`curated_count` 唯一定义 + 新增机械核对脚本

- **背景**：`docs/design/metric-dictionary.md` 已被登记为口径权威，实现者会照它执行；复核逐条对代码后发现 **3 处 Important + 4 处 Minor**（无 Critical），全部为**可对代码证伪**的硬伤，属"抄错/写超"而非设计问题
- **Important 1（样例表抄错 registry）**：§4.2「真实命名样例」表的 unit/freq 与 `industry_registry.py` 不符——`corn_price` 写 元/吨（实为 **元/kg**）、`piglet_price_15kg` 写 元/头（实为 **元/kg**，且与本文档规则 3 的示例自相矛盾）、`hog_corn_ratio` 写 比值/周（实为 `unit=""` / **daily**）。→ 该表**逐字重抄 registry**，并新增**可复跑核对脚本** [`scripts/check_metric_dictionary.py`](scripts/check_metric_dictionary.py)（逐行打印 文档 vs registry 的 unit/freq，不一致即非 0 退出；实测 `核对 8 行，MISMATCH 0 行`）。同时把 **未注册**的提案键（`margin_financing_balance` 等）从"已存在"表移入提案表——**把提案写成既有 key 会让实现者去找不存在的 `MetricDef`**
- **Important 2（`caliber_version` 写成既成事实）**：§4.4 原写"列已按 ADR-E 附带裁定新增"，但 `grep -rn "caliber_version" backend/app/` **无命中**、`industry_metrics` 无该列、`T1.2` 仍是未勾选。→ 改为「**该列当前不存在、待加**（Wave 1 / T1.2，依据 ADR 0013 附带裁定与 spec §S0.1，以"待应用"呈现）」
- **Important 3（Ruling 24：`curated_count` 两个定义 + 依据 3 被代码反驳）**：`:25` 定义"registry 注册数"、`:37` 又定义"已建成完整体验"，同名两度量；且"依据 3"称 registry 行业数 = `curated_count`，代码实为 **2**（`INDUSTRIES` = `pig` / `broiler`）。→ ① **唯一定义**收敛到 §4.1 一处（"已有**策展产物**：registry 指标定义 / overlay 种子 / 策展摘要 / 行业页 / 榜单配置"），删除"完整体验"这类不可判定表述 ② 新增**「语义归类已定 vs 数值待核」**小节：`16` / `3` 的**语义**归 `curated_count`（本次裁定），但**数值是 owner 图上标注、尚未与数据侧核对**（今日实测 **2**，与 16/3 均不符），并给出可复跑核对命令 ③ 明文禁止把未核对数字写成已核实事实
- **Minor 4**：上一条 Changelog 的"仅剩 3 处被更正对象的引用"实测为 **4 行**（该 bullet 自身内嵌了 grep 表达式）→ 更正为 4 行并注明构成
- **Minor 5**：§4.6 表头原标"既有实现（`ConceptMember`）"，但 `unresolved_count` **不是** `ConceptMember` 的列 → 改归属为 **API/schema 聚合**（`schemas/concept.py` 响应字段 + `repositories/concept_repo.py` 的 `count(*) FILTER (WHERE cm.stock_id IS NULL)`），并标出 `concept.py:64/66` 的列定义出处
- **Minor 6（Ruling 26）**：spec §S0.4 与 plan §11-A 仍把 `A股市场 18` 列为**未决复核项**，与已按 Ruling 4 判为删除冲突 → 两处补「**已按 Ruling 4 裁定删除**」并指向 `metric-dictionary.md` §4.1（plan 只改这一句）
- **Minor 7（Ruling 25）**：`docs/authority.md` 该行把字典标成唯一权威、却把 `plans/` 列入"勿用"，与字典自己"冲突以 spec 为准"矛盾 → 权威列改为「`specs/S0-platform-contracts.md` §S0.4（契约）+ `design/metric-dictionary.md`（落地细则，**冲突以 spec 为准**）」
- **边界**：§4.3 / §4.5 / §4.6 的实质裁定与 Ruling 19 四处更正**未动**（复核已确认正确）
- 涉及文件：docs/design/metric-dictionary.md、scripts/check_metric_dictionary.py（新建）、docs/specs/S0-platform-contracts.md、plans/2026-10-12-investment-research-platform-ia.md、docs/authority.md、docs/Changelog.md

## 2026-10-12 - 事实更正（陆股通持仓降级理由）+ S0.4 命名与口径字典落册（Wave 0 收尾）

- **事实更正（Ruling 19，仅更正理由，结论与替代口径不变）**：原主张「陆股通持仓（香港中央结算）**绝大部分是内地资金借道香港**」**缺乏权威证据**，且 Task 3 可核到的量级指向相反（兴业证券 2021-12 按港交所「托管机构」口径拆分北向持股：中资机构托管 **3.54%** / 外资银行 78.8% / 外资券商 17.66%，并引证监会口径"假外资"成交占比约 **1%**；**证据层级 = 卖方研报/媒体转引、2021-12 单源、非官方统计**，时点亦早于 2024-08-19 机制）
- **现理由（三条，均有官方出处，与 [`docs/design/data-source.md`](design/data-source.md) 附.1② 一致）**：① 香港中央结算是**名义持有人汇总账户**（[HKEX Stock Connect FAQ](https://www.hkex.com.hk/-/media/HKEX-Market/Mutual-Market/Stock-Connect/Getting-Started/Information-Booklet-and-FAQ/FAQ/FAQ_Cn.pdf) Q2.38–Q2.41），单一股东名册条目 = 全部沪深股通投资者合计；② **持仓主体不可拆分**——既不是单一外资机构，也无法从中拆出任何借道部分；③ **季度低频率**（每季度第 5 个沪深股通交易日披露上季末单只证券合计持有数量）。旁证：[证监会令第 200 号](https://www.gov.cn/gongbao/content/2022/content_5705841.htm) 第十三条第一款「沪股通和深股通投资者**不包括内地投资者**」（2022-07-25 施行，存量 2023-07-24 后只能卖出）
- **结论不变**：仍为 `可得但降级`，仍具名「陆股通持仓（季度）」，仍移出「外资画像」、降级进公司/股东股权视图
- **更正落点（4 处）**：`docs/specs/S0-platform-contracts.md` §S0.3 表第 2 行 · `docs/specs/S1-market-sentiment.md` §S1.7「不做什么」 · `plans/2026-10-12-investment-research-platform-ia.md` §8.1 表 + §11-B2 定案列。全文 `grep -rn "内地资金借道\|借道香港" docs/ plans/` 再无**作为理由使用**该表述的位置；命中 **4 行，全部为被更正对象的引用**：`docs/design/data-source.md` 2 行（附.1② ⚠ 更正注 + 附.3 一致性对照表，Task 3 的一手依据，按设计保留不动）+ 本条 Changelog 2 行（含本行内嵌的检查表达式）
- **S0.4 交付物落地**：新建 [`docs/design/metric-dictionary.md`](design/metric-dictionary.md)（六节 §4.1–§4.6）——① **两种徽标语义** `已接入数 ingested_count`（29/5/9/31）vs `已策展数 curated_count`（行业看板 3）；按 **Ruling 4 删除 `A股市场 18` 徽标**（与展开后 5 子节点、`主要指数 9`、`行业分类（申万）16` 均对不上，owner 未给语义，不允许以"大概是个数"存在）；`行业分类（申万）16` 定为 **`curated_count`**（数的是"申万一级中已策展的行业数"，不是分类总数 31；与同钻取路径的 `行业看板 3` 同语义，且与 spec §4.1 落位一致）② `metric_key` 命名 5 条规则 + 真实 registry 样例（`hog_price` / `sow_inventory` / `piglet_price_15kg`…）+ 落册提案（`idx_turnover_ratio_sse` / `erp_10y` / `reverse_repo_7d_rate` / `fr007` / `fdr007` / `northbound_holdings_quarterly`），并明说既有 `company.*` 带点键属兼容遗留、新键一律只用下划线 ③ `publisher_tier`（`legal`/`official`/`sellside`/`media`，回答"**谁发布的**"）与既有 `industry_metrics.source_tier`（`official`/`highfreq`/`calc`/`manual`/`derived`，回答"**指标怎么来的**"）的逐项区别 + 5 条判例（含"两层恰好同值但问题不同"与"派生项 `publisher_tier` 不适用"）④ 口径版本：`caliber_version` + 断点标记 + 4 条硬性约束，已知案例 M1（2024-12 修订、2025-01 起纳入个人活期存款 + 非银支付机构客户备付金）⑤ 模块边界 5 处裁定（涨跌分布归情绪 / 资金流计算归一处按维度取数 / 龙虎榜大宗为资讯原料 / 价值分配与利润流向合并 / `标的分类` 拆为关注池 + 资产类型）+ 共性判据"口径与计算各只有一个所有者，视图可任意多" ⑥ 兜底规则写成**条件表达式**（`该实体在目标分类体系中无有效归类`，**无日期常量**），引用 `backend/app/models/concept.py:3-5` 的既有代码事实（稳定业务键 + 外键可空 + `unresolved_count` 外露 + overlay 人工覆盖优先，ADR 0005），并明确**不建**"服务 4 个消费方的通用人工裁定队列"（Wave 3+ 之事）
- **口径一致性收尾（3 处）**：① 计划 §11-B1 原措辞把兜底模式推广为服务多消费方的通用队列，与 spec §4.6 / 本字典 §4.6 **直接冲突** → 改为「**不建**通用裁定队列；各分类场景**复用既有模式**（稳定业务键 + 可空外键 + 缺失量外露）」，三个落点链接（S0.4 §4.6 / S3.5 / S4.12）保留 ② 计划 §11-C 的列名**误用 `source_tier` 承载 official**（该列描述"指标怎么来的"）按本字典 §4.3 更正为 **`publisher_tier`**（描述"谁发布的"），与 §4.5 ③ 的表述对齐 ③ 本字典登记进 [`docs/authority.md`](authority.md) 新增「命名与口径」权威行（权威文件 = 本字典；勿用列 = 勿在 `architecture/` 或 `plans/` 重复定义口径）
- 涉及文件：docs/design/metric-dictionary.md、docs/specs/S0-platform-contracts.md、docs/specs/S1-market-sentiment.md、plans/2026-10-12-investment-research-platform-ia.md、docs/Changelog.md

## 2026-10-12 - 计划评审修正（ponytail）：新增表 16→8，删规则 DSL / 删 action 层 / 缩 Wave 1.5

- **背景**：对同日登记的投研工作台主线计划做「先爬梯子」评审，发现**初版是它自己批判的对象**——主张复用却复制了 4 张既有表，还引入一个解析器去解决 0 个当下问题
- **四处复制品（全部改为加列复用）**：
  - `app/models/industry_research.py:30` 的 `industry_metrics` **已经是**统一指标表（`industry_key/stock_id + metric_key + source + source_tier + freq + period + value + unit + extra` + 6 列唯一键 + 2 索引）；原方案要新建 `metric_series`（只多 2 个字段）→ 改为**加 `published_at` / `caliber_version` 两列**，读 API 与徽章复用既有
  - 同文件 `:102` 的 `industry_signals` 注释即 *"rules engine output history"*，`basis` JSONB 就是证据链；原方案要新建 `signals` → 改为**加 4 列**（`rule_key/rule_version/confidence/severity`）+ 唯一键扩为 `(industry_key, rule_key, effective_date)`
  - `app/services/cycle_engine.py` 已跑通「纯函数规则 + 阈值进 registry」并明写 *"阈值调整应改 registry 而非本文件逻辑"*；原方案要引入**文本 DSL + 解析器** → **删 DSL**，规则=纯函数、阈值=registry
  - `app/models/feature.py` 的 `stock_features` 已物化 `return_percentile/max_drawdown/trend_slope` 等（按 `asof_date` 分区）；原方案要建通用 transform 引擎 `factor_defs` → **删表**，复用物化模式
- **其他删减**：删 `action` 层（ADR-D 已裁定不下单 → "动作"= 通知 + 待办，与 `alerts` 同路径，不建只有一个实现的接口）；删 `backtest_events`（逐笔明细读时 join 即得）；删 `graph_nodes`（节点已是 `stocks`/`sw_industry`/`industry_knowledge` 的行）；删 `content_metrics` 与 `extracted JSONB` 的双写（JSONB 只留 LLM 原始输出）；**事件日历从"地基"降级推迟到 Wave 3**（Wave 1 零消费方）；不预先发明 `strategy:write`/`risk:write`/`trade:write` 权限 scope
- **命名冲突修正**：新增的发布主体分层原用 `source_tier`，但 `industry_metrics.source_tier` 已被占用（`official|highfreq|calc|manual|derived`，描述**指标怎么来的**）→ 改名 **`publisher_tier`**（描述**谁发布的**），避免同名不同义让 `grep` 与 review 双双失效
- **事实纠正（B1 的依据）**：原计划以为 owner 图上"2026年5月以后上市的公司"是硬编码业务规则；实际 `app/models/concept.py:3` 写着 *"`stocks` 名录会滞后（**实测冻结在 2026-05-08**，66 只新上市股票缺行）"* ——**同一个日期**，owner 描述的是真实数据陈旧度。而仓库已有更省的解法（`symbol` 业务键 + 可空 `stock_id` + `unresolved_count` 外露）→ S3.5 收缩为**只服务资讯模块**，不做"服务 4 个消费方的通用裁定队列"
- **量化结果**：新增表 **16 → 8**；Wave 1.5 由 **6 spec → 4 spec**（S2.1 因子不建表 / S2.2 复用 `industry_signals` / S2.3 最小回测 / S2.4 提醒）；spec 总数 **59 → 57**；地基 **6 → 5 真 + 1 推迟**
- **保留不动**：10 导航 IA · 依赖倒序 · F1 原料视图分离 · F3 PIT 及**两条机械保证**（信号 `published_at ≤ as_of` 护栏 + 回测「披露日 vs 报告期」对照实验）· F4 多态关注池 · 13 条旧路由 redirect · `source_span` 溯源与抽取一致率 ≥95% 门槛
- **新增防复发机制**：`docs/specs/README.md` 通用约束新增第 0 条「**先爬梯子**」+ 7 行"已有资产 → 不要重复造"清单（`industry_metrics`/`industry_signals`/`cycle_engine`/`industry_registry`/`stock_features`/`concept.py`/前端原语）；主线计划 §1.1 同表补录；§2 非目标新增 6 条"不做"
- 涉及文件：plans/2026-10-12-investment-research-platform-ia.md、docs/specs/README.md、docs/specs/S0-platform-contracts.md、docs/specs/S1-market-sentiment.md、docs/specs/S2-signal-engine.md、docs/specs/S3-content.md、docs/Changelog.md

## 2026-10-12 - 规格拆分：新建 docs/specs/ 规格层（59 spec）+ 主线计划四项阻塞定案

- **背景**：`plans/2026-10-12-investment-research-platform-ia.md` 只回答"做什么、按什么顺序"，缺每个模块的契约与验收判据；且其 §11 的 4 项待拍板阻塞 Wave 0
- **新建规格层** `docs/specs/`（与 `plans/` 排期、`architecture/` 当前态、`decisions/` 为什么 分工）：
  - `README.md` — 编号规则（文件 `S<交付序>-<主题>`、条目 `S<序>.<n>`，**交付序 ≠ Wave 号**：Wave 1.5 在交付序里是 S2）· spec 模板 · **验收判据三类**（数据契约 / 行为 / 门禁，每类必须可机械核对，禁止"体验流畅"式描述）· **跨 spec 通用约束 8 条**（原料视图分离 / 禁止平行实现 / 数据徽章 / 可溯源 / PIT / 口径变更标注 / QUEUES 注册 / 不做项）· **全量 59 spec 目录表**
  - `S0-platform-contracts.md` — Wave 0 无业务代码：**5 篇 ADR 的完整内容**（原料视图分离 · 多态关注与标签 · PIT 时点数据 · 不做自动下单 · 信号引擎为跨模块基础设施，含 `metric_series` 与 `industry_metrics` 收敛方案二选一）+ **10 导航路由表与 13 条旧路由 redirect 契约**（一条都不能删）+ 7 项数据源三态复核 + 命名与口径字典
  - `S1-market-sentiment.md` — Wave 1（14 spec）：`metric_series` 统一时序表（含 `published_at` PIT 列与硬性约束）· 全球标的代码映射（**修正纳斯达克综合指数 NDX 串号**）· 行业分类钻取（SCD2 + 补成交额）· 热力图（对标 iFinD）· A 股排行 · **资金流四件套替代北向** · 情绪 7 组指标
  - `S2-signal-engine.md` — Wave 1.5 拱心石（6 spec）：`factor_defs`（纯函数）· **规则 DSL**（阈值/分位/拐点/交叉/事件/表达式，**时点由引擎注入，表达式无法指定 → 结构性防未来函数**）· `signals` 证据链 · **事件研究回测（PIT 强制 + 样本切分强制 + 退市股不剔除）** · 提醒订阅 · `action` 层接口（append-only 审计，含"应触发未触发"留痕）
  - `S3-content.md` — Wave 2 资讯地基（7 spec）：内容事实层四表（**用户状态必须归用户维度，不得写在事实行**）· 公告/快讯采集 · **LLM 抽取管线（`source_span` 原文定位必填，一致率 ≥95%）** · 人工裁定队列（人工终态优先于自动）· 资讯视图层 · 「有效研报」三条判据
- **四项阻塞定案**（owner 采纳建议，落点见 S0.4）：**A** 徽标区分 `已接入数`/`已策展数`（`A股市场 18` 列复核项）· **B1** 「其他分类」兜底**改为动态规则不硬编码日期**并推广为通用人工裁定队列 · **B2** 外资画像**换口径**为资金流四件套（ETF 份额+两融+产业资本+南向），「外资画像」改名「陆股通持仓（季度）」并降级 · **C** 模块边界五处裁定（涨跌分布归情绪 / 资金流计算归一处 / 龙虎榜为资讯原料 / 价值分配与利润流向合并 / `标的分类` 拆为关注池+资产类型）
- **渐进明细**：S0–S3（Wave 0/1/1.5/2）写详细契约，S4–S7（Wave 3–6）为 `catalog-only`，**启动前一周才细化**——Wave 1 的数据事实底座未落地前，Wave 5 的因子契约必然返工
- **识别出两个 PIT 的机械保证**（本计划可信度的基石）：`signals.evidence` 中每个引用的 `published_at` 必须非空且 ≤ `as_of`（违反则信号不生成并告警）；回测须构造「用披露日 vs 用报告期」的**对照实验**并产生显著差异（差异为 0 即证明 PIT 未生效）
- **文档系统扩展**：`docs/index.md` 分层表新增「规格」层 + 变更写哪里新增规格行；`docs/authority.md` 新增"投研平台模块契约与验收判据"权威行
- 涉及文件：docs/specs/README.md、docs/specs/S0-platform-contracts.md、docs/specs/S1-market-sentiment.md、docs/specs/S2-signal-engine.md、docs/specs/S3-content.md、plans/2026-10-12-investment-research-platform-ia.md、plans/index.md、docs/index.md、docs/authority.md、docs/Changelog.md

## 2026-10-12 - 计划登记：投研工作台信息架构与数据模型主线（10 导航 · 6 地基 · 8 Wave）

- **背景**：owner 给出 10 张手绘思维导图（市场 / 资讯 / 行业分析 / 公司分析 / 宏观分析 / 情绪分析 / 价值网图谱 / 策略引擎 / 风险管理 / 交易执行）+ 6 轮对齐讨论，指出当前产品"还不是一个优秀的投研产品"。代码实测基线：一级导航只有 5 个 tab（`frontend/src/app/layouts/MainLayout.tsx:18-22`），10 个模块里只有"市场 + 半个行业"；关注模型是两套硬绑 `symbol` 的表（`app/models/stock.py:16` 的 `stock_user_tags` + `app/models/watchlist.py` 的 `user_watchlists`/`user_watchlist_items`）；数据形态以数值型为主，缺**文本事件 / 多态关注 / 时点数据**三类新形态与**信号引擎**一类新计算基建
- **产出**：`plans/2026-10-12-investment-research-platform-ia.md`（10 张图结构逐条转录 → 10 导航 × 子模块 × 依赖表；提炼 **6 个横切地基**：F1 原料/视图分离 · F2 指标→因子→信号→回测→触达(action) 引擎 · F3 PIT 时点数据 · F4 多态关注池与标签 · F5 事件日历 · F6 传导图；**原料→视图映射表**（10 张图自证 6 处同源）；**8 个 Wave 的依赖倒序路线图**；明确不做清单与数据源风险替代口径），并登记 `plans/index.md`
- **已拍板决策（记入计划 §12）**：① 自选/标签采用**方案 A**——合并为多态 `tag` + `user_target_tag`，一级导航保持 10 个纯净，落为顶栏全局工具 + `/me` 工作台；② 实施顺序**依赖倒序**——市场/情绪 → 资讯 → 行业/公司/宏观 → 价值网 → 策略 → 风险/交易；③ 原料/视图分离确立；④ 图上 4 条蓝色批注升格为全局产品原则（时序性 · 摘要性 · 可对比性 · 可溯源性）
- **实测纠错（写入计划 §3.4 / §8）**：图1 `纳斯达克综合指数` 与 `纳斯达克100` 都标 NDX（综合应为 IXIC）；**北向资金被 2 处依赖而每日数据已取消**——2024-05-13 取消盘中实时、2024-08-19 取消每日净买入并改按季度披露持股，替代口径为 ETF 份额变化 + 两融 + 产业资本增减持 + 南向；图上 `资金利率` 缺 **7 天 OMO**（2024-07 后 MLF 地位弱化，OMO 成为主要政策利率）；M1 口径 2024-12 修订（2025-01 起纳入个人活期存款 + 非银备付金）造成历史断点，须标 `口径版本`
- **阻塞**：计划 `status: draft`，阻塞于 §11 的 4 项待拍板（7 个徽标数字口径 / `2026年5月` 时点语义 / 外资画像替代口径 / 模块边界三处），拍板后启动 Wave 0（5 篇 ADR + IA 与路由表冻结）
- 涉及文件：plans/2026-10-12-investment-research-platform-ia.md、plans/index.md、docs/Changelog.md


## 2026-10-12 - 修复「点个股后按返回键回不到原界面」：Market 分类 Tab 改为 URL 状态 + 错误边界随路由重置

- **根因（用户反馈，Playwright 实测复现）**：`/market` 的分类 Tab 是 `useState("overview")` 组件内存态，而 `/market` 与 `/stock/:symbol` 是兄弟路由 —— 进个股页会卸载 `MarketPage`，浏览器返回只还原 URL，组件重新挂载即落回默认「指数总览」，用户永远回不到离开时的「短线情绪」（口径/展开态等页内状态一并丢失）。实测链路：`/market` → 短线情绪 → 点个股 → `goBack()` → `activeTab=指数总览`；既有 e2e 每条用例都得手动点回该 Tab，正是这个缺陷的化石
- **修复（主）**：`pages/market/index.tsx` 用 `useSearchParams` 派生 `activeKey`（短线情绪 → `?tab=sentiment`），`onChange` 走 `setSearchParams(..., { replace: true })`；**默认 Tab 不写参数**（保住既有 `navigate("/market")` 与 `toHaveURL(/\/market$/)` 的语义），`replace` 让返回栈只留真正访问过的页面（实测连续切 Tab `history.length` 不变）
- **修复（次生，同一症状的第二成因）**：`App.tsx` 的全局 `ErrorBoundary` 进入错误态后永不自愈 —— 个股页一旦渲染异常，按返回键只会反复看到同一张「页面渲染出错」，只能刷新。改为 `RouteErrorBoundary` 以 `location.pathname` 作 key（**不用** `location.key`，否则 `?tab=` 变化会整树重挂）；实测渲染异常后返回能正常回到行情中心
- **门禁**：新增 e2e `limitUpSentiment.spec.ts`「从短线情绪进个股，浏览器返回仍停在短线情绪 Tab」；`marketTrust.spec.ts` 抽屉用例的 `toHaveURL(/\/market$/)` 放宽为「pathname=/market 且无 board 参数」（其本意是"抽屉不改 URL"，不因 Tab 入 URL 而误红）。本地（vite dev + mock）实测：limitUpSentiment / marketTrust / conceptBoard / marketDataFace / marketPolling / darkmode 共 50 例全绿，`npx tsc --noEmit` 通过
- 涉及文件：frontend/src/pages/market/index.tsx、frontend/src/App.tsx、frontend/e2e/limitUpSentiment.spec.ts、frontend/e2e/marketTrust.spec.ts、docs/Changelog.md
## 2026-10-12 - Task 8 最终评审修复波：e2e 门禁可复现 / backfill_only 插入约束 / hot-boards 降级 / 脚本退出码

最终整支评审结论 **Ready after 1 must-fix**（四个承载性不变式真库复现全部 ENFORCED、炸半径与无回归 OK）；本轮为一次性统一修复，4 项：

- **Critical 1（e2e 门禁不可复现）**：`tests/test_limit_up_stock_daily.py` 的 `_clean_acceptance_day` 原先在 `finally` 里**只 DELETE 不恢复**验收日 `2026-09-08` 的 178 行 → 同一会话里"先跑 facts 文件、再跑 backfill 文件"必然红在 `missing_fact_rows: ['2026-09-08']`（首次会话绿只因字母序 `test_backfill_*` 在前）。改为**先 `SELECT *` 快照、清空、`finally` 里逐列恢复**（列集取自 `MarketLimitUpStockDaily` 表定义 —— 过滤掉 `xmin` 等系统列，不手抄列名），并把"前置状态 / 如何清理 / 如何恢复 / 验收日形状（`local_calc`·178 行·is_lu=75·is_zb=39）"写进 docstring；前置状态本来就空时打印回补命令提示。**双向自证**：两种先后各连跑两遍（4 次）全绿；把恢复去掉（模拟旧行为）→ "先 facts 后 backfill" 立刻红并指名该日
- **Important 2（`backfill_only` 只约束 DO UPDATE）**：`ON CONFLICT ... WHERE` 管不到**插入**分支 —— 回补批里若某行 `fact_source`/`source` 写错，会 INSERT 出 `local_calc` 行并**永久免疫** Ruling 18 的修复。`upsert_limit_up_stock_daily`/`upsert_sentiment_daily` 在 `backfill_only=True` 时改为**先校验来源、非法即 `ValueError`**（零 SQL 发出）；新增 3 条用例（两类 row 各自的拒绝 + 实时路径 `backfill_only=False` 仍接受 `local_calc` 的对照），变异（跳过校验）→ 2 红
- **Important 3（hot-boards 在落后 schema 上 500）**：`market_service._hot_boards_snapshot_rows` 的读表查询会碰迁移 `a3f8c2d6e9b1` 才引入的 `capture_kind` 列，而它原在端点 `try` **之外** → 一版落后的库上"本可降级"变成 **500**。改为把读表包进 try/except：**任何 DB 错误一律视为"今日无快照"**、`logger.warning` 后回落既有实时路径（与 Ruling 11 闸门语义一致）；新增用例（内层 `load_hot_board_snapshot` 抛缺列 `ProgrammingError` → 走实时且返回 items，不 500），变异（去掉 try）→ 红
- **Important 4（全 `kept_realtime` 仍 exit 0）**：`backfill_market_history.py` 抽出 `exit_code(summary)`：任一天 `error` → 1；**`written_days == 0` 且 `skipped_kept_realtime > 0` → 1**（一天都没回补却报成功会让运维/CI 误判），dry-run 与"窗口整体不完备"等干净空跑仍为 0；新增 5 条用例（含 `main()` 接线与 `capsys` 输出的显式失败原因），变异（回退旧表达式）→ 2 红；脚本头部补退出码表
- **门禁**：全量 **820 passed / 67 deselected / 0 failed**（上一轮 811 + 9 条新用例）；`ruff check` + `ruff format --check app/ tests/` + `mypy app` + `bash scripts/self_review.sh --full`（免手工 export `UV_CACHE_DIR`）绿；`backend/scripts/` 全目录 ruff 的 14 个既有错误（`migrate_jsonl_to_pg.py` 等，非本轮文件）与 CI 口径一致，未动
- 涉及文件：backend/tests/test_limit_up_stock_daily.py、backend/tests/test_backfill_market_history.py、backend/tests/test_board_snapshot.py、backend/app/repositories/limit_up_repo.py、backend/app/services/market_service.py、backend/scripts/backfill_market_history.py、docs/Changelog.md

## 2026-10-12 - Task 8 收口：文档一致性 + 选定卫生修复 + 活链路验证（计划 Task 8）

- **文档一致性**：`docs/architecture/database-architecture.md` 增 2.1.2.1 节登记 `market_limit_up_stock_daily`（用途 / 唯一键 `(trade_date, stock_id)` / `fact_source`+`enrich_source`+`observed_at`）与 `sector_moneyflow_snapshots` 的 `amount`/`flat_count`/`capture_kind` 扩列（明写**只有 `capture_kind='eod_full'` 的日子是完整日**）；`docs/features.md` 两个 trends 行 + 行情台行核对**未改**（描述与代码/schema 一致）；`docs/decisions/0009-…md` 按 `docs/decisions/README.md` 的「单篇 ≤ 40 行」从 41 行收到 **38 行**（A/B/C 判据、与 ADR 0004 的边界、被否决方案、`eod_full` 产地四条内容一条未丢）；计划头部翻 `status: completed` / `next-action: -`、T1/T2/T8 的 Step 与验收总清单勾选、新增「实施结果」节（逐 Task 提交 + 头条数字）；`plans/index.md` 状态改 `completed` 并写依据
- **Changelog 两处文本缺陷更正**：① T4 条目把**被撤回的** content md5 `ea1f1662…`（含自增 `id`）换成真值 **`96c5cc73e19a46d8e66871b1372cc58e`** 并写明"剔除 `id` 后的内容指纹"；② T6 修复轮 1 的 Important #3 原文"**一条 SQL 都不发**"改为属实表述"**不发起任何 `daily_quotes` 查询**"（锚点聚合仍会跑一条 `Index Only Scan`，这才是门禁钉住的性质），并在该节头加 Task 8 更正说明
- **环境卫生修复（免手工 export）**：`scripts/self_review.sh` 在 `UV_CACHE_DIR` 未设置时默认落到**工作区** `backend/.uv-cache`（沙箱拒 `~/.cache/uv` 会让 ruff/mypy/pytest 三段假红，与代码无关）；显式传入仍以调用方为准。实测 `bash scripts/self_review.sh --full` 在**不导出** `UV_CACHE_DIR` 的环境下通过
- **T3 遗留**：`schemas/market_data.py:360` 的 `degraded_trading_calendar` 注释改为**当前语义**（"并集提供了主源缺失的交易日"或主源不可用，不是"主源表不存在"）；从 `reconciliation_service.py:75-77/506-508/700` 与 `tests/test_market_coverage.py` 撤掉**被撤回的**"真库 `daily_quotes` 缺 2026-09-09..09-17 共 7 个交易日"（实测是窗口化查询产物），改为"任何单源都可能缺日"的一般论证；`tests/test_market_coverage.py:304` 的过期测试名 `..._denominator_is_daily_quotes_trading_days` → `..._denominator_is_union_trading_days`（实现用的是并集）；把 `@e2e` 里**拿可变真库状态断言 `degraded is False`** 的用例降级为环境探针（`assert calendar` + 打印），契约由两条**桩**用例（正例 `..._degraded_when_union_fills_primary_gap` / 反例 `..._not_degraded_when_primary_complete`）钉住 —— 行情自己缺日时该标记**应当**为 true，原断言会随数据合法变化而随机变红
- **T6 遗留**：`get_sector_trends` 的 `daily_quotes` 扫描**左端加界**（`anchor - (days * 2 + 14)` 自然日，并与该维度最早行取较晚者）——原来从该维度最早行扫起、横跨整部维度历史而窗口只取末尾 `days` 个交易日；`tests/test_sector_trends.py` 增 `test_daily_quotes_scan_window_is_bounded_not_history_wide`（桩记录 `(start, end)`；变异回退成"从最早行扫"→ 变红，还原转绿），并让该文件所有桩用例对"扫描下界仍装得下 60 天窗口"做守卫；恢复空维度用例丢失的**专用断言** `cache.sets == []`（空 `boards` 不得被冻结 300s）并把名字改成它真正断言的两件事（`test_empty_dimension_early_exit_never_touches_daily_quotes`；"一条 SQL 都不发"的说法同时改正）
- **T7 遗留（前端）**：`SectorMoneyflowCard` 表头并排显示**请求窗口**与**实际观测跨度**（`请求近 {window.days} 交易日 · 实际观察 {window.observedDays} 个交易日（from → to）`，`data-testid="sector-continuity-window"`）——短观测不再能读成短请求；`limitUp.ts` 的 `ztAboveMedianDays` 改**非空**（与后端 `int = 0` 对齐，删掉 `--` 死分支与 Thermometer 的 `streak == null` 分支）；`marketData.ts` 删掉无人消费的 `attribution` 映射与 `SectorAttribution` 类型，归属口径**只留 `attributionNote`** 一个真源（`window.observedDays` 同步补进 mapper 与类型）；e2e 增窗口并排断言、mock 补 `observed_days` 并删掉冗余 `attribution` 字段
- **T1 遗留**：`_enrich_stock_facts_day` 的**空池**也 `logger.warning`（Ruling 10 要求空池与 0 命中并列可见），并把 `enrich_stock_facts_from_pool` 调用**纳入 best-effort `try`**（写库失败 → 新原因码 `enrich_write_failed` + 告警，事实施工结果原样返回、不回滚不上抛），与 docstring 声明的"best-effort 覆盖整段增强"一致；新增 `test_empty_pool_logs_a_warning` 与 `test_enrichment_write_failure_is_best_effort`
- **T4 遗留**：`backfill_market_history.py` 的逐日 `db.commit()` 移入当日 `try`（两个域）——提交失败此前会冒泡出 `run_backfill` 让整段回补崩掉，与脚本承诺的 per-day 隔离矛盾；新增 `test_backfill_survives_a_failing_commit` / `test_backfill_stock_survives_a_failing_commit`（假 session 第 2 天 commit 抛错 → 该日 `error` + rollback，后一天照跑）
- **活链路验证（闭环 Task 7 声明的缺口）**：不重建共享 Docker 栈，用本 worktree 的临时 `uvicorn`（127.0.0.1:8099，连本地 PG 5433 / Redis 6380）：`GET /api/v1/market/sentiment/trends?days=250` → **200**（`samples=239`，`zt_count` p50 76 / `broken_rate` p50 0.2921 / `promo_1to2` p50 0.1591，`zt_above_median_days=119`）；`GET /api/v1/market/sector-trends?dimension=industry&days=60&top_n=20` → **200**（`available_from=null`、`attribution=current_sw_snapshot`、`window={days:60,observed_days:20,samples:0}`、`missing_days` 20 天）；`GET /api/v1/market/hot-boards?category=industry` → **200**（`source=eastmoney_boards`、items 10）；`GET /api/v1/market/data-freshness` → **200**（`coverage` 8 域，`sector_moneyflow complete_days=0` 如实）。**环境注意**：该端点经 `apply=False` 仍会构造 TuShare client，故 `TUSHARE_TOKEN` 为空时 **500**（`ValueError: TuShare token is required`）—— 这是环境依赖不是回归，验证时用 `backend/.env` 的 token。进程已杀并证明端口空闲（`netstat … | grep "\.8099 "` 无输出、`lsof` 无监听、curl 连接被拒）
- **门禁**：全量 **811 passed / 67 deselected / 0 failed**（基线 806 + 5 条新用例）；`ruff check` + `ruff format --check` + `mypy app` + `npx tsc --noEmit` + `npm run build` + `bash scripts/self_review.sh --full`（**免手工 export `UV_CACHE_DIR`**）绿；新 e2e 6 passed、既有五个 market spec **38 passed 零回归**
- 涉及文件：docs/architecture/database-architecture.md、docs/decisions/0014-market-derived-fact-persistence.md、docs/Changelog.md、plans/2026-10-12-market-hotspot-persistence-and-trends.md、plans/index.md、scripts/self_review.sh、backend/app/schemas/market_data.py、backend/app/services/reconciliation_service.py、backend/app/services/market_data_service.py、backend/app/services/limit_up_service.py、backend/app/repositories/market_data_repo.py、backend/scripts/backfill_market_history.py、backend/tests/test_market_coverage.py、backend/tests/test_sector_trends.py、backend/tests/test_limit_up_stock_daily.py、backend/tests/test_backfill_market_history.py、frontend/src/shared/api/limitUp.ts、frontend/src/shared/api/marketData.ts、frontend/src/features/market/components/SentimentThermometer.tsx、frontend/src/features/market/components/SectorMoneyflowCard.tsx、frontend/e2e/marketTrends.spec.ts

## 2026-10-12 - 前端消费两个统计端点：情绪历史分位 chip + 板块连续性 chip（计划 Task 7）

- **mapper（snake→camel 只在 api 层一处）**：`shared/api/limitUp.ts` 新增 `fetchSentimentTrends(days=250)`（`window`/`series`/`percentile`/`streak`/`insufficientHistory`）与 `shared/api/marketData.ts` 新增 `fetchSectorTrends(dimension, days=60, topN=20)`（`attribution`/`attributionNote`/`availableFrom`/`luCountSource`/`missingDays`/`leaderChanges`）。`percentile=null` **原样保留**（不映射成空对象）、`broken_rate`/`promo_1to2` 与缺日 NULL **绝不落成 0**
- **情绪温度计分位区块**（`SentimentThermometer` +trends prop，新增 `.thermo-pct*` 样式）：每个 chip 带**该字段**的样本窗口（`近 239/231/228 交易日分位`，不是窗口行数）与 `P25/P50/P75` 分位梯（比值 ×100），区块头带样本区间 `from → to` 与「涨停家数高于中位数 N 天」；`insufficient_history=true` ⇒ 只显示「样本不足（近 N 个交易日 · 不足 60 日不给分位）」，**不给任何分位数字**；某字段全 NULL ⇒ 「无有效样本」。趋势线**复用同一个 `TrendSpark`** 画 trends 长窗口序列（端点未就绪/失败回落 30 天日历序列），零新增图表依赖
- **板块连续性 chip 条**（`SectorMoneyflowCard` +`sector-trends` query，key 带 `dimension`；新增 `SectorMoneyflowCard.css`）：连续上榜天数 / 连续净流入-流出 / 末完整日净流入（`null` → `--`）/ 窗口内龙头更替次数；`attributionNote` 与 `自 available_from 起累积` **必须上屏**（行业=「按当前分类回溯，非当时成分」，概念=时点口径——标注随维度切换联动，不静默错标）；`missing_days` 按缺日披露「不计 0 流入」（计数全量、逐日最多 8 个）；`luCountSource=unavailable` 时**不渲染**家数
- **e2e（`frontend/e2e/marketTrends.spec.ts`，6 条全 mock）**：RED **5 failed / 1 passed**（组件不存在）→ GREEN **6 passed**；覆盖分位窗口文案与 ×100 渲染、`insufficient_history` 分支（含**变异验证**：往「样本不足」文案里塞 `P50 90` → 转红 → 还原转绿）、`attribution_note`/`available_from` 上屏、缺日 `--` 而非 `0.00亿`、维度切换重取数。既有五个 market e2e（`limitUpSentiment`/`marketTrust`/`marketDataFace`/`marketPolling`/`darkmode`）**38 passed 零回归**；`npx tsc --noEmit` + `npm run build` 绿
- **活链路未验证（当时如实披露，Task 8 已闭环）**：本机 `backend_api` 镜像构建于 2026-09-29（早于 Task 6）→ 两个新端点经 dev 代理仍 404，chip 的真库渲染待重建镜像后复验；mock 载荷逐字段对齐 Task 6 的 Pydantic schema。**Task 8 的处置**：不重建共享 Docker 栈，改用本 worktree 的临时 `uvicorn`（127.0.0.1:8099）直连真库验证两个端点 → 均 **200** 且数字与 Task 6 验收行一致（见本文件 Task 8 条目）
- 涉及文件：frontend/src/shared/api/limitUp.ts、frontend/src/shared/api/marketData.ts、frontend/src/features/market/components/SentimentThermometer.tsx、frontend/src/features/market/components/SentimentThermometer.css、frontend/src/features/market/components/SectorMoneyflowCard.tsx、frontend/src/features/market/components/SectorMoneyflowCard.css、frontend/src/pages/market/index.tsx、frontend/e2e/marketTrends.spec.ts、plans/2026-10-12-market-hotspot-persistence-and-trends.md、docs/Changelog.md

### 评审修复轮 1（Task 6，3 个 Important）

> Task 8 更正：本节 Important #3 原文写"**一条 SQL 都不发**"，实为**不发起任何 `daily_quotes` 查询**（锚点聚合仍会跑一条 `Index Only Scan`，这才是门禁用例钉住的性质）；`test_empty_dimension_never_touches_daily_quotes` 的断言口径同步记为"执行过的语句里不含 `daily_quotes`"。

- **Important #1（死代码/注释不符）**：`market_data_repo.latest_sector_moneyflow_day` 与 `list_sector_moneyflow_complete_days` 原本零调用方、段头却写着"唯一的板块读路径"。**接线而非删除**：`get_sector_trends` 现在用前者作窗口**右端锚点**、用后者作窗口内**只含完整日**的读（`capture_kind='eod_full'` 由函数签名锁定，调用点漏传不再可能）；新增 `earliest_sector_moneyflow_day` 给出窗口左界的**有界**下探起点。段头改为逐函数标注"生产在用 / 同类但当前仅测试在用"，`latest_sector_moneyflow_day` 的"避免一串假缺日"注释改为与代码一致的表述（它锚"有行的最近日"而非"有完整日的最近日"，理由写在 docstring 里）
- **Important #2（窗口可静默超过 `days`）**：新增纯函数 `trailing_window_days(trading_days, days)`，窗口**恰好**是"以该维度快照最近日为右端、向前 `days` 个交易日"；`window` 改为并排三元组 `days`（**请求值**）/ `observed_days`（实际交易日跨度）/ `samples`（完整日数），使"天数不符请求值"不可能悄悄发生，并钉住不变量 `samples + len(missing_days) <= days`。原实现取 `[snapshot_first, snapshot_last]` 的全部交易日 —— 快照落后行情时会回 80–100 天并把行情独有的那几十天**全报成缺日**
- **Important #3（空维度全表扫）**：该维度一行都没有时**立即早退**返回 `_empty_sector_trends(...)`（`available_from=null`、`observed_days=0`、`missing_days=[]`、`samples=0`），**不发起任何 `daily_quotes` 查询**（这才是被门禁钉住的性质；锚点聚合仍会执行一条 `Index Only Scan`。原实现会落到 `list_trading_days(db, date.min, date.min)`（`trade_date >= 0001-01-01 AND <= 0001-01-01`，非 sargable 全表扫），且其后的 `if not trading_days: trading_days = list(snapshot_days)` 兜底**永远到不了**
- **新增默认门禁用例（4 条）**：`test_window_never_exceeds_requested_days_when_snapshot_lags_quotes`（假 DB：行情 100 天 / 快照只到第 80 天 / `days=60` → 窗口 `[D21,D80]`、`missing_days` 不含快照未覆盖的 20 天）、`test_empty_dimension_never_touches_daily_quotes`（假 session 记录语句：执行过的语句**表名不含 `daily_quotes`**，且给 `list_trading_days` 装**绊线**：被调用即抛错）、`test_empty_result_is_never_cached` 改用统一假 DB 助手并断言空维度形状、`test_sector_trends_endpoint_shape` 断言 `days`=请求值 60 而 `observed_days`=3
- **变异验证（逐条做，做完还原）**：① `trailing_window_days` 改成不截断 → `test_window_never_exceeds_requested_days_when_snapshot_lags_quotes` 变红（1 failed）；② 去掉空维度早退（`anchor or date.min` 回旧路径）→ `test_empty_dimension_never_touches_daily_quotes` 变红（1 failed）；还原后两次均 18 passed
- **真库复测**：空维度路径 **74.2ms**、仅一条 SQL（`Index Only Scan using ix_sector_moneyflow_dim_date`，`EXPLAIN` 实证：锚点/最早日聚合均非全表扫），响应 `window={days:60, observed_days:0, samples:0}`、`missing_days=[]`；6b 三个维度仍 `available_from=null` / `observed_days=20` / `samples=0` / `missing_days=20`（6a 数字不变：`days=250` → samples 239）
- 涉及文件：backend/app/services/market_data_service.py、backend/app/repositories/market_data_repo.py、backend/app/schemas/market_data.py、backend/app/api/v1/market_data.py、backend/tests/test_sector_trends.py、docs/features.md、plans/2026-10-12-market-hotspot-persistence-and-trends.md、docs/Changelog.md

## 2026-10-12 - 统计端点：情绪趋势/历史分位 + 板块趋势（读时计算，不物化）（计划 Task 6）

- **`GET /api/v1/market/sentiment/trends`（6a）**：最近 `days`（默认 250 / 上限 500）个交易日 `market_sentiment_daily` 升序序列 + **仅 3 个**指标（`zt_count`/`broken_rate`/`promo_1to2`，拍板 R6）的历史分位。分位是 **nearest-rank**（第 `ceil(pct/100×n)` 个样本）：端点值 p0/p100 可达且正确、全程无插值；**只用有效样本**——某字段当日为 NULL（如 `broken_rate` 分母 0）时**不进该字段样本、绝不计 0**（三字段样本数各自计数，响应里带 `sample_count`）；有效样本 <60 → `insufficient_history=true` 且 `percentile=null`（不给假分位）。`as_of` = 表内 `max(trade_date)`（不是"今天"），`as_of_quality` 复用 `market_day_service.resolve_latest_complete_day` 的完整性判据。缓存键含 `days`，**空结果不缓存**
- **`GET /api/v1/market/sector-trends`（6b）**：读时计算（不物化）——连续上榜天数（按当日 `main_net_inflow` Top-N，`top_n` 显式参数默认 20 且**并列进响应**，tiebreak `main_net_inflow DESC, board_code ASC`）、连续净流入/流出天数（当前连击 + 窗口内最长）、龙头更替次数与逐次事件、板块涨停家数序列；**Ruling 21 完整日闸门**：只统计当日有 `capture_kind='eod_full'` 行的日子，窗口内缺完整日的交易日落进 `missing_days`、**绝不**当 0 流入（连击在完整日序列上连续）；`available_from`（该维度最早完整日，可为 `null`）与 `attribution` **必填**；**Ruling 22 归属诚实**：`industry`/`region` = `current_sw_snapshot`（`attribution_note="按当前分类回溯，非当时成分"`），`concept` = `point_in_time`（成分由 `concept_members` 当前态 + `concept_member_changes` **反推**时点集合：当前态 − 该日之后的 add + 该日之后的 remove；绝不用当前成分冒充历史成分）。缓存键含 `dimension`/`days`/`top_n`，空结果不缓存
- **新增 repo 只读查询**（无写路径）：`limit_up_repo.latest_sentiment_day` / `list_sentiment_days` / `sentiment_days_in_range`；`market_data_repo.latest_sector_moneyflow_day` / `earliest_sector_moneyflow_complete_day` / `list_sector_moneyflow_complete_days` / `list_trading_days` / `list_sector_moneyflow_trading_days` / `list_concept_members_current` / `list_concept_membership_changes` / `list_limit_up_facts_in_range`。窗口读 `list_sector_moneyflow_range` **显式传 `capture_kind='eod_full'`**（不传会把 poll 行当完整日候选 —— 实测踩过并已加注释钉住）
- **板块涨停家数只对 `concept` 可得**：industry/region 没有"东财板块 → 个股"的同源映射（`concept_members` 只覆盖 concept；`sw_industry_members` 是申万 L3 码、`stocks.province` 与"XX板块"不同名），用相近名字硬凑会造出**看起来有**的错误归属 ⇒ `lu_count=null` + `lu_count_source="unavailable"` 显式声明，不编造
- **真库验收（2026-10-12，最后一次全量 pytest 之后）**：6a `days=250` → `window.samples=239`（`2025-10-15 → 2026-10-09`）、`insufficient_history=false`，三个指标 `sample_count` 全 239，`zt_count` p0/p50/p100 = **32/76/226**、`broken_rate` **0.0763/0.2921/0.681**、`promo_1to2` **0.0/0.1591/0.4762**（`promo_1to2` p0=0.0 正是"0 是可达的正经样本值"的端点证据）；6a `days=30` → `samples=30`/`insufficient_history=true`/`percentile=null`。6b：真库 `capture_kind='eod_full'` 行数当前为 **0**（T2 的 15:05 EOD job 上线前）→ 三个维度 `available_from=null`、`window.samples=0`、`boards=[]`、窗口内 20 个交易日**全部**列进 `missing_days`（闸门如实，未拿 poll 行冒充）；`@e2e` 用自建自清的哨兵行（`board_code='BK9999'`，写在窗口内最早交易日）验证 `available_from`/`as_of`/`samples`/`leader_change_count` 与"6b 行 == `list_sector_moneyflow` 同一行"逐字相等
- **TDD**：RED 26 failed / 2 deselected（新模块不存在）→ GREEN 27 passed / 2 deselected；全量 **803 passed / 67 deselected / 0 failed**；`ruff check` + `ruff format --check` + `mypy app`（175 文件）全绿
- 涉及文件：backend/app/api/v1/market_data.py、backend/app/services/limit_up_service.py、backend/app/services/market_data_service.py、backend/app/repositories/limit_up_repo.py、backend/app/repositories/market_data_repo.py、backend/app/schemas/limit_up.py、backend/app/schemas/market_data.py、backend/tests/test_sentiment_trends.py、backend/tests/test_sector_trends.py、docs/features.md、plans/2026-10-12-market-hotspot-persistence-and-trends.md、docs/Changelog.md

## 2026-10-12 - `daily_quotes.pct_chg` 365 天历史回填（计划 Task 5a）

- **回填（复用 `backend/scripts/backfill_pct_chg.py`，未改一行逻辑）**：`--start-date 2025-10-12 --end-date 2026-10-12` → 命中 `daily_quotes` 内 **241** 个交易日（`2025-10-13 → 2026-10-09`，窗口末端今天 2026-10-12 尚无行情行），TuShare `daily` 权威逐日重取，**1,322,196** 行 `pre_close`/`pct_chg` 更新、**0** 失败 / **0** 空回 / EXIT=0，耗时 **2m45s**（≈0.65s/日，约 241 次调用，未触发限流、无需断点续跑）。冒烟 `--limit 5` 4.0s / 27,788 行；对 `2025-10-13…10-17` 复跑幂等（非空数与行数逐日不变）
- **验收（计划 T5 验收行，按 365 天口径调整）**：窗口内既有限价行又落在窗口内的限价交易日 **241** 个，`pct_chg` 非空率 ≥0.99 的天数 **17 → 241（100%）**，0 失败日；254 天全量口径 **17 → 241**（超 ≥235 目标）。**13 个限价交易日（`2025-09-16 … 2025-10-11`）在 owner 批准窗口之外**，仍只有 29/5412 行非空，本轮**明确不承诺**回填
- **下游效果（真库实测）**：`market_day_service.is_day_complete`（行数 ≥0.9×universe + `pct_chg` 非空率 ≥0.99 + 该日有限价）对 3 个历史采样日由 `partial` → `complete`：`2025-10-15` 0.005355→1.000000、`2026-01-06` 0.005318→1.000000、`2026-06-10` 0.006531→1.000000（rows/universe/limits_present 不变）；窗口内 1,322,196 行**全部**非空（100%）
- **5b（龙虎榜/大宗/解禁/回购窗口回补）本轮跳过**：controller 裁定该子项可选且四表 27/27 天无洞，另立任务；**未消耗 TuShare 配额**
- 涉及文件：plans/2026-10-12-market-hotspot-persistence-and-trends.md、docs/Changelog.md（数据面回填，**无代码改动**）

## 2026-10-12 - 历史回补：一个脚本两个域（情绪 21→239 天 / 个股事实 0→40,894 行）（计划 Task 4）

- **`backend/scripts/backfill_market_history.py`**（`--domain sentiment|limit_up_stocks`、`--from`/`--to`/`--limit`/`--dry-run`）：按 `daily_quotes` 真实交易日**升序**逐日回补（`promo_*`/`broken_rate` 依赖前一日 streak，乱序会静默算错）；逐日记录纳入/跳过原因并在结尾打印汇总表 + 逐日列出跳过日；`--dry-run` 只跑只读闸门、不写库不增强不 commit。sentiment 域复用实时路径 `persist_snapshot`（零新口径），个股域只调 `persist_stock_facts`（**不**调东财 EOD 增强）。不新建 job/队列
- **E2 窗口完备性守卫 `limit_up_service.window_limits_complete(db, as_of, lookback)`**：窗口取自 `daily_quotes`（2023-05 起）而限价自 2025-09-16 起，`window_trade_days(10)=16` 个交易日窗口的**开头**落在限价上线前时 `LEFT JOIN limits` 整天落空 → streak 被截平、晋级率系统性低估。守卫要求窗口内**每一天**都有限价（且窗口凑满、末日 == `as_of`），否则整日跳过（原因 `window_limits_incomplete`）。真库只读预演：从限价首日 2025-09-16 起 254 个交易日里 **15 天被跳过**（2025-09-16…2025-10-14）→ 可回补 **239** 天，与 E2 推算逐日吻合
- **Ruling 18（回补对实时行不可变）**：`limit_up_repo` 的 `upsert_sentiment_daily`/`upsert_limit_up_stock_daily` 增 `backfill_only`，编译出 `ON CONFLICT ... DO UPDATE ... WHERE <已存储的 source/fact_source> = 'backfill'`（看**已存储**列而非 `excluded`）+ 单一字面量 `BACKFILL_SOURCE`（写入方与守卫共用一个值）；`persist_snapshot(fact_source=)` 把来源写进 `source` 列。回补只修回补行，实时行的判定列/东财增强列全部字节不变；实时路径不带守卫、语义不变。默认门禁用编译 SQL 断言守卫形状，`@e2e` 用 2000-01-03/04 哨兵日在真库双向验证（实时行拒改 + 回补行可修）
- **`get_snapshot(..., enrich=True)` / `persist_snapshot(..., enrich=True)` 新增开关（默认不变）**：历史回补传 `enrich=False` —— 封板明细是 A 类"当天不抓就永久丢"的数据，回补 239 天不该对东财外呼 239 次
- **真库验收（2026-10-12，最后一次全量 pytest 之后）**：`market_sentiment_daily` 21 → **239** 天（`2025-10-15 → 2026-10-09`）。**原因分布（修复轮 2 修正后的真实口径）**：**218 天 `ok`**（真写入/更新）+ **21 天 `kept_realtime`**（该日已有 `local_calc` 实时行、Ruling 18 守卫拒改，`upserted=0`）—— 首轮把这些天误报成 `ok`（丢了 upsert 的 rowcount），修复轮 2 才分开；`market_limit_up_stock_daily` **40,894** 行 / 239 天（`fact_source` 100% `backfill`；`seal_time`/`seal_fund`/`break_count`/`enrich_source` 非空计数 **0**，缺失恒 NULL 绝不填 0，54.8s）；2026-09-08 **178 行 / is_lu=75 / is_zb=39 / prev_is_lu=95**。黄金日字节不变：21 行 `local_calc` 聚合 md5 `0c8e6ffe…` 与黄金行 md5 `a5e745db…` 回补前后相同；连跑两次行数不变，且 facts 剔除 `id` 后的内容指纹 md5 `96c5cc73e19a46d8e66871b1372cc58e` 两次一致（含一次"整表被 pytest 迁移循环重建后重跑"）。**注**：4/21 个 `local_calc` 日的存量聚合与当前数据的重算不一致（09-10/11/14/15），Ruling 18 使其**不可被回补修复**（需显式实时重跑）—— 已由下方「修复轮 1」按裁决显式重算修好
- **修复轮 1（controller 裁决①：授权重算 + 机械收口）**：`market_sentiment_daily` 的模型契约原文即「**纯派生缓存：可随时由 `daily_quotes` + `stock_price_limits` 重建**」，故对 4 个与重算不一致的 `local_calc` 日各跑一次**显式实时重算** `persist_snapshot(db, cache=None, as_of=d)`（**不带**回补守卫、`source` 保持 `local_calc`，4 天共 1.26s）：`zt` 39→40 / 39→40 / 55→57 / 32→32，`zb` 26 / 25 / 38→39 / 24→25，`broken_rate`/`promo_1to2` 随分母同步（如 09-10 `0.4`→`0.3939`）。**根因是"写入时输入未全"而非口径变化**：该 4 天的差值恰等于 2026-09-18 08:31/08:34 才落库的行情/限价行中今日判为 `zt`/`zb` 的行数（+1 zt / +1 zt / +2 zt +1 zb / +1 zb），而 `_BREADTH_SQL` 的 zt/zb 谓词自 2026-09-14 引入后**逐字未变**（`git log -S` 证据）。黄金日 09-08 与新回补的 218 行未动（黄金行 md5 仍 `a5e745db…`）
- **新增全量不变式 e2e 门禁 `test_sentiment_daily_matches_fact_counts_for_every_day`**：对 `market_sentiment_daily` **每一天**断言 `count(is_lu) == zt_count` 且 `count(is_zb) == zb_count`（一条 SQL 对拍两张表），**零行的日子显式报 `missing_fact_rows`**、不静默跳过（静默跳过正是"情绪行在、个股事实缺"长期不被发现的原因）。已做破坏验证：注入 `zt+1` → 红并指名 `('2025-10-15', 84, 83, …)`；删掉一天事实 → 红并指名 `missing_fact_rows: ['2025-10-15']`。实测 239 天 **0 不一致 / 0 缺行**，`local_calc` 子集 md5 由 `0c8e6ffe…` 变为 `2b25e73e…`（仅这 4 行的授权变更）
- **修复轮 2（评审 Important #1）**：把 upsert 的**实际受影响行数**透上来，并给「被守卫拒绝」独立原因码。`persist_snapshot` 返回 `upserted`/`kept_realtime`（`backfill_only and upserted == 0`）；`persist_stock_facts` 返回 `upserted`/`kept_realtime`（`rows and upserted < rows`，含部分被拒）；脚本新增原因码 `kept_realtime` 与独立计数列 `skipped_kept_realtime`，`written_days` 只数真写入的天、`rows` 只数实际 rowcount。**真库重跑分布**：sentiment **218 ok / 21 kept_realtime**（逐日列出后者）、limit_up_stocks **239 ok / 0 kept_realtime**（事实表被套件清空后全新建，无实时行可拒）。新增默认门禁：脚本层「守卫拒绝→`kept_realtime` 且 `written_days` 不含该天」、service 层 rowcount 接线（sentiment `upserted==0`；facts `upserted<rows`），真库哨兵用例断言 PG rowcount 语义（实时行 0 / 回补行 1）
- **修复轮 2（评审 Important #2）**：E2 窗口守卫的单测替身改为**真的读它收到的参数**（`fake_missing` 按收到的 `dates` 求交并记录 spy；true/false 两例都断言收到**整段窗口**）。**变异验证**：把实现回退成 `missing_price_limit_dates(db, [as_of])`（E2 要防的那个 bug）→ `test_window_limits_complete_true_*`（spy 只见 `[as_of]`）与 `..._false_when_any_window_day_lacks_limits`（返回 True）双双变红；还原后逐字复现、4 例转绿
- 涉及文件：backend/scripts/backfill_market_history.py、backend/app/services/limit_up_service.py、backend/app/repositories/limit_up_repo.py、backend/tests/test_backfill_market_history.py、backend/tests/test_persist_snapshot_cache.py、plans/2026-10-12-market-hotspot-persistence-and-trends.md、docs/Changelog.md

## 2026-10-12 - 覆盖率只读巡检 + ADR 0014（计划 Task 3；合并时由 0009 改号）

- **修复轮 1（评审 Important #1/#2）**：① `degraded_trading_calendar` 语义改为 **「并集提供了主源缺失的交易日」**（`union_days != primary_days`，并保留主源表不可用时的 true）——只判「表是否存在」会漏掉真实状态：主源在库但缺日、由 `stock_price_limits` 补上时分母口径已经变了却标 ok；② **测量窗口末端统一锚定交易日历末日**（不再是各域自己的 `last_day`）：旧口径下 `dragon_tiger`/`block_trade` 分母被夹成 16、`cover_pct` 虚高 100.0 且把 2026-10-09 的洞夹掉，整域停更的域更会退化成「单日 100%」；新口径下两者为 `17` / **94.12** / 缺 `2026-10-09`（`repurchase` 报出 `09-09`）。同时把 `extra_days` 的文档与判定改准（**窗口外 ≠ 脏行**：当日盘前/盘中写下的 `poll`/盘中行只要落在窗口外就会出现在这里），补「整域停更」构造证据用例（同一桩数据并排断言旧口径 100% vs 新口径 33.33%）与真库 `@e2e` 反例；计划/`docs/features.md` 的分母口径文字同步改为「并集 + 窗口末端锚定日历末日」

- **`reconciliation_service` 显式分域**：`DOMAINS`（可自愈域：有 `_refetch_*` 的 `daily_quotes`/`daily_basic`/`price_limits`/`sentiment`）与新增 `INSPECT_DOMAINS`（只读巡检域：`sector_moneyflow`/`sentiment_daily`/`sentiment_intraday`/`limit_up_stock_daily`/`dragon_tiger`/`block_trade`/`share_float`/`repurchase`）在模块 docstring 里分节写明，配启动期断言（`DOMAINS` 每域必须有 `_refetch_<域>`）+ 两域集不重名；3 域补漏循环与 `sentiment` 特例**字节不变**，巡检域永不进 apply 路径。注：巡检域里用 `sentiment_daily`（而非 `sentiment`）以免与可自愈域名重名
- **`compute_coverage(trading_days, have_days, complete_days=None, *, window=None)`**（纯函数）：分母恒为 `daily_quotes` **交易日**、逐域收窄到该域自身 `[first_day, last_day]`；`have_days`（有任何行）与 `complete_days`（完整日）**并排返回**，`cover_pct` 基于 `complete_days`，分母为 0 → `None`（绝不 0/0）；窗口外/非交易日的未来脏行进 `extra_days` 而非被静默丢弃
- **`coverage_report(db, *, window_days=30)`（只读聚合）**：逐域查询包在 SAVEPOINT 里，缺表/缺列（`42P01`/`42703`，迁移未应用）→ 该域标 `missing_table`/`missing_column` 并继续渲染其余域（**不 500**）；域 → (表, 业务日期列) 唯一真源 `_COVERAGE_SOURCES`（`share_floats.float_date` / `stock_repurchases.ann_date` 逐域实测确定）；`sector_moneyflow` 单独查 `capture_kind='eod_full'` 得"完整日"（Ruling 13）。**不登记 `job:failures`**：本函数由只读端点每次请求调用，登记表 1 job 1 条，会被巡检刷成假告警
- **`DataFreshnessOut` +`coverage: list[CoverageOut]`（加字段，缺省空列表）**：响应模型随覆盖率行模型迁到 `app/schemas/market_data.py`（避免 market_data ↔ reconciliation 循环导入），`app/schemas/reconciliation.py` 保留同名 re-export（旧导入路径不断，`is` 恒等有测试守卫）；`/market/data-freshness` 仍 `apply=False`，覆盖率整体失败只回空列表（加法，不反噬新鲜度巡检）
- **ADR 0014**（`docs/decisions/0014-market-derived-fact-persistence.md` + index 登记；合并时由 0009 改号，见 Ruling 52 记录）：① A/B/C 三条判据（A 不可回补必当天落 / B 可回算可回补需窗口守卫 / C 快照型分类不得回算、只能聚合 + 显式标注）；② 与 ADR 0004 的边界（0004 管"快照型分类数据本身"，**不禁止**用行情事实聚合 + 按当前分类归属 + 标注 `current_sw_snapshot`）；③ 被否决方案（全读时计算 / 全落库含榜单 / 5 分钟全池明细 / 巡检域塞进补漏循环）；④ **产地决定可信度**：`eod_full` 才算完整日，`poll` 只是盘中子集
- 涉及文件：backend/app/services/reconciliation_service.py、backend/app/schemas/market_data.py、backend/app/schemas/reconciliation.py、backend/app/api/v1/market_data.py、backend/tests/test_market_coverage.py、backend/tests/test_data_freshness_api.py、docs/decisions/0014-market-derived-fact-persistence.md、docs/decisions/index.md、docs/features.md、docs/Changelog.md

## 2026-10-12 - 板块快照：`/market/hot-boards` 改读当日表 + 补 2 列 + 当日 EOD/补写（计划 Task 2）

- **`sector_moneyflow_snapshots` +3 列**（迁移 `e7b1c9d4a2f3` + `a3f8c2d6e9b1`，`down_revision` 串在 Task 1 的 `c4d8e1a72b93` 之后，单 head）：`amount Numeric(20,4)`（东财 `f6` 成交额）+ `flat_count Integer`（`f106` 平盘家数）—— 前端 `HotBoardItem` 契约本该有、表里却缺的两列，均 NULL 允许（历史行与上游给 `-` 的板块落 NULL，**绝不填 0**）；`capture_kind String(16) NOT NULL DEFAULT 'poll'`（`poll` | `eod_full`）行级记录产地，既有行一次性回填 `poll`
- **一次请求拿全**：`fetch_sector_moneyflow` 的 `fields` 扩成 `fetch_board_list` 的**超集**（+`f6,f106`，保留 `f62/f66/f72/f184/f128/f136/f140`）→ 不为这 2 列再发第二次 `clist`；顺带把 `f104/f105/f106` 三个家数字段统一走 `_count`（`fltt=2` 下无效值是 `-`，原样透传会落进 Integer 列让整个三维度 upsert 炸掉）
- **15:05 EOD 改为分页全量捕获（Ruling 11 + 评审 Important #1 的收盘守卫）**：`clist` 的 `pz` 上限就是 100（实测传 1000 仍只回 100 行），盘中 5 分钟轮询按 `fid=f62` 只拿得到 Top-100，当日表行是"每次轮询 Top-100 的并集、旧行永不刷新"（实测 industry 229 个 code 里 84 行停在开盘后第一轮）——既不是完整当日态、也不是 hot-boards 要的 `f3` 涨幅全集。新增 `fetch_sector_moneyflow_full`（复用 `_paged_clist`，industry 5 页 / concept 6 页 / region 1 页 ≈12 次请求/日，与单次路径共用字段集与 mapper）写 `capture_kind='eod_full'`，把当日**所有**板块刷新一次，不做 DELETE 剪枝；**写入前置收盘谓词** `is_after_close(now, close=15:00 SH)`（纯函数，aware 输入换算上海时钟）：盘中调用（手动 worker / 跨日补跑）**跳过且零写入**、返回 reason `before_close` 并经 `eod_alert_reasons` 进 `job:failures`（`eod_full` 单向不可降级，盘中打上它会让读表闸门提前整天打开）；`eod_alert_reasons` 统一判 `fetched < total` / 上游没报 total / `before_close`（**不许静默截断**）
- **`/market/hot-boards` 读表闸门（Ruling 11）**：该 `(day, dimension)` 存在 `capture_kind='eod_full'` 行（当日已全量捕获）**且**表内该维度最近日 == 最近**预期**交易日（`last_weekday(_today_sh())`）**且**该日 `eod_full` 只可能来自收盘后（≥15:00 SH，`before_close` 守卫保证）才服务表行（`source="eastmoney_boards"`、`as_of` = **行日**而非今天、`as_of_quality="partial"`、`pct_change DESC, board_code ASC` 确定性 tiebreak、只取 `eod_full` 行）；盘中只有 poll 行 / EOD job 还没跑 → **原实时路径不变**（绝不返回空表）；东财也炸 → 既有本地分组降级。读端点不写库。`eod_full` 单向不可降级（15:05 之后撞上来的盘中 tick 用 `CASE WHEN` 保持产地）。**历史缺口不可补**（A 类：东财无历史接口），覆盖率目标 = 上线日起 100%
- **当日 EOD / 缺 `eod_full` 补写**：`board_snapshot_eod_job`（交易日 15:05 收盘全量）+ `board_snapshot_rewrite_job`（15:35，**当日没有 eod_full 行**才补做全量 —— 只落过盘中轮询、错过 15:05 的日子也能补上收盘态），均 `Asia/Shanghai` cron；`MarketDataJobType` +`board_snapshot_eod`/`board_snapshot_rewrite`，worker 同队列同 service 方法（双轨制，`app/core/mq.py` 未改）；`list_sector_moneyflow_range(dimension, start, end, capture_kind=None)` 供 Task 6 只取完整日
- **真库验收（2026-10-12 盘中，Ruling 11 + 评审修复后）**：分页全量 `EOD` → industry **496/496** / concept **506/506** / region **31/31**（`fetched == total`，shortfalls 空），当日 496/506/31 行**全部** `eod_full`（`poll=0`）、`amount`/`flat_count` 非空率 **100.00%**；闸门开关可观测（全量前 `load_hot_board_snapshot=None` → 实时路径；全量后开 → 读表，items=10 且带 `amount`/`flatCount`）；读表 items 与 `/market/sector-moneyflow` 同 code 行 `mainNetInflow` **9/9 全等**；与同一时刻实时截取在同一批行上 `mainNetInflow`/`amount`/`flatCount` 全等，且全量后读表 Top-10 与实时 `fid=f3` Top-10 **同一集合**（并集/陈旧行问题消解）。**收盘守卫真库实测**：注入 14:30 调全量捕获 → `skipped/before_close`、当日 1033 行与 `max(updated_at)` **均不变**（零写入）；只有 poll 行的日子闸门为 False（`load_hot_board_snapshot=None` → `/market/hot-boards` 回落实时）；15:00 整（含边界）→ 正常全量写入（496/506/31，`fetched==total`）并打开闸门
- 涉及文件：backend/app/models/market_data.py、backend/app/migrations/versions/e7b1c9d4a2f3_extend_sector_moneyflow_snapshot.py、backend/app/migrations/versions/a3f8c2d6e9b1_add_sector_moneyflow_capture_kind.py、backend/app/core/providers/eastmoney_client.py、backend/app/repositories/market_data_repo.py、backend/app/services/market_data_service.py、backend/app/services/market_service.py、backend/app/scheduler/jobs.py、backend/app/scheduler/runner.py、backend/app/schemas/task.py、backend/app/workers/market_data_worker.py、backend/tests/test_board_snapshot.py、backend/tests/test_market_data_mapping.py、docs/Changelog.md

## 2026-10-12 - 个股级涨停事实表 + 东财 EOD 增强（计划 Task 1）

- **新增 `market_limit_up_stock_daily`**（唯一键 `trade_date + stock_id`，迁移 `c4d8e1a72b93`）：把 `_WINDOW_SQL` 的**判定**部分按日落库（`is_lu`/`touched`/`is_zb`/`prev_is_lu`/`streak`/`days_span`/`boards_in_window`/`missing_days`）+ 东财独有的 `seal_time`/`seal_fund`/`break_count`（`Numeric(20,4)`，历史恒 NULL **绝不填 0**）+ `fact_source`/`enrich_source`/`observed_at`。为什么：`market_sentiment_daily` 24 个字段全是聚合，个股只剩 `max_streak_symbol` 一个代码，情绪标的没有可统计对象
- **行集 = 候选窗口 as_of 行 ∪ 当日炸板行**：炸板选行新增 `limit_up_repo.fetch_day_broken_board_stocks`（谓词与 `_BREADTH_SQL` 的 `zb_count` 逐字一致；`_WINDOW_SQL` 的 `cand` 实测只覆盖 39 只炸板里的 12 只）。窗口派生的 `days_span`/`boards_in_window`/`missing_days` 只对候选窗口内的票可算 → "炸板-only"（今日触板但昨日未涨停，实测 27/日）落 NULL（缺失 ≠ 0）
- **真库验收（2026-09-08）**：`is_lu=75` / `is_zb=39` / 总行数 **178**（151 候选 + 27 炸板-only）/ `prev_is_lu=95`；**口径不变式** `count(is_lu) == market_sentiment_daily.zt_count`、`count(is_zb) == zb_count` 均成立，重复跑收敛。EOD 增强后 `is_lu` 零变化
- **东财 EOD 增强（A 类不可回补）**：`enrich_stock_facts_from_pool` **UPDATE-only**，只 `SET seal_time/seal_fund/break_count/enrich_source`（SQL 的 SET 子句 + 行 dict 双守卫，判定列永不覆写）。**裁决修正**：原计划的 15:05 独立增强 job 被取消 —— 15:05 时当日限价（16:50）与事实行（17:20）都还不存在，UPDATE-only 必然命中 0 行；改为 17:20 单 job 内 `persist_stock_facts` → commit → 增强（`persist_and_enrich_stock_facts`），增强 best-effort（外呼失败/空池/0 命中只登记 `job:failures`，不回滚事实、不让 job 失败）
- **实测东财池可取窗口**：2026-10-12 探测，`date=20260914`（第 14 个交易日）有数据、`date=20260911` 为 0 → 保留窗口 ≈ 最近 14 个交易日；2026-10-09 实跑 `persist_and_enrich_stock_facts` → `rows=119 / is_lu=72 / is_zb=20 / enriched=69`（真实封板时间/封单/炸板次数全落），不变式同样成立。worker `limit_up_stock_facts` 支持可选 `trade_date` 对该日补跑（retro 兜底，不新增 job 类型）
- 涉及文件：backend/app/models/market_data.py、backend/app/migrations/versions/c4d8e1a72b93_add_market_limit_up_stock_daily.py、backend/app/repositories/limit_up_repo.py、backend/app/repositories/market_data_repo.py、backend/app/services/limit_up_service.py、backend/app/services/intraday_sentiment_service.py、backend/app/scheduler/jobs.py、backend/app/scheduler/runner.py、backend/app/schemas/task.py、backend/app/workers/market_data_worker.py、backend/tests/test_limit_up_stock_daily.py、docs/Changelog.md

## 2026-10-12 - 计划登记：市场热点数据持久化与趋势追踪（P0–P4）

- **背景（真库实测）**：行情事实底座很深（`daily_quotes` 829 交易日 / `stock_price_limits` 254 交易日 **100%** 覆盖），但派生层浅且有洞——`market_sentiment_daily` 只有 21 天、`sector_moneyflow_snapshots` 13/20（**65%**，含 2026-10-08 缺失）、`market_sentiment_intraday` 6/11（**54.5%**）且只存 4 个计数（盘中池个股明细在 `upsert_intraday_snapshot` 被丢弃）；`/market/hot-boards` 完全不落库（Redis 60s）；`daily_quotes.pct_chg` 在 2026-09 之前非空率 ≈0.5%，导致 254 个限价交易日里只有 **17 天**过 `market_day_service.is_day_complete`；`reconciliation_service.DOMAINS` 只覆盖 4 个域 → 上述洞当天不告警
- **产出**：`plans/2026-10-12-market-hotspot-persistence-and-trends.md`（4 Phase / 13 Task；核心是三条落库判据 A 不可回补必当天落 / B 可回算允许回补 / C 快照型分类禁止回算，配「明确不做」清单、回滚方案与逐项验收数字），并登记 `plans/index.md`
- 涉及文件：plans/2026-10-12-market-hotspot-persistence-and-trends.md、plans/index.md、docs/Changelog.md

## 2026-09-29 - 生产内存治理：回收遗留 agent 进程 951MB + 服务器专用容器内存上限

- **背景实测**：服务器 3.8GB / **无 swap**、12 个容器**全部 `limit=0`**；`used 3011MB / available 710MB`。真正大头是宿主机上跑了 4~6 天的遗留 agent 进程（`hermes` ×2 + `pi` ×2 = **951MB**，住 tmux 会话 `code` 与 `systemd --user hermes-gateway`），与业务无关；其 `cron/executions.db` 自 Sep 22 起无执行记录，仅心跳空转
- **回收**：`systemctl --user stop hermes-gateway`（**stop 不 disable**，可恢复）+ 结束 4 个 agent 进程 → `used 3011→2060MB`、`available 710→1662MB`（**-951MB**）；保留 tmux 会话滚动缓冲便于回溯
- **容器限额**（`docker-compose.prod.yml`，服务器专用，本地不加载）：14 个服务加 `mem_limit` + `memswap_limit`（**同值**，即容器内可用 swap=0，触顶只 OOM kill 自己）—— api/scheduler/migrate 512m、worker 448m、postgres 768m、rabbitmq 320m、auth-service/auth-db/migrate-auth 256m、gateway/redis 192m、forward-auth 160m、caddy 128m、frontend 64m。口径 = 上线实测 `anon` ×2~4；**是天花板不是预留**，目的是把单服务失控的爆炸半径锁进 cgroup，而非"省内存"；已在服务器实建并用 `docker inspect` 核验 12/12 容器 `Memory` 非 0
- **静默失效的坑（已机械化拦截）**：`mem_swappiness: 0` 被 compose v5.1/v5.5 **无告警直接丢弃**（`docker compose config` 不渲染、`docker inspect` 仍为 nil），实测"写了等于没写"；正确写法是 `memswap_limit`。`scripts/doc_gate.sh` 新增**第 13 项「compose 静默丢弃键」**（黑名单可扩展 `SILENT_KEYS`），并已按门禁自约完成破坏验证：注入 `mem_swappiness: 0` → 报出 `文件:行` 并红；还原后 13 项全绿
- **口径纠正（写进文档）**：`docker stats` 的 `MemUsage` **含容器页缓存** —— 实测 postgres 596MB 中 374MB 是可回收缓存（真实 `anon` 仅 207MB，`shared_buffers` 才 32MB），据此判"内存泄漏"会误判；判真实占用看 cgroup `memory.stat` 的 `anon`
- **Swap 建议**：机器此前无任何 swap（`Committed_AS 6.6GB` vs `CommitLimit 1.9GB`，尖峰即 OOM kill 而非变慢），在 `operations.md` 给出 2GB swapfile + `vm.swappiness=10` 的标准做法（需 root，未代执行）
- 涉及文件：docker-compose.prod.yml、docs/deployment/production.md、docs/deployment/operations.md、docs/Changelog.md

## 2026-09-29 - 玄田增量门控合并 main + 容器化验证（补齐部署形态证据链）

- **合并**：`feat/xuantian-ingest-gating` → main（`afd412f`，9 提交 + final-fix）；Changelog 冲突按倒序合并（实施条目前置）；合并后全量 635 passed、app/tests 范围 ruff+mypy 绿
- **容器重建**：`docker compose build api worker scheduler` + `up -d`（新镜像 `34741d4d235e`；构建需 buildx 写 `~/.docker/buildx` 状态目录，沙箱内被拦需提权执行）；compose `migrate` 自动应用 `a9b3c7d1e5f2`；容器内核验 `scheduler force=False` / `worker force=True` 接线在位
- **容器化四态验证**（此前缺口：Task 7 只验证了宿主机直调 service 层）：worker 轨道经网关 `POST /api/v1/tasks/fetch-industry-metrics`（临时 admin 账号走真登录+CSRF，测毕已删）→ MQ → worker 容器全链路 `completed`，`stats.unchanged=79`（玄田零写入）、`revisions` 落 JSONB 不炸；scheduler 轨道（容器内 force=False）`gating=not_due` 零玄田请求；宿主机 force 直跑 `unchanged` ✓。worker 首跑曾报 `full`——Task 7 宿主机会话的 state 行未持久化所致（metrics 79 行在、state 行不在），worker 重写 state 后不再复现；运维启示：**跨进程验证 state 写入必须核对行存在，不能只看返回 dict**
- **dashboard API（网关→容器）**：`trends.sow_inventory` 返回 4 点真实月度序列（2025-07→2026-03，xuantian 3 + caaa 1 双源混存），3750 正常保有量参考线随 `effective_from` 正确挂载——多 freq 共存的查询端裁决在部署形态下成立
- 遗留：前端页面目视核对（打开 /research 工作台看 supply 卡片）仍待人工；`plans/index.md` 状态翻 completed 待目视后

## 2026-09-29 - 玄田增量门控 final review 修正：空写轮武装状态 + L1 谓词测试补真

- **MF-1 修复**（`_gated_xuantian_fetch`）：L2 哈希失配但 L3 零行可写时（DB restore / 哈希被清 / 仅 orphaned 响应）原来不写状态表 → `next_due_at` 永不推进，调度任务每天重抓重 diff 且 `last_checked_at` 冻结无告警；现在空写轮也 upsert 状态（新 `content_hash` + `next_due_at` + stats），`last_success_at` 保留原值（语义 = "最近一次有写入的成功抓取"），30 天 L1 节奏对该场景真正生效
- **MF-3 修复**：原 `test_l1_not_due_skips_fetch` 断言 `now < now+30d` 是同义反复；抽出纯谓词 `is_due(next_due_at, now, *, force)`（住 `industry_ingest_diff.py`），四态直接断言（未到期→拦 / force→绕过 / 无状态→放行 / 已过期→放行）+ 编排层行为测试（mock repo 驱动 `_gated_xuantian_fetch`：零请求短路、force 放行、MF-1 空写轮武装回归）
- 计划登记：`plans/2026-09-29-xuantian-capacity-ingest-gating.md` 头部 draft→active（实施完成待合并）+ 验收清单按 Task 7 实证勾选（前端卡片目视项未验证保持未勾）；`plans/index.md` 补登记行
- 涉及模块：backend/app/services/industry_metric_service.py, backend/app/services/industry_ingest_diff.py, backend/tests/test_industry_ingest_gating.py, plans/, docs/Changelog.md

## 2026-09-29 - 玄田增量门控 Task 7：真实 API 全链路验证 + 文档同步

- 行业投研：接入玄田数据产能通道（`XuantianClient` 直连 xt.yangzhu.vip，2009→now 能繁/猪肉产量/生猪存栏/出栏全历史落库，registry 新增 `pork_output`/`hog_inventory`/`hog_slaughter_quarterly` 三指标 + `xuantian` source）；ingest 增量门控三层（调度门/内容哈希/行级 diff，新表 `industry_ingest_state`，修订 old→new 留痕），稳定态 ingest 近零成本
- 实测（127.0.0.1:5433 真 PG + 真实 xt.yangzhu.vip API）：首抓 `gating=full`，`stats.new=79`；二跑 `gating=unchanged`（玄田零写入，state 表 `content_hash` 命中）；调度轨道 `gating=not_due`（L1 零请求）；篡改一行后 force 重抓 `gating=incremental`，`revisions` 留痕 `old=9999.0 → new=3080.0` 且自愈。DB 抽查：4 metric_key × (16 yearly + 3 quarterly) + sow_inventory 3 monthly = 79 行，min(period)=2009-12-31，ASF 锚点 2019-12-31 = 3080 ✓
- 文档同步：`docs/design/data-source.md` §二 L2 表三指标标注"已接入：xuantian 源" + 新增 `pork_output` 行、§五 能繁回补改"已完成"、§六 补玄田参考链接；`plans/industry-research-workbench.md` 产能回补项勾选（注明玄田直连取代 stats_gov CSV 通道）

## 2026-09-29 - 玄田增量门控 Task 6：三层门控接入 `ingest_industry_metrics` 主流程（集成任务）

- **门控编排**（`app/services/industry_metric_service.py`）：`_gated_xuantian_fetch(db, cfg, *, force)` 四元组 `(rows, gating, stats, revisions)` 编排三层门——L1 调度门（`next_due_at` 未到零请求，force=True 绕过）/ L2 内容门（`canonical_content_hash` 一致 → 只 `touch_ingest_state` 不动哈希水位）/ L3 行级 diff（`compute_ingest_actions` 只写 new+changed，orphaned 只计数，修订留痕）；节奏表与 `next_due` 住纯模块 `industry_ingest_diff.py`，service 只 import
- **短路条件裁定**（ponytail review 修正）：derive/signal 重算条件 = `upserted > 0`（全源零写入才跳过），**不是** `gating != "unchanged"`——玄田 unchanged 但 soozhu 日价有新行是常态，错误条件会停掉猪粮比/sow_mom 派生链；零写入时 signal 读 `latest_signal` 兜底
- **双轨接线**：scheduler `industry_metrics_refresh_job` 传 `force=False`（吃满门控）；worker `IndustryMetricsWorker` 传 `force=True`（手动兜底修订场景）；两轨仍共用同一 service 方法。返回 dict 追加 `gating` 键 +（akshare 分支）`stats`/`revisions` 键（`revisions.period` 转 ISO 字符串——worker 把 result 落 JSONB，默认序列化器不认 `date`）
- **配套件**：`repo.list_metric_rows(db, industry_key, source)` 按 industry+source 拉全量行供 L3 比对；`_as_row_dicts` ORM 行 → diff 输入 dict（键与 fetcher 标准行对齐）
- **验证**：`tests/test_industry_ingest_gating.py` 2 passed（纯逻辑：L1 next_due 单调性 + L3 padded 行增量分类）；全量 `pytest -x -q` 632 passed / 0 failed（基线 630 无回归）；`ruff check` + `ruff format --check` + `mypy app` 全绿

## 2026-09-29 - 玄田增量门控 Task 5：`_fetch_xuantian_capacity_rows` fetcher 接线 + ingest 主流程并轨

- **fetcher 接线**（`app/services/industry_metric_service.py`，service 层，client 留 providers 分层不破）：`_fetch_xuantian_capacity_rows(cfg, client=None)` 把玄田解析行按 registry 对照展开为标准 metric 行（每指标一行），`extra={"channel": "xuantian", "raw_period": ...}` 溯源；`client` 可注入假对象做纯单测（不触网）
- **防线双保险**：任何失败返回空列表不抛穿（client 已兜底，fetcher 再兜一层防注入实现抛穿）；registry 未命中的 metric_key 静默跳过（跨行业复用防线——他行业复用本 fetcher 时未定义指标不会炸）
- **ingest 并轨**：`ingest_industry_metrics` 的 akshare 分支在 caaa 行之后并入玄田行（upsert 前合并 → 四产能指标进入 `covered_metrics` → mock purge 覆盖）
- **验证**：`tests/test_industry_fetchers.py` 10 passed（新增 3：季度行 4+1 展开 / 失败→空列表 / 未注册指标跳过）；全量 `pytest -x -q` 630 passed / 0 failed；`ruff check` + `ruff format --check` + `mypy app` 全绿

## 2026-09-29 - 玄田增量门控 Task 4：行级 diff 纯函数 `compute_ingest_actions` + `canonical_content_hash` + `next_due` 节奏表

- **纯函数 diff 核心**（`app/services/industry_ingest_diff.py`，零重依赖——不 import config/settings/DB/httpx，这正是 next_due 节奏表从 service 迁入本模块的原因）：`compute_ingest_actions(fetched, existing)` 按 `(metric_key, freq, period)` 键比对，四分类 `new`/`changed`/`unchanged_count`/`orphaned_count`；value 比较先 `float()` 归一（兼容 Numeric(18,4) 回读 Decimal）再 1e-6 容差；修订行 `revisions` 留痕 `metric_key/freq/period/old/new`（官方回修幅度本身是投研信号）；orphaned（库有响应无）只计数不删除（官方数据语义不镜像上游删除）
- **内容哈希**：`canonical_content_hash(rows)` 排序消除顺序敏感 + `round(v,4)` 定点化消除 float 表示漂移，sha256 取前 16 hex——三层门控 L2 的"内容门"判据
- **源节奏表**：`_SOURCE_CADENCE_DAYS`（xuantian 30 / caaa 32 / akshare_soozhu 1 / akshare_sina 1），`next_due(source, now)` 未知源默认日更（宁多查不漏数据）——L1 调度门判据
- **验证**：`tests/test_industry_ingest_diff.py` 10 passed（含容差边界 1e-7、orphaned 不删、哈希顺序稳定性）；`ruff check` + `ruff format --check` + `mypy app` 全绿

## 2026-09-29 - 玄田增量门控 Task 3：registry 扩展 — 3 个产能 MetricDef + xuantian 源注册

- **新指标**（`app/services/industry_registry.py`，supply 分组、紧跟 `sow_inventory` 之后）：`pork_output`（猪肉产量，万吨）/ `hog_inventory`（生猪存栏，万头）/ `hog_slaughter_quarterly`（生猪出栏量，万头），均 `freq="quarterly"` + `tier=TIER_OFFICIAL` + `sources=["xuantian", "mock"]`；查询端 `_pick_row` 注册频率优先保证 latest 展示取季度行、yearly 长历史背景共存（零迁移设计前提）
- **sow_inventory 源扩展**：`["stats_gov", "caaa", "mock"]` → `["stats_gov", "caaa", "xuantian", "mock"]`——玄田为统计局口径门户镜像，排部委发布渠道 caaa 之后；`stats_gov` 保留占位（EasyQuery 直连大陆网络仍可用，后续接入优先级天然正确）
- **mock 垫底不破坏**：新增 2 测试（`test_sow_inventory_registers_xuantian_source` / `test_capacity_metrics_registered_with_official_tier`）+ 既有 `test_mock_always_last_in_registry_sources` / `test_registry_registers_caaa_source_with_mock_last` 同步更新（后者原硬编码旧源列表，随源扩展同步为四源断言）
- **新指标不配 mock_base**：mock builder 只为配置了 `mock_base` 的指标生成序列，未配置则跳过——mock 模式下无行，无害
- **验证**：`tests/test_industry_source_priority.py` + `tests/test_industry_mock_data.py` 12 passed；全量 `pytest -x -q` 617 passed / 0 failed；`ruff check` + `ruff format --check` + `mypy app` 全绿

## 2026-09-29 - 玄田增量门控 Task 2：`XuantianClient` 产能客户端 + 周期解析纯函数

- **纯函数解析层**（`app/core/providers/xuantian_client.py`）：`parse_capacity_rows` 把玄田原始 5 列行（`[周期, 能繁, 猪肉产量, 生猪存栏, 生猪出栏]`）映射为标准行 `{period, freq, values, raw_period}`；周期串三态解析（`"2009"`→年度 12-31 / `"2025年二季度（末）"`→季度末 / `"2025年7月"`→月末日），0 值列剔除（月度行的哨兵语义）、全零行丢弃、无法解析的周期 log warning 后跳过（skip 语义，单行失败不牵连整批）
- **HTTP 客户端**：`XuantianClient.fetch_capacity()` POST `xt.yangzhu.vip/data/getmapdata?ptype=7&areano=-1`（Referer/Origin/UA 头，15s 超时），非 200 code 与任何异常均 log warning 返回 None 不抛穿（与 CAAA 客户端同约定）；`get_xuantian_client()` 模块级懒加载单例
- **防上游漂移**：真实响应 fixture `tests/fixtures/xuantian_capacity.json`（2026-09-29 实机快照，22 行 = 16 年度 + 3 季度 + 3 月度）入库；快照测试锁形状 + ASF 锚点（2018→3189.0 / 2019→3080.0，数值变化即人工复核信号）
- **验证**：`tests/test_xuantian_client.py` 7 passed；`ruff check` + `ruff format --check` + `mypy app` 全绿

## 2026-09-29 - 玄田增量门控 Task 1：`industry_ingest_state` 表（模型 + 迁移 + 仓库层）

- **三层门控的锚点表落地**：新增 `IndustryIngestState` ORM（唯一键 `(industry_key, source)`，字段含 `next_due_at`/`content_hash`/`last_period`/`stats`），迁移 `a9b3c7d1e5f2`（down_revision `2614ed9a9ab4`，`alembic heads` 单头验证通过）
- **仓库层三函数**（`industry_metric_repo.py`）：`get_ingest_state`（miss → None）、`upsert_ingest_state`（整行覆写，唯一写者是 `_gated_xuantian_fetch`）、`touch_ingest_state`（内容未变只动 `last_checked_at`，哈希/水位不动）
- **测试策略**：不触真库——MagicMock/AsyncMock 打桩 AsyncSession，只锁接口形状与最小语义；真库读写验证按计划放在 Task 7 的 `alembic upgrade head` + 真实 API 全链路
- **模型注册**：`IndustryIngestState` 补进 `app/models/__init__.py`（alembic autogenerate 读 `Base.metadata`，不注册则未来 autogenerate 会把该表判为多余）
- **验证**：`tests/test_industry_ingest_state.py` 4 passed；全量 `pytest` 608 passed / 0 failed；`ruff check` + `ruff format --check` + `mypy app` 全绿
## 2026-09-29 - 玄田产能数据通道 + ingest 三层增量门控：实施计划定稿（plans/2026-09-29）

- **背景**：针对"投研系统资格"评估确认的两大缺口（旗舰产能数据为 mock、无增量语义导致全量重写），经三轮设计对话（通道选型 → DB 直存表结构 → 增量门控）定稿实施计划 [plans/2026-09-29-xuantian-capacity-ingest-gating.md](./plans/2026-09-29-xuantian-capacity-ingest-gating.md)（7 Task，TDD 分步）
- **通道选型（实测驱动）**：统计局 EasyQuery API 海外 403（UrlACL）；玄田数据 `POST xt.yangzhu.vip/data/getmapdata?ptype=7` 实测可用——一次调用返回 2009→now 产能四指标（能繁母猪存栏/猪肉产量/生猪存栏/生猪出栏）年/季/月全历史（22 行全量解析验证），原"stats_gov CSV 通道"整条废弃，CSV→API→DB 直存
- **表结构零迁移方案**：`industry_metrics` 唯一约束天然支持多 freq 混存，查询端 `_pick_row` 注册频率优先——yearly 长历史与 monthly 近端共存不破功能；registry 新增 `pork_output`/`hog_inventory`/`hog_slaughter_quarterly` 三 MetricDef + `xuantian` source（排序 stats_gov > caaa > xuantian > mock），零前端改动
- **三层增量门控（核心设计）**：新表 `industry_ingest_state`（每 industry×source 一行：content_hash/last_period/next_due_at/stats）为锚点——L1 调度门（未到期不发请求，scheduled 轨道 force=False / worker 手动 force=True 绕过）、L2 内容哈希门（规范化 sha256 一致 → 零写入零派生重算）、L3 行级 diff（new/changed/unchanged 三分类，修订 old→new 留痕进 stats jsonb）。**刻意不做"水位跳过历史"**：官方统计会修订（2023 畜禽监测样本轮换即实例），正确语义是"内容没变零成本通过、变了对症下药"；上游 orphaned 行保留只计数（官方数据不镜像上游删除）
- **防回归设计**：`test_xuantian_client.py` 内嵌 2026-09-29 实机响应快照 fixture（22 行 + ASF 锚点 2018=3189/2019=3080 断言），上游改版或数值修订时测试先红；0 哨兵（月度行未覆盖列）在解析层剔除，与既有 `0 < value` 护栏语义吻合
- 涉及模块（计划 touches，待实施）：backend/core/providers/xuantian_client, backend/models/industry_research, backend/migrations, backend/repositories/industry_metric_repo, backend/services/industry_metric_service, backend/services/industry_registry, backend/scheduler/jobs, backend/workers/industry_metrics_worker, backend/tests, docs/design/data-source.md, plans/industry-research-workbench.md

## 2026-09-28 - 投研板块改为 `research:read` 权限可见（公开行情不受影响）

- **背景**：`/research` 承载的是推断结论（周期阶段 / 买卖信号 / 仓位建议 / 标的对比 / 知识库），且 `GET /api/v1/industries` 的列表响应本身就带 `phase / signal_type / signal_date`——与「免登录行情」不同类，原状态为完全公开
- **口径定为「权限」而非「登录」**：`POST /auth/register` 对外完全开放（无邀请码/白名单/开关），新注册账号固定 `trader` —— 故"只要求登录"等于**任何人自助注册即可读投研**。而 RBAC 里 `research:read` 早已存在且定义就是本功能（`auth-data-model.md`："允许查看生猪等行业投研工作台"），授予 researcher/analyst/admin，刻意不给 trader/operator/viewer。该口径由用户拍板
- **服务端（唯一权威）**：`backend/app/api/v1/industries.py` 改 `APIRouter(dependencies=[require_permissions("research:read")])`——**router 级一处依赖覆盖全部端点、新增端点自动继承**，替代逐端点挂 7 处；匿名经内层 `CurrentUserDep` 得 401、已登录无权限得 403；写接口 `research:manage` 在其上叠加
- **前端按同一权限收敛体验**（非安全边界）：`/research*` 路由用 `RequireAuth permissions={["research:read"]}` 出 403 页（`returnTo` 回跳仍由 IsAuthenticated 分支负责）；顶部导航「投研」项对无权限账号隐藏（普通账号「没有这个功能」而非点了才报错）；公开的申万三级页复用 `GET /api/v1/industries` 渲染的投研 banner 改 `enabled: hasPermission("research:read")`，被动不发请求、不渲染；`/market/**`、`/stock/:symbol` 免登录定位不变
- **活链路授权矩阵（经网关实测）**：匿名 `GET /api/v1/industries`+`/pig/dashboard` → **401**；新注册普通账号（trader，无 `research:read`）→ **403**；researcher（`e2e_research`）与 admin（`stocks`）→ **200**；公开 `/api/v1/market/indices` → 200 未被误伤。UI 侧同验：普通账号导航无「投研」项、直连 `/research` 落 403 页（「您缺少必要的操作权限」）
- **测试**：`test_auth_guard.py::test_stock_api_endpoints_protected` 补三态——匿名 401、trader token（无 `research:read`）403、`research:read` token 非 401/403；`research.spec.ts` 凭据改由 `E2E_RESEARCH_USER/PASSWORD` 注入（**自注册拿不到权限，故不能再"注册即用"**），缺失整 spec `test.skip`，`beforeEach` 真登录 + `afterEach` 登出回收会话；`auth.spec.ts` 未登录重定向断言补 `/research`
- **e2e 踩坑（已沉淀 best-practices/testing）**：① 门禁上线后 mock 会话必然失效——只 mock `/auth/session` 而没真 cookie 时 `/api/v1/watchlists`（及门禁后的投研接口）401 → 全局 `auth:unauthorized` 清空 store → `isAuthenticated` 反复翻转（症状：布局每秒重挂、卡片/banner 永不渲染、Playwright 报 "element was detached from the DOM" 而非断言失败，极易误判为被测代码问题）；② 换权限门禁后**临时注册也没用**（默认角色被 403）。诊断手法：一次性 Playwright spec 量 `MutationObserver` 计数与 header 文案，别凭猜
- **顺带修掉一个假红**：`marketDataFace.spec.ts:121` 断言 `/\d+ 天前/` 只覆盖 `stale_days > 0` 分支，而 `FreshnessNote` 契约是 `staleDays <= 0` 时不加陈旧徽标——盘后采集写入当日资金流后（实测 `stale_days: 0`）必然假红。改为按 `stale_days` 分两个分支断言（>0 显示 / =0 必须不显示）
- **另两个存量失败用例根因查清并修复**（fixture 与真契约不符，非产品代码缺陷）：`limitUpSentiment.spec.ts:43` 环比 chip 按设计文档 §5 取 `trade_date < as_of` 的最近一点，fixture 却把 as_of 当日的日历点当"前日" → 实测渲染 +15 而断言要 +5（组件与断言同 commit 引入后再未改动 → 自提交起就不可能绿），修 fixture 为 09-05:70 / 09-08:75 自洽；`userIsolation.spec.ts:77` `GET /api/v1/watchlists` 契约是 `WatchlistOut[]`（`items` 嵌套，Stage 5 早于该 e2e 提交），mock 却返回扁平 item 数组 → 前端 `wl.items?.forEach` 静默得 0 条，修 mock 形状。两者均以"只改 fixture 即转绿"验证（337ms / 285ms，改前超时）
- **验证**：`E2E_BASE_URL=http://localhost E2E_RESEARCH_USER=... npx playwright test` 全量 **102 passed / 0 failed**（打真栈、真权限登录，跑完 Redis 活跃会话零增长）；`npx tsc --noEmit`、`ruff`、`mypy app`、`pytest tests/test_auth_guard.py` 均绿；修 e2e 前先取基线（HEAD 上该用例 570ms 通过）
- **文档**：新增 ADR 0008「投研板块读接口需 research:read 权限」（含为何不放边缘路径白名单、为何不用"只登录"、以及"仓内尚无分配角色接口"的遗留）；`features.md` 标注需 `research:read`；`frontend-e2e.md` 补凭据注入与账号准备步骤；`plans/2026-09-11-public-market-homepage.md` 加 supersede 注记
- 涉及模块：backend/api/v1/industries, backend/tests/test_auth_guard, frontend/app/layouts/MainLayout, frontend/app/router, frontend/pages/market-industry-level3, frontend/e2e(research/auth/limitUpSentiment/userIsolation/marketDataFace), docs/decisions, docs/features.md, docs/testing/frontend-e2e.md, docs/references/best-practices/testing.md, plans
## 2026-09-28 - 命令陷阱与敏感信息入库：新增两个门禁（shell_hazard_check · doc_gate 敏感串守卫）

- **背景（本次会话暴露的两类反**复**失败）**：① shell 写法陷阱 —— `set -o pipefail` + 管道末尾 `grep -q` 在大输入下因 SIGPIPE 静默失效（探测器 8 个分类全不命中却"看起来正常"）、macOS 无 `declare -A`/`timeout`/`cat -A`；② 在"脱敏"任务里把云主机实例名又写回 Changelog。前者靠人记不住，后者说明"知道要脱敏"不等于"不会复发"
- **新增 `scripts/shell_hazard_check.sh`**（接入 `self_review.sh` [2/4]，硬门禁）：4 条规则全部来自实测踩坑，跳过注释行，脚本头部写明每条规则的破坏验证方法（实测 4/4 注入均变红）
- **`doc_gate` 新增第 12 项「敏感串守卫」**：读**仓库外**清单 `~/.stock-bot/sensitive-patterns.txt`（600，9 条），对 `git grep --cached` 的暂存内容匹配；命中只打印 `文件:行`、绝不回显内容；清单若被纳入版本控制本身即判失败（避免"守卫自己泄露"）
- **按本仓库"规则进机器即从文档退场"的规矩**：`process-docs.md` 删掉 pipefail 长条目（已由 `shell_hazard_check` 接管），"已退场"表登记 3 条新接管项；同时新增 3 条**不可机械化**的纪律：因果结论必须有可证伪实测（反例：把 SSH 失败归因给 fake-IP 规则，实测证伪）、敏感任务的写入物不得复述原文、先估算检索范围再选工具（`git grep` 而非 `grep -r .`；禁止在 partial clone 上跑 `-S`）
- **验证**：两个门禁各自通过故意破坏（注入 4 类 shell 陷阱 → 全红；把清单值写入文件并 `git add` → 门禁列出位置并红），还原后 `self_review` 全绿、`doc_gate` 15 项通过

## 2026-09-28 - GitHub 历史重写：清除历史提交中的生产域名 / 服务器信息

- **原因**：上一条只脱敏了 HEAD；`git log -S` 显示敏感串仍在 **4 个历史提交**里（`feat(edge)` 引入域名、首次生产部署提交引入服务器地址），而仓库是 public。已公开的内容按"已披露"处理，故同时安排服务器侧加固
- **做法**：在 `/tmp` 的 mirror 克隆里跑 `git filter-repo --replace-text`（替换表：`deploy@<旧IP>:<旧端口>` / 旧 IP / 旧域名 / 云主机实例名 共 5 条），重写全部 refs 后强推 7 个分支 + 9 个 tag；本地仓库与 4 个 worktree 已同步到改写后历史
- **影响（协作者必读）**：**所有提交 SHA 已变化**（428 → 415：13 个"仅剩被替换内容、改完等于空提交"的提交被裁剪）；任何本地克隆需 `git fetch --force` 或重新 `clone`，否则再次推送会把旧历史带回来。本地专有分支（`chore/*`、`feat/concept-boards`、`feature/p1-financial-foundation` 等）经核查不含敏感串（均早于该两次提交）
- **遗留风险（如实记录）**：GitHub 仍保留**不可达对象**——知道旧 SHA 的人仍可通过 `api.github.com/repos/.../commits/<old-sha>` 读到（实测 200）。彻底清除需 GitHub Support 介入；由于内容此前已公开，应视为已披露
- **验证**：全新克隆后全历史 `-S` 复检 4 个敏感串均为 **0 提交、HEAD 0 命中**；远端 refs 与改写后本地逐一对齐；`main` 上的 ruleset（`deletion` + `non_fast_forward`）在强推期间临时停用并**已恢复为 active**

## 2026-09-24 - 文档系统二期（Harness 对齐 Phase 1+2）
- **权威与入口**：新增 `docs/authority.md`；`docs/index.md` 补「变更写哪里」与 first-run 链；`frontend-architecture.md` 压成转发，改前端任务指向 `architecture/frontend/ARCHITECTURE.md`
- **Tutorial**：`docs/deployment/first-run.md`；README / overview / deployment/index 链入
- **CI**：`docs-consistency` job 增加 blocking `scripts/doc_gate.sh`；`doc_gate` 增至 11 项（前端权威路由）+ 失败 `hint:` 行
- **工具**：`scripts/slop_scan.sh`（探测器可执行化，挂 `self_review.sh`）；best-practices **退场 8 条**（规则进 doc_gate/ADR/测试，登记于 `process-docs.md`「已退场」表）
- **计划**：`plans/2026-09-24-docs-system-phase2.md` completed

## 2026-09-24 - 文档系统二期复核：修 3 处“检查形同虚设”+ 归档被删除的前端旧架构
- **CI base ref 缺 `>> $GITHUB_OUTPUT`（既存隐患，二期把它变成阻断项后才致命）**：`Compute base ref` 只 `echo "REF=..."` 到 stdout，`steps.base.outputs.REF` 实为空串，于是下游 `"$BASE"...HEAD` 退化成 `...HEAD`，被 git 当 `HEAD...HEAD` **静默空 diff**（不报错！）—— 即「空白/冲突标记」「Changelog 启发式」「doc_gate 的 ADR 基准」三个步骤全部空转（之前靠 `continue-on-error: true` 掩住）。已修为写 `$GITHUB_OUTPUT`
- **`doc_gate.sh` 的 ADR 只增不改在 CI 下恒为空**：checkout 后工作区==HEAD，`git diff HEAD` 永远为空。新增 `DOC_GATE_ADR_BASE`（默认 HEAD，CI 传 PR base），并对不可解析的基准直接报错（防“传错值 → 静默变 no-op”。已用“base 含 ADR + 改正文”与“只改 status 行”两个方向验证：前者红、后者绿）
- **`slop_scan.sh` 在交互式终端会挂死**：`DIFF=$(cat)` 读继承的 tty stdin，等不到 EOF；而 `self_review.sh` 正是人手动跑的入口。已修为 `[ -t 0 ]` 时不读 stdin（改用工作区 diff），要扫任意 diff 则显式管道
- **归档被删除的旧前端架构**：`docs/frontend-architecture.md` 由 260 行直接换成 11 行转发，旧内容（shadcn/Tailwind 时代分层 + 早期测试策略）未落地到历史层；按本仓库“归档而非删除”的约定补上 [`docs/archive/frontend-architecture-tailwind-202604.md`](archive/frontend-architecture-tailwind-202604.md) 并在转发件与 `authority.md` 双向指向
- **验证**：`doc_gate.sh`（含二期新增的第 11 项前端权威路由）绿；死链 0；`docs/`+`plans/` 空文件 0；`slop_scan.sh` 在 tty 与 `/dev/null` 两种 stdin 下均不阻塞
- **首次真正跑 CI 的空白阻断项时抓到 2 处尾部空白**（`docs/index.md` / `scripts/doc_gate.sh`）：根因是 `self_review.sh` 的 `git diff --check` **只覆盖工作区与暂存区**，而 CI 阻断项用的是 `git diff --check BASE...HEAD` —— 于是“先 commit 再自检”会滑过去（与早在 CHANGED 采集里修过的同一类漏检）。已修两处空白，并把 self_review 的空白检查扩到**已提交未推范围**（`merge-base origin/main HEAD .. HEAD`），当场验证：在未修之前该新检查确实报红

## 2026-09-23 - 文档系统落地：分层知识库 + 决策记录 + 部署/测试操作层 + 文档门禁（`doc_gate.sh`）
- **改动**：按 `plans/2026-09-23-documentation-system.md` 建立五层文档体系：① 入口层 `docs/index.md`（按任务找文档）+ `plans/index.md`（20 份计划的状态表，依据只用可核对事实，推断项显式标 `unverified`）+ `plans/README.md`（文件头契约）；② 说明层 `docs/overview.md` · `docs/features.md`（每条带**代码入口**，可被机械校验）· 重写两份拼写错误的 `ARCHITERTURE.md` → `ARCHITECTURE.md`（后端补分层依赖方向/API 前缀清单/10 个队列注册表，前端补 feature-sliced 边界与 16 条路由）；③ 历史层 `docs/decisions/`（README 模板 + 7 篇追认补录 ADR + 索引，**只增不改**）· `docs/evolution.md`（v0 CLI 原型 → v7 工程门禁，每段 trigger/before→after/残骸/ADR）；④ 操作层 `docs/deployment/`（512 行 `build.md` 拆为 index/local-dev/production/data-migration/operations，`build.md` 压成 5 行转发以保历史链）· `docs/testing/`（门禁矩阵 + 后端/e2e/benchmarks，含 e2e 术语消歧）；⑤ 参考层 `best-practices.md` 286 行拆为 `best-practices/` 8 个分类（内容原样搬移）+ 写入门槛（能机械检测的必须升级为门禁并从文档删除）
- **新增门禁 `scripts/doc_gate.sh`**（挂进 `self_review.sh` [3/4] 段）：10 项检查 = ADR 只增不改 · ADR 头部与编号连续 · features 契约（页面/API 前缀/路由引用）· 门禁矩阵↔CI job 单向一致 · 端口表↔compose 宿主机映射 · plans 头部 · 文档引用的脚本与 `npm run` script 存在 · QUEUES/compose 服务名口径一致 · e2e 术语歧义（告警）· **docs/ 与 plans/ 下不得有空文件**
- **门禁当场抓到 3 个真问题**：① 我写的 `features.md`/后端架构文档把 `/api/v1/quotes|features|financials` 当成顶层前缀——实际它们挂在 `/exchanges/{exchange}/stocks/{symbol}/...` 下（已修正）；② `npm run lint` 是**死脚本**（`package.json` 声明 `eslint .` 但仓库未装 eslint、无配置文件），`AGENTS.md`/`README.md`/`build.md` 三处把它当常用命令 → 统一改为 `npx tsc --noEmit` 并落 [ADR 0006](decisions/0006-no-eslint-tsc-as-static-check.md)；③ 端口检查自身的宿主机/容器端口取值错误（已修为取映射倒数第二段）
- **空文件清理（新增第 10 项检查防复发）**：删掉 `docs/design/webpage.md`（我上一步误把 0 字节文件“并入 design/”，实际无内容可并）、`src/main.py`（0 字节，且根 `pyproject.toml` 的入口是 `src.cli.main:app`，无任何引用）；连同本轮删的 `docs/plan.md`（0 行）与 `docs/designs/index.md`，共清掉 4 个空文件。新增门禁：`docs/` 与 `plans/` 下出现 0 字节文件即失败（`.gitkeep` 例外），避免“空文件把目录伪装成有内容”
- **验证**：`scripts/doc_gate.sh` 各项均做过**故意破坏验证**（临时改 ADR 正文/删头部字段/指向不存在页面/加假 CI job/改端口/建无头部计划/引用不存在脚本或 npm script/加未登记队列 key/裸用 e2e/建空文件）——**均按预期变红**（e2e 那项为告警），还原后全绿；`bash scripts/self_review.sh` 通过
- **本计划内故意未做**（列为后续）：`plans/**` 20 份不回填头部（实测仅 4 份写明状态，给其余 16 份补 `unverified` 是噪声，改为在 index 维护）；`slop_scan.sh` / `doctor.sh` / `dev_up.sh` / 分层依赖结构测试属工程环境计划

## 2026-09-28 - 生产信息脱敏：域名 / 服务器地址 / 实例名改为占位符（本仓库为 public）
- **背景**：仓库是公开的，而 `docs/Changelog.md`、`docs/deployment/production.md` 与几个部署配置里带着**真实生产域名**、**部署账号@服务器 IP:SSH 端口**、**云主机实例名与规格**（均为历史提交遗留，非本轮文档改造引入）。已公开的内容应视为已披露，故同步在服务器侧加固（仅密钥登录 + 限制来源）——见本日“GitHub 历史重写”条目
- **改动**：`gateway/caddy/Caddyfile` 去掉真实域名默认值 → `{$QSTOCK_DOMAIN}`；`docker-compose.prod.yml` → `${QSTOCK_DOMAIN:?...}` **必填**（宁可快速报错，也不静默回落某个错域名导致签发失败/路由错站）；`.env.production.example` 用 `<你的域名>` 占位；`docs/Changelog.md`、`docs/deployment/production.md` 内全部真实值占位化
- **部署影响**：首次部署（或换机器）**必须显式设置 `QSTOCK_DOMAIN`**，未设置时 `docker compose` 会直接报错退出而不是默默用错域名；已在跑的服务器不受影响（其 `.env` 里本来就有该变量）
- **验证**：`git grep` 在 HEAD 上已无真实域名/IP/实例名；`bash scripts/self_review.sh` 与 `bash scripts/doc_gate.sh` 均通过

## 2026-09-23 - 缓存序列化换 orjson（真 Redis 实测 4.75ms/次）+ 补 `CacheClient` 边界测试
- **改动**：`backend/app/core/redis.py` 的 `CacheClient.get/set` 由 stdlib `json` 换成本仓库已声明、但**从未被任何代码使用**的 `orjson`（`pyproject.toml` 里躺着 `orjson>=3.9.0`）。真实 5550 行快照 payload（664KB）实测：`dumps` 3.757→0.894ms、`loads` 3.094→1.210ms，**一次缓存往返省 4.75ms**；该 payload 冷 178ms / 热 7.2ms，且被 4~6 个端点共用。零接口变动、零调用方改动
- **评审发现并修掉的 3 处语义分歧**（全部实测，非推断）：① `orjson.dumps` 对非 str dict 键（int/bool/None）抛 `TypeError`，而 stdlib 会静默字符串化 → 加 `OPT_NON_STR_KEYS` 恢复奇偶性；② orjson 只支持 64-bit int（stdlib 任意精度），`2**64` 抛 `TypeError`；③ **上述异常都不在原 `except` 里**（只捕网络错）→ 会从 `cache.set` 冒泡成 500，且失败点在“数据已查出来之后”。加 `except TypeError`（实测 `orjson.JSONEncodeError is TypeError`，一个 catch 覆盖全部编码失败模式，含循环引用），并把这层契约从“Redis 不可用优雅降级”扩写为“**序列化失败同样降级**”写进类 docstring
- **NaN 兼容性（如实记录，不美化）**：stdlib 写 `NaN`/`Infinity`（非法 JSON），orjson 写 `null` → 读回 `None`，类型 `float → NoneType`，这是本次**唯一的静默语义变化**，已由新测试显式钉死；旧 Redis 里 stdlib 写下的 `NaN` 值会被 orjson 严格拒绝 → 走既有 `JSONDecodeError` 分支降级为 miss，最长 TTL 3600s 内自愈。另实测真实快照 payload 经 orjson 严格解析通过，确认无 NaN/Infinity 落盘
- **补测试（该边界改前零覆盖）**：新增 `backend/tests/test_cache_client_serialization.py`（6 例，手写 10 行 redis 替身，**不引入 fakeredis**）。原因：仓里 ~20 个缓存替身直接存取 Python 对象、**绕过 `CacheClient.get/set`** 因而从不过 orjson；`test_market_snapshot.py` 里 `json.loads(json.dumps(payload))  # the real CacheClient boundary` 模拟的是 stdlib 而非真 CacheClient —— 即改前跑全量 pytest 对该改动近似零覆盖。变异验证：临时摘掉 `OPT_NON_STR_KEYS` 与 `except TypeError` → **3 例转红**，还原 → 全绿
- **验证**：默认门禁 598→**604 passed**；`ruff check` + `ruff format --check` + `mypy app`（169 文件）通过；`scripts/self_review.sh` 通过；**真实 E2E**（复用已在运行的栈：真 postgres 5579 股票 / 4,383,507 行 `daily_quotes` + 真 Redis 6380）：`-m e2e` **28 passed / 21 failed**，与改动前基线失败集**逐字节相同**（21 个失败全是 `httpx.ConnectError` —— `tests/conftest.py` 的 `API_BASE_URL` 默认 `http://host.docker.internal:8000` 在宿主机不解析，属既有环境问题，非本次引入，已如实披露而非跳过）；另跑真实冷热往返探针：5550 行 cold 178ms → warm 7.2ms、逐行 dict 等值、**删键后回落到 69ms**（证明热值确实来自 Redis 而非进程内残留）
- **重建并切换 api 后在生产形态下实测**（同 payload 605,598 字节；旧/新镜像并排 A/B）：微基准 `CacheClient.get` p50 **4.000 → 2.342 ms（-41%）**、`load_day_rows` warm p50 **8.864 → 7.531 ms（-15%）**；冷写路径（取行+dumps+set）p50 **70.72 → 67.69 ms（-3.03ms）**；**真实 HTTP 层**：强制端点缓存 miss + 快照缓存 hit 时 `/api/v1/market/distribution` p50 **11.46 → 9.07 ms（-20.9%）**，而端点缓存命中（423 字节小 payload，**最常走的路**）两者无实质差异（0.56 vs 0.62 ms）—— 即收益只落在“需要读那份 605KB 快照”的请求上，不夸大、不虚构
- **构建阻塞与解法（环境事实，供后续参考）**：`docker compose build api` 直连 `registry-1.docker.io` 返 EOF（本机为 **OrbStack** 且 `registry-mirrors` 为空，而 Dockerfile 注释写的是 Docker Desktop 路径 —— 文档/环境不一致，建议另行修正）。因两个 `FROM` 的 exact-digest 基础镜像（`python:3.13-alpine@1a63a539…` 与 `ghcr.io/astral-sh/uv@66cfb8e0…`）本地均已存在且都是 arm64，故改用 `/tmp` 下的临时 Dockerfile（**仅去掉 `@sha256:` 固定，其余行逐字相同**）+ `--pull=false` 构建；基础层全部 CACHED（层 ID 未变，产物等价），**未修改任何 tracked 文件、未重启 Docker**；产物已核验 arm64 / version label 一致 / `redis.py` 含 orjson
- **本轮同时撤回一条自己提错的方案**：曾提议“排序路径缓存排好序的全集”，实测 `StockEnrichedOut` 序列化 751 字节 → 2300 只 = **1.73MB**，且排序键含用户可控 `keyword`（键空间无界），比 `best-practices.md:80` 已主动砍掉的 1.2MB 更大 → 撤回，未实施
- 涉及模块：backend/app/core/redis.py, backend/tests/test_cache_client_serialization.py, docs/*

## 2026-09-21 - 修前端容器「Up 但 502」：官方 nginx 的 entrypoint 脚本卡死 + 补 healthcheck
- **现象**：把服务器 frontend 切到 0.0.8 后站点 **502 Bad Gateway**；容器 `docker ps` 仍显示 Up，Traefik 日志报 `ServiceAddr 172.18.0.12:80`（旧容器 IP）。实测容器内 nginx **根本没在 listen**：进程树里 `10-listen-on-ipv6-by-default.sh` 的两个 `sh` **卡死不退**，`nginx` 从未被 exec。即：官方 `nginx:alpine` 的 docker-entrypoint 脚本 + 我们自定义的 `default.conf` 组合下会挂死（旧 SWR 单架构 nginx 没这问题 → 换官方源后暴露）
- **修复**：frontend Dockerfile 加 `ENTRYPOINT []`（我们的 nginx 配置完全静态，不需要 envsubst/ipv6 改写/worker 调优），启动路径只剩「起 nginx」；并给 compose 的 `frontend` 服务加 **healthcheck**（`wget http://127.0.0.1/`）——此前「容器 Up 但服务不可用」可以静默存在几小时，现在 `docker ps` 会直接显示 unhealthy。本地用官方 nginx 基础镜像实建实跑验证：容器起来即 listen :80 且 serving 正确 bundle
- **应急恢复（记录在案）**：线上先用 `docker exec frontend nginx` 手工拉起 nginx 恢复 200，再走正规镜像修复；Traefik 对「被删除容器的旧 IP」需要重启 gateway 才会重新发现
- **生产验收**：`https://<生产域名>` → 200；真实浏览器点「短线情绪 → 盘中」**无渲染错误**、标题「盘中板块（东财口径）」、列名「东财板块」、50 行渲染；行名暂显 `--`（后端仍是 0.0.5，缺 `board_name`，等 backend:0.0.8 落地后自动显示东财板块名）
- 涉及模块：frontend/Dockerfile, docker-compose.yml, docs/*

## 2026-09-21 - 修 CD 前端 arm64 腿 EBADPLATFORM（换官方多架构基础镜像后暴露）
- **问题**：v0.0.7 的 CD 在 `Build and push frontend` 失败：`npm error code EBADPLATFORM / Unsupported platform for @rollup/rollup-linux-x64-gnu: wanted cpu x64, current arm64`。根因是 frontend/Dockerfile 里写死的 `npm i @rollup/rollup-linux-x64-gnu --no-save` —— 在**旧 SWR 单架构基础镜像**下 arm64 腿其实跑的是 amd64 userland（QEMU），所以能装上；换成官方**真多架构** `node:22` 后，arm64 腿是真实 arm64，装 x64 专属包必然失败。即：换官方源把「多架构名不副实」这个隐藏前提摆到了台面上（本地 arm64 机用镜像源前缀的临时 Dockerfile 已 1:1 复现该失败）
- **修复**：改为按 `uname -m` 选择平台包（`x86_64 → @rollup/rollup-linux-x64-gnu`、`aarch64 → @rollup/rollup-linux-arm64-gnu`，其它架构跳过），保留"补装 rollup 原生二进制"的原意；arm64 本地实建通过并核验产物（dist/index.html + assets）
- **教训**：凡是硬编码平台/架构的构建步骤，在基础镜像从单架构换成多架构时都会立刻爆掉；"CI 双平台都成功"曾经只说明两腿用的是同一个（amd64）基础层
- 涉及模块：frontend/Dockerfile, docs/*

## 2026-09-21 - 修盘中口径崩溃 + 板块卡改东财口径；基础镜像换官方源
- **线上故障（用户报「切盘中页面崩溃、拿不到数据」）根因**：`mode=intraday` 时板块端点按后端契约返回 `l1_code/l3_code/l3_name/l1_name` **全为 null**（东财涨停池不映射申万），而前端 `SwL3LimitUpBoard.tsx` 的排序比较器里 `a.l3Code.localeCompare(b.l3Code)` 无空值保护 → `TypeError: Cannot read properties of null` → 当时只有全局 ErrorBoundary，于是**整个短线情绪 tab 被替换成「页面渲染出错」**。四个端点实测全部 200，属纯前端渲染异常
  - **第二处缺陷**：`SectorLimitUpItemOut` 少了 `board_name` 字段，把后端 `_sectors_by_board_name` 已经算好的东财板块名在序列化时丢掉了 → 盘中卡片即使不崩也只能显示 `--`
  - **第三处（测试为何没拦住）**：`limitUpSentiment.spec.ts` 的 `routeSentiment` 对 `sector-limit-up` **恒返回收盘 payload**，盘中用例实际喂的是 SW 有值的数据 → 组件的 null 分支从未被任何用例执行
- **修复**：① 后端 `SectorLimitUpItemOut` 增加 `board_name: str | None`，盘中板块名正式上线（`test_intraday_endpoint.py` 增加「盘中每项必须有 board_name」断言，schema 层与快照层各一条）；② 前端类型改说实话 —— `SectorLimitUpItem.l3Code: string → string | null`、新增 `boardName`、`source` 增加 `"eastmoney_intraday"`：这样**任何未做空合并的写法都会在 `tsc` 阶段报错**（机制性防复发，而非靠记性）；③ `SwL3LimitUpBoard` 统一走 `rowKey/rowLabel`（`l3Code ?? boardName ?? leaderSymbol`）做排序键、React key 与行标题，并按 `data.source` 把列名/空态在「细分行业（申万）」与「东财板块」间切换；④ 卡片标题随口径切换为「盘中板块（东财口径）」；⑤ `ErrorBoundary` 支持 `fallback`，短线情绪 tab 五张卡各自包一层 **卡级降级**（一张卡渲染失败 = 一条告警 + 重试，不再整页白屏）
- **测试与反证**：新增 e2e「盘中口径板块卡按东财口径渲染且不崩」，夹具按真实契约（SW 全 null + board_name），且**前两条故意在 `(max_streak, zt_count)` 上并列**以强制走到名称比较子句 —— 首版夹具是 5板/2家 vs 2板/3家，注入原缺陷后测试**依然全绿**（数值子句短路，null 分支根本没执行）；改并列后注入缺陷 → **测试变红**，修复版 → 绿。`routeSentiment` 也改为按 mode 返回真实 payload，杜绝「缺 mock 掩盖缺陷」
- **基础镜像换官方源**（用户要求「不指定 SW，和业内一致」）：四个 Dockerfile 的 8 处 `FROM` 与 prod override 的 caddy 全部改为官方上游 `python:3.13-alpine` / `node:22` / `nginx:alpine` / `ghcr.io/astral-sh/uv:python3.13-alpine` / `caddy:2-alpine`，digest 从官方源重新取值并保留固定（官方 digest 的 index 同时含 amd64/arm64 → 修掉此前「arm64 产物名不副实」的隐患）。基建镜像（traefik/postgres/redis/rabbitmq）按用户要求**未动**，仍走 SWR
- **验证**：后端 598 passed + `ruff check` + `ruff format --check` + mypy 169 文件通过；前端 `tsc -b` + `build` 通过；e2e `limitUpSentiment`+`marketTrust`+`conceptBoard` **30 passed / 1 failed**，该 failed 为**存量**（`+5` 环比 chip 断言与实际 +15 不符，stash 后在干净树上同样失败，已单独记录）；真实浏览器在本地栈点「盘中」：无错误边界、标题为「盘中板块（东财口径）」、列名「东财板块」、行名来自 `board_name`（家居用品/出版/医疗服务），切回收盘回归正常
- 涉及模块：backend/{app/schemas/limit_up.py, tests/test_intraday_endpoint.py}, frontend/src/{shared/api/limitUp.ts, shared/ui/{ErrorBoundary,index}.ts, features/market/components/SwL3LimitUpBoard.tsx, pages/market/index.tsx}, frontend/e2e/limitUpSentiment.spec.ts, backend/Dockerfile, auth-service/Dockerfile, forward-auth/Dockerfile, frontend/Dockerfile, docker-compose.prod.yml, docs/*

## 2026-09-21 - 基础镜像按 digest 固定（治「每次发版重下 300MB」）+ 服务器切到 v0.0.5
- **问题**：v0.0.5 在服务器上拉取耗时 **14 分钟**，因为四个 Dockerfile 的基础镜像用的是浮动 tag（`python:3.13-alpine`/`node:22`/`nginx:alpine`/`uv:python3.13-alpine`），上游补丁版本一变，`uv pip install` 出来的依赖层 digest 就整层失效，服务器只能重下整套 site-packages（~300MB）
- **改动**：四个 Dockerfile 的 8 处 `FROM` 与 `docker-compose.prod.yml` 的 caddy 镜像全部改为 `image:tag@sha256:<manifest digest>`（2026-09-21 取值），并在文件与 `docs/build.md`「镜像构建」里写明取新 digest 的命令；顺带把 caddy 也固定
- **发现的既有问题（如实记录）**：华为 SWR `ddn-k8s` 源里的 `python:3.13-alpine`/`node:22`/`nginx:alpine` **只有 amd64** —— buildx 双平台构建时给出 `InvalidBaseImagePlatform: ... expected linux/arm64` 警告并继续用 amd64 基础层 + QEMU 产出 `arm64` 镜像，即**发布的 arm64 产物是名义多架构，真实 arm64 机器上会 `exec format error`**。本服务器为 amd64 故不受影响；要支持 arm64 需换基础镜像来源。已写入 `docs/build.md` 警示
- **验证**：pinned digest 下 amd64/arm64 两种 `--platform` 构建均通过（arm64 仅报上述既有警告，与改动前一致 → 不会让 CI 变红）；本地 `docker compose build api auth-service forward-auth frontend` 四镜像重建成功；服务器侧 `v0.0.5` 切换完成（`/health` = `v0.0.5 @ f3e83ad`、`env=production`，12 容器全 healthy）
- 涉及模块：backend/Dockerfile, auth-service/Dockerfile, forward-auth/Dockerfile, frontend/Dockerfile, docker-compose.prod.yml, docs/*

## 2026-09-21 - Caddyfile 改目录挂载（修「改了配置不生效」）+ 生产 HTTPS 全链路验收
- **踩坑与修复**：Caddyfile 原先以**单文件** bind mount 挂进容器；用 tarball 就地更新（`tar xzf` 替换 inode）后，容器里 **仍是旧 inode 的内容** —— 表现为 `caddy reload` 回 `config is unchanged`、HSTS 死活不出现（实测容器内 `grep` 计数为 0，宿主侧为 1）。修复：Caddyfile 移到 `gateway/caddy/Caddyfile` 并**挂目录** `./gateway/caddy:/etc/caddy:ro`，从此宿主侧替换文件即时可见；同时把"改完如何生效"写进 `docs/build.md`（`docker exec caddy caddy reload --config /etc/caddy/Caddyfile`，热加载不断连、不重签证书）
- **生产验收（全部实跑，外部视角）**：`https://<生产域名>/` → **HTTP/2 200** + `alt-svc: h3`（HTTP/3 已通告）+ `strict-transport-security: max-age=31536000`；HTTP → **308** 跳 HTTPS；证书 **Let's Encrypt**（`CN=YE2`，`Sep 21 → Dec 20 2026`，自动续期）；`/market/concept/BK0501`、`/api/v1/concepts`、`/api/v1/new-stocks`、`/api/v1/market/hot-boards` 全 200，`/auth/session` 匿名 401、`/auth/csrf` 200；Traefik access log 里 `ClientHost` 与 `XFF` 均为真实客户端公网 IP（`trustedIPs` 生效，未退化成 Caddy 容器 IP）
- **追加**：`APP_ENV=production` + `AUTH_COOKIE_SECURE=true` 已在服务器生效（auth-service 生产强校验通过并正常启动）；`CORS_ORIGINS=https://<生产域名>`；证书材料在卷 `stock-bot_caddy_data`（重建 caddy 后实测未重签，`obtaining certificate` 计数为 0）
- 涉及模块：gateway/{caddy/Caddyfile, dynamic/middlewares.yml}, docker-compose.prod.yml, docs/{build.md,ARCHITECTURE.md}, docs/*

## 2026-09-21 - HSTS 归属修正：从 Traefik 移到 Caddy（边缘层）
- **问题**：把 `stsSeconds` 加在 Traefik 的 `sec-headers` 中间件上**不生效** —— 实测 HTTPS 响应无 `strict-transport-security`。原因：TLS 由 Caddy 终结，Traefik 侧看到的是明文 HTTP，而其 STS 逻辑以「本层是否 TLS」为判据；备选 `forceSTSHeader: true` 更糟，它会在本地开发 `http://localhost` 上也发 HSTS，把开发机浏览器钉到 HTTPS（自伤）
- **改动**：HSTS 移到 `gateway/Caddyfile` 站点块内（`header Strict-Transport-Security "max-age=31536000"`，不含 includeSubdomains —— 该域名下还要挂其他站点）；`gateway/dynamic/middlewares.yml` 移除无效配置并留注释说明为什么不在这里；`docs/build.md`「边缘层」耦合表补一行
- 涉及模块：gateway/{Caddyfile,dynamic/middlewares.yml}, docs/{build.md,Changelog.md}

## 2026-09-21 - 生产 HTTPS 上线：边缘层 Caddy（自动签发）+ Traefik 仍作路由/鉴权面（两跳）
- **拓扑**：`Internet :80/:443 → caddy（TLS 终结，自动签发/续期）→ gateway:80（Traefik：路由/forward-auth/限流/安全头）→ api|frontend|auth-service`。选择**两跳**而非用户最初设想的「Caddy → Nginx → Traefik」三跳：Nginx 在本栈里的角色已由 `frontend` 容器内的静态服务占据，多一跳只增加 XFF/真实 IP 传歪的机会，收益为零
- **改动**：① 新增 `gateway/Caddyfile`（站点块 `{$QSTOCK_DOMAIN}` + `reverse_proxy gateway:80`，域名为 `<生产域名>`；含"再加站点"与"抽成独立 edge 项目"的说明）；② `docker-compose.prod.yml` 新增 `caddy` 服务（SWR 镜像源、80/443/443-udp、Caddyfile 挂载、`caddy_data`/`caddy_config` 卷、healthcheck），并用 **`ports: !override []`** 把宿主机 80 从 gateway 手上整体转交（追加会变成两个映射抢同一端口；该语法需 Compose ≥2.24，服务器实测 5.5.1 与本地均支持）；③ `gateway/traefik.yml` 加 `entryPoints.web.forwardedHeaders.trustedIPs`（compose 网段 + Docker Desktop 网段）—— 不配这一段，真实客户端 IP 会在审计/限流里退化成 Caddy 容器 IP；④ `middlewares.yml` 开 HSTS（1 年、不含 includeSubdomains，域名下还有别的站点）；⑤ `.env.production.example` 加 `QSTOCK_DOMAIN`/`ACME_EMAIL` 与 TLS 上线检查清单；⑥ 文档同步（`docs/build.md` 端口表拆「本地开发 / 服务器」两张 + 新增「边缘层」小节讲三处耦合与排障命令、`docs/ARCHITECTURE.md` 服务表与启动顺序）
- **本地开发不受影响**：`caddy` 与端口转交只存在于 prod override，`docker compose up`（base）仍由 gateway 提供 `:80`，实测 base 渲染仍为 `80:80`、无 caddy 服务
- 涉及模块：gateway/{Caddyfile,traefik.yml,dynamic/middlewares.yml}, docker-compose.prod.yml, .env.production.example, docs/{build.md,ARCHITECTURE.md}, docs/*

## 2026-09-21 - 首次生产部署：腾讯云服务器（tag v0.0.2）+ 本地数据全量迁移
- **服务器**：`deploy@<生产服务器>:<SSH端口>`（<云主机实例名> / Debian 12 / x86_64 / <规格>），Docker 29.8.1 + Compose v5.5.1，全新环境（无容器/无卷/端口空闲）
- **发布**：打 tag **v0.0.2** → CD 发布 4 个镜像到 ghcr（仓库为 public → 服务器**匿名即可拉取**，实测 `docker manifest inspect` 与 `compose pull` 均无需登录；4 镜像合计拉取 14 分钟）
- **取源码**：服务器 `git clone` 被墙（`HTTP/2 stream not closed` / `GnuTLS recv error -110`），改用 **codeload tarball**（实测 1MB/s）拉 `refs/tags/v0.0.2` 解包 —— 副产品：tarball 天然不含 `.env`/未入库的 `docker-compose.override.yml`，正好避免把本机环境假设带上服务器
- **数据迁移**：本地 `pg_dump -Fc` 501MB + auth 库 23KB → scp（约 1.3MB/s）→ `pg_restore` 2m33s。**先恢复数据再起应用**（顺序铁律）：`data_init` 的覆盖度检查只补了 **19 只新股 / 12,446 行 upsert / 0 失败**（约 15 秒），而非全量 3 年回填 —— 若空库直接起应用会烧一轮 TuShare 额度
- **对账一致**：`daily_quotes` 4,377,954 / `stocks` 5,579 / `concept_boards(active)` 504 / `concept_members` 71,928 / `index_dailies` 16,979 / `daily_basic_indicators` 1,960,362 / `financial_metrics` 3,567,191 / `stock_price_limits` 1,345,177，`alembic_version=2614ed9a9ab4`（migrate 自动 no-op）
- **验证**：外部访问 `http://<生产服务器>/` → 200（含 `/market`、`/market/concept/BK0501`、`/market/hot-sectors/concept` 等前端深链接与 `/api/v1/concepts*`、`/api/v1/new-stocks`、`/api/v1/market/hot-boards` 全部 200，`/auth/session` 匿名 401 符合预期）；BK0501 KPI 与本地一致（9 涨停 / 4 连板 / 龙头锡华科技），`/health` = `v0.0.2 @ d109af9`；对外监听面**只有 gateway 的 80**（5433/6380 仅回环）；13 个容器全 healthy / 无 error 日志
- **与生产模板的两处有意偏差**：`APP_ENV=development` + `AUTH_COOKIE_SECURE=false`（暂无 TLS；production 模式会 fail-fast，而 Secure Cookie 在 HTTP 下会导致登录态丢失）。JWT 密钥对、`INTERNAL_API_TOKEN`、RabbitMQ 口令均为服务器上 `openssl` 生成的随机值；`TUSHARE_TOKEN` 经 stdin 从本地 `backend/.env` 管道写入，未出现在命令行/日志里。**待办：域名 + TLS 后翻回 production 模式**
- 涉及模块：无仓库文件改动（部署动作）；tag v0.0.2；服务器 ~/stock-bot（tarball 解包）+ ~/full.dump

## 2026-09-21 - 收敛对外暴露面：postgres/redis 只绑回环，对外只剩 gateway
- **问题**：`postgres` 绑 `0.0.0.0:5433`、`redis` 绑 `0.0.0.0:6380` —— 任何能访问服务器网络的人都可直连业务库（默认口令 `stock_user/stock_pass`）与无认证 Redis；网关侧的鉴权（`/api` 走 forward-auth）因此形同虚设：绕过 80 端口即可长驱直入
- **改动**：① `docker-compose.yml` 两处 `ports:` 改为 **`127.0.0.1:5433:5432` / `127.0.0.1:6380:6379`**（宿主机上本地开发与 `pytest` 的真 PG 测试照常，外部不可达）；② 顺带把遗留的 `backend/docker-compose.yml`（未被任何现行文档引用的独立开发编排）同样改回环并加 `[LEGACY]` 头注 —— 它此前还漂移成 `redis 6379:6379`；③ 文档同步：`docs/build.md` 端口表 + 上线清单第 2 条、`docs/ARCHITECTURE.md` 服务表（gateway 标注为「对外唯一入口」）、`.env.production.example` 的「其他配置放哪里」
- **核验**：`docker compose ps` 仅 gateway 显示 `0.0.0.0:80`；`netstat` 只见 `127.0.0.1.5433/6380`，从本机 LAN IP 连接被拒；重建 `postgres`/`redis` 后 `api`/`worker`/`scheduler` 重新连上并 healthy、`http://localhost/` 200、`/health` 正常；`backend/tests/test_intraday_snapshot_table.py`（连 `localhost:5433`）仍可跑
- 涉及模块：docker-compose.yml, backend/docker-compose.yml, docs/{build.md,ARCHITECTURE.md}, .env.production.example, docs/*

## 2026-09-21 - Docker 镜像瘦身（backend −162MB）+ 生产版 env 模板（两份）
- **镜像优化（`74d9139` / `c3be7a5`）**：① runtime 只白名单拷贝 console script（backend/auth-service 拷 `uvicorn`+`alembic`，forward-auth 仅 `uvicorn`）——此前整目录拷贝把 builder 的 `uv`(29.5MB)/`uvx`/`pip` 一并带进 runtime，且 builder 的 `/usr/local/bin` 还夹带整套 python 工具链；② runtime 删 `pip`；③ `PYTHONUNBUFFERED=1` / `PYTHONDONTWRITEBYTECODE=1` / `TZ=Asia/Shanghai`（只影响日志时间戳，业务时区仍在代码里显式 `ZoneInfo`）；④ 四个镜像加 OCI labels + `ARG VERSION/COMMIT/BUILD_DATE`，CD 传 `github.ref_name`/`github.sha`；⑤ 服务级 `.dockerignore` 补齐本地缓存与产物（`.mypy_cache`/`.pytest_cache`/`.ruff_cache`/`dist`/`e2e`/`test-results`/`Dockerfile`/`docker-compose.yml`…）；⑥ 新增 `/health` 回报 `version`/`commit`（`app/config.py` 的 `APP_VERSION`/`APP_COMMIT`）。实测体积：backend **513→351MB**、auth-service 163→131MB、forward-auth 112→80.6MB、frontend 49.6MB 不变；`migrate`/`api`/worker/scheduler 全部复跑正常
- **生产 env 模板（本轮）**：新增根 `.env.production.example`（只列 compose `${VAR}` **真正消费**的 10 个键 + 上线清单 + 三条自证命令）与 `backend/.env.production.example`（`TUSHARE_TOKEN`/业务侧 `APP_ENV`/`CORS_ORIGINS`/连接池 + 自证），`.gitignore` 加白名单；`docs/build.md`「服务器部署」改用生产模板，并新增「镜像构建（做了哪些优化）」与两条上线必办项（数据库口令硬编码在 compose、5433/6380 需防火墙关闭）
- **订正**：早前 env 条目里「`AUTH_TRUST_FORWARDED_FOR` 回落为空 → 审计日志记 Traefik IP」是错的 —— compose 默认 `true`；根因是自证命令误用**宿主侧键名**去查容器内变量（容器内叫 `TRUST_FORWARDED_FOR`，另有 `AUTH_JWT_PRIVATE_KEY_PEM→JWT_PRIVATE_KEY_PEM`、`AUTH_COOKIE_SECURE→COOKIE_SECURE`）。文档自证命令已按容器内键名改正，并把 `APP_ENV` 需在**两个**文件同时设（auth-service 读根 `.env`，api/worker/scheduler 只读 `backend/.env`）写进模板
- **验证**：四镜像重建后 `uvicorn --version`/`alembic --version`/`import app.main` 正常、`/usr/local/bin` 已无 uv/uvx/pip；强制重建栈后 `migrate` 退出 0、`backend_api` healthy、`http://localhost/` 200、`/health` 返回 `{status,env,version,commit}`；backend 598 passed + `ruff check` + `ruff format --check` + mypy 绿；前端 `tsc -b` + `build` 绿
- 涉及模块：backend/{Dockerfile,.dockerignore,app/config.py,app/main.py,tests/test_health.py}, auth-service/Dockerfile, forward-auth/Dockerfile, frontend/{Dockerfile,.dockerignore}, .github/workflows/cd.yml, .env.production.example, backend/.env.production.example, .env.docker.example, .gitignore, docs/*

## 2026-09-21 - 修正 Docker 部署的 env 示例落位：根 `.env`（compose 插值）与 `backend/.env`（运行时）必须分开
- **问题**：示例文件本身是有的（根 `.env.docker.example` 36 键 + `backend/.env.example`），但文档与 AGENTS 一直教「`cp .env.docker.example backend/.env`」——而 compose 的 `${VAR}` 插值**只读项目根目录 `.env`**，`auth-service`/`forward-auth` 的密钥正是走插值注入。照文档做，`AUTH_JWT_PRIVATE_KEY_PEM`/`INTERNAL_API_TOKEN`/`AUTH_COOKIE_SECURE`/`APP_ENV`/`RABBITMQ_DEFAULT_*` 全部回落默认值：生产表现为「auth-service 跳过 HTTPS+Secure-Cookie 强校验、JWT 私钥每次重启轮换（已签发断言失效）、内部调用不带 `X-Internal-Token`」。本机活栈实测即为此状态（根 `.env` 不存在 → `docker exec auth_service` 里 `JWT_PRIVATE_KEY_PEM`/`INTERNAL_API_TOKEN` 为空、`COOKIE_SECURE=false`、`APP_ENV=development`）
  - **订正（同日复查）**：本条初版把 `AUTH_TRUST_FORWARDED_FOR` 也列为「回落为空 → 不信任 XFF → 审计日志记 Traefik IP」，**这是错的**：compose 给它的默认值是 `true`，容器内实测 `TRUST_FORWARDED_FOR=true`。根因是自证命令拿**宿主侧键名**去查容器内变量（容器内叫 `TRUST_FORWARDED_FOR`，另有 `AUTH_JWT_PRIVATE_KEY_PEM→JWT_PRIVATE_KEY_PEM`、`AUTH_COOKIE_SECURE→COOKIE_SECURE`），于是把「已生效」误判成「没配」。文档自证命令已按容器内键名改正
- **改动**：① `docs/build.md`「环境变量配置」重写为两文件对照表（谁读它 / 放什么）+「照旧做法会掉进什么默认值」清单 + 启动后自证命令（`docker compose config | grep` 与容器内变量自查）；② 「服务器部署」步骤同步改为 `cp .env.docker.example .env` + `cp backend/.env.example backend/.env`；③ `backend/.env.example` 补上缺失的 `TUSHARE_TOKEN`；④ AGENTS.md 部署约定同步
- **验证**：文档里的自证命令与活栈实测输出一致（`APP_ENV=development`、`AUTH_JWT_PRIVATE_KEY_PEM=<empty>`、`INTERNAL_API_TOKEN=""`、`AUTH_TRUST_FORWARDED_FOR` 缺省）
- 涉及模块：backend/.env.example, docs/build.md, AGENTS.md, docs/*

## 2026-09-21 - 修 CI 后端 lint 红：`ruff format` 违规 + self_review 改动集覆盖已提交提交
- **问题**：推 main 后 CI 的 `Lint (backend)` 红 —— `ruff format --check app/ tests/` 报 `app/services/concept_service.py` 需重排（合并时 follow-up 加的 `_latest_complete_quote_day` 签名跨行）。本地之所以"全绿"：`self_review.sh` 只采集**工作区**改动，而当时已在 commit **之后**跑，改动集为空 → 整段 lint 跳过并打印绿勾（`ruff check` 与 `ruff format --check` 是两件事，前者一直通过）
- **改动**：① `backend/app/services/concept_service.py` 按 formatter 重排（纯格式，4 行）；② `scripts/self_review.sh` 的改动集改为 **工作区 ∪ `origin/main..HEAD`**，commit 与否都覆盖，避免同类静默跳过
- **验证**：`uv run ruff format --check app/ tests/`（237 files already formatted）+ `uv run ruff check app tests` 绿；CI 复跑绿
- 涉及模块：backend/app/services/concept_service.py, scripts/self_review.sh, docs/*

## 2026-09-21 - 发布链路打通：CD 补 forward-auth 镜像 + 服务器 prod override 与数据迁移文档
- **问题**：已发布镜像这条路此前根本走不通——① `.github/workflows/cd.yml` 只发 3 个镜像，`forward-auth` 缺失，registry 部署起不来 `forward_auth`；② 触发条件是 `tags: ["v*"]`，与仓库的发布约定（git tag 带 `v`、镜像 tag 不带 `v`）没有写进文档；③ `docker-compose.yml` 每个应用服务都硬编码 `image: stock-bot-*:local` + `build:`（`frontend` 更是只有 `build:`），服务器没有 override 就必然本地构建；④ 服务器侧部署/数据迁移文档完全缺失
- **改动**：① `cd.yml` 新增 `IMAGE_NAME_FORWARD_AUTH` env + metadata + build-push 三个步骤（与其余三个同形，平台/registry/缓存不动），共 **4** 个镜像；② tag filters 保持 `["v*"]` 并在注释/文档里钉死命名约定：**git tag 带 `v`（`v0.0.1`）→ 镜像 tag 不带 `v`（`0.0.1` + `0.0` + `<short-sha>`）**，依据是 `metadata-action` 的 loose 语义（源码 `semver.parse(v,{loose:true})`，本地用 `semver.clean('v0.0.1')==='0.0.1'` 复现）、裸数字 git tag 刻意不触发 CD（同版本只允许一种写法）；③ 新增 `docker-compose.prod.yml`：8 个应用服务（`migrate`/`migrate-auth`/`auth-service`/`forward-auth`/`api`/`worker`/`scheduler`/`frontend`）显式 `ghcr.io/zuixi/stock-bot/{backend,auth-service,forward-auth,frontend}:${IMAGE_TAG:?…}` + `pull_policy: always`，基建/卷/网络/healthcheck/env 一律不动；④ `docs/build.md` 新增「服务器部署（发布镜像 + 数据迁移）」：发布流程（含 GHCR 凭据与 SWR 转推兜底）、`pull` + `up -d --no-build`、实测数据迁移（全量 501 MB/45 s，精简 264 MB/28 s，恢复 71 s，对账 6 张表 + `alembic_version=2614ed9a9ab4`）、`data_init` 开机回填 footgun 与两个安全做法、以及「不要复制 `docker-compose.override.yml`」的卷名陷阱
- **验证**：`IMAGE_TAG=0.0.1 docker compose -f docker-compose.yml -f docker-compose.prod.yml config` 通过且 `image: ghcr.io` 恰 **8** 处（未设 `IMAGE_TAG` 时按设计报错退出）；base-only `docker compose config` 仍通过；`cd.yml` YAML 可解析且 4 个 build-push 步骤齐；`bash scripts/self_review.sh` 门禁绿
- 涉及模块：.github/workflows/cd.yml, docker-compose.prod.yml, docs/build.md

## 2026-09-20 - 概念板块「查看全部」页改走本地聚合全量（504 板）+ 口径披露
- **问题**：合并 main 后 `/market/hot-sectors/concept` 的"查看全部"只有 **10 行**（`_hot_board_items_from_eastmoney` 硬切 Top-`_HOT_BOARD_LIMIT`，东财 `pz` 上限 100），"查看全部"名不副实；而我们的 `/api/v1/concepts` 有全部 504 个板块 + 涨跌家数
- **改动**：① 后端 `/api/v1/concepts` 的 `limit` 上限 100 → **1000**（页面一次取全 504；SQL 侧本就以 `CONCEPT_AGG_LIMIT=1000` 为上限，未放宽任何 SQL）；② 页面概念分类改接本地聚合端点（`fetchConceptList` 默认 `limit=1000`），行情来源从"东财实时 Top10"变为"**本地成分聚合（T-1）**"，并把 `refetchInterval` 设为 300s（对齐后端缓存 TTL，不再跟实时板块轮询）；③ 概念分类新增口径披露：**行情截至 A 日 / 成分快照 B 日（全库）** —— 列表页的 `membership_as_of` 是全表 max，措辞与详情页的按板口径区分开；④ 卡片（`/market` 热门板块）**仍走东财实时**，行业/地域分类与下钻抽屉行为不变
- **评审修复轮（`f55f854`）**：① **缺失值排序恒排最后**（原 `?? 0` 会让"不可判"的板块按 0.00% 排在下跌板块之前，与"0≠不可判"的口径自相矛盾；e2e 原断言只是全正夹具的巧合）；② 补降级/请求失败两条 e2e（此前删掉分支也全绿）；③ `pagination.total` 断言改为与 `items.length` 不同值以证明由信封驱动；④ `marketTrust.spec` 补 `/api/v1/concepts` mock（此前会漏真实请求，违背该文件"全 mock"约定）；⑤ 概念分类隐藏恒为 `--` 的成交额列（保留其余分类）
- **验证**：前端 `tsc -b` + `build` 绿、Playwright `conceptBoard` **11 passed** / `marketTrust` 12 passed；四处反证（缺失值排序、`total` 驱动、成交额列、降级/失败分支）均先红后复原；后端 598 passed 未受影响；实机 `GET /api/v1/concepts?limit=1000` → `total 504 / items 504`
- 涉及模块：backend/app/api/v1/concepts.py, frontend/src/{shared/api/concept.ts, pages/market-hot-sectors/index.tsx, e2e/{conceptBoard,marketTrust}.spec.ts}, docs/*

## 2026-09-20 - 合并 main（feat/market-sentiment-overhaul）+ 指数 K 线历史回补 5 年
- **合并**：`main` 先 fast-forward 到 `feat/market-sentiment-overhaul`（`c0adc80`），再并入 `feat/concept-boards`；7 个冲突文件按裁决解决，单提交 `a70983d`
- **重要发现（必须记录）**：**该分支已自带一套东财板块体系**（`bb97226` Task 14：真实 BK code 板块榜 + 实时成分股下钻端点；`2069249` Task 15：板块卡/列表页/成分股抽屉；`b9496d9` 修复轮）。我开工时 `main` 上概念分类确实还是 `return []`，但那套东西在并行分支里已经建好了 —— 本次 T1–T9 的"板块榜/成分/榜端点"与之**功能重叠**。唯一属于本分支的是：落库成分 + 每日差分（历史口径）、本地聚合口径、概念详情页（连板/涨停 KPI/板内梯队）、个股所属概念、次新股（BK0501）
- **冲突裁决**：① `get_hot_boards` 取 main 版（`-> dict` 信封、东财实时 60s 缓存、三类统一、跌破回落本地分组并标注）——**删除**我们的 `concept_service.hot_board_rows` + `HOT_BOARD_LIMIT` 与两条对应测试（同一面两个实现＝口径分歧源）；② `HotSectors` 卡片文案取 main 版（我们那条"本地成分聚合"声明在合并后变成假话）；③ 板块列表行点击：**概念 → 我们更丰富的概念详情页、行业/地域 → main 的成分股抽屉**；④ 东财客户端双侧保留（`_count`/`_BOARD_FS`/`fetch_board_list`/`fetch_board_stocks` 与 `_map_concept_*`/`_paged_clist` 各一次定义）；⑤ 调度注册断言双侧保留；⑥ Changelog 双侧保留；⑦ 新增 **Alembic merge revision `2614ed9a9ab4`**（`down_revision=("d5b7c9e1a3f2","e1f2a3b4c5d6")`，空升级）→ `alembic heads` 单头
- **活库迁移对账**（此前手工建表未记版本）：`alembic stamp d5b7c9e1a3f2` + `upgrade head`（只跑空 merge，无 DDL）；验证 `alembic_version=2614ed9a9ab4`、`concept_members=71928`、`concept_boards=504`、`market_sentiment_intraday` 仍在，`docker compose up` 的 `migrate` 服务由红转绿
- **合并后跟进（`e6ab14d`）**：概念端点此前用 `latest_quote_date`（raw `max(trade_date)`）而详情内的梯队快照用 `market_day_service.resolve_latest_complete_day` → 同一个信封里两套"最新交易日"判据（脏日会给出 `as_of=D` 的板聚合 + D−1 的梯队且无降级标记）；三处调用点统一到快照的判据，新增回归测试
- **指数 K 线历史回补（数据运维）**：`000688.SH 科创50` / `000905.SH 中证500` 只有 **12 根**（2026-09-03 起，因它们加入 `GLOBAL_INDICES` 时唯一一次回补已跑过），恒生/美洲等只有 2 年 → `backfill_global_index_history(years=5)`：**14 个指数 +16,979 行**，科创50/中证500 12 → **1211 根**（2021-09-22 起），全球指数 ~500 → **1224-1255 根**（2021-09-21 起），北证50 受指数发布时间限制为 1067 根（2022-04-29 起）
- **验证**：后端 `TUSHARE_TOKEN= uv run pytest` **598 passed** + concept/new-stock e2e 10 passed + `ruff check app tests` / `mypy` 绿；前端 `tsc -b` + `build` 绿、Playwright `conceptBoard` 8/8、`marketTrust` 12/12、`limitUpSentiment`+`public-homepage`+`landing` 36 passed（1 例 `+5` 失败已在 main 自身复现＝存量）；`docker compose up` 全栈 healthy（含 migrate）
- 涉及模块：backend/app/{services/{market_service,concept_service}.py, repositories/concept_repo.py, migrations/versions/2614ed9a9ab4_*.py, scheduler/jobs.py, core/providers/eastmoney_client.py}, frontend/src/{features/market/components/HotSectors.tsx, pages/market-hot-sectors/index.tsx, pages/market/index.tsx, shared/api/concept.ts}, docs/*

## 2026-09-18 - 概念板块 Phase 2 + Phase 3 落地：读路径（列表/详情/成分/反查/次新股）+ 概念 UI 全链 + 收尾三处修复
- **Phase 2 读路径（T6–T10）**：① `GET /api/v1/concepts`（`aggregate_boards` 本地聚合 + 东财资金流**内存 join**，`price_source="local_agg"` / `flow_source="em_clist"` 双源分字段，绝不 SQL join——快照只覆盖当日主力净流入 Top-100 的震荡集，join 会丢行/放大行数）；② `GET /concepts/{board_code}` 详情（单板聚合 + **板内梯队原样过滤** `/market/limit-up` 快照，绝不重算 streak；KPI 龙头 = 梯队首行；`degraded_reason` 照抄快照、仅 `no_limit_up_rows` 不透传，`no_members` 优先）；③ `GET /concepts/{board_code}/stocks`（纯数组 = `StockEnrichedOut`，前端零改映射复用 `StockTable`）；④ `GET /concepts/by-symbol/{symbol}`（反向查，板码过滤**进 SQL**，未知 symbol 返回空 items 而非 404）；⑤ `GET /api/v1/new-stocks`（东财 `BK0501` 成分 × 本地行情 × 梯队快照）；⑥ ~~`get_hot_boards("concept")` 由 `return []` 改为委托 `concept_service.hot_board_rows`~~ —— **已被同日合并 main 的东财实时板块体系取代（见下条），该适配器已删除**
- **口径裁决（写进代码注释与计划 §2.3）**：`pct_chg` 一律取 `daily_quotes.pct_chg @ as_of`（不用 enriched 的"最新两行推导值"，停牌票不按陈旧值分档）；`never_broken` 排除首日**哨兵限价行**（判据用相对比较 `up_limit < close*10`，固定阈值会误伤 688808 联讯仪器这类千元高价股）；`above_first_open` 为替代破发口径；缺失一律 `null` 由前端渲染 `--`
- **缓存规则**：`/concepts` 列表、`by-symbol`、`new-stocks` 均只写**非空且未降级**的响应（Redis 300s）——降级/空结果缓存 300s 会让上游补齐后前端仍看到自相矛盾的家数读数
- **Phase 3 前端（T11–T16）**：① `shared/api/concept.ts` 类型 + mapper（复用 `mapBackendStockEnriched`）；② `DegradedNotice` 从 `pages/market/index.tsx` 原样提取到 `shared/ui`（追加 `no_members` 文案）；③ 概念详情页 `/market/concept/:boardCode`（头部口径条「成分截至 X（东财）· 涨跌按本地聚合 n=有行情家数」+ 4 KPI + 板内梯队 + 复用 `StockTable` 并追加「连板」列，默认涨跌幅降序、`unresolved_count>0` 告警、404/降级各自空态）；④ 个股详情「所属概念」标签（失败/空**零占位**）；⑤ 短线情绪 tab「次新股情绪」卡（KPI 与 items 同源、`--` 是缺失非 0、替代口径注脚）；⑥ `conceptBoard.spec.ts` e2e 8 例
- **收尾修复（final fix wave）**：
  - **I1 截断列表不得触发批量下架**：`_paged_clist` 现在**只要 `len(rows) < total` 就 WARNING**（含空页提前收敛，原来只在上限耗尽时告警）；`ingest_concept_members` 用本轮前库内启用板块数为基线，`len(boards) < active` 时**跳过 `deactivate_missing_boards`**（短列表不是"在册全集"）
  - **I2 概念 tab 口径声明**：~~`HotSectors` 概念分类文案改为「…按本地成分聚合，每日 18:20 刷新」~~ —— **同日合并 main 后失效：卡片改由东财实时板块榜供数（非本地聚合），该文案已随合并删除**
  - **I3 次新股未收录成分披露**：`/new-stocks` 新增 `unresolved_count`（复用 `aggregate_boards_for_codes` 的同一份 `_AGG_SQL`，无聚合时退回 `symbol_to_stock_ids` 兜底），卡片 `>0` 时提示「另有 N 只成分股未收录（名录待刷新），未参与统计」
  - **文档同步**：`DataCoverageMatrix` 统计带从 `COVERAGE_ROWS` 推导（11 行 / 10 已上线）、`landing-market-theme.md` §5 补「概念板块成分」行、`data-source.md` payload 类型计数与 `concept_members`、`deactivate_missing_boards` docstring 订正（`not_in(codes)`：传在册全集 = 一行不停用）、计划触点 B 成分表列改为与 `StockTable` 实际渲染一致
- **数据面事实（dev DB）**：概念板块 **504** 个 / 成分 **71928** 行（`unresolved=110` 为名录滞后暴露面）；`stock_price_limits` 已回填至 **245 个交易日**（T0 补齐 66 只新股限价）；`stocks` 名录 **5579**
- **测试**：后端默认门禁 `uv run pytest` 全绿（新增 `_paged_clist` 短列表/完整列表告警、ingest 短列表跳停用、`unresolved_count` 暴露等单测）+ 触碰文件 `-m e2e`；ruff/mypy 绿。前端 `npx tsc -b`、`npm run build`、`npm run test:e2e -- conceptBoard` **8 passed**
- 涉及模块：backend/app/{core/providers/eastmoney_client.py, repositories/concept_repo.py, services/{concept_service,new_stock_service}.py, schemas/concept.py, api/v1/concepts.py, api/v1/new_stocks.py, api/v1/__init__.py}, backend/tests/{test_eastmoney_client,test_concept_ingest,test_new_stock_service,test_concept_api,test_concept_agg_sql}.py, frontend/src/{shared/api/concept.ts, shared/ui/DegradedNotice.tsx, features/{market/components/{HotSectors,DataCoverageMatrix}.tsx, concept/components/{NewStockBoard,ConceptTags}.tsx}, pages/{market,market-concept,market-hot-sectors,stock-detail}/index.tsx, app/router/index.tsx}, frontend/e2e/conceptBoard.spec.ts, plans/2026-09-18-concept-boards-and-new-stocks.md, docs/design/{landing-market-theme,data-source}.md

## 2026-09-18 - 概念板块 Phase 1 落地：三张表 + 东财成分采集（504 板 / 71928 成分）+ 每日 18:20 调度
- **交付（T1–T5，分支 `feat/concept-boards`）**：① 三张表 `concept_boards` / `concept_members` / `concept_member_changes`（迁移 `d5b7c9e1a3f2`，成分表以 `symbol` 为业务键、`stock_id` 可空解析列、无 FK、无多余 `board_code` 索引）；② 东财客户端 `fetch_concept_boards` / `fetch_concept_members`（`pz` 服务端上限 100 → 翻页 + 去重 + `_MAX_PAGES` 守卫）；③ `concept_repo`（`diff_members` 纯函数 + `upsert_members` 整板应用 + 本地聚合 `_AGG_SQL` + `member_history_stats`）；④ `concept_service.ingest_concept_members`（逐板失败隔离 + 完整性闸门）；⑤ `concept_members_refresh_job`（mon-fri 18:20 Asia/Shanghai，避开 17:45 对账/18:00 龙虎榜）+ `MarketDataJobType` 新增 `concept_members` 手动触发（复用 `market_data.fetch` 队列，**不新增 worker 容器**）
- **实机首跑（dev DB）**：`boards=504 / members_upserted=71928 / added=71928 / removed=0 / failed_boards=0 / partial_boards=0 / unresolved=110 / dropped_members=0`；二次跑幂等（0/0）。`unresolved=110`（≈95 B 股 + 约 15 只最新上市不在 `stocks`）正是“名录滞后”唯一暴露面
- **两处设计缺陷在评审中被实测撞出来并修正**：
  - **完整性闸门必须作用在清洗后的行上**：原来先用原始行数比 `member_total`、再过滤脏行（缺 `f14`）→ 脏行会让**仍在板上的成分**被写成 `change_type='remove'`，而 `concept_member_changes` 是追加式永久历史（幻影记录不可撤销），且脏行会抬高计数、把“少抓到的页”掩盖过去 → 改为 `_valid_members` 先于闸门，脏行使该板计为 `partial` 并整块跳过
  - **翻页护栏的页数上限必须由真实最大分页数推导**：`_MAX_PAGES=20`（20×100=2000）< `BK0596 融资融券` 的 3870 成分 → 该板永远拿不到成分，且因读路径 inner join `concept_members` 而在**所有概念视图中完全不可见**（只汄1 条 WARNING + `partial_boards=1`）→ 提到 60（6000 覆盖最大板；小盘仍然空页即收敛），复跑后 `partial_boards` 1→0、`BK0596` 入库 3870 行
- **降级语义订正**：`misfire_grace_time=None` 只在“宿主睡眠且进程存活”时补跑；scheduler 用默认 MemoryJobStore，**进程重启会忘掉错过的触发点**（原计划把它当验收项，已改）
- **测试**：默认门禁 302 passed（新增 `diff_members` 纯函数、SQLite 内存的 `_AGG_SQL` 口径守卫 —— 使 `unresolved/priced/up-flat-down/avg_pct/is_active/tiebreak` 不再只靠 `-m e2e`；ingest 隔离与闸门；调度注册与 worker 分发）；真库 `-m e2e` 覆盖计划形状与聚合计数；ruff/mypy 绿
- **后续**：Phase 2 读路径与 Phase 3 UI **已落地**（见下一条 Phase 2+3 条目）；活库 `alembic_version` 仍停在另一分支的 `e1f2a3b4c5d6`（本分支三张表已用等价 DDL 手工建入，合并后需按正常迁移链校对）
- 涉及模块：backend/app/{models/concept.py, migrations/versions/d5b7c9e1a3f2_add_concept_tables.py, core/providers/eastmoney_client.py, repositories/concept_repo.py, services/concept_service.py, scheduler/{jobs,runner}.py, schemas/task.py, workers/market_data_worker.py}, backend/tests/×6, plans/2026-09-18-concept-boards-and-new-stocks.md, docs/references/best-practices.md

## 2026-09-18 - 概念板块计划 T0 执行：名录解冻（5579）+ 66 只新股行情/限价/基本面补齐（数据运维，无代码改动）
- **名录解冻**：`stocks` 冻结在 2026-05-08（66 只 5-08 后上市新股无行）→ 逐交易所 `ingest_stock_universe` 重建：**5579 只**（+66）、`max(list_date)=2026-09-17`、`max(asof)=2026-09-18`、近一年上市 **162 只**（与东财概念板 `BK0501` 成分数完全一致）
- **历史行情回填**：66 只完全无行情 + 19 只覆盖不足 → `_ensure_trailing_three_year_daily_quotes` 85 只、**upserted 15272、failed 0**；验证“无任何行情”从 66 → **0**，且首行 = 上市日（如 `601091` C沈鼓 只有 2026-09-17 一行）；09-17 行情行数 5487 → **5553**
- **发现真缺陷（限价域漏新股，已绕过并修复数据）**：`ingest_stock_price_limits` 的补漏判据是“**该日无任何行**”，而 20 个期望日都有旧股票的行 → 任务报 `ok/fetched=0` 却把 66 只新股的限价**永久漏掉**（实测新股限价行 = 0，最新日“有行情无当日限价”恰为 66），直接让次新股的“未开板/涨停”不可判定。用显式 `trade_date` 逐日强制重拉 20 日 → **upserted 111120**，新股限价行 0 → **1140**，最新日缺限价 66 → **0**
- **派生重算**：09-17 的情结行在缺 66 只的条件下算过 → `persist_snapshot(as_of=2026-09-17)` 重算（同时失效 calendar 读缓存）：**涨停 50 / 跌停 2 / 炋板 23 / 最高板 5**；`/market/data-freshness` 四域全 ok（`universe 5579` vs `quotes_symbols_latest 5553`）
- **待办**：把限价域的存在性判据纳入对账体系（行数量级判据，与 quotes/basic 同口径），否则每新增一批股票就重现一次
- 涉及模块：docs/{Changelog,references/best-practices}.md（仅文档）

## 2026-09-18 - 概念板块成分 + 次新股追踪：实施计划入库（未实施，含 UI/UX 与解耦边界）
- **计划**：`plans/2026-09-18-concept-boards-and-new-stocks.md`。决策拍板：数据源**东财为主**（同花顺兜底**明确不做**：实测容器内 `ak.stock_board_concept_name_ths()` → `OSError: Error loading shared library libstdc++.so.6`，`python3.13-alpine` 装不了 `py_mini_racer` 的 V8）；历史口径**只做当日/向前**（不回算历史成分）；次新股口径=**东财概念板块 `BK0501` 成分**，不自建时间窗
- **实测依据（写进计划，勿再猜）**：东财概念板块共 **504** 个（`fs=m:90+t:3+f:!50`，`pz` 服务端硬上限 100、`fid=f12` 翻页稳定）；成分端点 `fs=b:BK0501` → `total=162`、**全部上市于 2025-09-19~2026-09-17**（与 TuShare 口径「近 1 年上市 162 只」完全一致）；首日新股 `stk_limit` 返回哨兵 `up_limit=99999.999/down_limit=0.01` 且 TuShare `pct_chg=373.8%` 与东财 `f3=177.74%` **基准不同**（禁止混源拼曲线）
- **现状定性**：概念不是没有而是「半截」——快照表已有资金流/涨跌家数，但 `get_hot_boards("concept")` 硬编码 `return []`（前端 tab 永远空白）、**完全没有成分股成员表**、且快照只拉 `pz=100` 不分页 → 至少 250 个板块永不落库且每行涨跌幅是「最后一次进 Top100 那一刻」的值
- **设计要点**：3 张新表（`concept_boards`/`concept_members`/`concept_member_changes`，成分表以 `symbol` 为业务键、`stock_id` 可空 → 名录滞后不再静默丢数，以 `unresolved_count` 暴露）；涨跌家数**本地聚合**消除样本偏差、资金流沿用东财快照且分字段不混算；读路径复用 `limit_up_service.get_snapshot()`（板内梯队与梯队卡同源）与 `market_service.get_stocks_enriched_by_symbols`（前端零改映射直接复用 `StockTable`）；采集复用 `market_data.fetch` 队列（不新增 worker 容器），每日 18:20，逐板失败隔离且失败**绝不表现为成分清空**；既有代码只改 4 个 seam
- **UI 触点**（含字段/交互/降级/空态设计）：热门板块概念 tab（后端委托后前端零改动）→ 概念详情页 `/market/concept/:boardCode`（头部口径条 + 4 KPI + 板内梯队 + 成分股表）→ 个股详情「所属概念」标签（失败即零占位）→ 短线情绪 tab「次新股情绪」卡（含「高于首日开盘」替代口径声明，因发行价未采集）→ 数据版图/资金流样本注脚
- **状态**：T0（解冻 `stocks` 名录，运维动作）+ T1–T16 待实施，T17 可选（成分变更历史入口，本期不做但差分表照写）
- 涉及模块：plans/2026-09-18-concept-boards-and-new-stocks.md（新增）, docs/references/best-practices.md

## 2026-09-17 - Phase 3 热门板块重做 + 体验：东财板块体系 + 成分股下钻 + a11y/暗色修复（市场数据可信度改造）
- **东财板块体系（Task 14）**：`eastmoney_client` 增 `fetch_board_list(category)`（`industry`/`concept`/`region` 三套，实测行业 496 行）+ `fetch_board_stocks(code, limit)`；`get_hot_boards` 由本地 SW 分组改为东财真板系统——`code` 为**真实 `BK` 代码**、`leaders` 非空、`mainNetInflow`/`amount` 就位，**`concept` 不再返回 `[]`**；东财失败 → 回落本地分组并在 payload 标 `source="local_grouping"` + `degraded_reason`（`concept` 无本地真实归类，回落返空 items 而非伪造省份行）；新端点 `GET /market/boards/{code}/stocks`（非 `^BK\d+$` → 400，无本地成分股源故上游失败 → 502 而非空列）；**实时 board 缓存 60s**（与前端 30s 轮询对齐；日级路径仍 300s）
  - 删除了 2 条断言“concept 恒空信封”的旧测试——它们编码的是计划要退役的**前提**（concept 无数据源）而非保证，裁定删除正确（无任何测试断言“上游挂→空信封而非 5xx”）；同时把 `code` 真值为 `f12`、回落标识、码格式均补测
- **前端板块体验（Task 15）**：新建 `BoardDrilldownDrawer.tsx`（点板块行打开抽屉取成分股）；**loading / 上游失败 / 成功但空**三态分派（`isError` 早于空态，**任何**错误形状都不会落到“此板块无成分股”的谎言分支）；`HotSectors.tsx` + 列表页均渲染**可见**降级标签（此前 T14 已把 provenance 放进 payload 但无人消费）；列表页排序（涨跌幅/成交额/主力净额/家数，严格数值比较，后端已 `float` 化无字符串序陷阱）+ 搜索（名/码，trim+大小写不敏感）+ 分类切换保留选中 + `?board=` 深链高亮（T14 的真实 code 使其生效）；新建 `e2e/marketTrust.spec.ts` 7 例（实现前 6 红）
- **a11y / 暗色 / 北向标注（Task 16）**：可点击条目按“**导航 → 真 `<Link>` / 原地展开 → `<button>`**”分流（抽屉触发器语义为按钮，非链接），补 `focus-visible` 环；北向卡显示“数据源已停更（最新 09-07）”（日期取自 payload 的 `as_of`，由 `sourceStatus === "discontinued"` 门控，非硬编码）；数据版图北向行标“规划中”
  - **修复了 Phase 3 前就存在的 `darkmode` 基线失败**：根因**不是**任何 `.ant-card`（5 个 Tab 全为 `rgb(30,34,45)`），而是 `body`——`data-theme` 仅在 `useEffect` 里设 → 首帧走 `:root` 亮色回退，叠加 `body{transition:background-color .2s}`，测试读到渐变中途的灰。修法：`index.html` `<head>` 内**首帧前**阻塞 bootstrap（`try/catch` 兼容 localStorage 异常，light 分支与 storage key 与 TS 侧一致），`resolveInitialMode` 优先读已设属性、provider 消费同一值 → 首帧即正确而非“移到第二次重绘”；**无 `!important`、无 CSS 补丁**，`darkmode.spec.ts` 断言未被修改
- **验收（总门禁）**：`self_review.sh --full` **532 passed**；`bench.sh` 全项在阈值 12% 内（实际 −24%~−31%）；`tsc --noEmit` / `check:design` / `build` ✓；后端镜像重建至 HEAD 并重启 api/scheduler/worker，前端 dist 重建并部署；经 gateway 全量 E2E **87 passed / 2 failed**（2 = `limitUpSentiment` +5 chip、`userIsolation` 自选股；**darkmode 白底基线已消除，基线由 3 降为 2**，零回归）；活栈 `concept` 返回真实 `BK1645` + 非空 `leaders` + `mainNetInflow`
- 修复脚本: `scripts/bench.sh` 两处 macOS 既有 bug（见 best-practices）——`$THRESHOLD）` 被全角括号吞入变量名、`set -u` 下空数组 `"${EXTRA_ARGS[@]}"` 在 bash 3.2 报 unbound variable；均与业务无关但阻断门禁
- 涉及模块：`backend/app/{core/providers/eastmoney_client.py,services/market_service.py,api/v1/market.py,schemas/market.py}`，`frontend/src/{features/market/components/{BoardDrilldownDrawer,HotSectors,NorthboundCard,DataCoverageMatrix}.tsx,pages/market-hot-sectors/index.tsx,shared/api/{market,marketEnvelope}.ts,index.html}`

## 2026-09-17 - Phase 2 盘中实时情绪：东财涨停池纯函数 + 盘中表/采集 + 前端双口径切换（市场数据可信度改造）
- **`intraday_sentiment_service.build_intraday_snapshot(pool_rows, *, as_of, captured_at)`（新，纯函数，无 IO）**：输出与收盘路径 `limit_up_service.get_snapshot` 同构（15 键，含 `echelons/kpis/sectors/breadth/market_days/as_of_label`），`source="eastmoney_intraday"`、`as_of_label=f"盘中 {captured_at:%H:%M}"`、`as_of_quality="partial"`。档位按 `streak` 降序分桶（盘中无窗口，故不用收盘的 gaps-and-islands）；`stock_id`/`yesterday`/`as_of_prev` 一律 `None`（**不伪造**窗口语义）；KPI 14 键与收盘 `sentiment_kpis` **逐键一致**。`fetch_intraday_pool(trade_date)` 薄封装东财客户端。夹具 `tests/fixtures/eastmoney/zt_pool_20260917.json`（47 行）
- **`market_sentiment_intraday` 新表 + 手写迁移**：`(trade_date, captured_at TIMESTAMPTZ, zt_count, dt_count, zb_count, max_streak)`，`UNIQUE uq_market_sentiment_intraday_date_captured (trade_date, captured_at)`；repo 的 `upsert_intraday_snapshot` 用 `ON CONFLICT (constraint) DO NOTHING` 幂等。`alembic upgrade head / downgrade -1 / upgrade head` 活库验证；**测试 teardown 必须把 dev 库恢复到 HEAD**（原先降到 PREV 会让本地库留在缺表中间态）
- **5 分钟盘中采集任务 `intraday_sentiment_poll`**：`CronTrigger(mon-fri, hour=9-15, minute=*/5, tz=Asia/Shanghai)` **加** task 体首行 `_is_workday()/_in_trading_hours()` 守卫（照 `sector_moneyflow_job` 范式）——只用 `hour="9-14"` 会在 09:00–09:25 盘前就外呼并在池非空时写入盘中前点位，且永不触发 15:00；失败非致命（`logger.exception` + `_alert_job_failure`）
- **新端点 `GET /market/sentiment/intraday?date=`**（默认上海今日、未来日期 400、按 `captured_at` ASC、60s 缓存）：返回 7 字段点序列
- **前端 Task 13**：情绪 Tab 增 **盘中/收盘** 切换（Ant Design 5，默认**收盘**→首屏行为不变）；盘中时三个涨停端点带 `mode=intraday`、卡头**逐字**渲染后端 `as_of_label`（`盘中 HH:MM` 或回落文案 `盘中不可用，已回落收盘`，不做美化/改写）；新增纯 SVG `SentimentIntradayChart.tsx`（0/1 点有独立空态、`max===min` 除零守卫、`role="img"`+`<title>`，**无新依赖**）；30s 轮询复用 `useMarketPolling("session")`（跟随 A 股时段、收盘停）；close/intraday 各用独立 query key，盘中查询 `enabled: isIntraday` → **不会双轮询**；close 模式 URL 恒等（`mode` 为 `close`/`undefined` 时不进 query string）
- **验收**：`self_review.sh --full` **514 passed**；`tsc --noEmit` / `check:design` / `build` ✓；活栈 `mode=intraday` → `source=eastmoney_intraday`/`as_of_label="盘中 01:27"`/`quality=partial`/5 档（streak 5·3·2·1，47 行），`mode=close` 字节不变（`local_calc`/09-17/complete/49）；序列端点按日读回真实点位；未来日期与历史 `mode=intraday` 均 400；**`market_sentiment_daily` 无盘中行**（盘中分支不调 `persist_snapshot`，单测用替身钉死）；经 gateway 全量 E2E **74 passed / 3 failed**（3 = 既有基线：darkmode `/market` 白底、limitUpSentiment `+5`、userIsolation 自选股，零回归）
- 涉及模块：`backend/app/{services/intraday_sentiment_service.py,services/limit_up_service.py,scheduler/{runner.py,jobs.py},models/market_data.py,repositories/market_data_repo.py,schemas/limit_up.py,api/v1/market_data.py,migrations/versions/e1f2a3b4c5d6_*}`，`frontend/src/{shared/api/limitUp.ts,shared/ui/SectionCard.tsx,features/market/components/SentimentIntradayChart.tsx,pages/market/index.tsx}`

## 2026-09-17 - Task 11: `mode=intraday` 端点接线（双口径并存，不污染收盘序列）
- **`limit_up_service.get_snapshot(..., *, mode="close")` 增 `mode` 关键字**：`intraday` 走新私有 `_get_intraday_snapshot`（东财涨停池 → `intraday_sentiment_service.build_intraday_snapshot` 纯函数）；`close` 走原路径——**字节级一致**，现有 24 个 close-path 单测零修改全绿
- **盘中缓存独立 key**：`market:limit-up:intra:{trade_date}` TTL=60s（与 30s 轮询半衰期对齐）；close 路径 `market:limit-up:snapshot:{date}:{lookback}` 维持原样。**关键不变量**：盘中分支**不**调 `persist_snapshot`（不污染 `market_sentiment_daily`），单测用 `_boom` 替身钉住
- **东财池失败 → 回落 close 路径**：as_of_label 标 `"盘中不可用，已回落收盘"`，as_of_quality 由 close 路径给出（不强行 override），logger.warning 可观测；回落**仍**走 `persist_snapshot`（close 路径当天数据落盘契约不破）
- **历史日期 400**：`mode=intraday` + 显式 `as_of != today` → ValueError（路由层 `except ValueError → HTTPException(400)`），盘中无历史意义
- **Schema 拓宽（单次完整 pass）**：`LimitUpLadderOut` / `SectorLimitUpOut` / `YesterdayLimitUpOut` 的 `source` → `Literal["local_calc", "eastmoney_intraday"]`；`SectorLimitUpItemOut.l3_code/l3_name/l1_code/l1_name` 改 `Optional`；`LadderStockOut.days_span/boards_in_window/missing_days` 改 `Optional`（盘中无窗口语义）；三个响应新增 `as_of_label: str | None = None`（盘中="盘中 HH:MM"、回落="盘中不可用，已回落收盘"、close 默认 None）
- **路由层**：`/market/limit-up-ladder` / `/sector-limit-up` / `/yesterday-limit-up` 增 `mode: Literal["close", "intraday"] = "close"` Query（默认 close，向后兼容）；`ValueError` 一律 400
- **测试 11 个**：`backend/tests/test_intraday_endpoint.py`（非空性证据：反向护栏 `test_intraday_branch_does_not_fall_through_to_close_path` 把 close 路径 repo 替成 boom、池替空 + 不抛错 → 验证真走盘中分支；其余用 `assert calls == []` / `assert ttl == 300` / 改 as_of_label 字符串等临时变更跑红验证 assertion 真生效）
- 验收：`TUSHARE_TOKEN= uv run pytest -q --no-cov -m "not e2e and not bench"` **501 passed**（490 + 11 新增）；`uv run --extra dev ruff check / ruff format --check / mypy app` 全绿；`self_review.sh` 通过
- 涉及模块：`backend/app/{services/limit_up_service.py,api/v1/market_data.py,schemas/limit_up.py}`，`backend/tests/test_intraday_endpoint.py`

## 2026-09-17 - Phase 1 性能与一致性：共用日快照 + 缓存键带 as_of + 缺失列裁剪（市场数据可信度改造）
- **`market_snapshot_service.load_day_rows`**（新）：从 `daily_quotes` 单次取全市场当日派生行，**改读已存储的 `pct_chg` 列**（不再 LATERAL 回看前收），返回 `csrc_desc/province/pct_chg/amount/basic_date`（行契约历经瘦身后共 5 键 + `basic_date`）。`group_by` / `summarize_group` 纯函数。**口径审批**：存储 NULL 的 `pct_chg` 记为 `flat` 且**从均值里剔除**——满足 `up + flat + down == total` 恒等式；旧 SQL 把 NULL 现收当 0% 参与 AVG（"小样本板块里凭空造 0"），已弃用
- **5 个聚合端点改走 loader**（`distribution/sectors/capital-flow/hot-boards`，`sw-industry/performance` 因 SW L1 多对多保留自家语句但同样不再回看前收）：Postgres 语句 30→7；同页冷 217→165→**130ms**；热 1.4ms。**真实收益是"页面级"而非单查询级**（"597ms→15.9ms"计划初稿目标在本库复现不了，实测旧形态 warm 43-46ms；共享快照的真正价值是 4-6 个端点合并为 1 次取行 + 1 份缓存）
- **缓存键带 `as_of`**：6 个日聚合结果键（`market:distribution:{as_of}` 等）+ `rankings` / `sw-performance` / `sector-moneyflow` / `market-moneyflow` / `northbound` / `dragon-tiger` 等，全部纳入 `as_of`（或窗口日期），修后/翻日后不会再把旧 payload 配新日期。`stale_days` 改为**读时实时计算**（缓存里冻的是"数据日期"，不是"今天距离"）
- **载荷瘦身 + 端点契约夹具**：共享快照 payload **1,275 → 637 KiB**（砍掉 6 个无消费者列：close/total_mv/circ_mv/turnover_rate/stock_id/symbol/name——排序仍由 SQL `ORDER BY stock_id` 保证）；命中路径 14.4→**6.4ms**。`market:indices` 改为 `market:indices:{day}` + `market:indices:latest-day` 60s 指针（修后不可见期 ≤300s→≤60s）。新增 4 个后端契约夹具 + 7 个 e2e 契约用例（`frontend/e2e/fixtures/*.sample.json`），前端 mock 与后端响应一起锁住
- **删除死代码**：`get_latest_trade_date` 及其测试（T1 之后已无生产调用者），保留 `last_weekday`
- **两条迁移**：`idx_daily_quotes_stock_id_adj_factor`（部分索引 `WHERE adj_factor IS NOT NULL`，把 adj_factor 回补子查询 **198.5→7.7ms**）+ `ix_sector_moneyflow_dim_date`（增长保险，当前 1,120 行规划器不选；盘中轮询恢复后该表按 ~10k 行/日增长）。两条都在 ORM 模型镜像（`quote.py` / `market_data.py`），避免 autogenerate 把它们当孤儿删掉。`alembic upgrade head/downgrade -1/upgrade head` + `compare_metadata` 验证
- **前端**：`useMarketPolling`（按 A 股时段驱动 30s / 关闭 + 指数卡常驻 300s 双模式） + `marketStatus`（上海时区工作日 09:30–15:00）+ 统一 query key（一个端点一个前缀，sectors/hot-boards 等去重）+ `SectionCard` 增 `quality`/`staleDays`/`sourceStatus` 徽标；`fallback` 不复述日期；A股核心指数拆 key 恢复盘中 30s（不被全球盘 300s 拖慢）
- 验收：`self_review.sh --full` 477 passed ✓；**经 gateway 全量 E2E 70 passed / 3 failed**（3 = 既有基线：`darkmode /market` 白底残留、`limitUpSentiment +5` 数据依赖、`userIsolation` 自选股，与干净 main 对账非本阶段回归）；活体 ladder/sectors/capital-flow/sentiment `as_of`+`quality` 正确；market_moneyflow_daily 已追平至 09-17
- 涉及模块：backend/app/{services/{market_snapshot_service,job_alert_service,market_data_service},repositories/{market_data_repo,quote_repo},schemas/{market,market_data},migrations/versions/{d1e2f3a4b5c6,d7c8b9a0e1f2}_*.py,models/{quote,market_data}.py}, frontend/src/{features/market/{hooks,components},shared/ui/SectionCard.tsx}, frontend/e2e/{marketDataFace,public-homepage,marketPolling}.spec.ts + fixtures/*.sample.json, docs/{Changelog,best-practices,design/{market-data-trust-and-realtime,landing-market-theme}}

## 2026-09-17 - Phase 0 数据可信度：日级完整性判据 + 脏源治理 + 失败可观测 + 读路径回落（市场数据可信度改造）
- **日级完整性判据**（新 `market_day_service`）：`rows ≥ 0.9×universe` **且** `pct_chg` 非空率 ≥ 0.99 **且** 该日 `stock_price_limits` 存在，三条件同时成立才算"完整日"；`resolve_latest_complete_day` 在候选日不完整时向前回看最多 5 个交易日，返回 `(day, quality=complete|partial|fallback, reason)`。**这是全仓"最新交易日"的唯一判据**：8 个按日聚合端点 + 情绪快照全部改走它，响应新增 `as_of_quality`/`as_of_reason`；`/market/{distribution,sectors,capital-flow,hot-boards}` 由裸数组改为信封 `{as_of, as_of_quality, as_of_reason, items}`。实测价值：一条当日脏行（`daily_quotes` 2026-09-17 仅 1 行）曾让 8 个端点退化成 1 行 / 情绪 Tab 整体降级，判据上线后同样数据状态下全部返回 09-16 完整数据并标注 `fallback`
- **脏源治理**：`data_init` 的单股覆盖窗口上界由 `date.today()` 改为 `last_completed_trading_day()`（含三年前行情/一年财务/指数三条窗口，覆盖任务再也写不进未收盘数据）；新增一次性修复 CLI `scripts/repair_market_day.py`（`--purge-incomplete-today` 需 `--yes`、`--backfill`、`--adj-factor`）。实修：09-17 脏行 1→0、09-14/09-15 的 `pct_chg` 1→5487/5485、复权因子缺口 165 行/21 股→0
- **失败可观测**：`job_alert_service` 记录失败到 Redis（`HSET job:failures` + 7 天 TTL，读时按条目过期过滤），`scheduler/jobs.py` 的 **18/18** 个 `except` 全部接线，`/market/data-freshness` 暴露 `failed_jobs`。同时修掉 13 天静默崩溃：`upsert_share_floats` 批量分片（`UPSERT_CHUNK=500`，原 6000 行×8 列=48000 bind 参数撞 asyncpg 32767 上限，任务每日 17:30 报错而 APScheduler 仍打 "executed successfully"）——实修后 469→6469 行；另发现并注册了从未注册过的 `market_moneyflow_daily`（Mon-Fri 16:20），其表此前停在 09-03
- **读路径回落 + 陈旧度标注**：板块资金流由"只认今天"改为"最近可用快照 + `stale_days`"（实测 `[]`→09-08 的 15 行）、北向资金 `source_status`（上游 TuShare `moneyflow_hsgt` 已事实停更，最新 08-21 → `discontinued`）、大盘资金流历史段 `history_as_of`/`history_stale_days`；`/market/rankings` 的 `is_latest_trading_day` 改为显式比较上海时区判据日（不再恒真）
- **复权因子不再被重灌清零**：`backfill_missing_adj_factors`（每股一次区间调用）修"有缺口"的股票，`reconcile` 重拉某交易日后自动回补**该日**缺口（`apply=False` 绝不触发、失败非致命、结果写 `adj_factor_repaired`），带 `ADJ_FACTOR_REPAIR_MAX_STOCKS=100` 预算与 `remaining` 可见性，并按股票失效 `quote:kline:*` 缓存。修复前 `adjust_available=false` 导致个股 K 线复权开关**永久禁用**
- **前端**：适配新信封（api 层解包 `.items`，元数据以常规对象暴露；不采用"把元数据挂数组上"的写法——任何数组派生都会静默丢标签）
- 验收：`self_review.sh --full` 387 passed；经 gateway 全量 E2E 60 passed / 3 failed（3 条为既有基线失败：/market 暗色白底、情绪卡数据依赖断言、watchlist 多租户，均已与干净 main 对账确认非本次回归）
- 涉及模块：backend/app/{services/{market_day_service,job_alert_service},scheduler/{jobs,runner},repositories/{market_data_repo,quote_repo},schemas/{market,reconciliation,market_data},api/v1/{market,market_data,stocks},scripts/repair_market_day}, frontend/src/{shared/api,features/market}, docs/design/market-data-trust-and-realtime.md, plans/2026-09-17-market-sentiment-overhaul.md

## 2026-09-17 - CI 修复：单测漏包 `_get_tushare()` seam（本机 .env 掩盖 → 本地绿 CI 全红）
- **现象**：push 后 CI `Test (backend)` 红，8 failed / 266 passed，全部是 `ValueError: TuShare token is required`；**上一版 main 的 CI 同样红（7 failed）**，只是没被注意到
- **根因**：`reconcile_market_data` 开头 `client = client or _get_tushare()` 比所有协作函数都早执行，而 `test_reconciliation_service.py` 的 `_seams` 只 patch 了 `expected_trade_dates`/`_row_counts`/`_refetch_*`，没包 client 构造点。本机 `backend/.env` 有真实 `TUSHARE_TOKEN` → 本地永远绿；CI 无该变量 → 必炸（经典「本地凭证掩盖 CI 缺口」）
- **修复**：fixture 补 `monkeypatch.setattr(rc, "_get_tushare", lambda: _CLIENT)`，并在 `_expected` 里断言 `client is _CLIENT`（钉住「注入的 client 必须透传」，避免只堵默认分支）；用 `TUSHARE_TOKEN= uv run pytest` 先复现失败再转绿（8 failed → 11 passed）
- **防御**：`scripts/self_review.sh --full` 的 pytest 改为 `TUSHARE_TOKEN= uv run pytest ...`——本地自检与 CI 同口径，同类“本地有凭证”的假阳性以后在门禁里就会被拦住
- 涉及模块：backend/tests/test_reconciliation_service.py, scripts/self_review.sh, docs/{Changelog,references/best-practices}.md

## 2026-09-17 - 本机数据卷恢复 + 全栈经 gateway 起全 + 垃圾清理（数据运维，无业务代码改动）
- **事故与恢复**：`docker compose up -d api` 触发 compose 连带重建 postgres —— main 里 `73f40d7` 把卷 pin 成 `stock_bot_wt_p7_postgres_data`，而本机真实数据一直在项目前缀卷 `stock-bot_postgres_data`（4.2G），于是新集群 initdb、`data_init` 见空 `stocks` 表→全量重播 3 年行情（烧额度）。旧库完好（`pg_controldata` + 临时实例对账：5513 stocks / 4,347,642 quotes / max(trade_date)=2026-09-16）。修法：新增本机专属 `docker-compose.override.yml`（已 gitignore）把 `postgres_data` pin 回 `stock-bot_postgres_data`，`docker compose down && up -d` 后数据回来
- **全栈经 gateway 起全**：之前只跑 api/worker/scheduler/frontend，本次补齐 `gateway(traefik) + auth-service + forward-auth + auth-db`，11 服务全 healthy；经 `:80` 实测 SPA 200 / 匿名 API 200（forward-auth 无 session 时旁路）/ `/auth/session` 401 / `/.well-known/jwks.json` 200
- **清理**：构建缓存 18.23GB→2.3MB；19 个匿名空库卷（≈880MB，历史 postgres 镜像 VOLUME 残留）+ 本项目 3 个孤立卷（`stock_bot_wt_p7_postgres_data` 事故重播卷 324M、`stock-bot_redis_data`、`stock-bot_rabbitmq_data`）+ `stock-bot-task6-frontend` 旧实验镜像 + 3 个一次性容器全部删除；本地卷 31→9 且 reclaimable 归 0
- **E2E**：经 gateway（`E2E_BASE_URL=http://localhost`）跑全套 63 例 → 60 passed；3 例失败（darkmode/limitUpSentiment/userIsolation）已用干净 main 前端重建做基线对账，**同样失败** → 存量/数据漂移问题，与本改动无关。`kline.spec` 5/5 通过，页面实际渲染 `数据截至 9月16日`
- 涉及模块：.gitignore, docker-compose.override.yml(未入库), backend/*, frontend/*, docs/references/best-practices.md

## 2026-09-17 - 个股页"数据截至"口径修复 + 名录冻结根治（拣选 fix/stock-asof-universe 内容重放，非 rebase）
- **口径修复**：个股头部"数据截至"原本渲染 `stocks.asof`（名录 ingest 时间戳），实测被冻在 `2026-05-08T15:09:16.546850Z` 而行情已到 09-16——差 4 个月的虚假时效标注。enriched 响应新增 `latest_quote_date`（最新行情 `daily_quotes.trade_date`），前端改绑并复用 `@/shared/ui/date` 的 `formatCnDate`（与 SectionCard/RankingMatrix/SectorFlow 同款「9月16日」），e2e 正则同步
- **名录冻结根治**（这才是真损失）：日线采集用 `stocks` 表把 `ts_code → stock_id`，映射不到的行 `skipped += 1; continue` **静默丢弃**。universe ingest 只在「空库首启」或手动 `/tasks/fetch-universe` 跑过，从不自动刷新 → 对账原始 JSONL 与 DB 得 **65 只次新股的行情从未落库**（`001232.SZ`…`603407.SH`，详情页 404），且每日 fetched 5550 / upserted 5485 的差额随上市递增。新增 `universe_refresh_job`（每周六 09:00 Asia/Shanghai，逐交易所失败隔离，与 `UniverseWorker` 共用 `ingest_stock_universe`，交易所清单复用 `models.stock.ExchangeName` 单一事实源而非另立常量）
- **可观测性补刀**（原分支缺）：`skipped` 拆出 `unknown_ts_codes` 并升级为 warning（含 sample），`_build_stock_id_map` 为空时由 warning 升 error 并带丢弃行数——「名录滞后」不再只体现为一个数字；`daily_basic` 同源路径同步
- **对账盲区修复**：`threshold = universe * 0.8` 的分母取自被冻结的 `stocks` 表，1.2% 缺失（65 只）永远过阈值。`/market/data-freshness` 新增 `quotes_symbols_latest`（最新期望日 daily_quotes 实际行数）与 `universe` 并排暴露，名录滞后在巡检端点可见
- 涉及模块：backend/app/{scheduler/{jobs,runner}.py,services/{tushare_ingest,reconciliation_service}.py,schemas/{stock,reconciliation}.py}, frontend/src/{shared/{api/stocks,types/index,ui/date}.*,features/stock-detail/components/StockHeader.tsx}, frontend/e2e/kline.spec.ts, backend/tests

## 2026-09-17 - 对账式自愈数据面落地：misfire 硬化 + reconciliation_service + 新鲜度端点（Phase 1+2 全部实施并实机验收通过）
- **L1 调度硬化**：`create_scheduler` 注入 `job_defaults={misfire_grace_time: None, coalesce: True, max_instances: 1}`（宿主挂起醒来补跑、堆积合并）；盘中五任务（SSE 快照×3/资金流轮询/公告轮询）例外 `grace=300` 防醒来堆积无意义快照。scheduler 注册 **18→21 任务**（+Startup reconcile/+Post-chain 17:45/+Weekend catch-up 10:00）
- **L2/L3 对账收敛器**（新 `reconciliation_service.py`）：trade_cal 期望集（截止昨日 T-1 语义，不可用降级工作日启发式）× 行数阈值（≥0.8×stocks 全市场数）判完整，`missing ∪ partial` 逐日重拉（upsert 幂等解 partial 死锁）；sentiment 仅在 quotes+limits 完备日补；底座补数先 commit 再派生（get_snapshot 独立会话可见性）。16:30/16:45 job 重写为单域对账薄封装，`_fetch_yesterday_*` 与 exists-skip 语义删除；worker `market_data.fetch` 队列新增 `reconcile` 类型
- **L4 缓存失效内聚**：`persist_snapshot` upsert 成功后 `delete_pattern("market:limit-up:calendar:*")`——任何调用方不再依赖"记得清缓存"（skipped 路径不失效）
- **L5 可观测**：`GET /market/data-freshness` 只读巡检（apply=False 复用对账判据），每域报 latest/missing/partial/status
- **L6 已拍板划掉**：后续部署服务器，开发机不做宿主改造
- **实机验收（superpowers 全流程 TDD）**：删 9/15 四域数据 → scheduler 重启 +2min 启动对账**自动补回**（quotes/basic 5546、limits 5560 行）；**首跑还发现了无人知晓的历史盲区**——情绪表 9/03-9/11 七天从未落库，一并补齐，日历趋势线 1→10 点且缓存经失效路径自动更新（无手动 DEL）；freshness 端点四域全 ok。测试 268 passed（新增 24 个：配置/对账/失效/端点/触发），ruff check+format ✓、mypy ✓
- 涉及模块：backend/app/scheduler/{runner,jobs}.py, backend/app/services/{reconciliation_service 新增,limit_up_service}.py, backend/app/schemas/{task,reconciliation 新增}.py, backend/app/api/v1/market_data.py, backend/app/workers/market_data_worker.py, backend/tests/×5 新增, plans/2026-09-17-data-sync-self-healing.md, docs/{Changelog,references/best-practices}.md

## 2026-09-17 - 数据定时同步根治方案设计：对账式自愈数据面（计划入库，未实施）
- **动机**：三次同构事故（9/10-9/14 partial 死锁、9/15-9/16 宿主挂起任务全丢）+ 一次缓存固化，共同模式是**正确性寄托在"调度触发"这个最脆弱环节**——开发机宿主睡眠是常态，方案必须让完整性由对账层保证
- **方案**（plans/2026-09-17-data-sync-self-healing.md，L1-L6 分层）：L1 `job_defaults={misfire_grace_time: None, coalesce: True}` 硬化（盘中四任务例外 300s）；L2 新增 `reconciliation_service.reconcile_market_data` 对账收敛器（trade_cal 期望集 × 行数阈值判据，触发三处冗余：启动+2min / 17:45 cron / worker 手动）；L3 删"只补 T-1+exists-skip"改窗口补漏（结构性解 partial 死锁）；L4 calendar 缓存失效内聚进 `persist_snapshot`；L5 `/market/data-freshness` 只读巡检端点；L6 宿主环境（关 Resource Saver）降级为非依赖项
- **关键设计决策**：完整性以**行数量级**（≥0.8×stocks 活跃数）而非存在性判定；派生表写入与读缓存失效同一服务方法内完成；数据时点维持 T-1 语义（当日拉取另立任务）
- **状态**：设计已定稿待评审，Phase 1（硬化+缓存失效）→ Phase 2（对账器+语义重写）→ Phase 3（扩域+UI 角标）分期实施，尚未动代码
- 涉及模块：plans/2026-09-17-data-sync-self-healing.md（新增）, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-17 - 手动补齐 9/15、9/16 行情与情绪数据 + 清除日历缓存固化（数据运维，无代码改动）
- **现象与根因**：情绪页 `as_of` 停在 9/14——9/16 16:21 主栈启动后宿主**再次挂起**（"Up 4 minutes" + scheduler 日志停在启动行后零输出），9/16 的 16:30/16:50/17:15 盘后任务链又一次没跑，叠加 9/15 缺口，两个交易日全靠手动补；Changelog 9/16 条「今晚任务链自动补齐」的预期落空，验证了 grace/coalesce 未落地前宿主挂起必复现
- **手动补数（worker 容器内按依赖链执行）**：`ingest_daily_quotes`+`ingest_daily_basic`（9/15、9/16 各 upsert 5546 行）→ `ingest_stock_price_limits`（按 daily_quotes 缺失日自动补漏，两日共 11120 行）→ `persist_snapshot(as_of=…)` 逐日落库（9/15：涨停 32/跌停 34/最高 5 板；9/16：涨停 91/跌停 4/最高 6 板）
- **新发现缓存固化坑**：`get_calendar` 的 Redis 键 `market:limit-up:calendar:{days}` 把"只有 9/14"的旧结果集固化（非空即缓存），补数后日历端点仍只返回 1 点——`DEL market:limit-up:calendar:*` 后恢复 3 点，趋势线与环比随之可用；沉淀进 best-practices（补数验证必须 DB + API 两端核对）
- **顺手修复**：best-practices.md 三条条目残留历史编辑混入的行号前缀脏数据（`108\t-`/`109\t-`/`110\t-`），已清理
- **遗留**：待办 A（misfire_grace_time/coalesce + 补漏窗口）与 C（关 Docker Desktop Resource Saver）仍未落地，宿主挂起即复现，9/17 盘后任务能否自动跑取决于宿主是否活跃
- 涉及模块：数据运维（补数/清缓存）, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-16 - 部署源切换到 main：docker 卷名锚定 + 清理 9/15 partial 脏数据
- **部署切换（用户拍板：只保留 main 分支 docker 栈，验证一律 kill 旧服务后重新部署）**：`stock_bot_wt_p7` 旧栈 down（保留卷）→ 主 worktree `docker compose up -d --build`（main 代码）。新 scheduler 注册 **18 个任务，含旧栈缺失的 `Price limits daily`（16:50）与 `Market sentiment daily`（17:15）**；前端为含短线情绪/申万榜重设计的 dist
- **卷名锚定（防空库）**：运行数据在 `stock_bot_wt_p7_*` 四个卷里，而主 worktree 默认 project=`stock_bot` 会新建空卷——docker-compose.yml volumes 段显式 `name:` 锚定既有四卷（postgres/auth/redis/rabbitmq），数据零丢失；注释写明原因
- **3002 dev server 彻底下线**：TaskStop 两次留下 vite 孤儿均按 `taskkill //F //T` 补杀；3000/3001/3002 全清，此后验证一律走 `localhost:80` docker 栈
- **9/15 partial 脏数据清除**：重启后 `as_of` 卡在 9/15 且 `is_partial=true`——根因是 backend 启动的"3 年日线覆盖"任务从 TuShare 单股接口拉到 9/15 已生成的仅 2 行（数据源侧当日未出全）写入库；而 16:30 回补 job 用"存在即跳过"判据会被这 2 行挡住形成死锁。已 `DELETE` 该 2 行，`as_of` 回到完整态 9/14，今晚 16:30/16:50/17:15 任务链自动补齐 9/15 全量（含限价与情绪快照）
- **已知误伤（无害）**：清理时把 9/15 完整的 5546 行 `daily_basic_indicators` 一并删除（TuShare daily_basic 出数早于 daily，属好数据被误判 partial）——今天 16:45 任务检测缺失会自动重拉全量
- **遗留风险**：APScheduler `misfire_grace_time` 仍为默认 1s（方案 A 未落地），宿主睡眠/Resource Saver 期间任务仍会被丢弃——今天 16:50/17:15 任务能否执行取决于宿主是否活跃；建议尽快落地 grace 配置 + 关闭 Docker Desktop Resource Saver
- 涉及模块：docker-compose.yml（volumes 锚定）, 部署运维（栈切换/脏数据清理）, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-16 - 回补失败根因诊断：宿主挂起 + misfire_grace_time=1s 全量丢弃
- **现象**：情绪接口 `as_of` 停在 9/14，9/15 数据从未回补；历史上 9/10、9/11、9/14 均需手动补
- **取证**：scheduler 容器 26h 日志 **0 次 "Running job"**，每个任务槽位只有 `Run time of job ... was missed by ...`（迟到 17s~3m47s，第一个就是 9/15 07:00 晚 3m47s）；RestartCount=0、无 OOM；RabbitMQ 全队列 0 消息且消费者健在（消息从未发布）
- **反证**：容器内 asyncio `sleep(5)` 实测三连 5.00s 零漂移（宿主活跃时定时器正常）→ 排除任务/代码阻塞，锁定**宿主睡眠或 Docker Desktop Resource Saver 把容器进程成段挂起**，醒来必超 APScheduler 默认 `misfire_grace_time=1s` → 到点任务全量静默丢弃
- **放大器**：`_fetch_yesterday_daily_quotes/_daily_basic` 只补 T-1、存在即跳过、无补漏窗口；运行栈是 wt_p7 旧镜像，调度器里根本没有 price_limits/sentiment 任务
- 排障提示：scheduler 容器日志时间戳为 **UTC**（北京 = UTC+8），"15:41 静默"实为北京 23:41 后夜间无任务
- 教训沉淀：docs/references/best-practices.md（misfire_grace_time + 容器挂起 + 补漏窗口 + UTC 日志）
- 涉及模块：诊断（无代码改动）, docs/references/best-practices.md

## 2026-09-15 - 服务管理规则固化 + 申万三级最高板热度榜重设计 + 昨日涨停卡口径澄清
- **服务管理规则（用户要求）**：Agent 起长驻服务前必须清掉旧实例——实测 wt_landing worktree 的 vite 在 3000/3001 各挂一个、上轮 TaskStop 只杀 npm 父进程留下 vite 孤儿继续占 3002；本次 `taskkill //F //T` 全部清掉并固化规则进 AGENTS.md 新增「服务管理约定」（netstat 查占用 → Get-CimInstance 确认身份 → `//T` 杀进程树 → 收尾不留孤儿/保留须报端口与 PID）
- **申万三级最高板重设计**（`SwL3LimitUpBoard` 重写）：block Segmented 塞 21 个一级行业把标签截成单字（不可用），换 CheckableTag wrap + 家数徽标；新增「仅看 ≥2 板」开关；表格换自绘热度榜行——板高 4 格热度色阶（与梯队同语言）+ 涨停家数占比条 + 板高 desc→家数 desc→l3Code 确定性排序；行点击跳龙头个股与 `.sector-limit-up` 根类契约保留
- **昨日涨停卡口径澄清**：卡头「数据截至」从涨停日（as_of_prev，显示 9月11日）改为表现日（as_of，9月14日），卡内首行新增「统计 9月11日 涨停股在 9月14日 的表现 · 行情 T+1 回补」——旧标法被用户误读为"数据落后一个交易日"，实为双日口径表达问题；当日表现等 T+1 回补后自然生成
- **契约同步**：limit-up-sentiment.md「UI 呈现契约」补 SwL3 与 as_of 口径两节；e2e 补「仅看 ≥2 板」与口径行断言
- 验证：`npm run build` ✔、vite dev(3002) 实测：CheckableTag 过滤（电子→3 行）、叠加仅连板（→1 行）、还原 38 行、明暗双主题
- 涉及模块：AGENTS.md（服务管理约定）, frontend/src/features/market/components/{SwL3LimitUpBoard.tsx,SwL3LimitUpBoard.css,YesterdayLimitUp.tsx}, frontend/src/pages/market/index.tsx, frontend/e2e/limitUpSentiment.spec.ts, docs/design/limit-up-sentiment.md, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-15 - 短线情绪 Tab 重设计：hero 风 KPI 温度计 + 连板梯队分档列（修复比值显示 bug）
- **修数据展示 bug**：`broken_rate`/`promo_1to2`/`promo_2to3` 后端是 0-1 比值，旧 UI 直接 `toFixed(2)%` 把 40.62% 显示成 "0.41%"、晋级率 "0.21%"/"0.75%"——重设计后统一 ×100 渲染（40.62% / 21.21% / 75.00%），契约已写入 limit-up-sentiment.md「UI 呈现契约」
- **情绪温度计 hero 化**（新 `SentimentThermometer` 替代 `SentimentHeader` datarow 版式）：涨停/炸板/跌停大数字瓦片 + 多空力量条（家数占比）+ 6 个二级指标瓦片（新露出 `yzt_avg_open_premium` 今开溢价均值）+ 涨停/跌停趋势线（纯 SVG）；环比 chip 与趋势线接通既有但从未消费的 `/market/sentiment/calendar` 端点（30 日），无前值只藏 chip、日历滞后 as_of 时以实时 KPI 补趋势末点、<2 点显示"累积中"占位
- **连板梯队分档列**（`LimitUpLadder` 重写）：每档一卡（板高热度色阶 + 家数占比条 + 家数 chip）替代平铺 Tag 流；档内按封板时间升序、行可点跳个股页；新露出 `seal_fund` 封单额与 `break_count` 炸板次数（仅 >0 时警示显示）；首板默认 12 家折叠 + "展开全部"；汇总 pill（空间板/连板/首板家数）
- **配套**：温度计卡头补 `数据截至` 行；`e2e/limitUpSentiment.spec.ts` 同步新结构（`.thermo` 根类 + 比值 ×100 断言 + calendar 路由 mock），内容契约（`--`/「缺少 N 个交易日」/降级文案）保持不变
- 验证：`npm run build`（tsc+vite）✔、`npm run check:design` 8/8 PASS、vite dev + chrome-devtools 实测明暗双主题/390px 移动端/环比 chip 数值逐项核对
- 涉及模块：frontend/src/features/market/components（SentimentThermometer 新增、LimitUpLadder 重写、SentimentHeader 删除）, frontend/src/pages/market/index.tsx, frontend/e2e/limitUpSentiment.spec.ts, docs/design/limit-up-sentiment.md, docs/references/best-practices.md

## 2026-09-15 - 数据回填：补齐日线缺口 + 权威 pct_chg 全量回填（数据运维，无代码改动）
- **发现**：`daily_quotes` 缺 2026-09-10 / 09-11 / 09-14 三个交易日（09-11 仅 1 行、09-14 仅 3 行），而 `daily_basic_indicators` 同期**有** 5548 行/日——两条回补链路覆盖不一致，缺口因此极难察觉；下游连板梯队只是把 `as_of_prev` 悄悄滑回 09-09，不报错
- **补齐日线**：按交易日显式重拉 `ingest_daily_quotes`（09-10 / 09-11 / 09-14，各 upsert 5548 行，原生 `pct_chg`/`pre_close` 一并落库），日线缺口归零
- **权威 pct_chg 全量回填**：`scripts/backfill_pct_chg.py --years 3` → **725 个交易日 / 3,881,294 行**；3 年窗口 `pct_chg` 覆盖率 **99.867%**（NULL 0.133% ≈ 停牌/新股），最新 3 个交易日 NULL 数为 0，满足计划 Task 2.3 的「< 1%」判据
- **限价补漏**：`ingest_stock_price_limits` 自动识别 20 个缺失交易日并补全 → **111,064 行**（2026-08-18 ~ 2026-09-14）
- **情绪快照**：`persist_snapshot` 落库 2026-09-14（涨停 57 / 跌停 18 / 炸板 39 / 破板率 0.4062 / 昨涨停今均 +3.2131% / max_streak 4 = `000993` 闽东电力）
- **结果**：`/market/{rankings,distribution,limit-up-ladder,yesterday-limit-up,sector-limit-up}` 全部返回真实数据，`degraded_reason` 由 `price_limits_missing` 变为 `null`；`/market/rankings` 的 `as_of` 从 09-09 前进到 **2026-09-14**
- **遗留（建议另立修复）**：`_fetch_yesterday_daily_quotes` 只拉「上一个工作日」且命中即跳过，没有补漏窗口——调度器停摆一天就会留下永久空洞，而限价链路有补漏、日线没有（已沉淀进 best-practices 一）
- 涉及模块：数据运维（无代码改动）, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-14 - 行尾归一化入库（.gitattributes）+ 双端检出与工作区属主修复
- **问题**：Windows 侧检出工作区为 CRLF、index 存 LF，WSL 侧未设 `core.autocrlf` → `git status` 一次性报 380 个文件「整体改写」（+76296/-76278），实际改动只有 2 个文件；同时仓库文件属主为 root，`.git` 不可写导致任何 commit/pull 必然失败
- **修复**：新增仓库根 `.gitattributes`（`* text=auto eol=lf`，二进制由 `text=auto` 自动跳过），gitattributes 优先于 `core.autocrlf`，双端不再需要各自的本地配置；工作区重新检出为 LF，`scripts/*.sh` 从此可在 WSL 直接 `bash` 执行（此前报 `set: pipefail: invalid option`）
- **renormalize**：`git add --renormalize .` 归一化了唯一以 CRLF 入库的 blob —— `backend/tests/fixtures/caaa_article.html`（461 行）；该 fixture 由 `Path.read_text()` 读取，行尾对其断言无影响
- **副产品**：4 个 worktree 的 `.git` 由 Windows 绝对路径（`F:/...`）改为相对路径（`gitdir: ../stock_bot/.git/worktrees/<name>`），WSL 侧恢复可用（原先 `git worktree list` 全部标 `prunable`）
- **踩坑**：批量重写工作区后 `git status` 仍报几百个 phantom 修改而 `git diff` 为空——index 缓存的 stat（size/ino）停在旧值，补一次 `git add -A` 重新 stat 才归零
- 验证：`git status --porcelain` 仅剩预期改动；`git diff --stat origin/main` 收敛到 .gitattributes + fixture 归一化 + Changelog/best-practices 四个文件
- 涉及模块：.gitattributes（新增）, backend/tests/fixtures/caaa_article.html, docs/Changelog.md, docs/references/best-practices.md

## 2026-09-14 - CI docker-smoke 根因修复：网关缺健康检查导致 `--wait` 提前返回
- **现象**：`Docker Compose Smoke Test` 全容器 Healthy，但经网关的**所有**路径都返回 Traefik 自己的 404（body 恰 19 字节 `404 page not found`，含 `PathPrefix(/` 的前端首页）；第一个失败还被 `curl -f ... | head -1` 吞掉退出码，由第二个 curl 以 exit 22 中断 job。
- **根因**：`gateway` 服务**没有 healthcheck**（compose 里 7 个服务有、它没有），所以 `docker compose up -d --build --wait` 在 Traefik 容器刚 Running 时就返回，而 Traefik 还需一个节拍去订阅 Docker 事件、把容器 labels 变成 router；这段窗口内路由表为空 → 一切 404。CI 日志实证：诊断步骤（+约 1.4s）后 `/api/rawdata` 已列出全部 `*@docker` router，健康检查 5/5 首次尝试即 200 —— 即窗口真实存在，且只有「立刻断言」才会踩中。
- **修复（治根而非加 sleep）**：给 `gateway` 加健康检查，断言**路由表已加载**（`wget -qO- http://127.0.0.1:8080/api/rawdata | grep -q 'frontend@docker'`），让 `up --wait` 等待真正的就绪条件；同时把 smoke 步骤的 `curl -f` 改成**条件式轮询**（30×2s，打印首次尝试与失败响应体）作为纵深防御，并新增 `Diagnose gateway routing (always)` / `Gateway logs on failure` 两步，把网关版本、容器状态、rawdata 路由表与日志留在现场（网关 dashboard 只在容器内可达，故用 `compose exec`）。
- 验证：`docker compose config -q` ✔；CI `Docker Compose Smoke Test` 由 failure → success（含后续 `Auth login closed-loop smoke`）。
- 涉及模块：docker-compose.yml（gateway healthcheck）, .github/workflows/ci.yml（docker-smoke 三步骤）, docs/build.md（启动流程补网关层就绪条件）, docs/references/best-practices.md

## 2026-09-14 - 连板梯队 PR#9 CI 修复：ruff format 本地门禁与 CI 口径对齐
- **CI `Lint (backend)` 转红**：CI 的后端 lint job 跑的是 `ruff check app/ tests/` **外加** `ruff format --check app/ tests/`（`.github/workflows/ci.yml:24-25`），本特性各任务的本地自检只跑 `ruff check`，于是 16 个改动文件从没被 format 过——本地全绿、CI 必红。修复：`uv run ruff format app/ tests/` 格式化本特性全部改动文件（16 files reformatted，其中 0 个是既有文件，说明全部由本 PR 引入）。
- **补门禁防复发**：`scripts/self_review.sh` 的 ruff 步骤增加 `ruff format --check $PY`（与 CI 同口径、同样只看改动文件），并在脚本头注释与失败提示里写明「跑 `uv run ruff format <files>`」。自检现在能提前抓到这类问题，而不必等 CI。
- 验证：`ruff format --check app/ tests/` → 194 files already formatted；`ruff check app/ tests/` → All checks passed；`uv run pytest -q` 246 passed / 30 deselected；`mypy app` 150 files clean；`bash scripts/self_review.sh` 新 format 步骤 ✔
- 涉及模块：backend/app/**/*.py（本特性 16 个文件格式化）, scripts/self_review.sh, docs/references/best-practices.md

## 2026-09-14 - 连板梯队 终审修复：SQLite 合成夹具把 gaps-and-islands 不变量钉进默认门禁 + 修 spec 自相矛盾（仅测试/文档）
- **streak 不变量默认门禁守卫**（终审 #1/#2）：新增 `backend/tests/test_limit_up_window_sql.py`（无 `-m e2e`、无 Postgres），在 SQLite 内存库建最小 schema（daily_quotes/stock_price_limits/stocks/sw_industry_members/sw_industry_classes），**直接 import `limit_up_repo._WINDOW_SQL`**（不复制 SQL 文本）跑合成夹具：股 1 is_lu 序列 [T, F, T, T, F]（其中 F 日有行情无限价行 → 走 COALESCE），钉死 false 岛后 true 岛必须从 1 重启（D3/D4 streak_upto=1/2）与缺限价行日 `is_lu`/`touched` 为布尔假而非 NULL；股 2 覆盖零跑不抬升涨停 streak；同文件搬入无扇出（(stock_id,trade_date) 唯一）与整窗（>2 个不同 trade_date）断言。变异验证：临时把 `PARTITION BY i.stock_id, i.is_lu, i.grp` 去掉 `is_lu` → D3/D4 变 2/3（红）；临时去掉两个 `COALESCE(..., false)` → `is_lu=None`（红）；还原后全绿且 `git diff` 对 `limit_up_repo.py` 为空。`test_limit_up_repo.py` 只留真库计划形状守卫（`-m e2e`），docstring 注明无扇出/整窗已上移默认门禁
- **spec 自相矛盾**（终审 #3）：`docs/design/limit-up-sentiment.md:118` 把已作废的「34ms / 895 行」改为「16 交易日整窗 2,411 行 / ~48ms，见 §六」，与 §六实测口径一致
- 验证：`uv run pytest -q` 246 passed / 30 deselected（新增 4 例默认门禁测试）；`uv run pytest tests/test_limit_up_repo.py -v -m e2e` 1 passed；`ruff check` 改动文件、`mypy app`、`bash scripts/self_review.sh` 全绿
- 涉及模块：backend/tests/test_limit_up_window_sql.py（新增）, backend/tests/test_limit_up_repo.py, docs/design/limit-up-sentiment.md, docs/references/best-practices.md

## 2026-09-14 - 连板梯队与市场情绪：本地 K 线自算主干 + 限价权威表 + 降级契约
- **权威限价**：新增 `stock_price_limits`（TuShare `stk_limit` 原值，`Numeric(12,4)`，唯一键 `(trade_date, stock_id)`）+ 按交易日补漏 ingest（16:50）。**`fetch_stk_limit` 必须显式传 `fields`**：`pre_close` 是 TuShare「默认显示=N」字段，不传只回 `[trade_date, ts_code, up_limit, down_limit]`（2026-09-14 实测），整列 NULL 且不报错；传 `fields` 后 5,637/5,637 非空。**实测否决了名称启发式**：2026-09-08 权威判 75 只涨停、名称 ST/前缀启发式判 83 只，10 处分歧里 9 只是 ST 名称股真实限幅为 10%（`ST晨鸣` up_limit=2.13 / pre_close=1.94）；前收一律用 `stk_limit.pre_close`（交易所口径、含除权），用 LAG 当日前收会把涨停数算成 142
- **本地自算主干**：`limit_up_repo` 单条窗口 SQL（候选 CTE 先收敛再回查，实测 16 交易日整窗 2,411 行 / ~48ms；限价表驱动的自然 join 顺序 620ms）+ `limit_up_calculator` 纯函数（梯队 / `N天M板` / 交集式晋级率 / 申万 L3 最高板 / 情绪 KPI）。候选集必须 `DISTINCT`——两日并集重复会让涨停数 75→94 并造出假 8 连板。三条已锁死的坑：①候选集必须 `DISTINCT`——两日并集重复会让涨停数 75→94 并造出假 8 连板（新增扇出守卫测试）；②窗口必须回整段交易日（只回 as_of/as_of_prev 会把 `boards_in_window` 锁在 2、`missing_days` 恒为 14）；③`streaked` 必须 `PARTITION BY stock_id, is_lu, grp`（`grp = rn_all - rn_by_val` 只保证组内常量、不保证组间唯一，实测窗口内 4 行 is_lu 的 streak 被虚高）
- **涨跌幅分母**：昨日涨停股的今日涨幅一律取 **as_of 当日行**的 `pre_close`（= 昨收）。取 as_of_prev 行的 `pre_close` 会变成「今日 ÷ 前前日」的 2 日收益：实测 2026-09-08 从真值 2.8225% 变成 13.9488%（95 只），5 倍偏差且不报错
- **申万 L3 细分最高板**：复用既有 L3→L2→L1 `parent_code` 两跳链，龙头按 `streak DESC, amount DESC, symbol ASC` 确定性裁决，未映射标的进 `unclassified_count` 兜底桶 + 回传 `sw_coverage`（实测黄金日 70/75 = 0.9333）
- **降级契约**：`source` **只用** `local_calc`（东财 `hybk` 是东财板块口径、填不进申万 L3，Web 不产出完整 payload）；限价缺失/当日行数 < 4000（正常 5,490）时返回空 payload + `degraded_reason`（`price_limits_missing` / `partial_day` / `no_quotes` / `no_limit_up_rows` / `insufficient_trade_days`），**不猜比例**；读端点不写库，唯一外呼是东财增强（失败静默）
- **Web 增强**：东财涨停池（`push2ex/getTopicZTPool`，`date` 必填、客户端 10s 超时）按 `as_of` 取数注入封板时间/封单资金/炸板次数，失败只 log 不改主路径状态；本地路径这三字段为 `null`，前端渲染 `--`。`push2delay` 对该端点返回空，故硬编码 `push2ex`
- **情绪周期**：`market_sentiment_daily`（纯派生缓存，可重建）盘后 17:15 落库 + `GET /market/sentiment/calendar`
- **验证**：TDD 先红后绿——新增单测 29 例（`test_limit_up_ingest.py` 3 / `test_limit_up_calculator.py` 纯函数 12（含 off-by-one、停牌 policy A、pre_close 分母）/ `test_limit_up_service.py` 降级契约与快照 9 / `test_limit_up_web.py` 映射、失败隔离、降级不外呼 5），另有 `@pytest.mark.e2e` 的 `test_limit_up_repo.py` 扇出/窗口完整性/计划守卫 2 例。黄金日 2026-09-08 全链路口径一致（梯队 4板×3/3板×3/2板×13/首板×56，涨停 75 / 跌停 1 / 炸板 39，昨日涨停池 95 只今日均值 +2.8225% / 今开溢价 +3.11%，`sw_coverage` 0.9333）。`uv run pytest -q` 242 passed / 31 deselected（基线 213/29，净增 29 单测）；`mypy app`、`npx tsc -b`、`npm run check:design`、`bash scripts/self_review.sh` 全绿；本特性 e2e `limitUpSentiment.spec.ts` 3/3 通过。既有问题（非本特性引入，如实记录）：`uv run --extra dev ruff check .` 全量 27~28 个错误全部在 `scripts/`、`crons/SSE/` 等既有文件，本特性改动文件 ruff 全绿；前端 `npm run lint` 不可用（仓库无 eslint 二进制/配置，2026-09-11 已记录）；全量 e2e 本机 42 passed / 16 failed / 5 skipped，成因为环境缺 gateway 容器（`docker ps` 无 traefik，`frontend/vite.config.ts` 的 `/api` 代理指向 `localhost:80`），凡未自带 mock 的用例一律 500，历史基线（网关在跑时）曾为 3 项既有失败
- 涉及模块：backend/app/{models/market_data.py,repositories/{limit_up_repo,stock_repo}.py,services/{limit_up_calculator,limit_up_service,market_data_service,tushare_ingest}.py,core/providers/{eastmoney_client,tushare_client}.py,schemas/{limit_up,task}.py,api/v1/market_data.py,workers/market_data_worker.py,scheduler/{jobs,runner}.py,migrations/versions/{a7c1f0b2d3e4_*,b8d2e1c3f4a5_*}}, backend/tests/test_limit_up_{ingest,repo,calculator,service,web}.py, frontend/src/{shared/api/limitUp.ts,features/market/components/{SentimentHeader,LimitUpLadder,SwL3LimitUpBoard,YesterdayLimitUp}.tsx,pages/market/index.tsx}, frontend/e2e/limitUpSentiment.spec.ts, docs/design/limit-up-sentiment.md

## 2026-09-14 - 连板梯队 Task 4 补钉：钉死严格 no_limit_up_rows 路径与降级快照不写缓存（仅测试）
- 控制器规则第三轮只加测试、不动生产代码。新增两条契约测试：①`test_no_limit_up_rows_when_limits_present_but_window_empty` 钉死 round 1 恢复的严格判据精确触发条件（限价存在 + 窗口空 + zt_count=75 ⇒ `degraded_reason=="no_limit_up_rows"`、`echelons==[]`、`kpis=={}`）；②`test_degraded_snapshots_are_not_cached_but_complete_is` 用最小 `_RecordingCache`（get 恒 miss、记录 set）断言 `price_limits_missing` 与 `no_limit_up_rows` 两条降级路径 `cache.set` 未被调用，并补正例控制（完整日快照必被 set、key 含 as_of+lookback 两维、ttl==SNAPSHOT_TTL）。另修正 `_lu_row` docstring「16 键」→「18 键」并说明口径（18=行键数，16=窗口交易日数）。两条新测试均做变异验证确认能真失败（改回旧判据 / 让降级也 set 均转红）
- 验证：`uv run pytest tests/test_limit_up_service.py -v` 6 passed；`uv run pytest -q` 234 passed / 31 deselected；`ruff check` 改动文件、`mypy app`、`bash scripts/self_review.sh` 全绿
- 涉及模块：backend/tests/test_limit_up_service.py, docs/references/best-practices.md

## 2026-09-14 - 连板梯队 Task 4 评审修复：恢复严格 no_limit_up_rows 判据并修正不真实 fixture
- 评审发现 `limit_up_service.get_snapshot` 曾把空候选窗口判据放宽为 `not rows and zt_count == 0` 以迁就不真实测试 fixture（空 window + 非零 zt_count），会产出「表头有 zt/zb、梯队/板块/昨日全空、无 degraded_reason 且被缓存」的半成品 payload。修复方向按控制器裁定：**恢复严格判据 `if not rows: → no_limit_up_rows`（改测试而非改生产语义）**，新增 `_lu_row` 构造器让两个 fixture 给出与 T2 `fetch_limit_up_window` 同形状（16 键）的非空窗口，并在完整日用例追加 `assert snap["echelons"]` 回归护栏（完整日必须真产出梯队）；断言值不变。真库 2026-09-08 复验仍与黄金日一致（4板×3/3板×3/2板×13/首板×56，zt=75/dt=1/zb=39，sw_coverage 0.9333，空间板 4/4）
- 验证：`uv run pytest -q` 232 passed / 31 deselected；`ruff check` 改动文件、`mypy app`、`bash scripts/self_review.sh` 全绿
- 涉及模块：backend/app/services/limit_up_service.py, backend/tests/test_limit_up_service.py, docs/references/best-practices.md

## 2026-09-14 - 连板梯队与市场情绪：实施计划审计修订（未落代码）
- **审计发现并修入计划**（`plans/2026-09-14-limit-up-sentiment.md` + `docs/design/limit-up-sentiment.md` 同步）：①`stk_limit` 的 `pre_close` 是 TuShare「默认显示=N」列，`fetch_stk_limit` 不传 `fields` 则整列 NULL（实测 5,637 行无 `pre_close`）；②窗口 SQL 末尾多了一句只回 `as_of`/`as_of_prev` 的 `WHERE`，会让 `boards_in_window≤2`、`missing_days` 恒为 14（16 交易日窗口整窗 2,411 行 vs 只回两天 302 行）；③gaps-and-islands 的 `grp = rn_all - rn_by_val` 只保证组内常量、不保证组间唯一，`streak_upto` 的 `PARTITION BY` 必须补 `is_lu`（实测窗口内 4 行 is_lu 被虚高）；④昨日涨停溢价的分母必须取 **as_of 当日行**的 `pre_close`（取前一日行 = 2 日收益，实测 2.8225% → 13.9488%）；⑤`source="web"` 完整 payload 与盘中增强触发条件（`as_of == 今日`）为死代码，已收窄契约：`source` 只用 `local_calc`，Web 仅作增强字段（东财 `hybk` ≠ 申万 L3）；⑥校实并修正计划里的错误指向：`latest_quote_date` 等函数在 `limit_up_repo` 而非 `market_data_repo`、`api/v1/market_data.py` 没有 `_iso()`、`SectionCard` 没有 `asof` 属性、个股页路由是 `/stock/:symbol`、窗口查询实际走 `idx_daily_quotes_stock_date`（非 uq_）、`sw_coverage` 实测 0.9333；⑦补齐自检步骤（`bash scripts/self_review.sh`）与缺失的测试用例（pre_close 字段契约、窗口完整性、pre_close 分母、空候选日不落库）
- 验证：对真库/真接口逐条实跱（TuShare `stk_limit` 2026-09-08 全量 + 东财 `push2ex/getTopicZTPool` 20260807/08/14 + PostgreSQL 16 交易日窗口 EXPLAIN ANALYZE），黄金日梯队 {4板×3, 3板×3, 2板×13, 首板×56} 与广度 75/1/39/5490 可复现；`bash scripts/self_review.sh` ✔
- **复审（第二遍，逐条重验上表）：审计结论成立**——真库跑双版本 SQL 确认 `grp` 撞组使 4 行 `is_lu` 的 streak 虚高（600127 @08-27/09-01 报 3 实为 1），且不影响 09-08 黄金日（所以只会被 `promotion_rate` 静默带坏）；东财池 `date` 语义验证：`date=20260908` 回 73 行、与本地涨停集合 73/73 重合且 `lbc` **100% 一致**，而 `date=20260904` 只重合 6 行且 0 一致 → 端点在按请求日期取数，但 `qdate` 恒为当天、不可作校验（仅服务最近约 20 个交易日，20260825 有行 / 20260820 及更早均 0 行，故对更早的 `as_of` 增强静默无操作属预期）；另修 4 处残留：前端 `source` TS union 未随契约收窄、Task 7 漏登 `shared/ui/{SectionCard,date}.tsx` 与 `format.ts`、`formatCnDate` 的或/或未决、spec 里 “20260707/20260708 可查” 实测为 0 行（应为 20260907/20260908）
- 涉及模块：plans/2026-09-14-limit-up-sentiment.md, docs/design/limit-up-sentiment.md, docs/references/best-practices.md

## 2026-09-11 - 首页公开化 Phase 3 终审修复：登录态导航/口径诚实/DRY/内链/Section 视觉收口（F1–F16）
- **F1 登录态导航**：`LandingNav` 原无条件渲染「登录」按钮，与已登录的 `CtaButton`「进入工作台」并存；改为 `isAuthReady` 后按 `isAuthenticated` 二选一（已登录渲染 `UserMenu`），并在 e2e 加「已登录不得出现登录按钮」断言（先证红：`toHaveCount(0)` 收到 1）
- **F2 公开文案不得宣称已死数据源**：`DataCoverageMatrix` 北向资金行标「规划中」（来源/频率 `—`、徽章中性灰），统计带 `10 大数据域` → `9 已上线数据域`、`100% 全部免费` → `已上线数据免费`；同步 `docs/design/landing-market-theme.md` §5
- **F3 快讯故障 ≠ 无新闻**（口径诚实，与脉搏块同批）：`AnnouncementFeed` 增 `isError` 分支独立错误占位「公告快讯暂不可用，请稍后重试」，不再落回 `暂无公告快讯`；e2e abort `/market/announcements` 断言错误占位（先证红）
- **F4 as_of/口径分家**：`SectorFlow` 申万口径补「· 数据截至 9月9日」（复用抽到 `format.ts#formatCnDate` 的格式化与 `section-card__asof` 样式）；`MarketPulse` 把「实时 + 每 60 秒刷新」限定到指数条 caption，分布/家数改「当日 …（T+1）」措辞
- **F5–F7 类型/DRY/内链**：`market.ts` `total_amount` 改 `number | null` 对齐后端 `float | None`；新增 `features/market/components/coreIndices.ts`（`CORE_TS_CODES` + `pickCoreIndices(list, count, isEligibleRest)`，其余补位过滤由调用方传入），宣传页指数条与核心指数卡共用；`SectionCard`/`DataRow` 内链改 `react-router` `<Link>`，消除整页刷新
- **F8/F9 区块视觉/占位**：`MarketMoneyflowCard` 抽出 `MarketMoneyflowContent`，`MoneySentiment` 在 `SectionCard` 内直接渲染纯内容 + 同构子标题（不再卡中卡，`/market` 用法不变）；日历块永久骨架屏改显式文案「日历即将上线」
- **F10–F16 收尾**：`CompactHero` 注释如实说明徽章为静态字符串（本页无时间源）；`get_latest_trade_date` 非 str/非法缓存值不再 `cast` 穿透，改为回退 DB；榜单 quote/turnover SQL 补 `stock_id ASC` 确定性 tiebreak（新增 3 条子串断言）；`RankingMatrix` 删除无样式死类 `ranking__asof`，改用有规则的 `section-card__asof`；`SectorFlow` 区分网络故障与合法空载荷占位；`test_market_contract._load` 全注解 `dict[str, Any]`；`landing.spec.ts` 固定 `waitForTimeout(800)` 改 network-idle + 稳定 URL
- **验证**：E2E(3010) 57 passed / 3 failed（3 项为 research×2 + userIsolation×1 既有失败，本分支未触碰该两文件）；`uv run pytest -q` 213 passed / 29 deselected（+4 新单测）；`npx tsc -b`、`npm run check:design` 12/12、`bash scripts/self_review.sh` 全绿（`npm run lint` 因 node_modules 缺 eslint 二进制不可用，非本次改动引入）
- 涉及模块：frontend/src/pages/landing/{index.tsx,landing.css,sections/{LandingNav,MarketPulse,CompactHero}.tsx}, frontend/src/features/market/components/{DataCoverageMatrix.{tsx,css},AnnouncementFeed,MarketMoneyflowCard,MoneySentiment.{tsx,css},RankingMatrix,SectorFlow,CoreIndexCards,coreIndices,format,index}.ts, frontend/src/shared/{ui/{SectionCard.{tsx,css},DataRow.tsx},api/market.ts}, frontend/e2e/{landing,public-homepage}.spec.ts, backend/app/services/market_service.py, backend/tests/{test_latest_trade_date,test_rankings,test_market_contract}.py, docs/design/landing-market-theme.md

## 2026-09-11 - 首页公开化 Phase 3 复审修复二：契约测试惰性解析 fixture + 恢复万亿元档
- **契约测试不再在 import 期算路径**：`test_market_contract.py` 原在模块级 `FIXTURES_DIR = _fixtures_dir()`，纯后端检出（无 `frontend/`）时 `_fixtures_dir()` 抛 `RuntimeError`——pytest 为读 `pytestmark` 会先 import 模块，早于 `-m` 反选，导致默认 `uv run pytest` 变成 collection ERROR。改为 `_load()` 内惰性解析，fixture 目录/文件缺失时 `pytest.skip(...)`；实测把 `fixtures/` 改名隐藏后 `uv run pytest -q` 仍 209 passed / 29 deselected 且无 collection error，`-m e2e` 为 2 skipped；fixtures 就位时容器内 2 passed
- **恢复 `fmtAmountParts` 的 `≥1e12 元 → 万亿元` 档**：前一轮把局部 `formatAmount` 抽成共享 helper 时漏了该档，当前数据不可达但属共享能力回退；已按原实现补回
- 验证：`uv run pytest -q` 209/29（fixtures 有/无均无 collection error）、契约测试容器内 2 passed、`npx tsc -b`、`npm run check:design` 12/12、E2E(3010) 32/32、`self_review` ✔
- 涉及模块：backend/tests/test_market_contract.py, frontend/src/features/market/components/format.ts, docs/references/best-practices.md

## 2026-09-11 - 首页公开化 Phase 3 复审修复：榜单/申万契约快照锁 + 行业广度条 + 缺失值收口
- **契约漂移防护**：把容器内实抓的 `/market/rankings`（四类）与 `/market/sw-industry/performance` 真实响应落为 `frontend/e2e/fixtures/rankings.sample.json` / `swPerformance.sample.json`；前端 e2e mock 改为读同一批文件（不再手写载荷），并新增 `backend/tests/test_market_contract.py`（`@pytest.mark.e2e`，DB 实连）断言两端点实际发出的顶层/条目 key 集与 fixtures 完全一致——后端字段改名会先让契约测试转红，而不是 mock 静默漂移后页面渲染 undefined。**仍不证明活链路**（3010 dev 代理指向旧镜像，故 mock 是必需的），活链路验证待分支部署
- **Task 3.2 广度条补齐**（计划 Step 2）：新增共享 `features/market/components/BreadthBar`（上涨红/下跌绿按 up_count:down_count 比例，4px 行内条，复用 `--up`/`--down`），从该目录 `index.ts` 导出，SectorFlow 申万行业行在 `DataRow` 下方消费
- **缺失值收口**：`SwPerformanceItemOut.total_amount` 改 `float | None`（`sum(amount)` 可空，原非可选会在匿名端点触发 Pydantic 500）；`DataRow` 增可选 `valuePlaceholder`，数值缺失渲染 `--`（纯涨跌幅行不传、行为不变），RankingMatrix 三 Tab 与 SectorFlow 成交额槽位消费；`amount` 千元换算抽到 `features/market/components/format.ts#fmtAmountParts` 供两处复用；总成交额作为行业行 value 显示（千元→元→亿元）
- **口径诚实**：SectorFlow 成员数小字改为「当日有行情 N只」（`member_count` 是当日 `pct_chg` 非空的成员数，非静态成分总数）
- **验证**：`uv run pytest -q` 209 passed / 29 deselected（新增 total_amount 可空单测）；契约测试容器内 2 passed（新增 `close_redis_pool()` 到 autouse dispose，避免模块级 Redis 池跨 event loop）；`npx tsc -b`、`npm run check:design`、`self_review` 全绿；E2E(3010) 32/32
- 涉及模块：backend/app/schemas/sw_performance.py, backend/tests/{test_sw_performance,test_market_contract}.py, frontend/e2e/fixtures/*, frontend/e2e/public-homepage.spec.ts, frontend/src/features/market/components/{BreadthBar.tsx,BreadthBar.css,SectorFlow.tsx,SectorFlow.css,RankingMatrix.tsx,format.ts,index.ts}, frontend/src/shared/ui/DataRow.tsx

## 2026-09-11 - 首页公开化 Phase 3（Task 2.7 / 3.1 / 3.2）：榜单切专用端点 + 申万一级行业聚合 + 板块区申万口径
- **Task 2.7 榜单**：`features/market/components/RankingMatrix` 从临时 enriched 排序路径切到 `GET /market/rankings`（新增 `shared/api/market.ts#fetchRankings`/`RankingType`/`RankingResponse`）；Tab 扩为四项（涨幅/跌幅/成交额/换手率，后端五种类型中 `volume` 不上首页）；头部按 `as_of` 显示「数据截至 9月9日」；`daily_quotes.amount` 千元口径沿用既有 mapper 约定（×1000→元 再 /1e8→亿元）不做「修正」；**移除 D2 遗留的客户端 null 行过滤**（Ruling T：新 SQL 已 `pct_chg IS NOT NULL`，客户端为重复层），保留 `DeltaText` 真缺失渲染 `--` 契约；确认零引用后删除临时 `fetchStockRanking` 及其 `RankingRow`/`RankingSortBy` 类型
- **Task 3.1 申万一级行情聚合**：新增 `GET /api/v1/market/sw-industry/performance?limit=31`（`app/schemas/sw_performance.py` + `market_service.get_sw_industry_performance` + `_SW_PERF_SQL`）：以 `sw_industry_members.symbol → stocks.symbol` 为 join 键，经 `sw_industry_classes` 两跳 `parent_code` 链把 L3 成员上卷到 L1（L1=31/L2=134/L3=346，全 L3 可解析）；聚合前 JOIN `daily_quotes` 于最新交易日并 `pct_chg IS NOT NULL`，故 `member_count`/`up_count`/`down_count` **只计当日真有行情的股票**、`avg_pct_chg` 为这些股票的真实均值；`total_amount`（`sum(amount)`）为 TuShare 千元、透传原值（注释据实标注）；匿名端点走 `CacheClient`（key `market:sw-performance`，`_MARKET_CACHE_TTL`）且空库降级为空 payload（不缓存）；`as_of` 复用 `get_latest_trade_date`
- **Task 3.2 板块区切申万**：`SectorFlow` 左列改 `fetchSwPerformance`（行版式 = 行业名 + `member_count` 小字 `35只` + `DeltaText(avg_pct_chg)`），口径标注改为「行业口径：申万一级」并删除「申万版即将上线」；**保留 CSRC 回退**——申万请求失败时回退 `/market/sectors` 且标注同步切回「行业口径：证监会」，标注永远与所展示的数据源一致，绝不静默错标（`retry: 0` 使回退即时）
- **验证**：TDD 先红后绿——Task 2.7 三条新断言先红（换手率 Tab/数据截至/不剔除 null）后 4 passed；Task 3.1 ImportError 先红后 7 passed；Task 3.2 申万标注断言先红后 4 passed；容器内以本分支代码 + ASGI 连真库实跑新端点，`limit=5` 200（as_of=2026-09-09，与原生 SQL 逐值一致）、默认 31 条且降序、`limit=0/99` 422，Redis `market:sw-performance` TTL≈277s；真库 SQL 实测 31 个 L1 组、4,058 个当日有行情成员且无 symbol 扇出
- 测试：`uv run pytest -q` 208 passed / 27 deselected；`uv run --extra dev mypy app` 143 files 无问题；前端 `npx tsc -b`、`npm run check:design`、`bash scripts/self_review.sh` 全绿；E2E（3010）landing 7 + darkmode 3 + public-homepage 22 = 32/32
- 涉及模块：backend/app/services/market_service.py, backend/app/schemas/sw_performance.py, backend/app/api/v1/market.py, backend/tests/test_sw_performance.py, frontend/src/shared/api/market.ts, frontend/src/shared/api/stocks.ts, frontend/src/features/market/components/{RankingMatrix,SectorFlow}.tsx, frontend/e2e/public-homepage.spec.ts

## 2026-09-11 - 首页公开化 Phase 2（Task 2.4–2.6）：共享交易日解析 + 公开 `/market/rankings` 榜单 + 索引计划锁
- **Task 2.4**：`market_service.get_latest_trade_date(db, cache=None)`（Redis key `market:latest_trade_date`，TTL 300s）为"数据截至日"的单一实现：`_latest_trade_date` 改为其无缓存薄委托（`max(trade_date)` 只写一处），既有四处 dashboard 读取器（distribution/sectors/capital-flow/hot-boards）复用同一实现但**行为不变、仍为直读无缓存**，rankings 走 300s 缓存路径；缓存按 Ruling Q 存 `as_of.isoformat()`、读出 `date.fromisoformat` 兜底（`CacheClient` 是 JSON 序列化，`date` 不可直接 JSON 化）；纯函数 `last_weekday` 为 `is_latest_trading_day` 的 Phase-2 启发式（节假日不处理，Phase 4 换 `trade_calendar` 同签名）
- **Task 2.5**：新增 `app/schemas/ranking.py`（`RankingItemOut` / `RankingResponseOut`，含 `as_of` + `is_latest_trading_day`；`amount` 注释据实标为 TuShare 原生**千元**、透传原值）与 `GET /api/v1/market/rankings?type=gainers|losers|amount|turnover_rate|volume&limit=`；五种类型经文件内硬编码 `_RANKING_ORDER` 白名单 f-string 拼 ORDER BY（Ruling O：bind 参数不能承载标识符/方向，删 `:rank_col_expr`，无用户输入进串；Pydantic `Literal` 边界校验 + service 侧 membership 复检双闸）；`daily_quotes` 四种保留 `pct_chg IS NOT NULL`（Ruling P：DESC 默认 NULLS FIRST，否则涨幅榜以 NULL 行领跑），`turnover_rate` 走 `daily_basic_indicators`；top-N CTE 排好后外层再 ORDER BY，保证补字段 JOIN 不重排；缓存优先 + `_MARKET_CACHE_TTL`（Ruling S）；**空库降级**：rankings 层捕获 `ValueError` 返回空 payload（`as_of`=最近工作日、`is_latest_trading_day=false`、`items=[]`，不缓存），公开首页块不再 500，而 `get_latest_trade_date` 对确需日期的调用方仍抛 `ValueError`
- **Task 2.6**：新增 `tests/test_rankings_index.py`（`@pytest.mark.e2e`，默认 addopts 排除），锁三条 Task 2.1 索引的计划不回退：gainers→`idx_daily_quotes_date_pct`、amount→`idx_daily_quotes_date_amount`、turnover_rate→`idx_daily_basic_date_turnover`，断言命中期望索引节点且计划无 `Sort`
- **验证**：TDD 先红后绿（Task 2.4 纯函数 ImportError→5 passed；Task 2.5 schema ModuleNotFoundError→7 passed）；容器内以本分支代码 + ASGI transport 连真库实跑，5 种类型 200 且返回真实行，非法 `type` 与 `limit=0` 均 422；Task 2.6 三例 e2e 容器内 3 passed，实抓计划分别为三种 Index Scan
- 测试：`uv run pytest -q` 201 passed / 27 deselected；`uv run --extra dev mypy app` 142 files 无问题
- 涉及模块：backend/app/services/market_service.py, backend/app/schemas/ranking.py, backend/app/api/v1/market.py, backend/tests/test_latest_trade_date.py, backend/tests/test_rankings.py, backend/tests/test_rankings_index.py

## 2026-09-11 - 首页公开化 Phase 2（Task 2.1–2.3）：日线落库原生 pct_chg/pre_close + 权威回填
- **Task 2.1**：Alembic 迁移 `cf4b8e317fe5`（`5a1b2c3d4e5f` →）为 `daily_quotes` 增可空 `pre_close Numeric(12,4)` / `pct_chg Numeric(8,4)`，并建 `idx_daily_quotes_date_pct(trade_date,pct_chg)`、`idx_daily_quotes_date_amount(trade_date,amount)`、`idx_daily_basic_date_turnover(trade_date,turnover_rate)`；实查 `pg_partitioned_table` 无 `daily_quotes` 行（未分区），故用普通复合索引、不加分区/合并分支；已 `alembic upgrade head`，`heads` 单行
- **Task 2.2**：`DailyQuote` 模型 + `tushare_ingest` 两处日线构造（按 trade_date 全市场、按股票区间）+ `quote_repo.upsert_quotes` 的 VALUES 与 `on_conflict_do_update.set_` 全部补 `pre_close`/`pct_chg`（落地点四处缺一即静默丢字段）
- **Task 2.3**：新增 `scripts/backfill_pct_chg.py`（`--years`/`--limit`/`--start-date`/`--end-date`，幂等）与 `quote_repo.update_pct_chg_for_date`（`sa.Values` + Core `UPDATE ... FROM (VALUES ...)` 单语句，仅 UPDATE 不 INSERT）；**只回填 TuShare 原生值，禁止 `LAG(close)` 现算**（除权日参考前收需为除权后价），理由写入 docstring
- **验证**：TDD 先红（`AttributeError: 'DailyQuote' object has no attribute 'pct_chg'`）后绿；`--limit 4`（2026-09-04…09-09）实机回填 22,196 行，重复运行同值（幂等），与 TuShare 原生逐行对拍 22,196 行 0 不一致（抽样 000001.SZ/300750.SZ/600000.SH 值全等）；全量 3 年约 750 次 `fetch_daily` 由运维另跑（见 Task 2.1–2.3 报告）
- 测试：新增 `tests/test_daily_ingest_pct_chg.py` 3 例（映射落库、upsert 冲突补列、回填仅 UPDATE 无 LAG）；`uv run pytest -q` 185 passed / 24 deselected
- **D4 复审修复**：三个排行索引补进 ORM `__table_args__`（`models/quote.py`、`models/daily_basic.py`），使 `alembic check` 不再对它们报 `remove_index`（输出中其余 13 项为既有漂移，不在本次范围）；upsert 测试改为按 `ON CONFLICT` 切分断言 INSERT 列清单——原整体字符串断言被 SET 的 `excluded.*` 覆盖，删掉 VALUES 字典条目仍会假绿；新增第二处 ingest（`ingest_daily_quotes_for_stock`）回归测试。测试共 4 例，两处断言均以变异测试验证会转红
- 涉及模块：backend/app/migrations/versions/cf4b8e317fe5_add_pct_chg_to_daily_quotes_and_ranking_.py, backend/app/models/quote.py, backend/app/models/daily_basic.py, backend/app/services/tushare_ingest.py, backend/app/repositories/quote_repo.py, backend/scripts/backfill_pct_chg.py, backend/tests/test_daily_ingest_pct_chg.py

## 2026-09-11 - 首页公开化 Phase 1 收口：板块/资金/快讯三块 + 零 401 与降级门禁（Task 1.7–1.10）
- **Task 1.7 板块区**：新增 `features/market/components/SectorFlow`（左列 `/market/sectors` 证监会口径、右列 `/market/capital-flow` 近似口径双向条）；`/market/sector-moneyflow` 实测返回 `[]` 无法支撑资金列，改走可用源并在 UI 标注「近似口径：涨/跌股成交额估算，非主力净流入」；两列独立查询独立降级
- **Task 1.8 资金区**：新增 `MoneySentiment` 复用 `MarketMoneyflowCard`（`/market/market-moneyflow`）；**北向卡整卡移除**——`northbound_daily` 无数据行（非仅滞后），不以本地序列替补
- **Task 1.9 快讯区**：新增 `MarketNewsFeed`（财报公告/重大事项两 Tab，各 10 条），复用并导出 `dataFace/AnnouncementFeed`（增 category/limit/timeMode 可选 props + `format.fmtRelativeTime`）；零新数据源（TuShare 新闻接口积分门槛），不做伪新闻流
- **Task 1.10 收口**：页脚加「数据来源：TuShare · 东方财富 · 巨潮资讯网 · 上海证券交易所」；免登录行情文案纠偏（移除行业树「注册后查看」、账号能力区「注册即解锁全部投研能力」与注册档「猪周期工作台完整能力」，仅个性化——自选/标签/同步/提醒——保留在注册档）；新增 e2e 硬门禁——匿名全链路除 `/auth/session` 探测外零 401/403（并断言确发出行情请求防假绿）、单源/全源 abort 不白屏
- 测试：public-homepage 8 → 20，合计 30/30（landing 7 + darkmode 3）全绿
- 涉及模块：frontend/src/features/market/components/{SectorFlow, MoneySentiment, MarketNewsFeed, dataFace/AnnouncementFeed, format.ts}, frontend/src/pages/landing, frontend/e2e/public-homepage.spec.ts

## 2026-09-11 - 首页公开化 Phase 1（D2 复审修复）：榜单剔除 null 行 + 脉搏区表面收口
- **榜单**：涨幅/跌幅榜（及成交额榜）按当前排序维度剔除 null 行再截断 top-10（多取 20 条），修掉后端 desc 把无行情次新股排到榜首、涨幅榜首屏两行 `--` 的问题；`--` 缺失值契约改在不受过滤影响的成交额榜断言（Task 2.7 切 `/market/rankings` 后 SQL 已 `pct_chg IS NOT NULL`，此守卫为保险）
- **脉搏区**：分布返回 `[]` 不再渲染成 `上涨 0 · 下跌 0`，改走「今日涨跌分布暂不可用」占位；分布柱家数补千分位与汇总行统一；`MarketPulse`/`index.tsx` 改直连模块路径，避免 barrel 把 ECharts 视图拖进匿名首页 chunk
- **清理**：删除已无引用的 `.landing-hero-secondary-cta`；`LandingNav` aria-label 改为「行情台区块导航」（`darkmode.spec.ts` 同步）
- 测试：分桶完整性用例显式断言「上涨+下跌 == 逐桶求和 == 常量」；新增空分布占位用例；18/18 全绿
- 涉及模块：frontend/src/features/market/components/RankingMatrix.tsx, DistributionBars.tsx, frontend/src/pages/landing, frontend/e2e

## 2026-09-11 - 首页公开化 Phase 1：免登录行情台 IA 重排（Task 1.4–1.6）
- **背景**：产品决策「免登录行情为主」——行情区块是首页主内容，营销区压缩到行情台之后；导航锚点从「功能/数据/行业」改为「脉搏/榜单/板块/资金/快讯」（`#pulse/#rankings/#sectors/#money/#news`）
- **Task 1.4**：`Hero` 压缩为 `CompactHero`（保留 H1/信任行/CTA，实测高 232px ≤ 240px 目标，大图与次 CTA 移除），`index.tsx` 区块序列改为 紧凑 Hero → 脉搏/榜单/板块/资金/日历/快讯 → ValueProps/ProductShowcase → DataCoverage/IndustryGrid/AccountPerks/BottomCTA；未填充行情区块以 `<SectionCard>` + AntD `Skeleton` 显式骨架挂载
- **Task 1.5**：新增 `features/market/components/DistributionBars`（横向双向柱，与 `/market` DistributionChart 同 11 桶口径），`MarketPulse` 包进 `<SectionCard id="pulse">` 并加 `.index-ticker` 锚；**修复分桶漏桶**：`UP_RANGES` 补 `0~1%` 使两侧对称穷尽（实测上涨由 954 → 1,903，不再静默丢掉 949 只），失败降级文案去掉「注册后」改为「行情数据暂不可用，请稍后重试」
- **Task 1.6**：新增 `shared/api/stocks.ts#fetchStockRanking`（走 Task 1.3 已缓存的 `/api/v1/exchanges/stocks/enriched` 排序端点）与 `features/market/components/RankingMatrix`（涨幅/跌幅/成交额三 Tab；换手率榜后端未支持，Phase 2 Task 2.7 补）；缺失涨跌幅经 `DeltaText` 渲染 `--`
- **测试**：`public-homepage.spec.ts` 扩至 7 例（含「上涨+下跌 == 11 桶总和」「null 渲染 `--` 不渲染 0.00%」「脉搏区失败不影响榜单区」）；`landing.spec.ts` 分桶期望随口径修正 134 → 234；landing 7 + darkmode 3 + public-homepage 7 全绿
- 涉及模块：frontend/src/pages/landing, frontend/src/features/market/components, frontend/src/shared/api/stocks.ts, frontend/e2e

## 2026-09-11 - 首页公开化 Phase 1（D7 修订）：暗色涨跌色达标卡片表面，门禁统一
- **问题**：`SectionCard` 内 `DeltaText`/`DataRow` 实际渲染在 `--bg-panel #1e222d`；暗色 `--up #f23645`(4.08:1) / `--down #089981`(4.45:1) 对卡片面不达 WCAG AA（对 `--bg-page #131722` 的 4.59/5.01 达标掩盖了该缺陷）。原 D7「暗色已达标故不动」是在错误表面（`--bg-page`）上测得，予以推翻
- **修复**：暗色 `--up #f2555a`（bg-page 5.30 / bg-panel 4.71）、`--down #0aa088`（bg-page 5.45 / bg-panel 4.84），同步 `:root[data-theme="dark"]` 与 `theme.ts` `THEME_COLORS.dark`；`:root` 兜底块保持浅色不变
- **门禁**：移除 `check-design-tokens.mjs` 内的暗色例外，`check:design` 对 fallback/light/dark 三块 × `--bg-page`/`--bg-panel` 两表面统一断言 ≥ 4.5，12 项全 PASS、exit 0
- 涉及模块：frontend/src/app/styles/theme.css, frontend/src/app/theme.ts, frontend/scripts/check-design-tokens.mjs, docs/design/landing-market-theme.md

## 2026-09-11 - 首页公开化 Phase 1（D1 复核补漏）：设计令牌门禁接入 CI + 覆盖卡片表面
- **问题**：`npm run check:design` 只存在于 package.json，无任何自动化路径调用，改色破坏 AA 或 `theme.ts`/`theme.css` 失同步时 CI/lint/build 全绿；且原脚本只对 `--bg-page` 校验，漏掉 DataRow/DeltaText 实际所在的 `SectionCard` 表面（`--bg-panel`）
- **修复**：CI `frontend-lint` job 在 `npm ci` 后新增 `npm run check:design` 硬门禁步骤；脚本对 light/fallback 同时校验 `--bg-page` 与 `--bg-panel` 对比度 ≥ 4.5
- **发现（已由 D7 修订销项）**：暗色 `--up #f23645`(4.08:1) / `--down #089981`(4.45:1) 对 `--bg-panel #1e222d` 不达 AA；本条目当时未改暗色、暂只校验 `--bg-page`，随后按 D7 修订改为暗色换值并统一两表面校验（见上一条）
- 涉及模块：.github/workflows/ci.yml, frontend/scripts/check-design-tokens.mjs, docs/references/best-practices.md

## 2026-09-11 - 首页公开化 Phase 1：enriched 排序路径加 Redis 缓存（公开流量防护）
- **问题**：`/api/v1/exchanges/stocks/enriched` 公开可达，带 `sort_by` 时每次请求都走「全市场查询 + LATERAL + Python 排序」且完全无缓存，匿名流量可打满 DB
- **修复**：`list_stocks_enriched` 排序分支前置 Redis 缓存（key `stocks:enriched:sort:{exchange}:{category}:{keyword}:{sort_by}:{sort_order}:{offset}:{page_size}`，TTL 60s），命中直接反序列化返回，不再触库；key 含全部过滤维度，避免不同筛选互串
- **连带修复**：端点原本以 `cache=None` 调用（注释称缓存参数未被使用），导致缓存无法生效；改为注入 `CacheDep` 传入真实 `CacheClient`，让防护真正短路
- 涉及模块：backend/app/services/stock_service.py, backend/app/api/v1/stocks.py, backend/tests/test_stock_sort_cache.py

## 2026-09-11 - 首页公开化 Phase 1：共享 UI 原语 DeltaText / DataRow / SectionCard
- 新增 `frontend/src/shared/ui/` 三个行情区块基础件：`DeltaText({value, suffix?})`（正 `+`/负 `−`/零无符号/缺失 `--`，消费 `--up`/`--down` CSS 变量随主题切换）、`DataRow({logo?, title, ticker?, value?, unit?, href?, delta?})`、`SectionCard({id?, title, moreHref?, moreText?, children})`；签名由后续所有行情区块消费，不得改动
- 三个组件与类型经 `shared/ui/index.ts` 桶文件导出；新增 `frontend/e2e/public-homepage.spec.ts` 最小冒烟（`/` 返回 200 且 H1 可见）
- 涉及模块：frontend/src/shared/ui, frontend/e2e

## 2026-09-11 - 首页公开化 Phase 1：涨跌色 WCAG AA 修复 + 设计令牌门禁
- **问题**：浅色模式 `--up #f5222d` / `--down #22c55e` 对 `--bg-page #ffffff` 对比度仅 4.08:1 / 2.28:1，均低于 WCAG AA 的 4.5:1
- **修复**：浅色涨跌色改为 `--up #c62828`（5.62:1）/ `--down #0a7d5f`（5.11:1），同步三处声明（`:root` 首帧兜底块、`:root[data-theme="light"]`、`theme.ts` `THEME_COLORS.light`）；`flat`/`hover` 不动。暗色当轮未动，后经 D7 修订换值（见上）——原判据只测了 `--bg-page`，未覆盖 `SectionCard` 的 `--bg-panel`
- **门禁**：新增 `frontend/scripts/check-design-tokens.mjs` 与 `npm run check:design`，校验三块 `--up`/`--down` 对比度 ≥ 4.5 且 `theme.ts` 同名值一致；任何改色先跑它
- 涉及模块：frontend/src/app/styles/theme.css, frontend/src/app/theme.ts, frontend/scripts, docs/design/landing-market-theme.md

## 2026-09-11 - 首页公开化 Phase 0：noindex / SEO 决策记录
- **核实**：`curl -sI` 实测 `http://127.0.0.1:80/`（frontend 路由，200）与 `/api/v1/market/distribution`（HEAD 405 / GET 200）均返回 `X-Robots-Tag: noindex, nofollow, nosnippet, noarchive`；四条 router（frontend/api/auth/api-tasks）均挂载 `sec-headers@file`，中间件定义在 `gateway/dynamic/middlewares.yml:35-45`
- **决策**：维持整站 `noindex`（理由：公开再分发行情数据有条款约束 + Vite SPA 难索引、投入产出不成比例）；本计划删除一切 SEO /「静态可索引落地路径」目标；页脚数据来源署名（Task 13）保留，定位为面向用户的合规署名而非 SEO
- **复核**：`api-ratelimit` 的 `burst(50) < average(100)` 仅记录为「待用户确认是否有意为之」，未改网关配置
- 涉及模块：plans/2026-09-11-public-market-homepage（§0.5.1、风险 ⑦）, docs/references/best-practices（八）

## 2026-09-11 - 首页公开化 Phase 0：数据语义核实（北向/指数覆盖/分区状态）
- **问题**：公开行情首页的三个前置事实未定，直接决定北向模块形态、指数条数据源与 Phase 2 索引方案
- **核实**：
  - 北向 `northbound_daily` 实测**全表 0 行**（非仅近 30 天断流），`/market/northbound` 返回 `[]`；`market_moneyflow_daily`、`sector_moneyflow_snapshots` 同为 0 行
  - `index_dailies` 含 6 个指数：`000001.SH/000016.SH/000300.SH/399001.SZ/399006.SZ/899050.BJ`，`last_date` 均 `2026-09-10`；`GLOBAL_INDICES` 已含沪深300/上证50/北证50，与 `market_service._TARGET_INDICES` 对齐，**无需修 job**
  - `daily_quotes` 实测**未分区**（`pg_class.relkind='r'`，`pg_partitioned_table` 计 0），Phase 2 用普通复合索引
- 风险表 ②③④ 已销项并附原始查询输出，Task 11/14 的处置已明确
- 涉及模块：plans/2026-09-11-public-market-homepage, docs/references/best-practices

## 2026-09-11 - 公开行情台首页实施计划（含免登录现状核实与数据面缺口盘点）
- 新增 `plans/2026-09-11-public-market-homepage.md`：6 阶段 tracer-bullet 计划 + 前置 spike，目标为首页在未登录状态即可作为完整行情台（指数/榜单/板块/日历/资讯），登录只解锁个性化（自选/标签/提醒）
- **免登录现状核实**（结论：已基本成立，非鉴权改造项目）：网关 `forward-auth` 对无 session cookie 请求匿名放行（`return Response(status_code=200)`）；路由层仅 `/watchlist`、`/tags` 包 `RequireAuth`；后端行情类端点（exchanges/stocks/financials/market/market-data/clusters/industries-GET）均未挂 `CurrentUserDep`
- **数据面缺口盘点**（决定成本的三项）：
  - 榜单无高性能路径——`stocks/enriched?sort_by=` 实为「拉全市场 10k + LATERAL + Python sort」（约 +70ms/2300 只），且不支持换手率/成交量排序
  - `pct_chg` 被丢弃——TuShare `daily` 原生返回 `pct_chg`/`pre_close`，但 `DailyQuote` 模型无此列且 `tushare_ingest.py` 未映射，导致涨跌幅只能查询时现算、无索引可支撑（修复成本极低）
  - 日历（财报/分红/IPO/宏观）与财经新闻**均无数据源**；交易日历无持久化表，调度守卫 `weekday()<5` 导致节假日空跑
- **设计面实测**：现有涨跌色浅色模式不达 WCAG AA——涨 `#f5222d` 4.08:1、跌 `#22c55e` 仅 2.28:1（暗色 4.59/5.01 达标），给出重取取值与对比度回归测试要求
- 设计参考：TradingView 首页 chrome-devtools 实测（248 CSS 变量具名调色板、`--v-rhythm-*` 四档响应式节奏刻度、`data-theme` 单属性换肤、模块模板×资产类别矩阵式 IA）作为**内部设计参考**记入附录 A
- **计划修订 + 任务级细化（同日二轮，评审后）**：
  - 定位拍板：首页**以免登录行情为主体**，营销区压缩为尾部一段（Spec D1 重写）；涨跌色修复从 Phase 5 提前到 Phase 1（独立可访问性缺陷）
  - 评审发现 3 处硬伤并已修入计划：① Phase 1 临时榜单路径（`stock_service.list_stocks_enriched` 排序分支）**完全无缓存**而公开暴露 → 新增 Task 1.3 Redis TTL 缓存；② 网关 `sec-headers` 对整站打 `X-Robots-Tag: noindex`，SEO 目标与之矛盾 → 降级为 Phase 0 决策项（默认维持 noindex）；③ Phase 2 验收曾引用**从未实施的 Tier 2 API 基准** → 改为 EXPLAIN 索引断言测试（Task 2.6）
  - 评审补充 6 项已并入：申万口径（CSRC 临时 + Phase 3 申万聚合端点）、北向数据语义核实（2024 披露口径变化）、`pct_chg` 回填改权威源重拉（禁 `LAG(close)` 现算——除权日会错）、防护清单（§0.5）、e2e 具体化（`public-homepage.spec.ts` 零 401 + 降级用例）、页脚数据来源署名
  - 新增 `plans/2026-09-11-public-market-homepage-tasks.md`：superpowers 任务级计划，Phase 0–3 共 17 个 Task，逐步 TDD（含测试与实现代码、EXPLAIN 断言、e2e 用例）；Phase 4（日历）/5（资讯增强）待 Phase 0 spike 结论后另立计划
- 涉及模块：plans、docs/Changelog、docs/references/best-practices

## 2026-09-09 - API Gateway 选型调研
- 比较 Docker Compose 场景下 NGINX、Traefik、Kong/APISIX 的动态路由、OIDC/JWT、限流、可观测性、配置复杂度与 Kubernetes 演进适配性，并结合当前 stock_bot 架构给出 Gateway 选择建议。
- 涉及模块：架构调研、部署、认证、可观测性

## 2026-05-09 - 数据回填管道完善（APScheduler 定时任务 + 手动触发 API）
- **问题**：每日增量数据回填未纳入 APScheduler，导致系统只依赖启动时一次性回补；缺失手动触发 daily_basic 回填的 API 端点
- **修复**：
  - 新增 APScheduler 定时任务 `daily_quotes_backfill_job`（16:30）和 `daily_basic_backfill_job`（16:45），工作日 16:30-16:45 执行昨日数据回填
  - 新增 `POST /api/v1/tasks/fetch-daily-basic` 端点，可手动触发 daily_basic 全量回填
  - 新增 `FetchDailyBasicRequest` Schema + `trigger_fetch_daily_basic()` Service 方法
  - `DailyBasicWorker` 已支持队列消费，只需发送对应 payload 即可触发
- 涉及模块：backend/scheduler/jobs, backend/scheduler/runner, backend/api/v1/tasks, backend/services/task_service, backend/schemas/task

## 2026-05-09 - 股票详情页估值指标数据缺失修复
- **问题**：`FundamentalCards`（估值目标/成长与盈利卡片）全为空白，根因是 `fetchStockBySymbol()` 调用基础 `/stocks/{symbol}` 接口返回 `StockOut`（无金融字段），且 `roe/revenueGrowth/profitGrowth` 后端未入库
- **修复**：
  - 后端新增 `GET /api/v1/exchanges/{exchange}/stocks/{symbol}/enriched` 端点，返回 `StockEnrichedOut`（含 latest_price/pe_ttm/pb/total_mv/circ_mv）
  - 后端 `get_stock_enriched()` service 复用 `get_stocks_enriched_by_symbols()` SQL，TTL 300s
  - 前端新增 `fetchStockEnrichedBySymbol()` 并切换 stock-detail 页至 enriched 接口
  - `roe/revenueGrowth/profitGrowth` 字段 Phase 2 待接入 TuShare `fina_indicator`/`profit_data`
- 涉及模块：backend/services/stock_service, backend/api/v1/stocks, frontend/shared/api/stocks, frontend/pages/stock-detail

## 2026-05-08 - 申万分类详情页金融数据空白修复（含完整数据链路闭环）
- **问题**：申万行业详情页个股表格金融列全空（最新价/涨跌幅/成交额/市值/PE），根因有三层：
  1. 前端 `mapBackendStock` 把金融字段硬编码 `undefined`
  2. SW 行业 API 只返回 `StockOut`（纯元数据），不含行情/基本面
  3. `daily_basic_indicators` 表为 0 行 → PE/市值无数据源
- **修复**：
  - **数据展示**：新增 `StockEnrichedOut` Schema + 批量 enriched API（DISTINCT ON JOIN stocks + daily_quotes + daily_basic_indicators 一次查询），前端 `fetchSwLevel*Stocks` 切至 `/enriched` 端点
  - **懒加载**：前端采用双 `useQuery` 渐进式渲染（basic 秒出骨架 → enriched 异步填金融列），避免加载卡顿
  - **数据源**：手动回补 `daily_basic_indicators` 近 1 年数据（242 个交易日，覆盖 5400+ 只股票，69 万行），验证 TuShare `daily_basic` API 全链路可用
  - **启动加速**：`data_init` 中 `daily_basic` 回补改为与 `daily_quotes` 并行执行（`asyncio.create_task` + `gather`），内部 3 并发（`Semaphore(3)`），从"排队数小时等不到"→"启动 3 分钟跑完"
  - **Worker 修复**：`QUEUES` 字典补全 `daily_basic.fetch` 映射，终止 Worker crash loop
- 涉及模块：backend/schemas/stock, backend/services/market_service, backend/services/data_init, backend/api/v1/market, backend/core/mq, backend/workers/daily_basic_worker, frontend/shared/api/swIndustry, frontend/pages/market-industry-level2, frontend/pages/market-industry-level3

## 2026-04-20 - 申万行业分类
- 基于本地 XLS/XLSX 文件实现申万三级行业分类与成分股联动，替代原 TuShare API 内存缓存方案
- 涉及模块：backend/models、backend/services、backend/api、frontend/features/market、frontend/pages/market-industry-*

## 2026-04-20 - 申万导入链路优化
- 新增“SQL 种子优先、XLS 解析兜底”的双路径导入机制：首次解析后自动导出 `sw_seed.sql`，后续部署可直接导入 SQL，显著降低启动导入耗时
- 涉及模块：backend/services、backend/config、docker-compose、backend/.dockerignore、backend/data

## 2026-04-21 - 分类市场分页修复
- 修复分类市场表格切换“条/页”后因固定 pageSize 被重渲染覆盖导致显示条数不变化的问题，并将分页状态改为受控持久化。
- 涉及模块：frontend/features/market、docs

## 2026-04-21 - 分类市场总数口径修复
- 修复分类市场“总条数固定 300”的问题：新增跨交易所分页接口并改为服务端分页，前端分页总数改用后端 `total`，确保 20/50/100 条切换与页码显示一致。
- 涉及模块：backend/api、frontend/shared/api、frontend/pages/market-category、frontend/features/market、docs

## 2026-04-21 - 申万分类缺失兜底修复
- 修复申万行业树总数与股票总数不一致问题：行业树计数统一按 `stocks` 口径统计，并新增 `OTHER(其他)` 一级分类承接未映射到有效三级行业的股票。
- 涉及模块：backend/services/market、frontend/pages/market-industry-*、docs

## 2026-04-21 - 前端代理 502 修复
- 修复 frontend 经 Nginx 代理后端接口偶发全量 502 的问题：启用 Docker DNS 动态解析，避免 backend 容器重建后 frontend 继续使用过期 upstream IP。
- 涉及模块：frontend/nginx、docker-compose、docs

## 2026-04-22 - SSE 交易所指数实时数据采集
- 整合 SSE 官方 JSONP 接口爬虫到 backend 服务：新建 `sse_index_snapshots` 表存储盘中快照，异步 httpx 爬取服务含完整反爬策略（UA 轮换/cookie 持久化/随机 jitter/指数退避），APScheduler 定时调度（交易时段 9:30-15:00 每 10min + 15:30 收盘补采），支持历史时间戳回填 4/1-4/21 数据，前端 MarketOverview 自动合并 SSE 实时数据优先展示
- 涉及模块：backend/models、backend/repositories、backend/services、backend/schemas、backend/api、backend/scheduler（新增）、docker-compose、frontend/shared/types、frontend/shared/api、frontend/features/market

## 2026-04-22 - 三年日K启动补齐
- 启动初始化由“固定近30交易日”升级为“按股票检查近3年覆盖并仅回补缺口”，采用小并发分批逐股拉取 TuShare 日线，兼顾补齐效率与服务启动稳定性。
- 涉及模块：backend/services、backend/repositories、backend/core/providers、backend/tests、docs

## 2026-04-22 - 股票自定义申万分类标签
- 新增 stock_custom_sw_tags 表支持每只股票自定义多个 SW 二级/三级行业标签；"其他"一级行业按股票自带 industry 字段自动分组为二级子分类；股票详情页新增分类标签展示与编辑功能。
- 涉及模块：backend/models、backend/services、backend/api、backend/migrations、frontend/shared/api、frontend/shared/types、frontend/features/stock-detail、frontend/pages/market-industry-level2、frontend/pages/stock-detail

## 2026-04-23 - 申万“其他”二级下钻交互统一
- 修复申万“其他”一级分类下二级卡片点击行为与其余分类不一致的问题，统一为路由下钻到独立详情页，避免在一级页内混合展示子分组个股。
- 涉及模块：frontend/pages/market-industry-level2、docs

## 2026-04-23 - 自定义申万三级联动修正
- 修正股票详情“编辑自定义申万分类”弹窗联动失效问题：三级下拉严格按已选二级实时过滤，未选二级时禁用三级下拉并提示“请先选择二级行业”。
- 涉及模块：frontend/features/stock-detail、docs

## 2026-04-23 - 自定义申万标签与行业详情联动修复
- 修复行业详情页未包含股票自定义申万二/三级标签的问题：行业树统计、层级个股列表与 OTHER 兜底口径统一合并官方成分与自定义标签。
- 涉及模块：backend/services/market、docs

## 2026-04-23 - 自定义标签变更即时刷新修复
- 修复修改股票自定义申万标签后行业计数和个股列表不即时刷新问题：前端统一失效申万树/层级查询缓存，后端同步清理 `market:sw-tree` Redis 缓存。
- 涉及模块：frontend/features/stock-detail、backend/api、docs

## 2026-04-23 - 用户自定义股票标签功能
- 新增用户自定义标签系统：每只股票可添加任意文字标签，独立于申万分类体系；新增标签列表页以 Card 网格展示所有标签及股票数量，点击可下钻查看该标签下的股票列表。
- 涉及模块：backend/models、backend/services、backend/api、backend/migrations、frontend/shared/api、frontend/shared/types、frontend/features/stock-detail、frontend/pages/tags、frontend/pages/tags-detail、frontend/app/router、frontend/app/layouts、docs

## 2026-05-09 - /market/category 路由股票列表金融数据空白修复
- 根因：`/api/v1/exchanges/stocks` 端点返回 `StockOut`（仅 `stocks` 表字段），前端 `mapBackendStock()` 把所有行情字段硬编码为 `undefined`。修复：新增 `/exchanges/stocks/enriched` 端点返回 `StockEnrichedOut`（JOIN daily_quotes + daily_basic_indicators），前端 CategoryPage 采用 dual useQuery progressive loading（基础查询先渲染骨架，enriched 查询异步填充金融列），复用 `market-industry-level2/3` 页已有的 enriched 基础设施。
- 涉及模块：backend/services/stock_service、backend/api/v1/stocks、frontend/shared/api/stocks、frontend/shared/api/swIndustry、frontend/pages/market-category

## 2026-05-09 - 股票列表 enriched 查询性能修复（DISTINCT ON → LATERAL JOIN）
- **问题**：SW 行业详情页/market category 页的 enriched 查询（最新价/涨跌幅/PE/市值）耗时 3.5s，比 basic 查询（27ms）慢 130 倍，导致表格金融列晚 2-3s 才出现
- **根因**：`_GET_ENRICHED_SQL` 使用 `DISTINCT ON (stock_id)` 子查询对 `daily_quotes`（3.8M rows）和 `daily_basic_indicators`（1.4M rows）做**全表 Seq Scan + Sort**，WHERE 条件未推入子查询，3.8M 行排序后才能与目标 stocks JOIN
- **修复**：将 3 个 `DISTINCT ON` 子查询改为 `LATERAL (SELECT ... WHERE stock_id = s.id ORDER BY trade_date DESC LIMIT 1)`，利用已有的 `idx_daily_quotes_stock_date` 和 `idx_daily_basic_stock_date` 复合索引，每个 stock 仅做 1 次 Index Scan Backward
- **效果**：enriched 查询延迟 3.5s → 23ms（152x 提升），全过程无需新增索引
- 涉及模块：backend/services/market_service

## 2026-05-09 - 表格排序双组件 Bug 修复（全局排序 + 默认数据显示）
- **问题 1 (StockTable)**: `sorter: true` 用字符串比较覆盖父组件数值排序 → 排序错乱；分页数据只排序当前页
- **问题 2 (WatchlistTable)**: 用 `fetchStockBySymbol`（basic 端点）→ 金融字段全 `undefined` → 排序无效
- **问题 3 (market-category 默认不显示)**: 默认 `sortBy="symbol"` 时向后端传 `sort_by=symbol` → basic 和 enriched 返回不同页股票 → merge 失败 → 金融列空白
- **修复**:
  - StockTable 改为 controlled sorter：`sortBy`/`sortOrder` props 控制排序指示器，父组件 `applySort` 负责实际排序
  - WatchlistTable 切换 `fetchStockEnrichedBySymbol`
  - market-category：默认 symbol 排序不传 sort 参数（basic/enriched 同页），金融排序切 enriched-only 模式
  - 后端 enriched 端点加 `sort_by`/`sort_order` → 全量 enrich + sort + paginate（2312 股 ~194ms）
- 涉及模块：frontend/features/market/StockTable, frontend/features/watchlist/WatchlistTable, frontend/pages/market-category, frontend/pages/market-industry-level2/3, frontend/pages/tags-detail, backend/api/v1/stocks, backend/services/stock_service, backend/schemas/stock

## 2026-08-30 - 猪智投·投资看板高保真原型（投研产品化第一步）
- **背景**：基于《农林牧渔-养殖业-生猪养殖 v3.2 产品化》PRD，设计行业投研工作台的「投资看板」页面并产出可交互原型
- **产出**：单文件 HTML 原型（antd v5 视觉体系 + ECharts，mock 数据 37 个月），包含：工作台状态栏与四 Tab 架构、综合指标带（含官方基准/高频参考/测算三级数据源徽章 + 迷你走势）、猪周期四阶段相位波（当前萧条磨底）、交易信号时间线、价格 vs 成本对比图、能繁存栏趋势图、仓位管理建议（50/30/20 堆叠条）、10 项核心指标速览
- **设计约定**：结论先行（周期阶段/信号/仓位置顶）、证据下钻（图表验证）、页面通用组件化以便泛化到其他行业；下一阶段按此布局迁移为 React 页面（/research/:industryKey）
- 涉及模块：docs/design（原型）、frontend（后续接入）

## 2026-08-31 - 行业投研工作台实施计划 + 数据源调研 + AGENTS.md 修正
- **产出**：
  - `plans/industry-research-workbench.md`：6 阶段 tracer-bullet 实施计划（骨架贯通→图表→官方产能→规则引擎→标的分析→知识库+泛化验证），核心架构决策：指标单表 industry_metrics（行业/公司级共用）、metric registry 代码即配置、政策锚点带生效日期、采集双轨复用、前端组件行业无关
  - `docs/design/data-source.md`：猪智投数据源四层调研（L1 免费自动 AKShare 价格类 / L2 官方半自动产能 / L3 人工 batch 导入 / L4 付费墙后置），含 metric_key 命名、派生指标定义与口径坑（能繁双口径、保有量锚修订史）
  - 根 `AGENTS.md`：修正前端技术栈过时描述（Tailwind+shadcn → 实际 antd v5），补充后端分层/双轨采集与关键文档索引；`frontend/AGENTS.md` 修正 best-practice.md 断链
- 涉及模块：docs/design, plans, AGENTS.md, frontend/AGENTS.md

## 2026-09-02 - 猪周期规则引擎复苏分支加固 + 纯函数测试套件
- 复苏分支与萧条期左侧"关注"信号新增盈亏平衡确认（price≥cost 或 ratio≥6 任一口径非空），杜绝关键指标全缺失时仅凭能繁去化误判复苏/发左侧信号；新建 21 项无 DB 纯单测锁定行为
- 涉及模块：backend/services/cycle_engine, backend/tests

## 2026-09-02 - 行业指标源优先级修复（mock 永远垫底 + 真实源落库后清除演示数据）
- registry 各指标 sources 重排为真实源优先、mock 永远垫底，并补登记 akshare_sina（此前 AKShare 期货写入的源名从未注册、mock 排首位导致切换真实源后看板仍裁决出 mock 行）；`_pick_latest` 对未登记源兜底改为取最新 period（确定性）；真实源成功落库后清除该行业全部 mock 行并在 ingest 返回中回报 `purged_mock`；新增 4 项纯单测锁定排序与裁决不变量
- 涉及模块：backend/services/industry_registry, backend/services/industry_metric_service, backend/repositories/industry_metric_repo, backend/tests

## 2026-09-02 - 行业指标日度→月度 rollup + latest 按注册频率裁决
- 日度指标（hog_price/corn_price）ingest 时按"每月最后一个非空日度值"补写月度行（period=月末、source 不变、extra 标记 rollup），月度趋势图不再因真实源只写日度行而空白；`latest_rows_by_metric` DISTINCT ON 增加 freq 维度（每 metric×source 最多 daily/monthly 两行），`_pick_latest` 先按 registry 注册频率过滤再走源优先级，月末日期的月度行不再压过当日日度行；新增 4 项纯单测
- 涉及模块：backend/services/industry_registry, backend/services/industry_metric_service, backend/repositories/industry_metric_repo, backend/tests

## 2026-09-02 - industry_metrics 唯一约束纳入 freq（勘误）
- 更正：上一条 rollup 记录遗漏了已知撞键问题——唯一约束 (industry_key, stock_id, metric_key, source, period) 不含 freq 时，月末同时承载日度观测与月度归档行（mock 批、rollup+猪粮比派生批均会出现），同批 upsert 触发 PG "ON CONFLICT DO UPDATE command cannot affect row a second time" 中断事务，且月度行会覆写日度行的 freq 致 rollup 非幂等；现将 freq 纳入唯一约束（新迁移 c9d0e1f2a3b4，模型/repo 冲突列同步），跨频月末共存合法、撞键与非幂等一并消除，新增 2 项纯单测锁定批级无重复键 + 月末跨频共存不变量
- 涉及模块：backend/migrations, backend/models, backend/repositories, backend/services/industry_mock_data, backend/tests

## 2026-09-02 - months 回补窗口贯通 + mock 序列 off-by-one 修复
- `ingest_industry_metrics` 此前收了 `months=37` 却从未使用：AKShare fetcher 硬编码 `df.tail(45)` 致多年回补不可能，现已全链贯通 API schema（`FetchIndustryMetricsRequest.months`，1..120 默认 37）→ worker payload 透传 → ingest → `_fetch_akshare_rows`（tail 换算 `max(45, months*31)`）；mock 分支 `build_pig_mock_points(months=...)` 越界钳制到 1..37 而非报错；同时修复 `_wobble_series` off-by-one（旧实现返回 n+1 点、调用方取前 n 点致日度末点带抖动，"日度末点==月度最新值"不变量破裂），新增 3 项纯单测锁定精确长度/窗口/跨频末点一致
- 涉及模块：backend/services/industry_metric_service, backend/services/industry_mock_data, backend/workers/industry_metrics_worker, backend/schemas/task, backend/tests

## 2026-09-02 - 读路径卫生与契约修正（行业工作台 backend 小修合集）
- `get_dashboard` 不再在每次缓存未命中时写库（原每次 GET 都调 `evaluate_and_store_signal` 落信号行）：改为读 `repo.latest_signal`，仅空库无信号行时补算一次引导；batch 导入抽出纯函数 `_prepare_batch_rows` 并引入 source 白名单 `IMPORT_ALLOWED_SOURCES = {"manual", "stats_gov"}`（采集适配器专属 source 不得经人工通道伪造），修复恒真的 source_tier 三元式，响应补 `derived_upserted` + `skipped_invalid_source`；history 端点查询参数 `months` 更名为 `limit`（实为行数上限，默认 500，1..5000）；`get_latest_metrics`/`get_metric_history`/`list_industries` 服务与路由去掉未用的 `cache` 参数（dashboard 保留真实缓存）；`DashboardOut` 新增 `data_source`（取 `settings.industry_data_source`，前端 Task 6 消费）；新增 3 项纯单测
- 涉及模块：backend/services/industry_metric_service, backend/api/v1/industries, backend/schemas/industry, backend/tests

## 2026-09-02 - 前端修正（EChart silent / 演示标签动态化 / 相位文案去本地化）
- 三项修正对应后端 Task 5 的 `DashboardOut.data_source` 契约：① `EChart` 封装的 `silent` prop 落实文档语义（真正关闭 animation + tooltip，旧实现仅切 canvas renderer 属无效近似）；② 工作台页"演示数据源：mock"标签改为仅在 `dashboard.dataSource === "mock"` 时条件渲染，切换真实源后不再误标演示；③ 删除前端本地 `PHASE_LABELS` 映射，周期阶段文案改从后端下发 `cycle.phases[].label` 派生（`PHASE_COLORS` 保留——纯展示常量）；`industryResearch.ts` 的 `BackendDashboard`/`Dashboard`/`mapDashboard` 同步补 `data_source`/`dataSource` 字段
- 涉及模块：frontend/shared/ui/EChart, frontend/shared/api/industryResearch, frontend/pages/research-workbench

## 2026-09-02 - 终审修复（mock purge 覆盖 derived 行 + basis 键名对齐）
- mock→真实源切换的清除范围扩展：`delete_mock_rows` 泛化为 `delete_rows_by_source(db, industry_key, sources)`，清除集 `PURGE_SOURCES = {"mock", "derived"}`（派生计算只 upsert 不删除，旧实现漏删 derived 行会让 mock 算出的能繁环比/猪粮比序列存活并继续喂给周期引擎，空库下可误报复苏/买入）；ingest 返回键 `purged_mock` 相应更名为 `purged`（worker 透传 dict、scheduler 日志不依赖该键），新增纯单测锁定清除集；前端 `CyclePhaseStrip` 的 basis 读取键 `sowConsecutiveDecline` 修正为后端 snake_case 的 `sow_consecutive_decline`（此前"连续 N 个月回落"证据行静默不渲染）；顺带移除 upsert 冲突 SET 中 freq 的无效自赋值（freq 已在冲突键内）
- 涉及模块：backend/repositories/industry_metric_repo, backend/services/industry_metric_service, backend/tests, frontend/features/industry-research

## 2026-09-03 - 任务派发竞态修复 + 行业工作台 E2E 测试套件
- **问题**：task_service 先发 MQ 消息、请求结束才提交任务行，worker 提前消费时 `update_task_status` 查无此行静默跳过，任务永远停在 pending（docker 实测复现，影响所有 worker 队列）
- **修复**：抽取 `_dispatch_task` 助手统一 5 个 trigger —— 先 commit 任务行、后 publish；publish 失败标记 failed 防孤儿 pending；worker 侧任务行缺失时输出告警
- **E2E**：新增 `tests/test_industry_e2e.py`（pytest marker `e2e`，需 docker 栈，离线 `-m "not e2e"` 跳过）8 项：任务生命周期/连续触发竞态回归、latest 频率裁决、dashboard 契约、history limit+月末双频、batch 白名单、ingest 幂等、前端烟雾；docker 环境全链路验证通过（迁移链 c9d0e1f2a3b4、mock ingest 408 行、月末 daily/monthly 共存、62/62 测试）
- 涉及模块：backend/services/task_service, backend/workers/base_worker, backend/tests

## 2026-09-03 - AKShare 真实数据源接入（搜猪网/新浪期货实机验证）
- AkShareClient 重写为四个实机验证接口（2026-09-03 · akshare 1.18.94）：搜猪网 `spot_hog_year_trend_soozhu`（生猪当年均价 ~200 行）/`spot_corn_price_soozhu`/`spot_soybean_price_soozhu`（各 15 行，元/kg 日度，长历史靠逐日滚动累积）+ 新浪 `futures_zh_daily_sina(LH0)`（全历史日行情，元/吨）；删除已证伪的生意社 `spot_price_qh` 路径（100ppi 页面改版上游抛 AttributeError），全部 TODO(api-verify) 清零
- fetcher 改表驱动 `_AKSHARE_SPECS`（metric_key/source/client 方法/日期列/数值列/护栏上限），`_fetch_akshare_rows(cfg, months, client=None)` 支持注入假 client 做纯单测；补上 LH 循环缺失的未来日期守卫，新增数值健全性护栏（现货 0<v<100、期货 0<v<100000，越界跳行告警）
- source 命名：hog/corn/soybean 从 `akshare_100ppi` 改为 `akshare_soozhu`（registry sources 同步，mock 仍垫底）
- mock 清除策略修订（修订 C2 裁定）：从"整行业清除"改为"按已覆盖指标清除"——新增纯函数 `_covered_purge_keys(covered)`（hog_price 与 corn_price 同时覆盖→连同清除 hog_corn_ratio 的 derived 旧行，重算即真实值），`delete_rows_by_source` 增加 `metric_keys` 可选过滤，未覆盖指标（能繁/成本/仔猪等）保留 mock 演示数据；ingest 返回新增 `covered_metrics`，移除 `PURGE_SOURCES` 常量
- akshare 进 pyproject 运行依赖（`uv add akshare`，锁定 1.18.94）；`backend/.env` 写入 `INDUSTRY_DATA_SOURCE=akshare`（本地栈真实化，代码默认 mock 不变）；新增 `tests/test_industry_fetchers.py`（不触网：假 client fixture 单测字段映射/未来日期剔除/护栏/单指标隔离/months 窗口/覆盖清除键/规格-registry 对齐）
- 涉及模块：backend/core/providers/akshare_client, backend/services/industry_registry, backend/services/industry_metric_service, backend/repositories/industry_metric_repo, backend/tests

## 2026-09-03 - Agent 指令文档治理（AGENTS.md/CLAUDE.md 单一事实来源）
- 合并 best-practice.md 与 best-practices.md 为后者（30 条去重合并），删除单数版并修正全部引用；根 CLAUDE.md 改为转发 AGENTS.md（backend/crons 的 CLAUDE.md 本就是 symlink）；重写 backend/AGENTS.md 对齐实际目录结构（修正 SQLModel→SQLAlchemy 2.0、不存在的顶层组件）；根 AGENTS.md 新增"常用命令"小节（uv/npm/docker compose，命令均已实跑验证）并标注根目录 src/ 为早期 CLI 遗留；重写 crons/AGENTS.md 澄清与 app/scheduler、app/workers 的分工；修复 frontend/AGENTS.md 与 docs/references/index.md 的断链
- 涉及模块：AGENTS.md（root/backend/frontend/crons）、CLAUDE.md、docs/references/best-practices.md、docs/references/index.md

## 2026-09-03 - Playwright 浏览器级 E2E（投研列表 + 猪智投工作台）
- frontend 新增 Playwright 套件（devDep `@playwright/test` + `npm run test:e2e` 脚本，chromium 本地安装）：`/research` 断言生猪养殖行业卡片、申万Ⅲ标签与"指标接入 x/x"覆盖度形状；`/research/pig` 经卡片点击导航后断言头部"周期阶段/当前信号"标签（信号 ∈ 买入|卖出|关注|空仓）、指标带生猪均价卡片数值非空、周期相位条四阶段（繁荣/衰退/萧条/复苏）且唯一"当前"高亮、仓位建议三段（核心底仓/波段仓位/现金储备）、≥2 个 EChart canvas、核心指标速览网格、行业知识库 Tab 的 P6 占位文案；断言全部锚定中文标签与 DOM 结构、不锚定实盘数值（数据为真实/混合源）；tsconfig include 未覆盖 e2e/ 目录，`tsc -b` 与 vite 构建不受影响（已验证）
- 涉及模块：frontend/e2e（research.spec.ts）、frontend（package.json / playwright.config.ts）

## 2026-09-03 - 能繁协会源接入
- 新增 `CaaaClient`（pig.caaa.cn 中国畜牧业协会猪业分会）：行业动态栏目列表页（`/html/pig_rd/pig_hydt/`，年份目录 403 但栏目页 200）倒序发现最新"全国生猪产品数据"月度文章，正文纯文本正则解析（无表格）"能繁母猪存栏XXXX万头，环比下降/上升X%"——`parse_sow_article`/`find_latest_data_article` 为纯函数可离线单测；数据期取标题月份的月末（"2026年3月份"→03-31，与正文"1季度末"一致），环比按方向词归一符号；任何失败 log warning 返回 None，绝不抛穿；`CAAA_SOW_ARTICLE_URL` 设置可指定文章直连兜底
- ingest 接线：`source=="akshare"` 时 `_fetch_caaa_sow_row`（可注入假 client）在 upsert 前并入 → sow_inventory 进入 covered_metrics，mock purge 覆盖能繁演示行；registry sow sources → `["stats_gov", "caaa", "mock"]`（统计局 CSV 通道仍最高优先）；row extra 落 article_url/mom_pct 溯源
- 实跑验证：live 探针返回 `{period: 2026-03-31, inventory: 3904.0 万头, mom_pct: -1.5%, article: /2026/0427/2467.html}`；新增 `tests/test_caaa_client.py` 18 项离线单测（真实文章快照 fixture + 符号归一 + 兜底链 + 容错 + 服务接线/registry 对齐）
- 涉及模块：backend/core/providers/caaa_client, backend/services/industry_metric_service, backend/services/industry_registry, backend/config, backend/tests

## 2026-09-03 - P5 标的分析核心（公司指标 / 头均市值 / companies 端点 / 对比表）
- **Registry**：新增公司级指标 group="company"（stock_id>0 落表）——`company.hogs_sold_monthly`(万头,monthly,manual)、`company.cost_complete`(元/kg,quarterly,manual)、派生 `mcap_per_head`(元/头,monthly,calc)；**修正 sw_l3_codes 110301→110702**（110301 实为林业Ⅲ，生猪养殖=110702，与 docs/references/sw/申万行业分类.md 及库内 SW seed 一致，companies 端点实测暴露）
- **派生**：`_annualize_hogs` 纯函数（≥12 个不同月 trailing-12M SUM；1-11 个月最新月×12 粗年化，extra.annualized 标记；同月多点取最新）→ `_compute_derived_metrics` 追加头均市值 = daily_basic 最新 total_mv(万元) ÷ 年化出栏(万头)，单位相消为元/头；历史分位暂缓（需 ≥1 年派生行积累）
- **修复（实跑暴露的存量缺陷）**：能繁环比派生在多源同 period 共存（caaa 真实行 + 重跑 mock 演示行）时产出重复 period 行，单批 ON CONFLICT 二次命中即 CardinalityViolation——按 registry 源优先级逐期去重后再算环比
- **API**：`GET /api/v1/industries/{key}/companies` —— sw_l3_codes 成分股（复用 market_service，抽取 `list_symbols_by_industry_codes` 泛化原 level3 查询）+ enriched 行情/估值（复用 `get_stocks_enriched_by_symbols`）+ 公司指标 latest（新增 repo：`latest_company_rows`/`get_company_metric_history`，`_pick_latest` 泛化为 `_pick_row` 供公司级复用）；列定义 registry 驱动下发（固定 代码/名称/最新价/总市值(亿)/PE(TTM)/PB + company 指标列）
- **前端**：`fetchIndustryCompanies` + `CompanyComparisonTable`（列由 payload columns 驱动、数值右对齐可排序、行点击跳 /stock/:symbol、antd Tabs 惰性挂载保证 Tab 激活才发请求）挂入工作台「行情调研追踪」Tab；market-industry-level3 页命中已产品化行业（sw_l3_codes 与当前二级/选中三级交集）时展示"进入投研工作台"banner（复用 industries 查询缓存）
- **测试**：新增 `tests/test_industry_companies.py` 10 项纯单测（年化出栏各分支/列下发/stock_id 透传/L3 码锁定）；e2e 追加 companies 全链路（tree 动态解析成分股 → 双猪股导入 6 个月出栏+成本 → 断言头均市值>0 与 registry 列）+ 未知行业 404；Playwright 追加对比表渲染跳转 + L3 页 banner 两用例；全量 99 backend + 4 Playwright 通过（含 docker 重建实跑）
- 涉及模块：backend/services/industry_registry, backend/services/industry_metric_service, backend/services/market_service, backend/repositories/industry_metric_repo, backend/api/v1/industries, backend/schemas/industry, frontend/shared/api, frontend/features/industry-research, frontend/pages/research-workbench, frontend/pages/market-industry-level3, backend/tests, frontend/e2e

## 2026-09-03 - P5 ETF/可转债管道
- **表与迁移**：新增 `fund_etf_daily` / `cb_daily`（迁移 d5a6b7c8d9e0，链头自 c9d0e1f2a3b4），镜像 daily_quotes 数值口径（OHLC Numeric(12,4)、volume/amount 原样落库不换单位），UNIQUE(ts_code, trade_date) 支持幂等 upsert
- **TuShare 采集**：TuShareClient 新增 `fetch_fund_daily`/`fetch_cb_daily`/`fetch_cb_basic`（经 RateLimitedSyncProvider 节流）；`securities_service.ingest_industry_securities` 按 registry `etf_codes`/`cb_codes` 逐代码回补（单代码失败 log+跳过且错误摘要进任务 result），`map_daily_rows`/`build_code_series` 为纯函数可离线单测
- **Registry 标的（2026-09-03 实机核验）**：pig `etf_codes=["159865.SZ"]`（国泰中证畜牧养殖ETF，fund_basic 实测名）；`cb_codes=["127045.SZ","123107.SZ","127049.SZ"]`（cb_basic 按 9 只成分股正股名过滤、仅留在市 delist_date 为空：牧原转债/温氏转债/希望转2；已退市的希望转债 127015、正邦转债 128114 排除）；`securities_names` 下发 code→展示名
- **双轨任务**：QUEUES `securities.fetch` + `SecuritiesWorker` + `POST /api/v1/tasks/fetch-securities`（backfill_days 默认 365，ge=1 le=1825）+ APScheduler `securities_refresh_job` 工作日 17:10（industry_metrics 17:05 之后），调度走 10 天增量窗口、手动通道负责全年回补
- **API**：`GET /api/v1/industries/{key}/securities?type=etf|cb&limit=90` → `{type, codes:[{ts_code, name, latest, change_pct(close vs pre_close), series}]}`，未拉取时 series 空（前端空态引导）
- **前端**：「行情调研追踪」Tab 成分股对比表下新增"行业 ETF"（代码/名称/最新价/涨跌幅/成交量/近期走势 sparkline，复用 sparkOption+EChart）与"可转债"（registry 无在市转债时不渲染）两张紧凑表 +「拉取数据」按钮（触发任务后 3s 延迟刷新）
- **测试**：`tests/test_industry_securities.py` 11 项离线单测（registry 标的/名称覆盖、TuShare 行映射含脏行跳过、序列组装涨跌幅、冲突列常量=模型约束=迁移、db 透传回归锁定）；e2e 追加 fetch-securities 任务→securities 端点全链路（≥30 序列行 + cb 分支随 registry 源无关）+ 404/422；Playwright 追加 ETF 表用例；全量 111 backend（99 offline + 12 e2e）+ 5 Playwright 通过（docker 重建实跑：ETF 243 行、3 只转债 729 行入库）
208	- 涉及模块：backend/models/securities, backend/migrations, backend/core/providers/tushare_client, backend/services/industry_registry, backend/services/securities_service, backend/repositories/securities_repo, backend/core/mq, backend/workers/securities_worker, backend/scheduler, backend/services/task_service, backend/api/v1/tasks, backend/api/v1/industries, backend/schemas, frontend/shared/api, frontend/features/industry-research, frontend/pages/research-workbench, backend/tests, frontend/e2e
209	
210	## 2026-09-09 - 前端统一请求层、认证状态机与路由守卫 (Stage 2)
211	- 重构 `shared/api/client.ts` 通用底层请求（强制 `credentials: include`、CSRF 单飞并发获取与自动注入、结构化 `ApiError` 统一解析、401 拦截事件广播），新建 `shared/api/auth.ts` 契约客户端，实现基于 Zustand + React Query 的 `features/auth` 登录状态机与 `RequireAuth` 路由守卫，集成 `/login` 登录/注册表单与导航栏 `UserMenu`。
212	- 涉及模块：frontend/shared/api, frontend/features/auth, frontend/pages/login, frontend/app/router, frontend/app/layouts


## 2026-09-03 - P6 行业知识库
- **表与迁移**：新增 `industry_knowledge`（迁移 e6f7a8b9c0d1，链头自 d5a6b7c8d9e0）：`industry_key/kind(org|principle|mindmap)/payload JSONB/sort`，同 kind 多行按 (kind, sort, id) 读序，索引 (industry_key, kind, sort)；纯内容管理，第二行业零表结构改动
- **迁移内 seed（内容即数据）**：内容单点维护于 `app/services/industry_knowledge_seed.py`，迁移与单测共用同一份（本仓库 alembic env 本就运行于 app 包内，env.py 已 import app.*，import app 内容模块与既有运行方式一致）；猪智投 14 机构（官方 5/协会 2/数据平台 6/期货 1，tier 对齐 SourceBadge 五级权威性）、数据权威性使用原则 5 条、思维导图 EChart tree（供给/需求/成本/政策/金融 五分支，叶子 ≤2 层深）；PRD 原文不在仓库，内容以 data-source.md §四（口径与坑）+ §六（参考链接）为基准整理
- **API**：`GET /api/v1/industries/{key}/knowledge` → `{org:[...], principle:{title,items}|null, mindmap:{name,children}|null}`；未知行业 404（与既有端点同语义）、已知行业无内容 → 空形状 200；脏行（形状不合法/非 dict payload）log 后跳过不打挂 Tab
- **前端**：`fetchIndustryKnowledge`（payload 透传无 mapper）+ `KnowledgeTab`（机构图谱四分组卡片：名称+SourceBadge+一句 desc；原则编号列表；思维导图 EChart tree orient LR 可折叠 420px）挂入工作台「行业知识库」Tab 替换占位，antd Tabs 惰性挂载首激活才请求
- **测试**：`tests/test_industry_knowledge.py` 7 项离线单测（种子形状：四分组/tier 合法/名称唯一/原则 ≥4/树可序列化 ≤2 层/sort 组序 + 装配纯函数空态与脏行容错）；e2e 追加 knowledge 聚合（org ≥12 四分组、原则 ≥4、思维导图 ≥4 分支）+ 未知行业 404；Playwright 知识库用例由占位断言升级为分组卡片/机构条目/原则/思维导图 canvas 断言（`.ant-card-head-title` 作用域规避 SourceBadge 同文案混淆）；全量 121 backend + 5 Playwright 通过（docker 重建实跑）
- 涉及模块：backend/models/industry_research, backend/migrations, backend/services/industry_knowledge_seed, backend/services/industry_knowledge_service, backend/repositories/industry_knowledge_repo, backend/api/v1/industries, backend/schemas/industry, frontend/shared/api/industryResearch, frontend/features/industry-research, frontend/pages/research-workbench, backend/tests, frontend/e2e

## 2026-09-03 - P6 收尾：research 列表信号化 + broiler 泛化验证 + 计划勾选
- **列表卡片信号化**：`GET /api/v1/industries` 每行业增 `phase / signal_type / signal_date`（list_industries 逐行业 latest_signal 查询，从未 ingest 的行业为 null）；`/research` 卡片增状态行——周期阶段 Tag（prosperity/recession/depression/recovery 四色与工作台同板，色板与文案映射抽取 `features/industry-research/constants.ts` 供列表/工作台共用）+ 当前信号加粗 Tag + 信号日期，保留指标接入覆盖度与数据截至
- **泛化验证（broiler 白羽肉鸡第二行业，零新页面）**：registry 增 `BROILER_INDUSTRY`（申万Ⅲ 110703 肉鸡养殖——三源核验：sw_seed.sql + 申万行业分类.md + 库内 live tree，7 只成分股与生猪 110702 不相交）；2 个 mock-only 指标 `chick_price` 鸡苗价格 / `broiler_price` 毛鸡价格（`MetricDef` 新增 `mock_base` 基准值字段）；复用通用四周期键位（描述按肉鸡口径改写）与 `_position_slices` 仓位模板；周期引擎 `evaluate_pig_cycle` 增可选 `cfg` 参数（驱动预警档与仓位模板，缺省仍 PIG_INDUSTRY，规则本体保持猪周期口径）
- **通用 mock builder**：`build_generic_mock_points(cfg)` 对所有配置 `mock_base` 的指标按注册频率（daily/weekly/月度对齐）生成 seeded 抖动序列，末点精确等于基准值；`build_industry_mock_points` 统一分发（pig 走原型对齐专用序列，其余走通用）——新演示行业无需再写 builder 模块；`source=mock` ingest 链路（worker payload industry_key=broiler）端到端跑通（90 行落库 + 信号评估）
- **测试**：新增 `tests/test_industry_generalization.py` 10 项离线单测（registry 双行业 sw 码不相交 / broiler mock-only / 通用 builder 无重复冲突键+确定性+末点=基准 / 引擎 cfg 注入）；e2e 追加 broiler ingest→列表→dashboard 全链路 + pig/broiler 成分股隔离断言；Playwright 追加列表状态行与"broiler 卡片零新页面进入工作台"两用例（并修一处竞态：列表卡片新增周期/信号 Tag 后，SPA 路由切换瞬间旧列表 DOM 触发严格模式多元素，现先等 Tab 栏挂载再断言头部标签）；全量 133 backend（117 offline + 16 e2e）+ 7 Playwright 通过（docker 重建实跑）
- **文档收尾**：`plans/industry-research-workbench.md` P1-P6 验收项按实际完成情况勾选留痕（未勾选项附原因：猪粮比独立图表、统计局 CSV 脚本、多源对比 Drawer；头均市值分位注明"分位待历史积累"）；`plans/2026-09-03-workbench-p3-p6-completion.md` 五阶段标记完成；data-source.md 补 `sow_inventory_mom` 派生行与 broiler mock 演示指标注记（metric_key 交叉引用对齐）
- 涉及模块：backend/services/industry_registry, backend/services/industry_mock_data, backend/services/industry_metric_service, backend/services/cycle_engine, backend/schemas/industry, frontend/pages/research, frontend/pages/research-workbench, frontend/features/industry-research, frontend/shared/api/industryResearch, backend/tests, frontend/e2e, plans, docs

## 2026-09-03 - 个股/指数详情 K 线周期切换修复
- **问题**：个股详情（及指数详情）K 线图切换 1月/3月/6月/1年 周期"显示不对、似未生效"——任何周期都只渲染所选区间尾部 40%（dataZoom 固定 start:60），且 `ReactECharts` 默认 merge 模式下用户滚轮缩放状态粘滞，切周期后可见窗口不重置；后端 start/end 过滤与缓存 key 均正常（已实测 30d=22 行 / 365d=242 行）
- **修复**：`KLineChart` 与 `IndexKLineChart` 去掉 dataZoom 固定 `start:60/end:100`（周期切换后默认展示全量区间，滚轮缩放留给用户主动操作），并加 `notMerge`（与 `shared/ui/EChart` 封装既有约定对齐），确保切周期时完整重放 option、重置缩放状态
- **验证**：docker 重建前端后浏览器实测——滚轮缩放至窄窗口再切"1年"，视图与干净的全年视图逐字节一致（修复前该场景窗口冻结）；1月=整月 22 根、1年=全年 200+ 根全量渲染
- 涉及模块：frontend/features/stock-detail, frontend/pages/index-detail

## 2026-09-03 - 前端交互修复
- **面包屑可点击**：投研工作台（/research/pig、/research/broiler 共用组件）面包屑首项"投研"由纯文本改为 react-router `<Link to="/research">`（antd Breadcrumb item title 直接承载 Link，末项"工作台"保持当前页无链接约定）；/research 列表页无面包屑，不涉及
- **行业卡片等高 + 描述截断**：/research 两张行业卡片在部分宽度不等高（broiler 描述换行 2 行撑高 246/268px）——描述改 `Typography.Text` `ellipsis={{ tooltip: true }}` 单行省略（hover 出全文）消除换行差；再以 Col `display:flex` + Card `height:100%` 拉伸兜底（窄宽度 Tag 换行等场景仍等高）；卡片头部 h4 名称加 `minWidth:0` + `ellipsis={{ rows:1 }}`、申万 Tag/箭头 `flexShrink:0`，超长行业名同样省略号截断
- **测试**：Playwright 新增两用例——/research 两 `.ant-card` boundingBox 等高（≤1px 子像素容差）；/research/pig 点面包屑"投研"→ waitForURL `**/research` 且生猪养殖卡片可见；9/9 通过（docker 重建前端实跑；另 1280/480 双宽度实测 diff=0，ellipsis computed style 核验）
- 涉及模块：frontend/pages/research, frontend/pages/research-workbench, frontend/e2e

## 2026-09-03 - K线共享组件纯函数层（K线组件升级 Task 1）
- 新建 `frontend/src/shared/ui/kline/`（`klineMath.ts` + barrel `index.ts`）：MA5/10/20/60 定义与滑动平均（暖窗前为 null）、同年 MM-DD/跨年首日 YYYY-MM-DD 轴标签、日期区间裁剪 `cropToRange`、成交量/成交额格式化 `fmtVolume`/`fmtAmount`，并 re-export 复权三类型；`shared/types` 的 `KLinePoint` 增 `amount?: number`，文件末尾追加 `AdjustMode` / `KlineResult` / `KlineFetcher`——为个股/指数两处重复 K 线图合并为共享组件打底，类型签名作为后续任务的依赖契约冻结（plans/2026-09-03-kline-component-upgrade.md Task 1）
- 涉及模块：frontend/shared/ui/kline, frontend/shared/types

## 2026-09-03 - K线 option builder（K线组件升级 Task 2）
- 新建 `frontend/src/shared/ui/kline/klineOption.ts` 并入 barrel：`buildKlineOption(KlineOptionInput)` 消费 Task 1 纯函数产出完整 ECharts option——candlestick 主图 + visibleMas 过滤的 MA 折线叠加 + 成交量副图（涨红跌绿）、结构化 tooltip（DOM formatter 返回 HTML：OHLC/涨跌幅/量额/MA 行，按前收着色）、最新收盘价虚线 markLine、inside+slider 双 dataZoom、跨年轴标签；一次构建失败修复：tooltip 内 `maLine` 参数 `string` 收窄为 `MaKey`（string 不能索引 `Partial<Record<MaKey,…>>`）（plans/2026-09-03-kline-component-upgrade.md Task 2）
- 涉及模块：frontend/shared/ui/kline

## 2026-09-03 - K线共享容器组件 KlineChart（K线组件升级 Task 3）
- 新建 `frontend/src/shared/ui/kline/KlineChart.tsx` 并入 barrel：`KlineChartProps` 五字段契约（title/queryKey/fetcher/showAdjust/defaultRange）——React Query 按 `["kline", queryKey, range, adjust]` 取数并加 130 日历日 MA warm-up 缓冲，本地按 `rangeCutoff` 裁剪可见区间（K 线与 MA 同口径对齐）；工具栏 MA CheckableTag 开关（选中按均线色着色）/ 复权 Segmented（showAdjust 时展示，默认 qfq）/ 周期 Segmented（1/3/6月/1年）/ 重置缩放按钮——直用 ReactECharts + `notMerge` + `lazyUpdate` 并以实例 ref `dispatchAction(dataZoom)`，不走未透传 ref 的 EChart 封装；loading/empty 固定 400px 占位；一次构建失败修复：antd 5.24 顶层无 `CheckableTag` 命名导出，改 `Tag.CheckableTag`（对齐 market-industry-level3 既有用法），其连带的 onChange 隐式 any 次生报错随之消除（plans/2026-09-03-kline-component-upgrade.md Task 3）
- 涉及模块：frontend/shared/ui/kline

## 2026-09-03 - 个股/指数详情接入共享 KlineChart（K线组件升级 Task 4）
- 个股详情页与指数详情页切换至共享 `KlineChart` 容器（MA chips 显隐/周期与复权 Segmented/结构化 tooltip/重置缩放），删除 `features/stock-detail/components/KLineChart.tsx` 与 `pages/index-detail/IndexKLineChart.tsx` 两份旧图表（净 -136 行）；`fetchKlineBySymbol` 增第三参 `adjust` 透传（后端 P2 就绪前 FastAPI 忽略该 query 参数，属预期过渡）并返回 `KlineResult`（`adjustAvailable` 按响应 `adjust_available !== false` 推导），`fetchIndexKline` 返回 `KlineResult`（指数无复权恒 true），两个 fetcher 均映射 `amount`（成交额）；新增 `frontend/e2e/kline.spec.ts` 3 用例——MA chips 可见可切换、tooltip 涨跌幅/成交量/成交额、周期切换选中态跟随、指数页无复权控件；e2e 首跑失败修复：antd 5.24 Segmented 的 radio input 为零尺寸隐藏元素，控件断言从 `getByRole("radio")` 改为可见 `label.ant-segmented-item`（选中态断言 `ant-segmented-item-selected` 类）（plans/2026-09-03-kline-component-upgrade.md Task 4）
- 涉及模块：frontend/shared/api/quotes, frontend/shared/api/market, frontend/pages/stock-detail, frontend/pages/index-detail, frontend/features/stock-detail/components, frontend/e2e

## 2026-09-03 - K线复权懒加载回补接线（K线组件升级 Task 7）
- `quotes/daily` 端点增 `adjust` query 参数（`Literal["raw","qfq"]`，默认 raw）透传 service；响应 `adjust_available=false` 时由 FastAPI BackgroundTasks 触发 `quote_service.backfill_adj_factor(exchange, symbol)`——幂等（`has_adj_factor` 已有即 skip）、单股全历史拉取 TuShare adj_factor 批量 UPDATE 入库、`delete_pattern("quote:kline:{exchange}:{symbol}:*")` 失效该股 K线缓存，异常兜底 log 不影响响应；修复实机验证暴露的 `update_adj_factors` SQL 缺陷（VALUES 派生表未定型日期字面量被 PG 推断为 text，与 date 列比较抛 `operator does not exist: date = text`——补 `::date` 显式转型）；新增 2 个 service 级单测（fake cache 记录 set 调用：qfq 因子不完整不写缓存、raw 正常缓存）
- **验证**：pytest 19 failed / 125 passed（19 个全为基线 httpx.ConnectError 环境性失败，与 HEAD 失败集 diff 为空，零回归）；docker 重建后实机——600519 首次 qfq `available: False rows: 23`，25s 后二次 `available: True close[0]: 1358.98`，日志 `[adj_factor backfill] Shanghai_Stocks.600519 updated=808` 一条且三次请求不重触（幂等生效），库内 `adj_factor IS NOT NULL` 共 808 行；除权窗口（2026-06-22..30，因子 8.4464→8.6463）raw 与 qfq 数值分化（1241.41→1212.71，基准日及之后不动）
- 涉及模块：backend/services/quote_service, backend/api/v1/stocks, backend/repositories/quote_repo, backend/tests

## 2026-09-03 - K线复权开关前端完整接入（K线组件升级 Task 8）
- 共享 `KlineChart` 的复权 Segmented 接入 `adjustAvailable` 禁用态：`data.adjustAvailable` 为假（含首帧 loading）时降级为 Tooltip（"复权数据后台拉取中，稍后自动可用"）包裹的 `disabled` Segmented 且 value 固定 "raw"（表示当前展示即 raw 数据），`adjust` 状态保持 "qfq"、queryKey 不变不发额外请求；因子就绪后受控值恢复 "qfq" 无缝启用。`frontend/e2e/kline.spec.ts` 新增用例"数据就绪后可切换前复权/不复权"——按 Task 4 沉淀以 `label.ant-segmented-item` 文案定位 + `ant-segmented-item-selected` 类断言改写 brief 原始的 `getByRole("radio")`/`toBeChecked`（antd 5.24 radio input 零尺寸隐藏必失败），两处 30s 超时为后端懒加载回补留等待余量。验证：`npm run build` 通过（chunk 警告为既有基线）；docker 重建后 playwright 全量 16 passed / 4.7s（含既有 spec 零回归；600519 实况 `adjust_available: True`）
- 涉及模块：frontend/shared/ui/kline, frontend/e2e

## 2026-09-03 - K线组件升级整期收官（Task 0-9，P1 组件统一 + P2 复权链路 + P3 复权开关）
- **整期成果**：个股/指数两份重复 K 线图合并为共享 `shared/ui/kline/KlineChart`（P1：MA5/10/20/60 显隐、结构化 tooltip、inside+slider 缩放与重置、最新价 markLine、跨年轴标签）；后端打通 adj_factor 懒加载链路（P2：`GET /quotes/daily?adjust=qfq|raw` + `adjust_available` 标记 + BackgroundTasks 幂等单股回补 + `delete_pattern` 缓存失效）；前端复权开关完整接入（P3：因子未就绪禁用+Tooltip 降级，就绪后无缝启用）。净删除两份旧图表组件，e2e 扩至 16 用例
- **关键架构决策**：① 共享组件契约先冻结——`KlineFetcher = (days, adjust) => Promise<KlineResult>` 作为 Task 1 类型签名发布，shared 层不 import 业务 API，靠 fetcher 回调注入实现依赖倒置，个股/指数页各传一份；② 复权三重缓存防护——缓存 key 追加 `:{adjust}` 维度、qfq 因子不完整不写缓存、回补完成后 `delete_pattern("quote:kline:{exchange}:{symbol}:*")` 兜底，杜绝 qfq 结果污染 raw 缓存与回补后读到陈旧数据；③ `::date` 转型根因——手写 `UPDATE ... FROM (VALUES ...)` 派生表日期字面量被 PG 推断为 text 抛 `date = text`，此类 SQL 类型错误纯函数单测覆盖不到，接线任务以实机验证闭环
- **收官回归**：backend pytest 19 failed / 125 passed（19 个全为基线 httpx.ConnectError 环境性失败，与 Task 7 记录的失败集一致，零回归）；frontend `npm run build` 通过；playwright 16/16 passed
- 涉及模块：backend/repositories/quote_repo, backend/services/quote_service, backend/api/v1/stocks, frontend/shared/ui/kline, backend/tests

## 2026-09-09 - 微服务认证与授权最佳实践研究
- 基于 NIST Zero Trust、OWASP API Security、OAuth JWT Access Token/JWK 与 SPIFFE/SPIRE 官方资料，明确 Gateway 与业务服务二次校验、workload identity/mTLS、aud/iss/scope/RBAC 责任边界、token 转发与 exchange/downscoping 原则，并形成 stock_bot（FastAPI、RabbitMQ Worker、Scheduler、Redis、PostgreSQL、Docker Compose）的落地建议。
- 涉及模块：docs/references/best-practices、backend/api、backend/core、backend/workers、backend/scheduler、docker-compose

## 2026-09-03 - qfq 跨日死锁修复（K线组件升级最终审查 C1+I1）
- **问题（C1）**：每日 ingest 以 `adj_factor=None` upsert 新日期行情且 ON CONFLICT SET 无条件覆盖——重灌既有日期会抹掉已回补因子；回补幂等判定 `has_adj_factor`（任一行非空即 skip）与 `get_kline` 可用性口径（区间全部行非空）错位，且 skip 在缓存失效之前 return → 次日起 qfq 永久 `adjust_available=false` 死锁
- **修复**：① `upsert_quotes` SET 子句 `adj_factor` 改 `COALESCE(excluded.adj_factor, daily_quotes.adj_factor)`（NULL 不覆盖既有因子）；② 幂等判定换 `latest_adj_factor_present`（最新交易日行有因子才 skip，跨日新增 NULL 行可增量再触发）并删除口径错位的 `has_adj_factor`；③ 真实外呼后写 300s Redis 冷却 key `quote:adj-factor:backfill-cd:{exchange}:{symbol}`（skip 不冷却、失败也冷却防失败风暴），API 层 `add_task` 前以 `cache.exists` 守卫，key 模板提为 quote_service 模块常量两处共用
- **问题（I1）**：复权禁用态 Tooltip 承诺"稍后自动可用"但 5min staleTime 内不会自动重取——`useQuery` 加 `refetchInterval`（`adjustAvailable === false` 时 10s 轮询，就绪即停）
- **验证**：`tests/test_kline_adjust.py` 8/8（新增冷却 key 契约测试）；全量 pytest 126 passed / 19 基线环境性失败零回归；docker 重建后跨日自愈模拟——置 NULL 最新行 → qfq `adjust_available:False` 触发回补（updated=808，冷却 key TTL≈300）→ 20s 后 `available:True` 库内因子恢复；冷却期内重复请求不重触；容器内重灌 09-01 既有日期（因子 None）COALESCE 保住原值；frontend build + playwright 16/16
- 涉及模块：backend/repositories/quote_repo, backend/services/quote_service, backend/api/v1/stocks, frontend/shared/ui/kline, backend/tests

## 2026-09-03 - K线P4 图表区重构（频率Tab + 图内MA行 + 去日期轴）
- 共享 `KlineChart` 按主流行情终端风格重构：频率 Segmented（日K/周K/月K）替换范围选择，周/月K 纯前端聚合（`aggregateDaily`：open=组首、close=组末、high/low=极值、volume/amount=求和；freq 不进 queryKey、fetcher 固定 3650 天=库内全量，切换零请求）；MA 数值行从 Card extra 的 CheckableTag 移入图表左上角绝对定位 span 行（选中=线色带最新值、未选=灰 #9ca3af、可点击切换，修复 CheckableTag 对比度问题）；主图/成交量两轴 `axisLabel.show=false` 去掉成交量区日期轴，x data 用原始 ISO 日期串由 tooltip 直接消费；dataZoom 改 startValue/endValue 按末尾 `DEFAULT_TAIL_BARS=120` 根定初始视图（TradingView 式：数据全量、视图局部、slider 漫游），grid2 由 66%/13% 调至 68%/16%；删除 `MA_WARMUP_CALENDAR_DAYS/cropToRange/buildAxisLabels` 导出与 `defaultRange` prop（两个页面调用点无引用，零破坏）。验证：npm run build 通过；docker 重建后 playwright 全量 16 passed / 4.7s（首跑 15/16——MA60 off 态 span 文本为 "MA60 "，JSX 保留尾部空格致 `^MA60$` 正则不命中，改 `\s?$` 后全绿）
- 涉及模块：frontend/shared/ui/kline, frontend/e2e

## 2026-09-03 - K线P4 UI迭代收官（行情终端风格 + 个股头部8项网格）
- **MA对比度根因与图内数值行方案**：MA 开关原用 antd CheckableTag 承载，其选中态自带主题色实底，与 inline 均线色文字撞色（对比度 ~1.2:1）——改为图表左上角绝对定位文本行（选中=线色文字带最新值、未选=灰 #9ca3af、可点击切换），线色文字落在白底图区对比度天然达标（教训沉淀 best-practices）
- **频率Tab语义与前端聚合**：日K/周K/月K 是同一份日行情的"重新分桶"而非不同数据窗口——周/月K 由 `aggregateDaily` 纯前端聚合（open=组首/close=组末/high-low=极值/量额=求和），freq 不进 queryKey、fetcher 固定 3650 天拉库内全量，切换零请求零延迟；替代原"1/3/6月/1年"范围选择
- **头部8项**：个股头部升级 `Descriptions column={4}` 两行 8 项网格（今开/最高/最低/昨收/成交量/成交额/换手率/总市值），后端 enriched 查询补选最新行情行 open/high/low 三列（模式照既有 prev_close 同行取法）
- **默认120根**：初始视图改 dataZoom startValue/endValue 按末尾 `DEFAULT_TAIL_BARS=120` 根定位（TradingView 式：数据全量、视图局部、slider 漫游），成交量区去日期轴
- **收官回归**：backend pytest 126 passed / 19 failed（全为基线 httpx.ConnectError 环境性失败，零回归）；frontend `npm run build` 通过；playwright 17/17 passed（含新增头部8项网格与频率Tab/图内MA行用例）
- 涉及模块：frontend/shared/ui/kline, frontend/features/stock-detail, backend/services/market_service, backend/schemas/stock, frontend/e2e

## 2026-09-03 - 市值/成交额单位口径统一（单位归一 Task 1）
- **问题**：全站市值/成交额显示差 1e4/1e3 倍——后端 TuShare 口径 total_mv/circ_mv 为万元、amount 为千元，前端 formatCap 按元分档（≥1e12 万亿/≥1e8 亿/≥1e4 万），mapBackendStockEnriched 原样透传未换算，导致 600519 总市值显示为"1.62亿"量级错误
- **修复**：
  - mapBackendStockEnriched 单点换算为元（amount ×1e3、total_mv/circ_mv ×1e4，判空在前避免 null 归零），volume 保持手口径不换算
  - StockTable 成交额列 NumberText 补 `unit="cap"`（WatchlistTable 核对无缺）
  - KlineChart 死代码清理：删未使用 DEFAULT_TAIL_BARS import、MA 行冗余 `!isLoading &&` 守卫
- **验证**：e2e 头部用例追加形状断言（600519 总市值含"万亿"、成交额含"亿"），17 用例全绿；实机 curl 推演总市值 1.62万亿 / 成交额 26.34亿
- 涉及模块：frontend/shared/api/stocks, frontend/features/market, frontend/shared/ui/kline, frontend/e2e

## 2026-09-03 - K线 tooltip 列式布局（P5 小迭代）
- **问题**：tooltip 三行挤排（"开：x 高：x 低：x 收：x"横排）无数字对齐、无涨跌额，与主流行情终端差距明显
- **修复**：`klineOption.ts` formatter 重写为参考图 1 的两列式逐行布局（灰标签左 + 右对齐 tabular-nums 语义色数值右）：日期标题行 → 开盘/收盘/最高/最低/涨跌额/涨跌幅（随当日涨跌着色，涨跌额带正负号，首日中性）→ 成交量/成交额（中性）→ MA 尾行（线色，悬停根的值）；容器 min-width:150px 保证列对齐
- **验证**：e2e tooltip 用例强化（开盘/收盘/涨跌额标签断言），全量 17/17 绿；浏览器实测两列对齐与着色规则符合预期
- 涉及模块：frontend/shared/ui/kline, frontend/e2e

## 2026-09-03 - 市场页"其他"行业分类合并清零（数据运营）
- **问题**：申万成员表仅覆盖 74% 股票，1439 只（多为次新股）落入"其他"，按 TuShare 行业名分 83 组，其中 29 组与现行申万 L2/L3 精确重名、54 组近似名，视觉上"重合却未合并"
- **处理**：A 精确重名 321 只按同名映射 + B 近似名 52 组 1017 只按语义映射表 + C 电气设备 99 只按东财行业接口三方归类（同花顺双源核验特例）+ 2 只银行逐股，共 1439 行写入 stock_custom_sw_tags（幂等）；"其他"清零，L1 组 32→31
- **验证**：API 实测电子 524/电力设备 351/医药生物 498/银行 42 与推演一致；树缓存失效后浏览器即时生效
- 涉及模块：数据层 stock_custom_sw_tags（无代码变更）

## 2026-09-03 - 行业分类种子同步 — custom_tags overlay 种子与启动加载
- **问题**：1439 只"其他"合并数据只存在于运行库，repo 种子未同步——全新部署会退回"其他"83 组状态；且 `sw_seed.sql` 为自动再生成文件（仅覆盖申万官方两表），手改会被抹掉
- **修复**：新增 `backend/data/sw_custom_tags_seed.sql` overlay 种子（1439 行，INSERT ON CONFLICT DO NOTHING 加性语义，保留用户自建标签）+ `sw_industry_service` 新增 `import_custom_tags_from_sql()` 并接入 `import_all()` 两条路径（SQL 种子/XLS 引导），启动初始化自动加载；`.gitignore`/`backend/.dockerignore` 补豁免使种子进 git 与镜像
- **验证**：种子在活库幂等重跑（INSERT 0 0）；api 重建后容器内 loader 实测返回 1439；mypy 零新增（基线 5 项既有）
- 涉及模块：backend/data, backend/app/services/sw_industry_service, backend/.dockerignore, .gitignore

## 2026-09-03 - 种子一致性审计 + overlay 加载修复
- **审计**：临时库灌入 repo 种子与活库逐行 diff——sw_industry_classes 511 / sw_industry_members 4430 / stock_custom_sw_tags 1439 三表完全一致，全新 docker 部署分类数据与当前显示一致
- **发现并修复**：data_init 的 overlay 加载被 is_sw_data_loaded 门控——先于 overlay 的老部署升级后 SW 表已有数据、跳过导入、1439 行永不生效；改为加性幂等的 overlay 每次 startup 无条件尝试（日志可观测）
- 涉及模块：backend/app/services/data_init

## 2026-09-03 - 市场数据面 Task 1 — 7 张数据表模型与迁移
- 新增市场数据面数据层：板块资金流快照 / 龙虎榜 / 北向资金 / 大宗交易 / 限售解禁 / 股票回购 / 公告快讯 7 个 ORM 模型（`app/models/market_data.py`，注册到 models `__init__`）+ 手写 Alembic 迁移 `9d4e7a2c8b1f`（down_revision `e6f7a8b9c0d1`），含唯一约束去重键与 10 个查询索引
- 涉及模块：backend/app/models, backend/app/migrations/versions

## 2026-09-03 - 市场数据面 Task 4 — GET /market/global-indices 全球指数卡片
- 新增 `market_data_service.get_global_index_cards(cache)`：东财 push2delay 实时快照（`_em_code` 对齐 secid→code，60s Redis 共享缓存 `market:global-indices`）+ `index_dailies` 近 30 日 spark + 实时缺失时 EOD 兜底（全球指数行 pre_close=NULL，用相邻收盘逐日差值算涨跌额/幅）→ `GlobalIndexCardOut`（`app/schemas/market_data.py`）经 `app/api/v1/market_data.py` 挂到 `/api/v1/market/global-indices`（子路由不带 prefix、include 时挂 `/market`，与 market.router 共存）；活体验证 9 卡全 realtime、spark=30、二次请求命中缓存
- 涉及模块：backend/app/services/market_data_service, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/api/v1/__init__

## 2026-09-03 - 市场数据面 Task 5 — 板块资金流盘中采集与读取端点
- 新增 `market_data_repo`（`upsert_sector_moneyflow` 幂等 upsert 按约束 `uq_sector_moneyflow_dim_code_date`、显式 `updated_at: func.now()` 因 pg on_conflict 不走 ORM onupdate；`list_sector_moneyflow` 按 main_net_inflow DESC NULLS LAST）；`market_data_service.ingest_sector_moneyflow` 拉 industry/concept 两维当日快照（`dict[str,int]` 行数契约），`get_sector_moneyflow` 走 Redis `market:sector-moneyflow:{dim}` TTL 60s；`SectorMoneyflowOut` + `GET /api/v1/market/sector-moneyflow?dimension=&limit=`；scheduler 新增 `sector_moneyflow_poll`（mon-fri 9-15 每 5 分钟，job 内 `_is_workday`/`_in_trading_hours` 守卫）+ CLI `_main` 分支 `sector_moneyflow`；东财 clist 端点 base 由 push2 切至 push2delay（push2 当日开始拒连，delay 域同构可用）
- 涉及模块：backend/app/repositories/market_data_repo, backend/app/services/market_data_service, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/scheduler/jobs, backend/app/scheduler/runner, backend/app/core/providers/eastmoney_client

## 2026-09-03 - 市场数据面 Task 6 — 北向资金盘后净流入序列
- 新增 `market_data_repo.upsert_northbound`（幂等 upsert 按约束 `uq_northbound_date`，只刷 `net_amount`、source 首写不变）与 `list_northbound`（近 N 日按 trade_date 升序）；`market_data_service._map_hsgt_rows` 将 moneyflow_hsgt 全字符串列归一为 float|None（万元，NaN/空串兜底），`ingest_northbound` 近 30 日窗口采集，`get_northbound_series` 走 Redis `market:northbound:{days}` TTL 300s；`NorthboundPointOut` + `GET /api/v1/market/northbound?days=`；scheduler 新增 `northbound_daily`（mon-fri 16:10 盘后）+ CLI `_main` 分支 `northbound`；活体验证 23 个交易日入库、二次 ingest 幂等、端点升序返回且命中缓存
- 涉及模块：backend/app/repositories/market_data_repo, backend/app/services/market_data_service, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/scheduler/jobs, backend/app/scheduler/runner

## 2026-09-03 - 市场数据面 Task 7 — 龙虎榜 + 大宗交易采集与读取
- 新增 `market_data_repo.upsert_dragon_tiger`（按约束 `uq_dragon_tiger_date_code_reason` DO UPDATE 全部行情列，同批 (date,code,reason) 重复先去重）、`upsert_block_trades`（按约束 `uq_block_trades_dedupe` DO NOTHING——行无稳定业务键）、`max_dragon_tiger_date`/`max_block_trade_date`（读取端点 date 缺省值）、`list_dragon_tiger`（net_amount DESC NULLS LAST）、`list_block_trades`（amount DESC NULLS LAST + `split_part(ts_code,'.',1)` LEFT JOIN stocks 取股票名、symbol 过滤）；`market_data_service` 新增 `_map_top_list_rows`（reason 超 160 字符映射层截断防 DB 报错）、`_map_block_trade_rows`、`_dedupe_block_trade_rows`（同批去重键保留末次，ON CONFLICT 不处理语句内自冲突）、`ingest_dragon_tiger`/`ingest_block_trades`（None=今日上海日期）与 `get_dragon_tiger`/`get_block_trades`（Redis `market:dragon-tiger:{date}` / `market:block-trades:{date}:{symbol}` TTL 300s，缓存全量 100 行按请求切片）；`DragonTigerOut`/`BlockTradeOut` + `GET /api/v1/market/dragon-tiger?date=&limit=`、`GET /api/v1/market/block-trades?date=&symbol=&limit=`（非法 ISO date 返 400）；scheduler 新增 `dragon_tiger_daily`（mon-fri 18:00）、`block_trade_daily`（mon-fri 17:00）+ CLI 分支 `dragon_tiger [yyyymmdd]`、`block_trades [yyyymmdd]`；活体验证 20260902 龙虎榜 77 行/大宗 78 行入库、二次采集幂等（77/0）、两端点返回含 symbol/name 及正确单位（dragon 金额元，大宗 price 元/volume 万股/amount 万元）
- 涉及模块：backend/app/repositories/market_data_repo, backend/app/services/market_data_service, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/scheduler/jobs, backend/app/scheduler/runner

## 2026-09-03 - 市场数据面 Task 8 — 限售解禁 + 股票回购采集与读取
- 新增 `market_data_repo.upsert_share_floats`（约束 `uq_share_floats_dedupe` DO NOTHING；ann_date 可 NULL，Postgres 唯一约束不判重 NULL——NULL ann_date 行可能重复入库，属可接受偏差）、`list_share_floats`（float_date BETWEEN 窗口 DESC NULLS LAST + `split_part` LEFT JOIN stocks 取名、symbol 过滤）、`upsert_repurchases`（约束 `uq_stock_repurchases_dedupe` DO UPDATE end_date/exp_date/vol/amount/high_limit/low_limit——进度会修订，同批 (ann_date,ts_code,proc) 先 Python 去重）、`list_repurchases`（ann_date DESC）；`market_data_service` 新增 `_d_opt`（可空日期 NaN/None→None）、`_map_share_float_rows`/`_map_repurchase_rows`（float_share 万股、float_ratio %、vol 股、amount 元全程不换算；proc 映射层截断 String(16)）、`_dedupe_repurchase_rows`、`ingest_share_floats`/`ingest_repurchases`（近 N 日窗口 %Y%m%d，默认 7）与 `get_share_floats`/`get_repurchases`（缺省窗口：解禁 today-30d→today+90d 因解禁是未来事件必须含未来日期、回购 today-30d→today；Redis `market:share-floats:{start}:{end}:{symbol-or-all}` / `market:repurchases:...` TTL 300s，缓存全量 100 行按请求切片）；`ShareFloatOut`/`RepurchaseOut` + `GET /api/v1/market/share-floats?start=&end=&symbol=&limit=`、`GET /api/v1/market/repurchases?...`（非法 ISO 日期返 400）；scheduler 新增 `share_float_daily`（mon-fri 17:30）、`repurchase_daily`（mon-fri 17:40）+ CLI 分支 `share_floats [days]`、`repurchases [days]`；活体验证 解禁 469 行/回购 312 行入库、二次采集幂等（469/0、312/312 DO UPDATE 计 matched）、两端点返回 join name 与正确单位、symbol=002120 过滤与未来 float_date（解禁 09-04）默认窗口即含
- 涉及模块：backend/app/repositories/market_data_repo, backend/app/services/market_data_service, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/scheduler/jobs, backend/app/scheduler/runner

## 2026-09-03 - 市场数据面 Task 9 — 巨潮公告采集与快讯端点
- 新增 `CninfoClient`（cninfo hisAnnouncement/query 免 token 公告检索，追加进既有 `cninfo_client.py` 与 webapi 行情 `CnInfoClient` 共存；单例工厂命名 `get_announcement_client` 避开既有 `get_cninfo_client`）：财报/重大事项两类目分页拉取，`announcementTime` 毫秒转上海时区 naive wall-clock datetime（fromtimestamp tz=Asia/Shanghai 后去 tzinfo，避免容器 TZ 未设落 UTC 语义致时间 +8h 漂移）、标题剥 `<em>` 高亮标签、PDF 前缀 static.cninfo.com.cn、column=szse 覆盖沪深；`announcement_service.ingest_announcements` 近 3 日窗口两类目各拉一次 + announcement_id 内存去重 + repo `upsert_announcements`（`uq_announcements_cninfo_id` DO NOTHING）、`get_announcements`（Redis `market:announcements:{symbol|all}` TTL 300s，缓存整页 100 行按请求切片防 limit 进缓存键，announce_time ISO str）；`AnnouncementOut` + `GET /api/v1/market/announcements?symbol=&limit=`；scheduler 新增 `announcements_poll`（每日 8-22 点每 10 分钟——公告含非交易日发布，无 workday/交易时段守卫）+ CLI 分支 `announcements [days]`；活体验证 report 6/event 150 行入库、二次采集幂等（6/0、150/0）、端点标题无 em 标签、pdf_url HEAD 200
- 涉及模块：backend/app/core/providers/cninfo_client, backend/app/services/announcement_service, backend/app/repositories/market_data_repo, backend/app/schemas/market_data, backend/app/api/v1/market_data, backend/app/scheduler/jobs, backend/app/scheduler/runner

## 2026-09-03 - 市场数据面 Task 10 — market_data.fetch 队列 Worker + 手动触发端点
- 新增 `MarketDataWorker`（`app/workers/market_data_worker.py`，queue_key `market_data.fetch`）：按 payload `type` 分发 9 类采集（global_index_daily/backfill_global_index/sector_moneyflow/northbound/dragon_tiger/block_trades/share_floats/repurchases/announcements），参数顶层与嵌套 params 均可（`{**payload, **params}` 合并），`_run` 内开 session 且成功路径 commit、未知 type 返 `{"status":"failed"}` 不抛异常；`QUEUES` 注册 `stock_bot.market_data.fetch`、runner 实例化为第 6 个 worker；`POST /api/v1/tasks/fetch-market-data`（`MarketDataFetchRequest`：`type` Literal 9 选 1 + `params: dict|None`）→ `task_service.trigger_fetch_market_data`（复用 `_dispatch_task`）→ 202 `TaskOut`；单测 monkeypatch service 函数 + 模块级 `async_session_factory`（NullSession 假上下文）2 例通过；活体验证 northbound 202→worker `upserted=22`→status completed、非法 type 422（Literal 网关层拦截）、worker 容器无崩溃
- 涉及模块：backend/app/core/mq, backend/app/workers/market_data_worker, backend/app/workers/runner, backend/app/schemas/task, backend/app/services/task_service, backend/app/api/v1/tasks
- Review fix 1：`process` 去掉 blanket try/except——service 异常向上传播由 BaseWorker 标记任务 failed（数据源故障不再伪装成 completed），未知 type 在触库前经 `_KNOWN_TYPES`（schema Literal `get_args` 单一事实源）返回 failed dict，`_run` 的 else 降级为防御性分支；新增异常传播单测（共 3 例）+ process docstring 补 9 类 payload keys

## 2026-09-03 - 市场数据面 Task 13 — 板块主力资金流卡 + 北向折线卡替换近似实现
- 新增 `features/market/components/format.ts`（fmtYi/fmtSignedYi/fmtWanGu/fmtYiGu/fmtNorthYi 五个单位换算格式化器，null/undefined 统一返 "—"）；新增 `SectorMoneyflowCard`（行业/概念 Segmented 切换，`["sector-moneyflow", dimension]` staleTime+refetchInterval 双 60s 盘中轮询，TOP10 主力净流入横向柱图正红负绿，tooltip 带板块涨跌幅/主力净占比，非交易时段无数据展示"暂无资金流数据"空态）；新增 `NorthboundCard`（`["northbound", 30]` staleTime 5min，30 日净流入折线+零轴虚线，当日/近30日累计红绿着色，万元→亿换算）；市场页 Row2 由 CapitalFlowChart+HotSectors 换为 SectorMoneyflowCard+NorthboundCard，Row3 HotSectors 全宽（span=24）；删除近似实现 `CapitalFlowChart.tsx`（grep 确认仅 barrel+市场页引用）；`npm run build` 通过，活栈 `/market` 200，资金流卡 60s 自动刷新经 Network 请求复现（northbound 仅首载一次，符合无 refetchInterval 语义）
- 涉及模块：frontend/features/market/components, frontend/pages/market

## 2026-09-03 - 市场数据面 Task 14 — 数据面 Tab 区块（龙虎榜/大宗/解禁/回购/公告快讯）
- 新增 `MarketDataBoard`（antd Tabs 懒挂载 5 pane，首激活才发 useQuery）；`dataFace/` 五组件：`DragonTigerTable`（涨跌幅/净买额 ±红绿+金额加粗，行点击跳个股）、`BlockTradeTable`（成交额万元→亿新增 `fmtWanYi`，买/卖营业部 ellipsis）、`ShareFloatTable`（解禁数量亿股、占总股本%、类型）、`RepurchaseTable`（进度 Tag 实施=blue/完成=green/其他=default、金额元→亿、数量股→万股内联换算）、`AnnouncementFeed`（List 时间+分类 Tag+证简称+PDF 新窗链接，30 条滚动）；统一表格规范 size=small/pagination=false/scroll.y=320/空态文案；顺手清理 SectorMoneyflowCard 遗留的死导入 `fmtYi`；活数据校准两处 rowKey 防撞键（解禁同股多持有人追加 holderName、大宗同股同价同买方追加 volume）；`npm run build` 通过，活栈 `/market` 五 Tab 实数据切换验证通过（大宗 9034.54万→0.90亿换算核对）
- 涉及模块：frontend/features/market/components, frontend/pages/market

## 2026-09-03 - 市场数据面 Task 15 — 个股详情页相关数据卡（公告/龙虎榜/大宗/解禁/回购）
- 新增 `features/stock-detail/components/RelatedEvents.tsx`（Card title="相关数据" + Segmented 五视图：公告默认 Tab（时间+标题 PDF 新窗链接）、龙虎榜（fetchDragonTiger(50) 全市场最新日客户端 filter 本股，净买额 ±红绿）、大宗（fmtWanGu/fmtWanYi）、解禁（fmtYiGu/占比%）、回购（进度+fmtYi 金额）；每 Tab 独立 queryKey 且 enabled 门控首激活才请求，staleTime 5min，统一 size=small/pagination=false/空态文案，footer 注明"龙虎榜为全市场最新日筛选本股"数据语义）；接线 `pages/stock-detail/index.tsx`（K线/基础信息 Row 之后、末尾免责声明 Divider 之前插入全宽 Row，symbol 取 `stock.symbol` 规避 useParams 可空类型）；删除 brief 末尾防未用报错的脚手架残留（hidden span + useNavigate import）；`npm run build` 通过，活栈 `/stock/600519` 公告默认 Tab + 五 Tab 空态切换验证、`/stock/002536` 龙虎榜实数据（+4.59亿 红色）验证，零 console 错误
- 涉及模块：frontend/features/stock-detail/components, frontend/pages/stock-detail
- Review fix 1：RelatedEvents 大宗/解禁 rowKey 对齐 Task 14 dataFace 实证修复——大宗 `${tradeDate}-${buyer}-${price}` 追加 `-volume`（同股同日同价同买方多笔撞键）、解禁 `${floatDate}-${shareType}` 追加 `-holderName`（同股同日同类型多持有人撞键），照抄 brief 键前先核对既有同数据组件的 rowKey 教训

## 2026-09-03 - 市场数据面收尾 — e2e 用例与文档（Task 16，P1-P10 完成）
- 计划级总结——2026-09-03 市场数据面：全球市场指数区块（亚洲/美洲 Tab、徽章卡+30日sparkline）、板块主力资金流盘中轮询卡、北向资金折线卡、数据面五类榜单（龙虎榜/大宗/解禁/回购/公告快讯，TuShare+东财+巨潮）、个股相关数据卡；新增 7 表与 `market_data.fetch` 队列。
- 新增 `frontend/e2e/marketDataFace.spec.ts` 5 用例（全球市场 Tab 与指数卡/全球指数详情/板块资金流行业概念切换/数据面 Tab 表格/个股相关数据卡），数据缺失处按惯例 test.skip 守卫；全量 e2e 22 通过；`docs/design/data-source.md` 补「市场数据面数据源」小节（实测端点/字段/单位/调度限频）。
- 涉及模块：frontend/e2e, docs/design/data-source.md, docs/references/best-practices.md

## 2026-09-03 - 市场数据面评审修复
- 修复 RepurchaseTable rowKey 冲突（同日多进度回购行：补 proc 维度），并移除 market.ts 中无消费方的 `fetchSseLatestSnapshots`。
- 2026-09-04 市场数据面缺陷修复：调度守卫时区改用 Asia/Shanghai（容器 UTC 下盘中资金流轮询与 SSE 快照从未真正触发）；龙虎榜/大宗采集改补漏模式（自表内最新交易日起逐日拉齐缺失交易日，当日未发布自动重试）并补回 09-03 缺口；巨潮公告分页上限 5→10 页（重大事项类 3 天窗口实测 189 条，5 页截断 39 条）。
- 2026-09-04 资金流对齐东财数据中心板块资金页：新增地域维度（fs=m:90+t:1）与主力净流入最大股（f128/f140/f136），全链路入库到前端悬停展示；龙虎榜/大宗补漏模式顺带在空表上自动回补 9 个交易日历史。
- 2026-09-04 新增大盘资金流卡（沪深两市合成口径）：今日主力/超大/大/中/小四档净额实时 + 近 30 日主力净流入红绿柱；新表 market_moneyflow_daily，16:20 盘后增量 + 120 日历史回补，东财 fflow/daykline 恒等式入库校验。
- 2026-09-04 市场页重设计（对齐主流）：涨跌分布加涨跌平衡条与成交额头部、柱色改数值驱动强度渐变（移除桶名字符串匹配）；板块热力图改连续色阶（Finviz 式梯度）+ 浅色块深字 + 点击下钻行业页 + 领涨股 tooltip；大盘资金流接口补实时成交额（f6）。
## 2026-09-09 - P1 财务数据底座 + 估值分位（行业投研工作台前置能力）
- **背景**：按 `plans/industry-research-workbench.md` P1（财务与估值底座），当前系统只有日频行情/`daily_basic`，无三大报表与财务指标
- **新增（后端）**：
  - 六张财务表（`financial_raw_records` / `financial_report_versions` / `income_statement_facts` / `balance_sheet_facts` / `cash_flow_statement_facts` / `financial_metrics`），报告版本带 end_date/report_type/comp_type/ann_date/source/update_flag/quality_status，派生指标带 calc_method+quality；迁移 `b1f2c3d4e5a6`
  - TuShare Provider 新增 `fetch_income` / `fetch_balance_sheet` / `fetch_cash_flow` / `fetch_financial_indicator`
  - `FinancialIngestService`（拉取→raw JSONL+DB→报告版本→facts→派生指标），`FinancialWorker` + `financial.fetch` 队列 + `POST /tasks/fetch-financial`
  - 财务 API（`financial-summary` / `financial-statements` / `financial-metrics/history`）、估值历史分位 API（`valuation-history`，基于 daily_basic，按 1y/3y/5y 正样本计算分位）
- **派生指标口径**：折让比/费用率/负债率/杜邦拆解为 `calculated_from_statement`（derived），ROE/毛利率/同比等优先取 TuShare `fina_indicator` 的 `reported_by_provider` 值，缺失显示空而非 0；负值/缺失从估值分位样本中排除
- **前端**：个股详情页新增"估值/财务"Tab（指标卡+ECharts 趋势+三张报表表+估值分位图），数据来源/报告期/质量徽章展示（由子 Agent 并行实现）
- **注意**：P0 的 outbox 任务投递事务、跨交易所 symbol-only enriched 修复等仍未纳入本次切片；全市场财报回补需先按 `financial-data-dictionary` 做数据源 POC
- 涉及模块：backend/models, backend/migrations, backend/core/providers/tushare_client, backend/services(financial_ingest/financial_service), backend/repositories/financial_repo, backend/api/v1(financials/tasks), backend/workers, backend/core/mq, frontend/pages/stock-detail, frontend/features/stock-detail, frontend/shared/api

## 2026-09-08 - 全市场财务回填 + 财务表查询索引优化
- 财务数据改为"自动回填"：新增 `financial_backfill` service 逐批幂等回填缺失财报的标的（共享 FinancialWorker 同源 ingest），scheduler 注册 `financial_backfill` cron（工作日 7-8/15-23 点每 20 分钟），并新增 `python -m app.scheduler.backfill` 一次性全量入口
- 新增 financial_metrics 两个索引：覆盖索引 `(stock_id, metric_key, report_version_id)` 加速个股财务指标时序读取与排序，`(report_version_id)` 外键索引加速 join 与级联删除；实测个股财务时序查询 ~6ms
- 涉及模块：backend(scheduler/services/repositories/models migrations), 全市场约 5300 只标的逐步补齐

## 2026-09-08 - 后端 CI 债务清理（并入财务 PR）
- **根因**：CI 的 `uv run` 未带 `--extra dev`，导致 ruff/mypy/pytest 从未真正安装 → 后端 Lint/TypeCheck 形同虚设，存量 92 文件格式分叉、mypy 115 错累计到 main
- **修复**：
  - ci.yml 三个后端 job 的依赖安装统一加 `--extra dev`；Test job 的 alembic 步骤补 `DATABASE_URL` 指向 `stock_bot_test`；pytest 改 `-m "not e2e"`
  - 全仓 `ruff format` 对齐（141 文件）+ 全部 115 个 mypy 错误清零（历史欠账+财务新代码）
  - 修复 2 个真 bug：`task_repo` 缺 `func` import（count_tasks 运行时 NameError）、`financial_ingest` 缺 FinancialReportVersion import
  - `test_health`/`test_stocks` 标记为 e2e（依赖真实运行 API），CI 只跑非 e2e（本地 125 passed）
- 涉及模块：backend(全仓 lint/type/test), 顶层 CI 配置, backend/services(task/financial_ingest), backend/repositories/task_repo, backend/tests

## 2026-09-09 - feat/market-data-face PR CI 修复（合并 main 落债务清理 + Alembic merge 迁移）
- **根因**：PR #1 基于旧 main，CI 跑的是坏管道（dev group 未装→Lint/TypeCheck spawn 失败；alembic 无 DATABASE_URL）；债务清理此前锁在 PR #2，未落 main
- **修复**：先合并 PR #2 到 main（落 CI 修复+全仓格式/mypy 清理+.env.example），再把 main merge 进 PR #1（解决 10 处冲突，双侧逻辑并集）；新增 Alembic merge 迁移 49741053b341 合并财务/市场数据两条链；ruff format 对齐 PR#1 自有 11 文件
- 本地验证 ruff/mypy 全绿、pytest 155 passed；PR #1 六项 CI 全绿
- 涉及模块：backend(migrations/models/scheduler/services/providers/repos), frontend(stock-detail), docs

## 2026-09-09 - 认证微服务、Gateway 与数据归属架构设计 (Stage 0)
- 完成企业级认证授权与多租户数据隔离体系的 Stage 0 顶层设计：明确 Gateway BFF 与 auth-service 目标拓扑、HttpOnly Session Cookie 与 CSRF 双重防御机制、下游 API 零信任短时 Principal Assertion (RS256) 签名与 JWKS 验签机制、统一 JSON 错误契约 (code/message/details/trace_id)、独立 auth_* 数据模型 (Argon2id / RTR Token Family)、业务模型多用户改造方案 (stock_custom_sw_tags 增加 user_id、自选股服务端化、tasks 增加 requested_by、Redis Key 用户隔离) 与 Stage 0-6 分阶段实施计划。
- 涉及模块：docs/architecture/authentication-and-gateway, docs/architecture/auth-data-model, plans/2026-09-09-auth-gateway-implementation

## 2026-09-09 - auth-service 独立微服务开发与凭证/JWKS体系 (Stage 1)
- 实现独立的 `auth-service` 微服务工程体系，构建基于 Argon2id 的安全密码哈希与防暴力锁定、Redis 高性能滑动会话与 DB 持久化同步、CSRF 双重校验、RSA 短时 Principal Assertion JWT 签名与 RFC 7517 JWKS 公钥分发端点，完善了注册、登录、登出、会话查询与多端下线、网关会话内省等接口，提供全链路 100% 通过的离线单元与集成测试套件。
- 涉及模块：auth-service(core/models/services/schemas/api/migrations/tests), plans, docs

## 2026-09-09 - 引入 Traefik API Gateway 并完成容器拓扑与端口收敛 (Stage 3)
- 引入 Traefik v3 API Gateway 作为整站统一流量入口，配置 JSON 结构化日志、Prometheus 监控指标与动态中间件（Security Headers、Rate Limit、Compression）；重构根 `docker-compose.yml` 容器编排体系，新增 `auth-db`、`migrate-auth` 与 `auth-service` 微服务，收敛下线 `api:8000` 与 `frontend:3000` 的宿主机端口暴露，实现基于 Traefik labels 的动态路由分发与 tasks 任务触发写限流保护，并提供 `.env.docker.example` 容器配置模板。
- 涉及模块：gateway, docker-compose.yml, auth-service, backend/docker-compose.yml, .env.docker.example, plans, docs

## 2026-09-09 - Stock API 零信任身份断言验签与权限矩阵保护 (Stage 4)
- 实现基于 JWKS 异步加载与本地公钥缓存（单飞刷新）的 Principal Assertion (RS256) 验签器与 RBAC 权限依赖注入器（CurrentUserDep / OptionalUserDep / require_roles / require_permissions）；全面保护 tasks 触发与取消、行业指标 batch 导入、SSE 历史回补等敏感接口；客户端未签名 X-User-* 伪造头一律严格丢弃；修复前端跨交易所 fallback 仅对 404 降级与股票详情页 error/not-found 状态分流；新增 9 项无 DB 依赖单元与集成测试。
- 涉及模块：backend/config, backend/core/auth, backend/api/deps, backend/api/v1/tasks, backend/api/v1/industries, backend/api/v1/market, frontend/shared/api, frontend/pages/stock-detail, backend/tests

## 2026-09-09 - 用户标签与自选股服务端化与多用户数据隔离 (Stage 5)
- 实现多用户业务数据隔离与自选股服务端化：ORM 模型（StockUserTag / UserWatchlist / UserWatchlistItem / Task）增加 `user_id` / `requested_by` 并完成 Alembic 迁移脚本 `5a1b2c3d4e5f`；新增 `watchlists` 路由、改造 `user-tags` 及全局标签端点接入 `CurrentUserDep` 与 `user:{user_id}:*` 缓存隔离；前端对接服务端自选股 API，升级 `useWatchlist` 状态机与标签组件按登录态分流读写，登出全量清理；编写 9 项纯单元与路由隔离测试，验证 100% 通过。
- 涉及模块：backend/models, backend/migrations, backend/repositories, backend/services, backend/schemas, backend/api/v1(stocks/tags/watchlists/tasks), frontend/shared/api(watchlist/userTags), frontend/features/watchlist, frontend/features/stock-detail, frontend/pages(watchlist/tags/tags-detail), backend/tests

## 2026-09-09 - CI/CD 自动化门禁扩展与端到端测试覆盖 (Stage 6)
- 扩展 `.github/workflows/ci.yml` 引入 `auth-service` 的 Lint、TypeCheck 与 Test 门禁 job，并更新 `docker-smoke` 支持在 Traefik 网关（端口 80）下验证前端、API 及 `/auth` 接口全链路连通性；扩展 `.github/workflows/cd.yml` 增加 `auth-service` 多架构镜像构建推送；新增前端 Playwright E2E 认证与多用户隔离测试套件（`auth.spec.ts` 与 `userIsolation.spec.ts`）；完成全系统各微服务本地全量验证与阶段性实施计划收官。
- 涉及模块：.github/workflows/ci.yml, .github/workflows/cd.yml, frontend/e2e, plans, docs




## 2026-09-09 - P0 评审修复：forward-auth 断言注入与 JWT 契约统一
- 修复 Traefik 未向 backend 注入身份断言导致登录后受保护 API 全部 401 的 P0 缺陷：新建 `forward-auth` sidecar（会话 Cookie 换取短时 Principal Assertion、CSRF 强制、匿名放行、fail-closed 503），新增 `strip-assertion` 防伪造中间件并接线 `api`/`api-tasks` 路由；统一 auth-service 与 backend 的 JWT iss/aud 默认契约（`stock-bot-auth` / `urn:stock-bot:api`）并在 compose 显式对齐，新增两端交叉契约测试与 CI 注册→登录→受保护接口 401/200 闭环 smoke。
- 涉及模块：forward-auth(新建), gateway/dynamic, docker-compose.yml, .env.docker.example, auth-service/config, backend/tests, auth-service/tests, .github/workflows/ci.yml, docs, plans

## 2026-09-09 - P1 安全加固：凭据收敛、CSRF 强制与生产安全配置
- 落实安全评审 P1 项：登录响应体不再返回 session_id/csrf_token（凭据仅经 Set-Cookie 下发）；移除 X-Session-Id Header 旁路（会话识别仅认 Cookie）；auth-service 对 register/login 强制匿名 double-submit CSRF、logout 升级为会话绑定校验（失败 403 AUTH_CSRF_FAILED）；/internal/* 新增 X-Internal-Token 共享密钥校验（非空即强制，401 拒绝）；JWT 密钥支持经环境变量持久化注入并在 APP_ENV=production 时对 COOKIE_SECURE=false 启动 fail-fast；前端类型同步、CI smoke 补注册 CSRF 步骤、架构文档 3.3/4.5/4.6 与计划文档更新。
- 涉及模块：auth-service(api/auth, api/internal, config, schemas, tests), frontend/shared/api/auth.ts, docker-compose.yml, .env.docker.example, .github/workflows/ci.yml, docs, plans

## 2026-09-09 - P1/P2 评审修复：基建暴露面收敛、会话绝对上限与审计脱敏
- 落实最后一批评审修复：RabbitMQ 替换 guest/guest 默认凭据并移除 5672/15672 宿主机映射（migrate/api/worker 的 RABBITMQ_URL 统一引用新变量）；Traefik dashboard 8080 端口不再映射宿主机；nginx 仅保留 /health 透传、不再公开后端 docs/redoc/openapi.json；auth-service CORS 默认列表移除 8000/8001 端口项；会话引入 7 天绝对过期上限（超限强制登出语义）；审计事件 payload 中明文 session_id 改为 SHA-256 哈希；X-Forwarded-For 仅在 trust_forwarded_for 开启时解析（默认不信任）；Vite 开发代理补全 /auth 与 /.well-known；补存量标签认领策略与暴力破解双层防护文档。
- 涉及模块：docker-compose.yml, .env.docker.example, backend/.env.example, frontend/vite.config.ts, frontend/nginx.conf, auth-service(config, api/auth, services, tests), docs/architecture, plans

## 2026-09-09 - 本地 Docker Compose 实跑验证（P7 认证闭环 E2E）
- **实跑结果**：全栈经 Traefik Gateway 完成认证闭环实测 10 项场景全绿——公开路由 200、注册/登录 CSRF double-submit 正常、登录响应体无凭据、带 Cookie 访问受保护接口 200（forward-auth 断言注入 + backend JWKS 验签全链打通）、匿名 401、写请求无 CSRF 403、自选股写入正确归属 user_id、api:8000 与 frontend:3000 直连被阻断
- **修复一**：Traefik ≤3.3 与 Docker Engine 29（最低 API 1.44）不兼容导致 Docker provider 协商 v1.24 被拒、路由无法加载——镜像钉至 v3.6.2 并注释勿降级
- **修复二**：forward-auth 生产路径 lifespan 在 state 未挂 http 属性时访问即崩溃（测试注入路径掩盖）——getattr 兜底修复并新增回归测试
- 涉及模块：docker-compose(gateway 镜像版本), forward-auth(app lifespan + tests), plans(实跑记录)

## 2026-09-09 - 前端空数据库白屏修复 + 全局 ErrorBoundary
- **问题**：全新部署（空库）下打开 /market 白屏——ECharts treemap 对内部虚拟节点执行 label 渲染时 `changePercent` 为 undefined，SectorHeatmap formatter 抛 TypeError 导致 React 18 卸载整棵树；且浏览器缓存旧 index.html 使修复不可见
- **修复**：SectorHeatmap label/tooltip formatter 与两张资金流卡片 tooltip 补空值防御；新增全局 `ErrorBoundary`（路由级兜底，任何子树渲染异常降级为错误卡片而非白屏）；nginx SPA 入口增加 `Cache-Control: no-cache`（assets 仍长缓存，入口每次回源，杜绝发版后浏览器跑旧 bundle）
- 涉及模块：frontend/features/market/components(SectorHeatmap/MarketMoneyflowCard/SectorMoneyflowCard), frontend/shared/ui(ErrorBoundary 新增), frontend/App, frontend/nginx.conf

## 2026-09-10 - TradingView 风格明暗双主题基础设施（Stage A）
- 落实 `docs/design/landing-market-theme.md` §1 配色契约：`theme.ts` 重构为 `buildAntdTheme(mode)` 工厂（dark 走 darkAlgorithm）+ `THEME_COLORS` 双模式色板（up/down 红涨绿跌随主题切换）；新建 `app/styles/theme.css` 双轨 CSS 变量（`:root[data-theme]` + 首帧浅色兜底）；新建 `ThemeContext/ThemeProvider`（localStorage `stockbot-theme` > `prefers-color-scheme`，写 `data-theme` 持久化）；新增 `ThemeToggle` 挂 MainLayout Header；MainLayout/SearchBar/UserMenu 壳层硬编码色全部换 CSS 变量；`shared/ui/EChart` 封装内深合并注入 axisLabel/legend/splitLine/textStyle 主题色（调用方显式设置恒优先）；ChangeText/KlineChart/klineOption 全局件改经 `useTheme().colors` 取具体 hex。业务卡片内部细节留给 Stage C。
- 涉及模块：frontend/app(theme 新工厂/theme-context 新增/styles 新增/layouts/MainLayout), frontend/shared/ui(EChart/ChangeText/ThemeToggle 新增/kline), frontend/features/search, frontend/features/auth(UserMenu), frontend/main.tsx, frontend/App

## 2026-09-10 - StockBot 品牌宣传页 Landing（Stage B）
- 落实 `docs/design/landing-market-theme.md` §0-§5：`/` 从重定向 /market 改为公开 lazy Landing 页（独立布局不套 MainLayout，已登录停留此页）；10 区块全量落地——透明吸顶导航（滚动 >24px 加底色+边框，锚点 功能/数据/行业，登录态 CTA）、Hero（「把一个行业，研究透。」+ 信任行 + 纯 CSS accent 渐变装饰）、实时脉搏卡（/market/indices 8 ticker + /market/distribution 涨跌摘要，60s 轮询，失败静默降级占位）、价值三卡、深色产品展示区（纯 CSS+DOM 绘制周期相位条/来源徽章/信号卡，明暗主题恒定）、§5 十行数据覆盖矩阵（统计带 + 免费徽章）、申万行业网格（按个股数 accent 透明度阶梯，失败占位文案）、账号能力三档横条、底部 CTA、页脚（免责声明 + GitHub）。CTA 登录态感知：已登录「进入工作台」→ /market，未登录「免费开始」→ /login（isAuthReady 前隐藏文字防闪现）；颜色全部走 CSS 变量或 useTheme().colors，不引入新依赖。
- 涉及模块：frontend/app/router, frontend/pages/landing(新增 index/landing.css/useLandingCta/sections×11)

## 2026-09-10 - 市场页 TradingView 化重构与全量主题适配（Stage C）
- 落实 `docs/design/landing-market-theme.md` §4/§5：`/market` 重构为四分类 Tab（`data-testid="market-tabs"`：指数总览=全球指数卡+A股核心指数卡 / A股全景=涨跌分布+板块热力图+申万行业网格+热门板块+行业分类 / 资金流向=板块+大盘+北向三卡 / 数据面=龙虎榜等 Tab 表现状迁移），页脚上方新增数据版图精简矩阵；新增 `CoreIndexCards`（六核心指数复用 `GlobalIndexCardView`，与 GlobalMarketBoard 共享 ["global-indices"] 查询缓存）；`GlobalIndexCardView` 按 TV ticker 行规范重排（名称左/价格右对齐/涨跌幅色块带 +/- 号）并接入 useTheme；市场页卡片规范（--bg-panel 底、1px --border、8px 圆角）经 `pages/market/market.css` 落地。
- Stage B 的 DataCoverage 矩阵与 IndustryGrid 提取为共享组件 `features/market/components/DataCoverageMatrix` + `SwIndustryGrid`（CSS co-locate 随组件走，landing.css 移除已迁移规则），landing 两 section 改为薄包装，市场页直接复用。
- Stage A 主题债全量清偿：features/market、features/industry-research、features/stock-detail 全部静态 `COLORS.*` 引用（15 处）与图表 `backgroundColor:"#fff"`/splitLine `#f0f0f0` 硬编码改经 `useTheme().colors` 或 CSS 变量注入（纯函数 buildOption 改收 colors 参数）；市场页五张图从裸 ReactECharts 迁至 `shared/ui/EChart` 封装（默认注入 backgroundColor transparent + 轴/分隔线主题色，新增 onEvents 透传支撑热力图点击）；SectorHeatmap/DistributionChart 渐变端点色改由主题色推导（Dark class 阈值 0.8 保留，近中性块文字用 textPrimary/textSecondary 随主题成立）；e2e marketDataFace 板块资金流/数据面两用例补顶层 Tab 切换。deprecated `COLORS` 导出保留（research-workbench 存量引用另行迁移）。
- 涉及模块：frontend/pages/market(重构+market.css 新增), frontend/pages/landing(sections 两处薄包装+landing.css 瘦身), frontend/features/market(components×10+新增 CoreIndexCards/SwIndustryGrid/DataCoverageMatrix), frontend/features/industry-research(components×5), frontend/features/stock-detail(RelatedEvents), frontend/shared/ui(EChart), frontend/e2e(marketDataFace)

## 2026-09-10 - 宣传页/TV 风格市场页/暗色模式 E2E 自测收尾
- **E2E 新增与修复**：新增 landing.spec.ts（8 用例：公开路由/登录态 CTA 互换/脉搏卡/数据矩阵/行业网格）与 darkmode.spec.ts（3 用例：切换/持久化/暗色无白底）；修复 auth.spec 两处 strict-mode 脆弱选择器（注册表单子串双匹配、Modal 双标题）
- **E2E 环境**：vite dev proxy 改经 Gateway(:80)（端口收敛后 8000/8001 不可达）、dev server 显式绑 127.0.0.1（默认 [::1] 致 Chromium 拒连）
- **全量回归**：36 用例 33 过；余 3 项（research×2/userIsolation×1）为存量数据态依赖（industry metrics 空、固定用户名重复注册非幂等），与本分支无关
- **调试沉淀**：Playwright addInitScript 与 expect 断言器存在状态翻转交互，清 storage 场景应改用「加载后清理+reload」；Mock 数据字段名必须对齐前端类型（MarketIndex 用 value/changePercent/tsCode）
- 涉及模块：frontend/e2e(landing/darkmode 新增, auth 修复), frontend/vite.config

## 2026-09-11 - 宣传页市场脉搏与市场页数据同源化（修复滞后性）
- **问题**：用户实测 landing 首页"实时市场脉搏"与 /market 页指数对不上且滞后——landing 走旧接口 `/api/v1/market/indices`（`market_service.list_market_indices` 读 index_dailies 盘后日线，asof 硬编码 15:00），market 页走 `/api/v1/market/global-indices`（东财实时快照 + 60s 缓存），两条链路口径不同（EOD vs realtime）；landing 文案"每 60 秒自动刷新"轮询的却是盘后库表
- **修复**：
  - 前端 `MarketPulse.tsx` 改用与市场页同源的 `fetchGlobalIndices()`（global-indices 实时链路），Ticker 适配 GlobalIndexCard 字段（price/pctChange），文案改"东财实时快照，与市场页同源"
  - 后端 `GLOBAL_INDICES` 补 5 只 A 股宽基：沪深300/中证500/科创50/上证50/北证50（东财 secid 1.000300/1.000905/1.000688/1.000016/0.899050 全部实测返回实时行情），landing 8 格与市场页 A 股指数完全对齐
  - E2E mock 同步：`**/api/v1/market/indices` → `**/api/v1/market/global-indices`（snake_case 载荷）
  - 单测同步：registry shape / cards 合并断言 9 → 14，新增 A 股 em_secid 集合锁定
- **验证**：tsc ✓、ruff ✓、mapping 18 测试 ✓、gateway 实测 14 条（8 CN 全 realtime）
- 涉及模块：frontend/src/pages/landing/sections/MarketPulse.tsx, frontend/e2e/landing.spec.ts, backend/app/services/market_data_service.py, backend/tests/test_market_data_mapping.py

## 2026-09-11 - 申万 custom-tag overlay 导入修复（"其他"分类 1497→61 只回落）
- **问题**：用户实测宣传页行业网格出现"其他 1497 只"分类。诊断链：tree API 的"其他"= 未命中申万 L3 成分与自定义标签的股票兜底聚合（market_service.get_sw_industry_tree）→ 库里 stock_custom_sw_tags 为 0 行 → 2026-09-03 的 OTHER→SW merge（1439 行）在 P7 重建数据库后从未导入
- **根因**：`import_custom_tags_from_sql` 按 `;` 切分且把以 `--` 开头的块整块跳过——seed 文件是「9 行注释头 + 单条 1439 行 VALUES INSERT（行内无分号，仅末尾一个分号）」，切分后首块恰好 = 注释头+完整 INSERT，以 `--` 开头被整体丢弃，导入恒为 0 行（api 日志 "Imported custom-tag overlay: 0 rows" 安静存在了两天）；seed 文件本身格式正确（40ff00c 入库即无分号），问题纯在解析器
- **修复**：解析重构为 `_split_sql_statements`（先剥离注释行再按分号切分，纯函数可单测）+ 修正 seed 头行分号 + 新增 tests/test_sw_seed_import.py 3 用例锁定畸形输入行为
- **验证**：api 重建后 data_init 自动导入 1439 行；清 `market:sw-tree` 缓存后 tree API "其他"= 61 只（残余为确无申万映射股票，兜底保留），5560 只全可见
- 涉及模块：backend/app/services/sw_industry_service.py, backend/data/sw_custom_tags_seed.sql, backend/tests/test_sw_seed_import.py(新增)

## 2026-09-11 - 代码评审 P1-P4 优化（缺失值语义 / 解析器边界 / 快照键碰撞 / 日志语义）
- **P1 缺失涨跌幅误显示 0.00%**（用户可见）：MarketPulse 的 Ticker 对 `pctChange=null` 用 `?? 0` 兜底，渲染成"0.00% + 中性色"会被读成平盘（EOD 兜底且 spark 不足时真实可达）。改为与价格同口径显示 `--`；先写 E2E 复现（Playwright 明确报 `unexpected value "0.00%"`）再修复，landing.spec 7/7 通过
- **P2 解析器边界**：`_split_sql_statements` 原先只处理注释独占行，行尾注释（`INSERT ...; -- 说明`）会与下条语句粘连并被当语句执行（报语法错）。改为对每个分号块逐行剔除注释（覆盖两种形态），docstring 显式声明"不支持字符串字面量内分号"；单测 5 个（含红→绿复现）
- **P3 快照键跨市场碰撞**：`_em_code` 取数字段短码，沪/深同号段（1.000001 vs 0.000001）会静默错配——单测精确复现（`assert 222.0 == 111.0`，沪市指数拿到深市报价）。client 返回体新增完整 `secid`（f13 市场号 + f12 代码），service 改按 secid 索引并删除 `_em_code`
- **P4 导入日志语义含混**：`Imported ... N rows` 实为全表计数（排障时被误读为"本次导入量"）。改为 `table now has N rows (+M this run)`，实机验证输出 `1439 rows (+0 this run)`（幂等重放）
- **验证**：后端 ruff/mypy ✓、单测 180 通过 ✓、landing E2E 7/7 ✓、实机重建 api+frontend 后 global-indices 14 条全 realtime 无回归 ✓
- 涉及模块：frontend/src/pages/landing/sections/MarketPulse.tsx, frontend/e2e/landing.spec.ts, backend/app/services/sw_industry_service.py, backend/app/services/market_data_service.py, backend/app/core/providers/eastmoney_client.py, backend/tests/{test_sw_seed_import,test_market_data_mapping,test_eastmoney_client}.py
## 2026-09-09 - 浏览器登录会话安全研究
- 基于 RFC 9700、RFC 10017（OAuth 2.0 for Browser-Based Applications）、OpenID Connect Core 与 OWASP Session/CSRF Cheat Sheet，形成 stock_bot 的 BFF+HttpOnly 会话、刷新令牌轮换、CSRF 与 Cookie flags 建议。
- 涉及模块：安全架构、backend、frontend、Docker Compose、docs

- **根因**：根 README.md 长期与实现漂移（仍写 SQLModel / Tailwind+shadcn，实际已迁至 SQLAlchemy 2.0 async + Ant Design 5），且只字未提产品化方向"行业投研工作台"；Docker 部署细节散落且未链接到 build.md，双份 README（中/英）重复漂移
- **修复**：
  - 重写根 README.md 为中文唯一权威文档：技术栈/项目结构/API 路由全面对齐当前实现，补充 Docker 部署核心步骤（`cp .env.docker.example backend/.env`、填 TUSHARE_TOKEN、前端预构建说明）并链接 docs/build.md
  - 收敛双文档：删除 README_zh.md（无任何文件引用）
  - 补强根 AGENTS.md：新增"部署约定"章节（.env 生成、前端 runtime 预构建、交叉引用一致性规则），关键文档列入 build.md / ARCHITECTURE.md / 投研工作台计划
  - 校正部署文档与 docker-compose 不一致处：docs/build.md 服务数 7→9（补 scheduler / redis-init）、redis 端口 6379→6380、启动顺序加 scheduler；docs/ARCHITECTURE.md 服务表补 scheduler / redis-init
- 涉及模块：README.md, README_zh.md(删除), AGENTS.md, docs/build.md, docs/ARCHITECTURE.md

## 2026-09-09 - best-practices 重构（按主题归档 + 去重）
- 将 112 条扁平沉淀重组为 8 类主题分组并加目录：数据源与采集 / 数据库与性能 / Docker 与部署 / 前端 / 测试与E2E / 架构与分层 / 指标建模与规则引擎 / 工程流程与文档
- 合并确认重复条目：时区/交易日守卫（UTC vs Asia/Shanghai）两处并一、同表多频指标 freq 仲裁与冲突键两条并一、uv `--extra dev` 冗余提及清理；条目 112→97
- 保留 `best-practice.md` / `best-practices.md` 两文件合并历史说明；无有效经验丢失（已用关键锚点校验）
- 涉及模块：docs/references/best-practices.md

## 2026-09-09 - 完成前自检门禁（强档：约定 + 脚本 + pre-commit/CI 兜底）
- **问题**：best-practices 只是"被动检索"，agent 改完代码不会有机制逼它拿已知错误对照本次 diff；规范类错误有 CI 兜底，业务类（时区/source 优先级/N+1/dockerignore）无兜底
- **修复（四层强档）**：
  - AGENTS.md 新增「完成前自检门禁」章节：每次收尾强制 6 步（列改动 → 探测器对照 → 跑 self_review → 文档同步 → 反馈闭环 → 固定格式收尾结论）
  - best-practices.md 新增「自检探测器映射表」：8 分类 → 关键词，把"对照已知错误"变成可 grep 的机械操作
  - 新增 `scripts/self_review.sh`：快检（空白/冲突 + 改动文件 ruff + 文档同步告警）+ `--full`（追加 mypy/pytest/tsc）
  - 接入 `.pre-commit-config.yaml` 本地钩子（每次 commit 强制跑 self_review）
  - CI 新增 `docs-consistency` job（信息性不阻断）：PR/提交 diff 空白硬检 + 文档未 touch Changelog 告警
- 涉及模块：AGENTS, docs/references/best-practices, scripts/self_review.sh(新增), .pre-commit-config.yaml, .github/workflows/ci.yml

## 2026-09-09 - pre-commit 扩展：每次 commit 自动跑测试 + 前端类型检查
- **需求**：每次 commit 前自动跑 test / 格式 / benchmark
- **现状核实**：后端格式(ruff/ruff-format)+类型(mypy) 原本已在 pre-commit；非 e2e 测试 155 个可无 DB 独立运行(~2.4s)；**前端实际未配置 eslint/prettier**（package.json 无常量依赖亦无 eslint.config，CI 的 "Lint(frontend)" job 实为 `tsc --noEmit`）；仓库无 benchmark 基建
- **修复**：
  - pre-commit 新增 `backend-test` 钩子：任何 backend .py 变更即跑 `uv run pytest -m "not e2e" --no-cov`（e2e 仍需 DB，留在 CI）
  - pre-commit 新增 `frontend-typecheck` 钩子：任何 frontend ts/tsx 变更即跑 `npx tsc --noEmit`（与 CI 一致的既有前端静态校验）
- 涉及模块：.pre-commit-config.yaml, docs/Changelog.md

## 2026-09-09 - Benchmark Tier 1 落地（Phase A：纯计算域硬门禁）
- **需求**：按 [plans/2026-09-09-benchmark-tiers.md](../plans/2026-09-09-benchmark-tiers.md) 落地 CPU 基准硬门禁，让纯计算热点回归能被 PR 自动卡住
- **实现**：
  - 新增 `backend/tests/benchmarks/` 三个基准：规则引擎周期判定（`evaluate_pig_cycle` 批量 400 样本）、指标 rollup 与源/频率仲裁（`_rollup_monthly_rows` 730 行日频 / `_pick_latest` 冲突裁决）、行情归一化 mapper（`map_daily_rows` 5000 行）；全部无 DB 纯函数，固定 seed=20260909 + 尺寸常量即基线契约
  - `pytest-benchmark` 入 dev extra（5.3.0；**有意放 dev 而非独立 bench extra**：pytest 收集 tests/benchmarks/ 需要包在场，避免为一个小包拆两套 CI sync；文件内 `pytest.importorskip` 兜底）；pyproject 注册 `bench` marker，addopts 默认 `-m 'not e2e and not bench'`
  - marker 泄漏修正：pre-commit `backend-test`、CI `backend-test`、`self_review.sh --full` 三处 `-m "not e2e"` → `"not e2e and not bench"`
  - `scripts/bench.sh`（gate / `--save-baseline` / `--quick` 三模式，`--benchmark-warmup=on` 抗冷启动）+ `scripts/bench_compare.py`（按 median 相对退化判定，阈值默认 12%，新增基准无基线也红）；`benchmarks/baseline.json` 入库、`last.json` 进 .gitignore
  - CI 新增 `bench-cpu` 硬门禁 job（uv sync --frozen --extra dev → bench.sh，产物 artifact 7 天）；AGENTS/AGENTS(backend) 常用命令与自检门禁第 3 步引用基准脚本
- **验证**：4 基准 ~5s 跑完；同机三次 gate 全绿（波动 -1.2%~+8.1% < 12%）；临时收紧阈值验证门禁可红（exit 1 + 明细）；大样本基准（13ms/次）抖动 7.5% → 缩到 5000 行后 1.2%
- 涉及模块：backend/tests/benchmarks(新增), backend/pyproject.toml, backend/uv.lock, scripts/bench.sh(新增), scripts/bench_compare.py(新增), benchmarks/baseline.json(新增), .github/workflows/ci.yml, .pre-commit-config.yaml, scripts/self_review.sh, AGENTS.md, backend/AGENTS.md, .gitignore

## 2026-09-09 - bench-cpu CI 门禁修复：同 runner A/B 基线（跨机器基线不可比）
- **问题**：PR#4 的 bench-cpu 连续两次失败——第一次 exit 126（Windows 创建的 bench_compare.py 无执行位，Linux 直接执行 Permission denied）；修复执行方式后第二次仍红，4 项基准全部"退化"37-49%，根因是 **wall-clock 基线绑定硬件**：本机 Windows 跑的 baseline.json 对 CI runner 无参照意义，跨机器相对对比必假红
- **修复**：
  - `scripts/bench_compare.py` 加 `--allow-added`（新增基准不判失败——A/B 的 base 侧本就没有新基准）
  - `scripts/bench.sh`：`--allow-added` 透传；`--benchmark-json` 接 `$BENCH_CURRENT`（原硬编码漏改）；base commit 无 `tests/benchmarks/` 时短路写空产物；产物路径支持 `BENCH_BASELINE/BENCH_CURRENT` 环境变量覆盖（CI 指到 `$RUNNER_TEMP`，避免污染 tracked 的 baseline.json 导致 `git checkout` 拒切）
  - CI `bench-cpu` 重写为**同 runner A/B**：resolve base（PR base.sha → event.before → HEAD~1 兜底）→ A 侧 checkout base + `uv sync` + `--save-baseline`（临时路径）→ B 侧 checkout head + sync + gate；入库 `benchmarks/baseline.json` 降级为本地开发参考
- **验证**：本地 T1 save 路径覆盖 / T2 正常 A/B（退化 -1.2% 内）/ T3 空基线全新增放行 全绿；CI 待推送后观察
- 涉及模块：scripts/bench.sh, scripts/bench_compare.py, .github/workflows/ci.yml, plans/2026-09-09-benchmark-tiers.md(决策记录), docs/references/best-practices.md, docs/Changelog.md

## 2026-09-09 - bench-cpu A/B 修复二轮：base 改 origin/main + 空产物兜底
- **问题**：A/B 首跑 127——PR 的 `base.sha` 是 PR 创建时点的 main（162f576，早于 PR#3 合并），checkout 后连 scripts/bench.sh 都不存在；且 base 的 uv.lock 无 pytest-benchmark 时 importorskip 全 skip，pytest 不产出 JSON 会让 save 的 cp 失败
- **修复**：base 语义改为 `git rev-parse origin/main`（合并目标最新 head，脚本/依赖齐全，语义也更正确——"PR 合并后 main 性能不得退化"）；bench.sh 在 pytest 无产物输出时补写空 JSON，save/gate 流程不中断
- 涉及模块：.github/workflows/ci.yml, scripts/bench.sh, docs/Changelog.md

## 2026-09-09 - bench-cpu A/B 修复三轮：A 侧从 head 取回 bench 脚本
- **问题**：bench.sh 本身随本 PR 新增，origin/main 上没有——A 侧 checkout base 后 `bash ../scripts/bench.sh` 报 127（前一轮同症状但根因不同：上一轮是 base.sha 太老，这一轮是脚本未入库）
- **修复**：A 侧 checkout base 后 `git checkout head -- scripts/bench.sh scripts/bench_compare.py` 借回脚本再跑（当前 PR：base 无 tests/benchmarks → 短路空基线 → B 侧全新增放行；未来 PR：A 侧真跑基线）；同时把 git pathspec 操作移回仓库根（pathspec 相对 cwd，backend/ 子目录会解析成 backend/scripts/）
- 涉及模块：.github/workflows/ci.yml, docs/Changelog.md
