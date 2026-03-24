# IntegriRef

**Multi-Layer Reference Integrity Verification Engine**

L0-L4 五层验证栈 — 覆盖学术、专利、法律、政府、金融、标准六大领域的引用完整性引擎。62 个注册表适配器，15+ 语言，L2 语义验证精度 93.5%（超越人类一致性 89.1%）。

---

## 核心能力

| 验证层 | 能力 | 精度/性能 | 竞品现状 |
|--------|------|-----------|----------|
| **L0** | 存在性验证 + 字段匹配 + 幻觉检测 + 撤稿检查 | 18,555 refs/s (字段) · 336K refs/s (幻觉) | CiteTrue/SwanRef 仅此层 |
| **L1** | 引文意图分类 (Supporting/Contrasting/Mentioning) | 86.0% F1 (SciBERT on SciCite) | scite.ai 独立评估仅 29% F1 |
| **L2** | 语义声明验证 (claim ↔ abstract NLI 对齐) | **93.5% acc** (DeBERTa-v3 on SciFact) | **无竞品** |
| **L3** | 引文图谱异常检测 (7 种异常 + 2-hop OpenAlex) | CIDRE 变体 + Benford 定律 | **无竞品** |
| **L4** | 贝叶斯风险评分 (18 信号 × 似然比) | 校准后验概率 · 4 风险等级 | **无竞品** |

**额外检测能力**: 281 扭曲短语 · GRIM 数值验证 · statcheck p 值重算 · Sneaked Reference 检测 · 邮箱风险评分

---

## 架构

```
┌──────────────────────────────────────────────────────────────┐
│  L4: Bayesian Risk Scoring                                    │
│  └─ 18 signals × likelihood ratios → posterior probability    │
├──────────────────────────────────────────────────────────────┤
│  L3: Citation Graph Analysis                                  │
│  └─ OpenAlex 2-hop │ 7 anomaly detectors │ health score      │
├──────────────────────────────────────────────────────────────┤
│  L2: Semantic Claim Verification                              │
│  └─ DeBERTa-v3 NLI │ ONNX INT8 加速 │ sentence-level        │
├──────────────────────────────────────────────────────────────┤
│  L1: Citation Intent Classification                           │
│  └─ SciBERT fine-tuned │ 3-class │ heuristic fallback        │
├──────────────────────────────────────────────────────────────┤
│  L0: Reference Existence Verification                         │
│  └─ FieldComparator │ HallucinationDetector │ Retraction      │
│  └─ TorturedPhrases │ GRIM │ statcheck │ SneakedRefs         │
├──────────────────────────────────────────────────────────────┤
│  Foundation: 62 Registry Adapters × 6 Domains                 │
│  ┌─────────┐ ┌────────┐ ┌───────┐ ┌──────┐ ┌─────┐ ┌─────┐ │
│  │Academic │ │Patents │ │ Legal │ │ Gov  │ │ Fin │ │ Std │ │
│  │   35    │ │   5    │ │   8   │ │  6   │ │  4  │ │  4  │ │
│  └─────────┘ └────────┘ └───────┘ └──────┘ └─────┘ └─────┘ │
├──────────────────────────────────────────────────────────────┤
│  Infrastructure: Cache │ Circuit Breaker │ Async I/O │ ONNX   │
└──────────────────────────────────────────────────────────────┘
```

---

## 快速开始

```bash
# 克隆 & 安装
git clone https://github.com/GeoffreyWang1117/IntegriRef.git
cd IntegriRef
pip install -r requirements.txt

# 可选：异步 + ONNX 加速
pip install aiohttp uvloop onnxruntime

# 运行测试 (480+ tests)
pytest tests/ -x -q
```

### 基本用法

```python
from core.discovery import RegistryDiscovery

# 初始化 — 自动注册 62 个适配器
discovery = RegistryDiscovery()
discovery.auto_register()

# 按标识符查询 (自动检测 DOI/arXiv/PMID/ISBN/专利号等 15 种格式)
result = discovery.verify_reference(
    title="Attention Is All You Need",
    identifier="10.48550/arXiv.1706.03762",
)
print(result["found"])            # True
print(result["registries_hit"])   # ["crossref", "openalex", ...]
```

