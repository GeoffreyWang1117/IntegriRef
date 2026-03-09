# IntegriRef

**Cross-Domain Reference Integrity Engine**

跨域文档引用完整性验证引擎 — 不是引用检查工具，而是跨学术、专利、法律、金融、政府、标准六大领域的引用完整性基础设施。

---

## 定位

当前市场上的引用检查工具（CiteTrue, SwanRef, Citely, scite.ai）都局限于：
- 仅学术领域
- 仅L0存在性验证（"这篇文献存在吗？"）或L1意图分类（"支持/反对/提及"）
- 不做声明-原文事实对齐
- 不做引用图拓扑异常检测
- 不跨域（专利→论文、判决→法规、标准→标准 等关系完全空白）

**IntegriRef 的目标是 L2-L5 的全栈验证**，在 L0/L1 与竞品持平的同时，提供：

| 验证层级 | 能力 | 竞品现状 | IntegriRef |
|---------|------|---------|------------|
| L0 | 存在性验证 | CiteTrue/SwanRef/Citely | 92个数据源, 14种语言 |
| L1 | 引用意图分类 | scite.ai | NLI模型 + 引用上下文 |
| L2 | 声明-摘要语义对齐 | **无商业产品** | 核心差异化功能 |
| L3 | 引用图拓扑异常检测 | **无商业产品** | CIDRE+PDCN变体 |
| L4 | 跨域引用验证 | **无任何存在** | 六大领域跨库映射 |
| L5 | 假实验/论文工厂检测 | **无任何存在** | 图指纹+数值一致性 |

---

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    IntegriRef Engine                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Format       │  │  Semantic    │  │  Graph           │  │
│  │  Adapters     │  │  Verification│  │  Analysis         │  │
│  │  .bib .pdf    │  │  NLI/LLM    │  │  异常检测/风险评分  │  │
│  │  .docx .djvu  │  │  (计划中)    │  │  (计划中)          │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────────┘  │
│         │                 │                  │              │
│  ┌──────▼─────────────────▼──────────────────▼───────────┐  │
│  │              Core Engine                               │  │
│  │  ICEntity (实体模型) + RegistryDiscovery (智能路由)      │  │
│  └──────────────────────┬────────────────────────────────┘  │
│                         │                                   │
│  ┌──────────────────────▼────────────────────────────────┐  │
│  │              Registry Adapters (31个已实现)             │  │
│  │  Academic(13) Patents(4) Legal(4) Gov(4) Fin(2) Std(4)│  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 三层核心

1. **验证接口层** (Verification Interface Layer)
   - 为每个公开数据库构建统一的 `RegistryAdapter` 适配器
   - 内置速率限制、重试、超时
   - 不缓存原文内容，只返回验证结果

2. **实体标准化层** (Entity Normalization Layer)
   - `ICEntity` — 统一跨库实体模型，12种实体类型
   - 15种标识符格式自动检测 (DOI, arXiv, PMID, 专利号, CELEX, RFC, CVE, ISBN, ORCID...)
   - 跨ID桥接 + 标题模糊匹配消歧

3. **跨库映射层** (Cross-Registry Mapping Layer)
   - 在异构数据库之间建立引用关系图
   - 专利→论文 (NPL引用)、判决→法规、标准→论文 等跨域边
   - 引用图异常检测 (孤岛引用、时序反常、共引偏离、自引环)

---

## 项目结构

```
IntegriRef/
├── core/
│   ├── entity.py          # ICEntity 数据模型 (12种实体类型, ExternalID, 标准化)
│   ├── registry.py        # RegistryAdapter 抽象基类 (rate limiting, HTTP helpers)
│   └── discovery.py       # RegistryDiscovery 智能路由 (15种ID检测, 跨库查询)
│
├── registries/            # 31个已实现的数据库适配器
│   ├── academic/          # (13) CrossRef, arXiv, DBLP, S2, OpenAlex, PubMed,
│   │                      #      ORCID, DOAJ, Unpaywall, CORE, Europe PMC,
│   │                      #      DataCite, OpenCitations
│   ├── patents/           # (4)  USPTO, EPO OPS, WIPO, Lens.org
│   ├── legal/             # (4)  CourtListener, GovInfo, EUR-Lex SPARQL, Federal Register
│   ├── government/        # (4)  World Bank, IMF, WHO GHO, UN Digital Library
│   ├── financial/         # (2)  SEC EDGAR, Federal Register
│   └── standards/         # (4)  NIST NVD, IETF RFC, Wikidata SPARQL, Open Library
│
├── adapters/              # 文档格式适配器
│   ├── base.py            # FormatAdapter 抽象基类
│   ├── bibtex.py          # .bib 解析
│   ├── pdf.py             # PDF (GROBID + PyMuPDF fallback)
│   ├── docx_adapter.py    # DOCX (python-docx)
│   └── text.py            # TXT + DjVu (via djvutxt)
│
├── refcheck.py            # 遗留: 5源级联验证引擎 (将用 Rust 重写)
├── test_registries.py     # 冒烟测试 (31 adapters + 15 ID检测)
├── requirements.txt
├── ROADMAP.md             # 详细技术路线图 + 竞品分析 + 商业模型
└── RESEARCH.md            # 调研报告 (92个数据源, 学术蓝海方法, 多语言覆盖)
```

