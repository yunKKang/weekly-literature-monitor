     1|# Literature Monitor v3.0 — 重构计划
     2|
     3|> 状态: Phase 0 进行中 | 生成时间: 2026-04-28 | 基于 v2.2.0
     4|
     5|## 目标
     6|
     7|从 GFCF 单主题工具 → 通用多主题文献监控系统。
     8|核心诉求：覆盖面广、准确、全面。
     9|
    10|## 架构诊断
    11|
    12|### 技术债务
    13|
    14|| 等级 | 编号 | 问题 |
    15||------|------|------|
    16|| HIGH | H1 | 主题耦合在 keywords.json 顶层 schema（gfcf_vocabulary 焊死在顶层键） |
    17|| HIGH | H2 | 评分逻辑两处实现（src/relevance_filter.py + core/scoring.py），行为已分叉 |
    18|| HIGH | H3 | sys.path 操控作架构黏合剂（6 处 insert） |
    19|| HIGH | M1↑ | state JSON 的 seen_dois 精准=10000 FIFO上限，正在主动丢弃历史 DOI |
    20|
    21|### 可保留资产
    22|
    23|- A1: GFCF 领域词表（关键词内容，重组方式不改内容）
    24|- A2: SQLite 5表+FTS5 schema（核心数据模型健康）
    25|- A3: Provider 适配器（crossref.py / openalex.py 实现干净）
    26|- A4: 1255行测试（行为不能退步的基准）
    27|- A5: state JSON ~10000条已见DOI（迁入SQLite，不丢弃）
    28|
    29|## 技术选型
    30|
    31|| 决策 | 推荐 | 核心理由 |
    32||------|------|------|
    33|| 主体语言 | Python（不引入Go/Rust） | LLM生态在Python，收益不成立 |
    34|| Topic抽象 | YAML配置 + Pydantic schema | 新增主题零代码 |
    35|| 配置格式 | YAML | 支持嵌套+注释，生态成熟 |
    36|| HTTP并发 | asyncio + httpx.AsyncClient | 为LLM异步接入铺路 |
    37|| DB访问 | sqlite3→aiosqlite（不上ORM） | 最小改动完成async化 |
    38|| 评分管道 | Pipeline类 + Stage列表 | LLM作为普通Stage接入 |
    39|| Legacy处理 | 改造为Topic预设 + 并行验证期 | 消除H2/H3但保全功能 |
    40|
    41|## 目标架构
    42|
    43|```
    44|weekly-literature-monitor/
    45|├── topics/                     # 每个主题一份YAML
    46|│   ├── gfcf_environment.yaml
    47|│   └── algal_bloom_ml.yaml
    48|├── shared/                     # 跨主题共享资产
    49|│   ├── journals/               # 按领域拆分的期刊库
    50|│   └── keyword_libs/           # 可被主题引用的词表
    51|├── litmon/                     # 核心包（原 literature_monitor/）
    52|│   ├── cli.py
    53|│   ├── topic/                  # Topic一等抽象
    54|│   ├── pipeline/               # Stage化评分管道
    55|│   ├── providers/              # 异步provider层
    56|│   ├── db/
    57|│   ├── exporters/
    58|│   └── api/                    # FastAPI（辅助层）
    59|├── tests/
    60|└── scripts/                    # 运维脚本
    61|```
    62|
    63|## 阶段路线
    64|
    65|| 阶段 | 目标 | 预计 |
    66||------|------|------|
    67|| 0 | 准备与基线固化 | 1-2天 |
    68|| 1 | Topic抽象层（一等公民） | 3-5天 |
    69|| 2 | 评分管道化（Stage） | 5-7天 |
    70|| 3 | State迁移（解除10000上限） | 1-2天 |
    71|| 4 | 异步化（httpx+aiosqlite） | 3-5天 |
    72|| 5 | Legacy降级+并行验证 | 2-3天 + 2-4周并行 |
    73|| 6 | Legacy下线+sys.path清理 | 1天 |
    74|| 7 | LLM二次筛选（可选） | 5-7天 |
    75|
    76|## 风险管理
    77|
    78|| 风险 | 缓解措施 |
    79||------|----------|
    80|| 新旧评分不一致 | 阶段0基线快照 + 阶段2 parity测试 + 阶段5并行期 |
    81|| Topic schema设计难改 | Pydantic严格校验 + version字段 + 先服务2个主题再公开 |
    82|| State迁移损坏 | 备份 + checksum对比 |
    83|| 异步并发bug | semaphore限制 + provider独立retry + 指数退避 |
    84|| LLM成本失控 | max_papers_per_run + max_cost_usd强制配置 |
    85|
    86|## 进展追踪
    87|
    88|- [x] Phase 0: 计划方案落盘
    89|- [ ] Phase 0: 安装新依赖
    90|- [ ] Phase 0: 基线快照脚本 + 运行
    91|- [ ] Phase 1: Topic schema + loader
    92|- [ ] Phase 1: YAML配置文件（gfcf_environment + algal_bloom_ml）
    93|- [ ] Phase 1: CLI topic子命令
    94|- [ ] Phase 1: 测试 + parity验证
    95|