### 语义验证 (L2)

```python
from semantic.nli_verifier import NLIVerifier

verifier = NLIVerifier()  # DeBERTa-v3, 93.5% accuracy
result = verifier.verify(
    claim="Transformer models outperform RNNs on translation",
    abstract="Experiments on WMT 2014 show the model achieves 28.4 BLEU, "
             "surpassing the best previously reported RNN-based models."
)
# result.label → SUPPORTED (confidence: 0.91)
```

### 异步批量验证

```python
import asyncio
from core.async_discovery import AsyncRegistryDiscovery
from core.batch_processor import BatchProcessor

async def main():
    discovery = AsyncRegistryDiscovery()
    discovery.register_all(all_adapters)

    processor = BatchProcessor(discovery, max_concurrent=32)
    results = await processor.verify_batch(references)
    # 19.8x throughput vs sequential

asyncio.run(main())
```

---

## 项目结构

```
IntegriRef/
├── core/                      # 核心引擎
│   ├── entity.py              # ICEntity 数据模型 (12 种实体类型)
│   ├── registry.py            # RegistryAdapter 基类 (限速/重试/熔断)
│   ├── discovery.py           # RegistryDiscovery 路由 (15 种 ID 检测)
│   ├── async_registry.py      # 异步适配器基类 (aiohttp)
│   ├── async_discovery.py     # 异步发现引擎 (asyncio.gather)
│   ├── batch_processor.py     # 批量处理器 (Semaphore 并发控制)
│   ├── cache.py               # 双层缓存 (LRU + Redis)
│   ├── circuit_breaker.py     # 熔断器 (滑动窗口/3 状态)
│   └── parsing.py             # 统一解析工具
│
├── verification/              # L0 验证层
│   ├── engine.py              # 多注册表级联验证引擎 (1050+ 行)
│   ├── field_comparator.py    # 字段模糊匹配 (RapidFuzz + Jaccard)
│   ├── hallucination_detector.py  # AI 引文幻觉检测 (7 种模式 + kill-shot)
│   ├── retraction_checker.py  # Crossref 撤稿检测
│   ├── tortured_phrases.py    # 扭曲短语检测 (281 模式, Aho-Corasick)
│   ├── statistical.py         # GRIM + statcheck 统计验证
│   ├── sneaked_references.py  # Sneaked Reference 检测
│   └── email_risk.py          # 邮箱风险评分 (55 免费 + 25 工厂域名)
│
├── semantic/                  # L1-L2 语义层
│   ├── nli_verifier.py        # NLI 验证器 (DeBERTa-v3, 93.5%)
│   ├── intent_classifier.py   # 引文意图分类 (SciBERT, 86% F1)
│   ├── onnx_exporter.py       # ONNX 导出 + INT8 量化
│   └── semantic_pipeline.py   # 端到端语义管线
│
├── graph/                     # L3 图谱层
│   ├── builder.py             # CitationGraph 构建
│   ├── anomaly.py             # 7 种异常检测器
│   ├── metrics.py             # 5 维健康评分
│   └── openalex_builder.py    # OpenAlex 2-hop 图谱
│
├── scoring/                   # L4 评分层
│   ├── bayesian.py            # 贝叶斯风险评分 (18 信号)
│   ├── dempster_shafer.py     # D-S 证据融合 + Grouped Bayesian
│   ├── calibration.py         # 经验校准器 (Platt/Isotonic/Bayesian)
│   ├── scorer.py              # 多维度完整性评分
│   └── profiles.py            # 领域评分配置
│
├── registries/                # 62 个数据库适配器
│   ├── academic/   (35)       # OpenAlex, Crossref, PubMed, S2, arXiv, DBLP, ...
│   ├── patents/    (5)        # USPTO, EPO, WIPO, Google Patents, KIPRIS
│   ├── legal/      (8)        # CourtListener, EUR-Lex, Legifrance, ...
│   ├── government/ (6)        # data.gov, e-Gov Japan, e-Stat, ...
│   ├── financial/  (4)        # EDGAR, FRED, INPI, ...
│   └── standards/  (4)        # ISO, IEC, NIST, ...
│
├── models/                    # 微调模型
│   ├── intent_classifier/     # SciBERT on SciCite (86% F1)
│   └── scifact_nli/           # DeBERTa-v3 on SciFact (93.5%)
│
├── tests/          (480+)     # 测试套件
├── benchmarks/                # 基准测试套件
│   ├── bench_comparison.py    # IntegriRef vs CheckIfExist 对比
│   ├── bench_fusion.py        # 信号融合消融 (Bayes/Grouped/D-S)
│   ├── bench_early_stop.py    # 级联早停模拟
│   └── baselines/             # 基线实现 (CheckIfExist 等)
├── web/                       # 展示网站 + 文档
└── docs/                      # 开发笔记 + 战略分析
```

