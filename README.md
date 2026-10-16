# IntegriRef

**Multi-Layer Reference Integrity Verification Engine**

L0-L4 五层验证栈 — 覆盖学术、专利、法律、政府、金融、标准六大领域的引用完整性引擎。62 个注册表适配器，15+ 语言，L2 语义验证精度 93.5%（超越人类一致性 89.1%）。

---

## 📍 当前状态 (2026-10-16)

**这篇论文未发表。** NLPCC 2026 曾 Oral 录用（2026-08-12），作者选择撤稿而非支付
注册费。撤稿已获会议秘书处书面回复（2026-10-14）：投稿 358 与 355 **已从录用名单
移除**。回复来自 Conference Secretariat，未逐字提及 proceedings 或 Springer LNAI
卷，因此此处只陈述其原话涵盖的范围。撤稿不计入任何发表记录。

**论文该主张什么，以 [docs/IPM_POSITIONING.md](docs/IPM_POSITIONING.md) 为准。**
2026-10-11 的对抗性审计删掉了大部分候选论点（包括「现有工具只检查 DOI 能否解析」
这条可被 Dunford 2024 单篇证伪的断言），剩下的是一条关于**如何审计这类流水线**的
方法论发现，不是检测方法的新颖性。五层框架的提法应当停止作为贡献出现 —— 四位
NLPCC 评审都要求收窄它。

**投稿去向：**

| 去向 | 状态 | 原因 |
|---|---|---|
| arXiv | 包已就绪，未投 | `paper/nlpcc2026/IntegriRef_arxiv.tar.gz`，14 页 A4，验证可解压编译 |
| ECIR 2027 Resource | ❌ 排除 | 要求至少一位作者到场（Southampton），作者 2026-10-16 决定不到场 |
| AAAI-27 W11 (DiMiGenAIS) | 候选，截稿 2026-11-20 | `paper/aaai2027_w11/`，正文 6 页 / 含 refs 7 页；**archival 与否未知，待问组织者** |

**基准已发布：** 九个 split 共 12,381 条，见
[benchmarks/data/dataset_card.md](benchmarks/data/dataset_card.md) 与
[benchmarks/results/README.md](benchmarks/results/README.md)。其中
`Borderline-L1-v2` / `Borderline-L2-v2` 是对 ACL-ARC 与 SciFact dev 的定种子抽选，
**以脚本形式发布而非转载第三方数据** —— 详见 [NOTICE.md](NOTICE.md)。

**测试：** `pytest -m "not network" --block-network` 是离线回归闸门（530 passed，
约 1.3 秒，确定性）。`tests/test_integrity_benchmark.py` 的 60 项需要实时注册表，
通过与否取决于对方是否限流，不构成回归信号。

**论文版本：**

| 目录 | 状态 | 模板 |
|---|---|---|
| `paper/nlpcc2026/camera_ready/` | **权威版本**（唯一含审计修正，引用 Dunford 2024） | Springer LNCS |
| `paper/aaai2027_w11/` | W11 候选，由 camera_ready 原样移植 | AAAI-27 双栏 |
| `paper/nlpcc2026/main.tex` | 2026-08-04 旧稿，**早于审计** | LNCS |
| `paper/main.tex` | 2026-03-18 旧稿，**早于审计** | acmart |
| `paper/gem2026/`、`paper/ase2026/` | 早期草稿，未跟踪 | — |

⚠️ 后两版不要再用于投稿：它们仍含审计已删除的断言。

> **关于 `paper/nlpcc2026/review_round_*.json`**：那是 `self-review` skill 生成的
> **模拟评审**（`venue: neurips`），不是真实评审意见，其分数不代表论文质量。真实的
> NLPCC 评审意见体现在同目录的 `rebuttal_{3tFe,Fr11,HE9x,JYpA}.md` 中。

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

# 运行测试：确定性的离线子集（530 passed，约 1.3 秒）
pytest -m "not network" --block-network