---

## 当前状态

### 已完成

- **31个 RegistryAdapter** 全部通过冒烟测试
- **ICEntity 数据模型** — 12种实体类型, 跨库ID管理, 标题/作者标准化
- **RegistryDiscovery** — 15种标识符格式自动检测, 智能查询路由
- **格式适配器** — BibTeX, PDF (GROBID), DOCX, DjVu/TXT
- **调研完成** — 92个可接入数据源清单, 5大商业化蓝海, 学术SOTA方法

### 计划中

- **Phase 0.5**: Retraction Watch 集成 (Crossref 扩展, 零成本)
- **Phase 1a**: 7个新英语API (bioRxiv, ClinicalTrials, NASA ADS, INSPIRE, Zenodo, FRED, zbMATH)
- **Phase 1b**: 17个多语言API (HAL, CiNii, J-STAGE, SciELO, Legifrance, etc.)
- **Phase 1c**: OAI-PMH 通用基类 + 7个OAI-PMH源
- **Phase 2**: 语义验证 — NLI模型实现 claim-abstract 对齐 (L2核心功能)
- **Phase 3**: 引用图异常检测 (CIDRE引用环 + PDCN论文工厂)
- **Phase 4**: 风险评分系统 (多维度加权, 按领域可配)
- **Phase 5**: 跨域引用边发现

---

## 数据源覆盖

**92个数据源**, 覆盖 **14种语言/地区**:

| 领域 | 已实现 | 待实现 | 合计 |
|------|:-----:|:-----:|:----:|
| 学术 | 13 | 24 | **37** |
| 专利 | 4 | 4 | **8** |
| 法律 | 6 | 7 | **13** |
| 金融/经济 | 3 | 4 | **7** |
| 政府/国际 | 8 | 6 | **14** |
| 标准/知识库 | 7 | 4 | **11** |
| 研究诚信 | 0 | 2 | **2** |

### 多语言覆盖

| 语言 | 学术 | 专利 | 法律 | 数据源 |
|------|:---:|:---:|:---:|--------|
| 英语 | ★★★ | ★★★ | ★★★ | 53个adapter |
| 法语 | ★★★ | ★★★ | ★★★ | HAL, Persée, theses.fr, INPI, Legifrance |
| 日语 | ★★★ | ★★★ | ★★☆ | CiNii, J-STAGE, EPO(JPO), e-Gov Law |
| 韩语 | ★★☆ | ★★☆ | ★★☆ | KCI, KIPRIS, Korean Law Center |
| 葡语 | ★★★ | ★★☆ | - | SciELO |
| 德语 | ★★☆ | ★★★ | ★★☆ | DDB, EPO(DPMA), Open Legal Data |
| 西语 | ★★☆ | ★★☆ | - | Dialnet, Redalyc (OAI-PMH) |
| 俄语 | ★★☆ | ★★☆ | - | CyberLeninka (OAI-PMH) |
| 中文 | ★★☆ | ★★★ | - | OpenAlex/Crossref间接 + EPO(CNIPA) |

---

## 快速开始

```bash
# 安装依赖
pip install -r requirements.txt

# 运行冒烟测试 (31 adapters + ID检测)
python test_registries.py

# 带实际API调用的测试
python test_registries.py --live
```

### 基本用法

```python
from core.entity import ICEntity, EntityType
from core.discovery import RegistryDiscovery
from registries.academic import ALL_ACADEMIC

# 初始化发现引擎
discovery = RegistryDiscovery()
discovery.register_all(ALL_ACADEMIC)

# 自动检测标识符类型并查询
results = discovery.query_by_id("10.1038/nature12373")  # DOI → CrossRef
results = discovery.query_by_id("2301.12345")           # arXiv ID → arXiv
results = discovery.query_by_id("CVE-2023-44487")       # CVE → NIST NVD

# 高级验证 (ID查询 → 标题搜索 fallback)
report = discovery.verify_reference(
    title="Attention Is All You Need",
    identifier="10.5555/3295222.3295349",
    domains=["academic"]
)
print(report["found"])            # True
print(report["registries_hit"])   # ["crossref", "semantic_scholar", ...]
```