---

## Benchmark 结果

### 完整性基准 (IntegriRef-Bench Golden Test Set, 58 cases)

| 指标 | 优化前 | 优化后 | 说明 |
|------|--------|--------|------|
| 撤稿召回率 (≥ ELEVATED) | 100% (10/10*) | **73.9% (17/23)** | 全量 23 篇撤稿论文，6 篇缺少撤稿标记 |
| 幻觉召回率 (≥ HIGH) | 42.9% | **100% (14/14)** | 14/14 假引用全部检出 |
| 误报率 (real → ≥ ELEVATED) | 0% (0/15) | **0% (0/18)** | 18 篇真论文零误报 |
| Chimera 检测率 | 100% | **100% (3/3)** | 3/3 元数据嵌合体 |
| 幻觉精确率 | 100% | **100%** | 无假阳性 |

\* 优化前仅处理了 10/25 篇撤稿论文（超时）；优化后为全量 58-case 运行。

**关键优化**: Phantom DOI 信号独立于搜索结果 · Kill-shot override 阻止贝叶斯软化 · 搜索阶段 author guard 防止假匹配

### L0 独立筛查模式 (ref-check, 92 秒完成)

| 指标 | 值 | 说明 |
|------|-----|------|
| 幻觉召回率 | **100% (14/14)** | 所有假引用被 FAIL/WARN |
| 真论文正确率 | 70% (7/10) | 3 篇误报可由人工复核修正 |
| Phantom ID 检测 | 4 个 | 格式正确但不存在的 DOI/arXiv |
| 撤稿检测 | 0% | L0 无 Retraction Watch 集成 |

### Head-to-Head: IntegriRef vs CheckIfExist

在 58-case golden test set 上与 CheckIfExist (Abbonato et al. 2025) 的正面对比：

| 指标 | IntegriRef (L0+L4) | CheckIfExist | 说明 |
|------|:--:|:--:|------|
| 幻觉召回率 (≥ HIGH) | **100% (14/14)** | 0% (0/14) | CIE 无法产生高置信度判断 |
| 幻觉召回率 (≥ ELEVATED) | **100% (14/14)** | 92.9% (13/14) | CIE 标记为 suspicious 但非 HIGH |
| 撤稿召回率 (≥ ELEVATED) | **73.9% (17/23)** | 26.1% (6/23) | |
| Chimera 检测率 | **100% (3/3)** | 33.3% (1/3) | CIE 对标题正确的 chimera 完全盲目 |
| 误报率 | **0% (0/18)** | 11.1% (2/18) | CIE 因标题格式差异误判真论文 |
| 平均延迟 | 38.5s | **2.3s (17x)** | CIE 仅查存在性，速度优势明显 |

### 信号融合消融实验

三种 L4 评分方法对比（同一 58-case 测试集）：

| 方法 | 准确率 | 幻觉召回 (≥HIGH) | 误报率 | 说明 |
|------|:--:|:--:|:--:|------|
| **Naive Bayes + Kill-shot** | **89.7%** | **100%** | **0%** | 当前默认 |
| Grouped Bayesian | 89.7% | 50% | 0% | 组内取 max LR，防双重计数 |
| Dempster-Shafer | — | 100% | 100% | Pignistic 概率分裂不确定性 |