# 全套，含 60 个打实时 registry 的测试（约 40 分钟，且会受限流影响）
pytest tests/ -x -q
```

`tests/test_integrity_benchmark.py` 的 60 个测试会调真实 registry（实测涉及 48
个域名），**它们的成败取决于当时各服务是否限流** —— 同一份代码连跑两次得到
10 和 9 个失败，来自 30 次 HTTP 429 和 6 次 DNS 失败。所以它们不是回归信号，
已用 `network` marker 标出。`--block-network` 会装一个 socket 守卫，让以后新加
的联网测试如果忘了打 marker 就立刻失败，而不是几个月后变成间歇性红灯。

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

### 规模评估 (IntegriRef-Bench external splits, 8,317 cases) — **论文 §4.2**

| 指标 | rv-1926 | ret-6391 | 说明 |
|------|:--:|:--:|------|
| 幻觉召回率 (≥ ELEVATED) | **99.9%** [99.5, 100] | — | 1,215 个 Crossref-DOI namespace probes |
| Chimera 召回率 (≥ ELEVATED) | **100%** [98.9, 100] | — | 339 例 |
| 撤稿召回率 (≥ ELEVATED) | 100% [85.2, 100] | **98.3%** [97.9, 98.6] | ret-6391 = 5,488 Retraction Watch + 903 control |
| FPR | 4.6% [2.6, 7.3] | 4.2% [3.0, 5.7] | matched controls |
| 整体 acc. | 99.1% | 97.9% | |

标签来源外部权威 (Retraction Watch / Crossref / PubMed) 而非作者人工分类；括号为 Clopper-Pearson 95% CI。

### Chimera 压力测试 (3×60 + 60 controls) — **论文 §4.3**

| Split | 检测率 | FPR | 备注 |
|---|---|---|---|
| 作者交换 (author-only) | 60/60 CRITICAL | 0/60 | Crossref-only |
| 年份偏移 -7y (year-only) | 0/60 | 0/60 | **盲点** |
| 组合 (author+year) | 60/60 HIGH | 0/60 | 机制变为 `phantom_doi` |
| 跨域 8 主题 author-swap | 60/60 CRITICAL (每主题 100%) | 0/60 | CV/DL/NLP/NN/量子/材料/能源/气候 |

### 完整性基准 (Golden Test Set, 58 cases) — **论文 §4.2 Table 1**

| 指标 | 优化前 | 优化后 | 说明 |
|------|--------|--------|------|
| 撤稿召回率 (≥ ELEVATED) | 100% (10/10*) | **73.9% (17/23)** | 全量 23 篇撤稿论文，6 篇缺少撤稿标记 |
| 幻觉召回率 (≥ HIGH) | 42.9% | **100% (14/14)** | 14/14 假引用全部检出 |
| 误报率 (real → ≥ ELEVATED) | 0% (0/15) | **0% (0/18)** | 18 篇真论文零误报 |
| Chimera 检测率 | 100% | **100% (3/3)** | 3/3 元数据嵌合体 |
| 幻觉精确率 | 100% | **100%** | 无假阳性 |

\* 优化前仅处理了 10/25 篇撤稿论文（超时）；优化后为全量 58-case 运行。

### Platt 标定 (论文 §4.5)

| 设置 | ECE (10-bin) | Brier | 备注 |
|---|---|---|---|
| Raw L4 hand-set LR | 0.356 | 0.173 | 8,817 案例池 |
| 5-fold CV Platt | **0.003** | 0.014 | 论文主报告 |
| 时序 hold-out (year ≤2023 train / >2023 test) | **0.006** | — | 确认非 in-distribution 过拟合 |

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
| **P0** | NLPCC 2026 提交 | 主稿 12pp R7 8.25 已提交 (2026-05-29) | ✅ 已提交 |
| **P0** | NLPCC 评审应对 | 准备 rebuttal / 新实验（如有要求）| ⏳ 等待 notification |
| **P1** | 选择 ACL workshop 平行投稿 | non-archival，论文已就绪 | ⏳ 待定 |
| **P1** | LLM-tool-degrades 独立工作 | Table 1 现象单独成文，投 ACL/EMNLP workshop | 待规划 |
| **P1** | 数据集扩展 (爬虫) | 7 个爬虫就绪；rv-1926/ret-6391 已扩到外部权威标签 | 🔄 持续 |
| **P1** | 接入 4 个未连接的检测器 | tortured/email/GRIM+statcheck/sneaked → engine | 待做 |
| **P1** | 端到端集成测试 | 用真实论文 fixture 验证完整管线 | 待做 |
| **P2** | FastAPI REST API | 商业化 MVP，5 个核心 endpoint | 待做 |
| **P2** | 跨数据集泛化测试 (L2) | HealthVer, FEVER, ClimateVER | 待做 |
| **P2** | L3 检测器重新调优 | 干净文献集 73.7% FPR (论文 §4.4)，需将 reciprocal/orphan/temporal 拆为低基础发生率信号 | 待做 |
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

1. **系统论文** (**NLPCC 2026**) — L0-L4 全栈 + 消融实验 + CheckIfExist + LLM panel 对比，✅ 2026-05-29 已提交
2. **LLM 引文幻觉检测** (ACL/EMNLP workshop) — 工具增强反而降级 (论文 §4.2 / Table 1) 可作独立故事
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
| [paper/nlpcc2026/main.tex](paper/nlpcc2026/main.tex) | **当前主稿**：12pp LNCS, R7 8.25 |
| [paper/nlpcc2026/review_round_*.json](paper/nlpcc2026/) | 7 轮 self-review (5.0 → 6.0 → 7.0 → 7.0 → 7.5 → 8.0 → 8.25) |
| [paper/gem2026/](paper/gem2026/) | 实验脚本与 `bench_results/` 全部 raw JSON |
| [docs/DEVELOPMENT_NOTES.md](docs/DEVELOPMENT_NOTES.md) | 完整技术记录：架构设计、微调记录、性能优化、Bug 修复 |
| [docs/STRATEGY.md](docs/STRATEGY.md) | 竞品分析、技术路线、商业化路线图 |
| [web/index.html](web/index.html) | 产品展示页 |
| [web/docs.html](web/docs.html) | 在线文档 |
| [ROADMAP.md](ROADMAP.md) | 早期技术路线图 |
| [RESEARCH.md](RESEARCH.md) | 数据源调研报告 |

---

## License

MIT License. See [LICENSE](LICENSE) for details.
