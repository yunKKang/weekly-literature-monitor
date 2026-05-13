# Literature Monitor v3.0 — 重构计划

> 状态: Phase 0-7 全部完成 + 优化迭代 | 最后更新: 2026-05-12 | 基于 v2.2.0

## 目标

从 GFCF 单主题工具 → 通用多主题文献监控系统。
核心诉求：覆盖面广、准确、全面。

## 已完成阶段

- [x] Phase 0: 准备与基线固化 (2026-04-28)
- [x] Phase 1: Topic 抽象层 — YAML 配置 + Pydantic schema + loader + CLI (2026-04-28)
- [x] Phase 2: 评分管道化 — Pipeline + 7 Stages + parity tests (2026-05-11)
- [x] Phase 3: State 迁移 — seen_dois SQLite 表 + 迁移脚本 (2026-05-11)
- [x] Phase 4: 异步化 — httpx + aiosqlite + async_search_service (2026-05-11)
- [x] Phase 5: Fetch 策略对齐 + 评分优化 (2026-05-11)
- [x] Phase 6: Legacy 下线 — compat.py 删除, literature_monitor/ 自包含 (2026-05-11)
- [x] Phase 7: LLM 复核 — DeepSeek deepseek-v4-flash 两阶段筛选 (2026-05-12)

## 进行中

- [x] 词表扩充: capital_stock_terms + 环境词扩展 + OR 硬阈值 + 负面关键词
- [x] 两阶段 LLM 筛选: title 快速过滤 → abstract 深度判断
- [x] 用户反馈机制: feedback.py --update-prompt 集成到 prompt 更新流程
- [ ] 扩大时间窗口验证 recall（标杆论文检查）

## 系统能力（当前）

| 能力 | 详情 |
|------|------|
| 关键词 | 8 组，163 个去重词（含 OR 条件硬阈值） |
| 管道 | 4 条（Env/MRIO/Trade/Policy）|
| 负面词 | 26 个（生物/材料化学/金融） |
| LLM | DeepSeek deepseek-v4-flash，5 个 few-shot，6 维输出，两阶段筛选 |
| 反馈 | feedback.py 迭代微调（--show / --export-examples / --update-prompt） |
| 测试 | 186 passed |

## 精度演进

```
Run 18 (原始规则):     MEDIUM 误召 = 104/107 (97%)
Run 23 (硬阈值+词表):  MEDIUM 误召 = 0/5    (0%)
Run 25 (LLM 复核):    MEDIUM 误召 = 0/2    (0%)  ← 最终状态
```

## 技术选型

| 决策 | 推荐 | 核心理由 |
|------|------|------|
| 主体语言 | Python | LLM 生态在 Python |
| Topic 抽象 | YAML + Pydantic | 新增主题零代码 |
| 配置格式 | YAML | 嵌套 + 注释 |
| HTTP 并发 | asyncio + httpx | 为 LLM 异步铺路 |
| DB 访问 | sqlite3 → aiosqlite | 最小改动 async 化 |
| 评分管道 | Pipeline + Stage 列表 | LLM 作为普通 Stage |
| LLM 提供商 | DeepSeek deepseek-v4-flash | OpenAI 兼容，便宜 |

## 目标架构

```
weekly-literature-monitor/
├── topics/                     # 每个主题一份 YAML
│   ├── gfcf_environment.yaml
│   └── algal_bloom_ml.yaml
├── config/                     # 共享期刊库
├── literature_monitor/         # 核心包（自包含，无 sys.path hack）
│   ├── cli.py                  # CLI 入口
│   ├── topic/                  # Topic 抽象层
│   ├── pipeline/               # Stage 化评分管道
│   │   ├── base.py             # PipelineState + Stage + Pipeline
│   │   ├── engine.py           # build_pipeline()
│   │   └── stages/             # 7 个 Stage + LLM 复核
│   ├── providers/              # 异步 provider 层
│   ├── db/                     # SQLite + FTS5 + DOI 去重
│   └── api/                    # FastAPI（辅助层）
├── scripts/
│   ├── snapshot_baseline.py    # 基线快照
│   └── feedback.py             # 反馈循环
├── tests/                      # 186 tests
└── docs/
    └── REFACTOR-PLAN.md        # 本文件
```