信号相关性：`phantom_doi`↔`reference_not_found` 共火 100% (8/8)；`metadata_mismatch`↔`no_id_match` 共火 71% (15/21)。

### 级联早停优化 (C3PO-inspired)

L0→L4 中间评分后，可跳过 L1–L3 的模拟实验：

| 阈值策略 | 跳过率 | 准确率回归 | 节省延迟 |
|----------|:--:|:--:|:--:|
| 保守 (>0.80 / <0.01) | 43.1% | 0 | ~56s |
| 中等 (>0.70 / <0.02) | 60.3% | 0 | ~79s |
| **激进 (>0.50 / <0.05)** | **67.2%** | **0** | **~88s** |

所有 18 篇真论文 (posterior < 0.05) 跳过 L1–L3；8 个 "tier change" 均为向上偏移 (CRITICAL→HIGH)，早停严格安全。

### 精度

| 层 | 微调前 | 微调后 | 公开 SOTA | 说明 |
|----|--------|--------|-----------|------|
| L1 (SciCite) | 69.8% acc / 46.9 F1 | **87.6% acc / 86.0 F1** | 88.9 F1 (ImpactCite) | 仅 110M 参数即接近 SOTA |
| L2 (SciFact) | 52.4% acc | **93.5% acc** | 88% F1 | **超越人类一致性 89.1%** |

### 吞吐量

| 组件 | 吞吐量 | 延迟 |
|------|--------|------|
| L0 FieldComparator | 18,555 refs/s | 0.05 ms |
| L0 HallucinationDetector | 336,136 refs/s | <0.01 ms |
| L1 SciBERT batch=8 (GPU) | 263.5 refs/s | 3.8 ms |
| L2 NLI (GPU) | 25.1 refs/s | 39.8 ms |

### 加速基础设施

| 优化 | 加速比 |
|------|--------|
| asyncio.gather vs 顺序 | **14.5x** |
| Async + 缓存命中 | **145.7x** |
| 批处理 32 并发 | **19.8x** |
| ONNX INT8 vs PyTorch CPU | **2.2x** |

---

## 竞品对比

| 能力 | **IntegriRef** | scite.ai | CiteTrue/SwanRef | Papermill Alarm | RefChecker |
|------|:-:|:-:|:-:|:-:|:-:|
| L0 存在性验证 | **62 registries** | 30M+ papers | 150M+ | -- | LLM-based |
| L1 引文意图 | **86.0% F1** | ~29% F1* | -- | -- | -- |
| L2 语义验证 | **93.5% acc** | -- | -- | -- | -- |
| L3 图谱异常 | **7 types** | Basic | -- | -- | -- |
| L4 贝叶斯风险 | **18 signals** | -- | -- | -- | -- |
| 扭曲短语 | **281 patterns** | -- | -- | Yes | -- |
| GRIM / statcheck | **Yes** | -- | -- | -- | -- |
| 多语言 (15+) | **Yes** | Partial | -- | -- | -- |
| 开源 / 自部署 | **Yes** | No | No | No | Yes |

\* scite.ai 自报 F1~74%；独立评估 (Bakker et al. 2023) 仅 F1~29%。

---

## 多语言覆盖 (15+ 语言)

| 语言 | 适配器 |
|------|--------|
| 法语 | HAL, Legifrance, Persee, theses.fr, INPI |
| 日语 | CiNii, J-STAGE, e-Gov Japan, e-Stat |
| 韩语 | KCI, KIPRIS, Korean Law Center |
| 拉美 | SciELO, Redalyc, Dialnet |
| 土耳其语 | DergiPark |
| 俄语 | CyberLeninka (OAI-PMH) |
| 印尼语 | GARUDA (OAI-PMH) |
| 印度 | Shodhganga, Indian Kanoon |

---

## 开发路线图

### 已完成 (v0.1 → v0.3)