---

## 核心设计原则

1. **不下载、不存储原文** — 只做验证、标准化、映射
2. **每个 Adapter 都是独立的** — 统一 `RegistryAdapter` 接口, 可插拔
3. **速率限制内置** — 每个 adapter 自动遵守 API 配额
4. **ICEntity 是唯一的交换格式** — 所有 adapter 的输出都归一化为 ICEntity
5. **基础功能必须与竞品持平** — L0/L1 是入场券, L2+ 是差异化

---

## 验证层级详解

### L0: 存在性验证 (竞品基线)
> "Smith (2020) 这篇文献存在吗？元数据对吗？"

- ID解析 (DOI/arXiv/PMID → 官方记录)
- 标题+作者+年份模糊匹配
- 元数据交叉验证 (多库一致性)
- **撤稿检测** (Retraction Watch via Crossref)

### L1: 引用意图分类 (追平 scite.ai)
> "这是支持性引用、反对性引用、还是背景引用？"

- 引用上下文提取 (citation sentence + ±1 句)
- NLI 模型分类 (supporting / contrasting / mentioning)
- 零样本可用, 微调可提升

### L2: 声明-摘要语义对齐 (核心差异化, 无竞品)
> "本文说 Smith 研究了 X, 但 Smith 的摘要说的是 Y — 标记为高风险"

- 从引用上下文提取 claim (对引文做了什么声明)
- 获取被引文献的摘要 (via S2/CrossRef/OpenAlex)
- NLI 对齐: claim vs abstract → ENTAILMENT / NEUTRAL / CONTRADICTION
- **CONTRADICTION = 自动高风险标记**

### L3: 引用图拓扑异常 (无竞品)
> "这篇论文的引用图结构是否异常？"

- 孤岛引用检测 (引文之间零互连)
- 时序反常检测 (引用未来论文)
- 共引偏离分析 (引用组合偏离领域基线)
- 自引环检测 (CIDRE 变体)

### L4: 跨域引用验证 (无竞品)
> "专利中引用的论文、判决中引用的法规 — 跨库验证"

### L5: 假实验/论文工厂检测 (无竞品)
> "PDCN论文工厂指纹 + 跨论文数值一致性"

---

## 关键文件

| 文件 | 用途 |
|------|------|
| `core/entity.py` | ICEntity 数据模型 — 所有 adapter 的输出归一化格式 |
| `core/registry.py` | RegistryAdapter 抽象基类 — 所有 adapter 的实现契约 |
| `core/discovery.py` | RegistryDiscovery — 智能路由 + 15种ID检测 |
| `registries/*/` | 31个数据库适配器 (按领域分组) |
| `adapters/` | 文档格式适配器 (BibTeX/PDF/DOCX/DjVu) |
| `ROADMAP.md` | 详细技术路线图 + 竞品分析 + 商业模型 + 评分系统设计 |
| `RESEARCH.md` | 92个数据源调研 + 学术蓝海方法 + 多语言覆盖矩阵 |

---

## 竞品对比

| | CiteTrue | SwanRef | Citely | scite.ai | **IntegriRef** |
|---|:---:|:---:|:---:|:---:|:---:|
| 存在性验证 (L0) | 16库 | 2库 | 3库 | 1.6B引用 | **92源** |
| 意图分类 (L1) | - | - | - | DL模型 | NLI模型 |
| 语义对齐 (L2) | - | - | - | - | **NLI+LLM** |
| 图异常 (L3) | - | - | - | - | **CIDRE+PDCN** |
| 跨域验证 (L4) | - | - | - | - | **六大领域** |
| 多语言 | 英语 | 英语 | 英语 | 英语 | **14种语言** |
| 专利/法律/金融 | - | - | - | - | **全覆盖** |
| 撤稿检测 | - | - | - | 有 | **有** |

---

## 相关项目

- **refcheck-oss** (`refcheck-oss-v1.0.zip`) — 开源CLI版本 (MIT), 纯学术BibTeX验证
- **ref-check SaaS** (`~/Tools/ref-check/app/`) — FastAPI Web平台原型

---

## License

Proprietary. All rights reserved.
