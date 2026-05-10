# Literature Monitor v3.0 — 重构计划

> 状态: Phase 0 进行中 | 生成时间: 2026-04-28 | 基于 v2.2.0

## 目标

从 GFCF 单主题工具 → 通用多主题文献监控系统。
核心诉求：覆盖面广、准确、全面。

## 架构诊断

### 技术债务

| 等级 | 编号 | 问题 |
|------|------|------|
| HIGH | H1 | 主题耦合在 keywords.json 顶层 schema（gfcf_vocabulary 焊死在顶层键） |
| HIGH | H2 | 评分逻辑两处实现（src/relevance_filter.py + core/scoring.py），行为已分叉 |
| HIGH | H3 | sys.path 操控作架构黏合剂（6 处 insert） |
| HIGH | M1↑ | state JSON 的 seen_dois 精准=10000 FIFO上限，正在主动丢弃历史 DOI |

### 可保留资产

- A1: GFCF 领域词表（关键词内容，重组方式不改内容）
- A2: SQLite 5表+FTS5 schema（核心数据模型健康）
- A3: Provider 适配器（crossref.py / openalex.py 实现干净）
- A4: 1255行测试（行为不能退步的基准）
- A5: state JSON ~10000条已见DOI（迁入SQLite，不丢弃）

## 技术选型

| 决策 | 推荐 | 核心理由 |
|------|------|------|
| 主体语言 | Python（不引入Go/Rust） | LLM生态在Python，收益不成立 |
| Topic抽象 | YAML配置 + Pydantic schema | 新增主题零代码 |
| 配置格式 | YAML | 支持嵌套+注释，生态成熟 |
| HTTP并发 | asyncio + httpx.AsyncClient | 为LLM异步接入铺路 |
| DB访问 | sqlite3→aiosqlite（不上ORM） | 最小改动完成async化 |
| 评分管道 | Pipeline类 + Stage列表 | LLM作为普通Stage接入 |
| Legacy处理 | 改造为Topic预设 + 并行验证期 | 消除H2/H3但保全功能 |

## 目标架构

```
weekly-literature-monitor/
├── topics/                     # 每个主题一份YAML
│   ├── gfcf_environment.yaml
│   └── algal_bloom_ml.yaml
├── shared/                     # 跨主题共享资产
│   ├── journals/               # 按领域拆分的期刊库
│   └── keyword_libs/           # 可被主题引用的词表
├── litmon/                     # 核心包（原 literature_monitor/）
│   ├── cli.py
│   ├── topic/                  # Topic一等抽象
│   ├── pipeline/               # Stage化评分管道
│   ├── providers/              # 异步provider层
│   ├── db/
│   ├── exporters/
│   └── api/                    # FastAPI（辅助层）
├── tests/
└── scripts/                    # 运维脚本
```

## 阶段路线

| 阶段 | 目标 | 预计 |
|------|------|------|
| 0 | 准备与基线固化 | 1-2天 |
| 1 | Topic抽象层（一等公民） | 3-5天 |
| 2 | 评分管道化（Stage） | 5-7天 |
| 3 | State迁移（解除10000上限） | 1-2天 |
| 4 | 异步化（httpx+aiosqlite） | 3-5天 |
| 5 | Legacy降级+并行验证 | 2-3天 + 2-4周并行 |
| 6 | Legacy下线+sys.path清理 | 1天 |
| 7 | LLM二次筛选（可选） | 5-7天 |

## 风险管理

| 风险 | 缓解措施 |
|------|----------|
| 新旧评分不一致 | 阶段0基线快照 + 阶段2 parity测试 + 阶段5并行期 |
| Topic schema设计难改 | Pydantic严格校验 + version字段 + 先服务2个主题再公开 |
| State迁移损坏 | 备份 + checksum对比 |
| 异步并发bug | semaphore限制 + provider独立retry + 指数退避 |
| LLM成本失控 | max_papers_per_run + max_cost_usd强制配置 |

## 进展追踪

- [x] Phase 0: 计划方案落盘
- [ ] Phase 0: 安装新依赖
- [ ] Phase 0: 基线快照脚本 + 运行
- [ ] Phase 1: Topic schema + loader
- [ ] Phase 1: YAML配置文件（gfcf_environment + algal_bloom_ml）
- [ ] Phase 1: CLI topic子命令
- [ ] Phase 1: 测试 + parity验证