| 版本 | 里程碑 | 关键成果 |
|------|--------|----------|
| v0.1 | 基础设施 | 31 注册表适配器 + ICEntity 模型 + RegistryDiscovery 路由 |
| v0.2 | L0-L4 全栈 | 5 层验证管线 + IntegriRefPipeline 编排器 + 信号融合 |
| v0.3 | 扩展与优化 | 62 适配器 (6 领域 15+ 语言) + 异步/缓存/熔断基础设施 + Rust 加速 |

**核心指标** (58-case golden benchmark):
- 幻觉召回 100% (14/14) · 误报率 0% (0/18) · 撤稿召回 73.9% (17/23)
- CheckIfExist head-to-head 全面优于 · 级联早停 67.2% 跳过率零回归

### 当前进行中

| 优先级 | 任务 | 说明 | 状态 |
|--------|------|------|------|
| **P0** | 数据集扩展 (爬虫) | 7 个爬虫就绪，需要扩充撤稿/幻觉/统计错误样本 | 🔄 进行中 |
| **P0** | 系统论文撰写 | GEM 2026 投稿，L0-L4 全栈 + 消融 + 对比 | 🔄 进行中 |
| **P1** | 接入 4 个未连接的检测器 | tortured/email/GRIM+statcheck/sneaked → engine | 待做 |
| **P1** | 端到端集成测试 | 用真实论文 fixture 验证完整管线 | 待做 |
| **P2** | FastAPI REST API | 商业化 MVP，5 个核心 endpoint | 待做 |
| **P2** | 跨数据集泛化测试 (L2) | HealthVer, FEVER, ClimateVER | 待做 |
| **P3** | LLM 幻觉引文 benchmark | GPT-4/Claude/Gemini 生成引文验证 | 待做 |

### 数据集爬虫状态

`benchmarks/crawlers/` 下 7 个爬虫：

| 爬虫 | 数据源 | 输出 | 状态 |
|------|--------|------|------|
| `retracted_papers.py` | Crossref + PubMed retracted | 撤稿论文元数据 | ✅ 已有数据 |
| `reference_verification.py` | Crossref API | 真实引用验证对 | ✅ 已有数据 |
| `scifact_converter.py` | SciFact dataset | NLI claim-evidence 对 | ✅ 已有数据 |
| `grim_crawler.py` | PMC full-text | GRIM 可检测论文 | 待扩展 |
| `statcheck_crawler.py` | PMC full-text | 统计报告论文 | 待扩展 |
| `pmc_fulltext.py` | PMC OA subset | 全文引用上下文 | 待扩展 |
| `temporal_anomaly.py` | Crossref + OpenAlex | 时间异常引用 | 待扩展 |

### 研究方向

1. **系统论文** (GEM 2026) — L0-L4 全栈 + 消融实验 + CheckIfExist 对比 (**撰写中**)
2. **LLM 引文幻觉检测** (ACL/EMNLP) — 最热话题，基础设施已就绪
3. **跨领域引文操控** (QSS) — 6 领域 × 10K 论文的异常模式对比
4. **多语言引文验证** (JCDL) — 15+ 语言验证精度差异与改进

---

## 设计原则

1. **不下载、不存储原文** — 只做验证和元数据比对
2. **每个 Adapter 独立可插拔** — 统一 `RegistryAdapter` 接口
3. **速率限制 + 熔断器内置** — 自动遵守 API 配额，故障自动隔离
4. **ICEntity 唯一交换格式** — 所有输出归一化
5. **CPU 优先，GPU 可选** — 每层都有启发式 fallback

---

## 相关文档

| 文件 | 内容 |
|------|------|
| [docs/DEVELOPMENT_NOTES.md](docs/DEVELOPMENT_NOTES.md) | 完整技术记录：架构设计、微调记录、性能优化、Bug 修复 |
| [docs/STRATEGY.md](docs/STRATEGY.md) | 竞品分析、技术路线、商业化路线图 |
| [web/index.html](web/index.html) | 产品展示页 |
| [web/docs.html](web/docs.html) | 在线文档 |
| [ROADMAP.md](ROADMAP.md) | 早期技术路线图 |
| [RESEARCH.md](RESEARCH.md) | 数据源调研报告 |

---

## License

MIT License. See [LICENSE](LICENSE) for details.
