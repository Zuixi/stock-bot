"""P6 知识库内容种子（猪智投）— 机构图谱 / 数据权威性原则 / 行业思维导图。

内容是数据不是代码：本模块只声明 JSON payload，迁移（e6f7a8b9c0d1）与离线单测
（tests/test_industry_knowledge.py）共用同一份内容，单点维护。
第二行业（Stage E）接入时新增各自 INDUSTRY_KNOWLEDGE_ROWS，表结构与 API 零改动。

内容来源说明：PRD 原文（docs/农林牧渔-养殖业-生猪养殖v3.2-产品化-20260814 - 猪智投.md）
不在仓库中，本种子内容以 docs/design/data-source.md §四（口径与坑，含多源分歧原则）
与 §六（参考链接）为基准整理；机构 tier 与 registry/SourceBadge 的五级权威性对齐。
"""

from __future__ import annotations

# 机构图谱四分组：官方 / 协会 / 数据平台 / 期货（sort 十位数区分组、个位数组内排序）
PIG_ORG_PAYLOADS: list[dict] = [
    # ── 官方：产能与价格的最终基准 ─────────────────────────────────────
    {
        "name": "农业农村部",
        "group": "官方",
        "tier": "official",
        "desc": "产能最终基准：能繁母猪存栏（月度环比）、规模以上屠宰量；"
                "全国农产品批发市场价格信息系统的猪肉批发价为官方价格口径",
        "urls": ["http://www.moa.gov.cn"],
    },
    {
        "name": "国家发展改革委",
        "group": "官方",
        "tier": "official",
        "desc": "猪粮比官方口径（周度）与过度下跌/上涨预警分级发布，收储/投放政策主责部门",
        "urls": ["https://www.ndrc.gov.cn"],
    },
    {
        "name": "国家统计局",
        "group": "官方",
        "tier": "official",
        "desc": "能繁母猪存栏季度末绝对数、生猪存栏/出栏量与猪肉产量，长周期对比基准",
        "urls": ["https://data.stats.gov.cn"],
    },
    {
        "name": "海关总署",
        "group": "官方",
        "tier": "official",
        "desc": "猪肉及杂碎月度进口量 —— 猪价高位时的重要边际供给变量",
        "urls": ["http://www.customs.gov.cn"],
    },
    {
        "name": "大连商品交易所",
        "group": "官方",
        "tier": "official",
        "desc": "生猪期货（LH）挂牌交易所：合约规则、交割质量标准与结算基准价的权威出处",
        "urls": ["http://www.dce.com.cn"],
    },
    # ── 协会：官方数据的规范转载渠道 ───────────────────────────────────
    {
        "name": "中国畜牧业协会猪业分会",
        "group": "协会",
        "tier": "official",
        "desc": "每月转载五部门“全国生猪产品数据”（能繁环比等），HTML 规范易解析，"
                "为本项目协会自动采集源（pig.caaa.cn）",
        "urls": ["https://pig.caaa.cn"],
    },
    {
        "name": "中国饲料工业协会",
        "group": "协会",
        "tier": "official",
        "desc": "饲料产量月度数据：猪料产量指向存栏消耗，玉米/豆粕需求侧的交叉验证",
        "urls": ["https://www.chinafeed.org.cn"],
    },
    # ── 数据平台：免费自动层主力 + 付费高频墙 ─────────────────────────
    {
        "name": "AKShare",
        "group": "数据平台",
        "tier": "highfreq",
        "desc": "开源财经数据接口库：生意社/搜猪现货、新浪期货 LH 主力等，L1 免费自动层主力",
        "urls": ["https://akshare.akfamily.xyz"],
    },
    {
        "name": "TuShare",
        "group": "数据平台",
        "tier": "highfreq",
        "desc": "股票行情/估值（daily_basic）、ETF（fund_daily）与可转债（cb_daily）日线，"
                "标的分析与行情面数据管道",
        "urls": ["https://tushare.pro"],
    },
    {
        "name": "生意社",
        "group": "数据平台",
        "tier": "highfreq",
        "desc": "生猪/玉米/豆粕现货日度报价（100ppi），价格类指标的免费主源",
        "urls": ["https://www.100ppi.com"],
    },
    {
        "name": "涌益咨询",
        "group": "数据平台",
        "tier": "highfreq",
        "desc": "周度能繁存栏、出栏体重、样本点养殖利润等高频产能调研（L4 付费，万元级/年起）",
        "urls": [],
    },
    {
        "name": "Mysteel（上海钢联）",
        "group": "数据平台",
        "tier": "highfreq",
        "desc": "高频产能与价格调研数据（L4 付费），行业信息差的主要来源之一",
        "urls": [],
    },
    {
        "name": "卓创资讯",
        "group": "数据平台",
        "tier": "highfreq",
        "desc": "高频产能调研数据（L4 付费），与涌益/Mysteel 互为交叉验证",
        "urls": [],
    },
    # ── 期货：价格发现与周期情绪 ───────────────────────────────────────
    {
        "name": "DCE 生猪期货（LH）",
        "group": "期货",
        "tier": "official",
        "desc": "2021-01 上市；主力连续反映市场对未来供需的预期，与现货的价差（基差）"
                "是周期情绪温度计",
        "urls": ["http://www.dce.com.cn"],
    },
]

# 数据权威性使用原则（data-source.md §四.4 多源分歧原则的展开）
PIG_PRINCIPLE_PAYLOAD: dict = {
    "title": "数据权威性使用原则",
    "items": [
        "产能以农业农村部为最终基准：能繁母猪存栏等产能指标多源分歧时，以农业农村部口径定案",
        "价格以发改委/农业农村部为官方基准：猪粮比周度官方值可校准自算口径",
        "高频数据仅作边际参考：生意社/搜猪等市场化高频报价用于跟踪边际变化，不作为基准",
        "同一指标至少两源对比：多源并存分 source 存储，读路径按 registry 源优先级裁决",
        "口径差异显性化：双口径（月度环比 vs 季度末绝对数）、自算与官方口径差异"
        "须在 UI 以徽章/提示标注",
    ],
}

# 行业思维导图（EChart tree 直用结构；根 → 分支 → 叶子共两层叶子深度）
PIG_MINDMAP_PAYLOAD: dict = {
    "name": "生猪养殖投研",
    "children": [
        {
            "name": "供给",
            "children": [
                {"name": "能繁母猪存栏（10 个月时滞）"},
                {"name": "生猪出栏量 / 猪肉产量"},
                {"name": "规模以上屠宰量"},
                {"name": "效率：MSY / PSY / 料肉比"},
            ],
        },
        {
            "name": "需求",
            "children": [
                {"name": "猪肉批发价（消费端）"},
                {"name": "进口：猪肉及杂碎"},
                {"name": "替代蛋白（禽 / 牛羊肉）"},
            ],
        },
        {
            "name": "成本",
            "children": [
                {"name": "玉米价格"},
                {"name": "豆粕价格"},
                {"name": "行业平均完全成本"},
                {"name": "猪粮比（盈亏核心）"},
            ],
        },
        {
            "name": "政策",
            "children": [
                {"name": "产能调控（保有量锚 3750）"},
                {"name": "收储 / 投放"},
                {"name": "环保与防疫"},
            ],
        },
        {
            "name": "金融",
            "children": [
                {"name": "生猪期货 LH（远期预期）"},
                {"name": "头均市值（跨周期估值锚）"},
                {"name": "标的：成分股 / 畜牧ETF / 可转债"},
            ],
        },
    ],
}

# 组序：官方 → 协会 → 数据平台 → 期货（组间百位、组内个位递增）
_GROUP_ORDER = {"官方": 0, "协会": 1, "数据平台": 2, "期货": 3}


def build_pig_knowledge_rows() -> list[dict]:
    """猪智投知识库种子行（industry_knowledge 直插形状，迁移与单测共用）。"""
    rows: list[dict] = []
    for i, payload in enumerate(PIG_ORG_PAYLOADS):
        rows.append({
            "industry_key": "pig",
            "kind": "org",
            "payload": payload,
            "sort": _GROUP_ORDER.get(payload["group"], 9) * 100 + i,
        })
    rows.append({"industry_key": "pig", "kind": "principle",
                 "payload": PIG_PRINCIPLE_PAYLOAD, "sort": 1000})
    rows.append({"industry_key": "pig", "kind": "mindmap",
                 "payload": PIG_MINDMAP_PAYLOAD, "sort": 1000})
    return rows
