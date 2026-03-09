# IntegriRef — 跨域引用完整性验证引擎

## 定位

**一句话：** 不是引用检查工具，而是跨域文档引用完整性的基础设施层。

**核心架构三层：**
- 验证接口层（Verification Interface Layer）— 统一对接各公开档案库的查询协议
- 实体标准化层（Entity Normalization Layer）— 将不同库中同一实体映射为唯一标识
- 跨库映射层（Cross-Registry Mapping Layer）— 在异构档案库之间建立引用关系图

**不做什么：** 不下载、不存储任何库的内容。只做验证、标准化、映射。

---

## 竞品分析（2026.03）

### 学术赛道

| 工具 | 能力级别 | 核心技术 | 定价 | 短板 |
|------|---------|---------|------|------|
| CiteTrue | L0 存在性验证 | 16库API+LLM | 免费+限额 | 纯元数据,不做语义 |
| SwanRef | L0 | CrossRef+GScholar | 免费 | 仅2库,覆盖窄 |
| Citely | L0 | CrossRef/PubMed/arXiv | 积分制 | 95%准确率=5%错 |
| scite.ai | L1 引用意图分类 | 深度学习,1.6B引用语句 | $12-16/月 | 不验证声明真实性 |
| SemanticCite | L2 语义对齐(原型) | LLM+混合检索 | 开源 | 学术原型,未产品化 |
| **IntegriRef** | **L3 图+实验+跨域** | 图建模+语义对齐+风险评分 | SaaS | **目标位置** |

### 关键空白

1. **无产品做声明-原文事实对齐**（scite只分类意图,不验证事实）
2. **无产品做引用图拓扑异常检测**（仅有学术论文,无产品实现）
3. **无产品覆盖学术之外的引用验证**（专利/法律/金融/标准完全空白）
4. **假实验检测是无人区**

---

## 目标市场：远超学术

### Tier 1 — 高付费意愿市场（优先）

| 领域 | 文档类型 | 引用什么 | 公开档案库 | 年市场规模(估) |
|------|---------|---------|-----------|-------------|
| **专利** | 专利申请/审查 | 在先技术(prior art)、标准、论文 | USPTO/EPO/WIPO PatentScope, Google Patents | $2B+ (IP服务) |
| **法律** | 判决书/法律备忘录 | 判例、法规、条例 | CourtListener, GovInfo, EUR-Lex, 裁判文书网 | $1B+ (法律科技) |
| **金融** | SEC filing/年报/ESG报告 | 财务标准、监管规则、审计依据 | EDGAR, XBRL, Basel Committee | $3B+ (RegTech) |
| **政府** | 白皮书/政策文件 | 法规、统计数据、国际协议 | GovInfo, data.gov, UN Digital Library | $500M+ |

### Tier 2 — 中等付费意愿

| 领域 | 文档类型 | 引用什么 | 公开档案库 |
|------|---------|---------|-----------|
| **标准** | ISO/IEEE/NIST 标准文档 | 其他标准、测试方法 | standards.iso.org, IEEE Xplore (部分), NIST |
| **医药** | FDA提交/临床试验报告 | 临床数据、指南 | ClinicalTrials.gov, PubMed, FDA Orange Book |
| **工程** | 技术规范/安全报告 | 标准、测试结果 | ASTM, UL Standards |

### Tier 3 — 学术（低价高量,用于获客和口碑）

| 领域 | 文档类型 | 公开档案库 |
|------|---------|-----------|
| 论文 | .bib/.pdf/.docx | arXiv, CrossRef, DBLP, S2, OpenAlex, PubMed |
| 学位论文 | PDF | ProQuest (部分), 各大学机构库 |

---

## 三层架构详解

### Layer 1: 验证接口层（Verification Interface Layer）

**目标：** 为每个公开档案库构建统一的查询适配器,屏蔽协议差异。

```
VerificationInterface
├── AcademicRegistries
│   ├── CrossRefAdapter      (REST, DOI → metadata)
│   ├── ArXivAdapter         (Atom/XML, ID → metadata)
│   ├── DBLPAdapter          (REST/JSON, title → metadata)
│   ├── SemanticScholarAdapter (REST, title/ID → metadata + citations)
│   ├── OpenAlexAdapter      (REST, entity search → full graph)
│   ├── PubMedAdapter        (E-utilities API, PMID → metadata)
│   └── ORCIDAdapter         (REST, author disambiguation)
│
├── PatentRegistries
│   ├── USPTOAdapter         (PatentsView API, patent# → prior art refs)
│   ├── EPOAdapter           (OPS API, EP# → citations + legal status)
│   ├── WIPOAdapter          (PatentScope API, PCT# → metadata)
│   └── GooglePatentsAdapter (web scrape fallback)
│
├── LegalRegistries
│   ├── CourtListenerAdapter (REST, case → opinions + cited cases)
│   ├── GovInfoAdapter       (USGPO API, statute → full text)
│   ├── EURLexAdapter        (SPARQL/REST, CELEX → legal refs)
│   └── CaseLawAccessAdapter (Harvard, bulk case data)
│
├── FinancialRegistries
│   ├── EDGARAdapter         (SEC full-text search, CIK/ticker → filings)
│   ├── XBRLAdapter          (inline XBRL → structured financials)
│   └── BaselAdapter         (regulatory framework references)
│
├── StandardsRegistries
│   ├── ISOAdapter           (OBP API, ISO# → metadata + references)
│   ├── IEEEXploreAdapter    (API, standard# → metadata, 部分免费)
│   ├── NISTAdapter          (CSRC API, SP/FIPS → metadata)
│   └── RFCAdapter           (IETF Datatracker, RFC# → metadata + refs)
│
└── GovernmentRegistries
    ├── DataGovAdapter       (CKAN API, dataset → metadata)
    ├── UNDigitalLibAdapter  (OAI-PMH, doc → metadata)
    └── WorldBankAdapter     (API, indicator → data series)
```

**关键设计原则：**
- 每个 Adapter 实现统一的 `query(identifier) → RegistryRecord` 接口
- 内置速率限制、重试、超时
- 返回标准化的 `RegistryRecord`（见 Layer 2）
- **不缓存原文内容**，只缓存验证结果的哈希摘要

### Layer 2: 实体标准化层（Entity Normalization Layer）

**问题：** 同一份文献在不同库中以不同形式存在。

```
同一论文:
  CrossRef:  DOI 10.1234/example
  arXiv:     2301.12345
  DBLP:      conf/icml/Smith23
  S2:        abc123def456
  OpenAlex:  W1234567890
  PubMed:    PMID 39876543

同一专利:
  USPTO:     US11,234,567B2
  EPO:       EP3456789A1
  WIPO:      WO2023/012345

同一法规:
  GovInfo:   PLAW-117publ167
  EUR-Lex:   32022R2065
  通俗名:    "CHIPS Act"
```

**标准化实体模型：**

```
IntegriRef Canonical Entity (ICE)
├── ice_id: "ice:xxxxxxxxxxxx"    (全局唯一ID)
├── type: paper | patent | case | statute | standard | dataset | report
├── identifiers:                   (所有已知外部ID)
│   ├── doi: "10.1234/..."
│   ├── arxiv: "2301.12345"
│   ├── pmid: "39876543"
│   ├── patent_number: "US11234567B2"
│   ├── case_cite: "410 U.S. 113"
│   └── ...
├── normalized_title: "..."        (Unicode NFC, 去LaTeX, 去标点)
├── normalized_authors: [...]      (姓名标准化, ORCID关联)
├── year: 2023
├── type_specific_metadata: {...}  (领域特定字段)
└── registry_sources: [...]        (在哪些库中被发现)
```

**实体消歧策略：**
1. **ID直接映射**：DOI↔CrossRef, arXiv ID↔arXiv, PMID↔PubMed — 确定性映射
2. **跨ID桥接**：OpenAlex 和 S2 都存储 DOI+arXiv+PMID — 用它们做桥
3. **模糊匹配兜底**：标题 token Jaccard ≥ 0.85 + 第一作者姓氏匹配 + 年份 ±1 → 合并
4. **专利族映射**：USPTO/EPO/WIPO 通过 priority number 关联同一发明的不同国家申请
5. **法规版本链**：同一法规的修订版本形成有向链（Amendment → Original）

### Layer 3: 跨库映射层（Cross-Registry Mapping Layer）

**这是核心壁垒。** 不是简单查API,而是在异构档案库之间建立引用关系图。

```
                    ┌──────────────────────┐
                    │   跨库引用图 (CRG)     │
                    │   Cross-Registry      │
                    │   Reference Graph     │
                    └──────────┬───────────┘
                               │
          ┌────────────────────┼────────────────────┐
          │                    │                    │
    ┌─────▼─────┐      ┌──────▼──────┐      ┌─────▼─────┐
    │  学术子图   │      │  专利子图    │      │  法规子图   │
    │ (OpenAlex  │◄────►│ (USPTO/EPO  │◄────►│(CourtListener│
    │  citation  │      │  prior art  │      │ case cited │
    │  graph)    │      │  refs)      │      │ by)        │
    └─────┬─────┘      └──────┬──────┘      └─────┬─────┘
          │                    │                    │
          └────────────────────┼────────────────────┘
                               │
                    ┌──────────▼───────────┐
                    │  跨域引用边 (关键!)     │
                    │                       │
                    │  专利 → 论文 (NPL ref) │
                    │  判决 → 法规           │
                    │  标准 → 论文           │
                    │  白皮书 → 统计数据     │
                    │  SEC filing → 标准    │
                    └───────────────────────┘
```

**跨域边的发现方式：**

| 跨域引用 | 发现方式 |
|---------|---------|
| 专利 → 论文 | USPTO PatentsView NPL (Non-Patent Literature) 字段 |
| 专利 → 标准 | 专利说明书中 "in accordance with ISO xxx" |
| 判决 → 法规 | CourtListener opinion 中的 statute citations |
| 判决 → 判决 | CourtListener cited_by 关系 |
| SEC filing → 标准 | EDGAR full-text 中 "FASB ASC xxx" / "IFRS xx" |
| ISO标准 → ISO标准 | ISO OBP normative references 字段 |
| IEEE标准 → 论文 | IEEE Xplore bibliography section |
| 白皮书 → 统计 | data.gov dataset citations |
| 论文 → 专利 | OpenAlex 'referenced_works' 中的 patent DOIs |

---

## 技术深度：图结构建模

### 引用图异常检测信号

**1. 孤岛引用 (Orphan Cluster)**
```
正常:                          异常(论文工厂特征):
  A ─── B ─── C                    A
  │ ╲   │   ╱ │                  ╱ │ ╲
  │   ╲ │  ╱  │           ref1  ref2  ref3
  D ─── E ─── F            │     │     │
  (密集互引=同领域)          ×     ×     ×
                          (引文之间零连接)
```
实现: 从 OpenAlex 拉目标论文的 references → 再拉每篇 reference 的 references → 计算子图密度。密度 < 阈值 → 标记。

**2. 时序反常 (Temporal Anomaly)**
```
引用链: Paper(2023) → cites → Paper(2019) → cites → Paper(2015)  ✓ 正常
引用链: Paper(2023) → cites → Paper(2022) → claims method from → Paper(2025)  ✗ 时间倒流
```
实现: 构建有向无环图(DAG),检测环或方法声明的时间一致性。

**3. 共引偏离 (Co-citation Deviation)**
```
领域基线: {PaperA, PaperB} 共引频率 = 0.72 (经常一起被引用)
目标论文: 引用了A但没引用B,且引用了完全不相关的C
→ 共引偏离分数高 → 可能对领域文献不熟悉或刻意回避
```
实现: 用 OpenAlex co-citation 数据构建领域共引矩阵,计算目标论文引用集合的偏离度。

**4. 自引环 (Citation Ring)**
```
  Journal_X ──大量引用──► Journal_Y
       ▲                      │
       │                      │
       └──────大量引用─────────┘
```
实现: CIDRE 算法变体 — 构建期刊/作者级引用图,检测异常互引密度。

### 语义对齐验证

**三层验证深度:**

```
浅层 (L0): "Smith (2020) 存在吗?"
          → API 查询 → 存在/不存在

中层 (L1): "本文说 Smith (2020) 研究了 X, Smith 真的研究了 X 吗?"
          → 提取 claim → 获取 Smith 的 abstract → embedding 对齐
          → SUPPORTED / UNSUPPORTED

深层 (L2): "本文说 Smith (2020) 在 CIFAR-10 上达到 95.3%, 数字对吗?"
          → 提取 claim 中的数值 → 解析 Smith 全文中的 Table/Figure
          → EXACT_MATCH / CLOSE / MISMATCH / NOT_FOUND
```

深层验证是杀手锏 — 当前无任何产品触及。

---

## 风险评分系统

```
IntegriRef Integrity Score: 87/100

┌─ 元数据准确性 ──────────────── 95/100
│  └─ 所有引文ID、作者、年份与官方记录一致
│
├─ 引用存在性 ────────────────── 98/100
│  └─ 47/48 引文在至少一个公开库中被确认
│
├─ 引用图健康度 ──────────────── 72/100  ⚠
│  ├─ 共引一致性    80  (引用组合符合领域模式)
│  ├─ 时序合理性    90  (引用时间链无异常)
│  ├─ 子图密度      60  ⚠ 引文之间互引偏少
│  └─ 自引/互引     58  ⚠ 检测到3篇来自同一小团体
│
├─ 语义对齐度 ────────────────── 85/100
│  ├─ 意图分类      92  (引用上下文与实际关系一致)
│  ├─ 声明-原文     78  ⚠ 3处声明在原文中缺乏支撑
│  └─ 数值一致性    85  (实验数字与来源一致)
│
└─ 诚信风险 ──────────────────── 92/100
   ├─ 撤稿引用      100 (无)
   ├─ 论文工厂指纹   84  ⚠ 2篇来自可疑期刊
   └─ 标准/法规有效性 92  (引用的标准均未被废止)
```

**各市场的评分权重配置:**

| 维度 | 学术投稿 | 专利审查 | 法律文书 | SEC Filing | ISO标准 |
|------|:-------:|:-------:|:-------:|:--------:|:------:|
| 元数据 | 0.15 | 0.20 | 0.25 | 0.20 | 0.20 |
| 存在性 | 0.20 | 0.30 | 0.30 | 0.25 | 0.25 |
| 图健康 | 0.20 | 0.10 | 0.05 | 0.05 | 0.15 |
| 语义对齐 | 0.30 | 0.25 | 0.25 | 0.30 | 0.25 |
| 诚信风险 | 0.15 | 0.15 | 0.15 | 0.20 | 0.15 |

---

## 实施路线图

### Phase 0 — 夯实地基 (当前 → 1个月)
- [x] 5源学术API级联验证引擎 (refcheck.py)
- [x] 多格式适配器 (PDF/DOCX/DjVu/TXT)
- [x] SaaS Web 平台原型 (FastAPI + 前端)
- [ ] 开源 refcheck-oss-v1.0,积累GitHub星标
- [ ] 添加 PubMed adapter (E-utilities API)
- [ ] 添加 ORCID adapter (作者消歧)

### Phase 1 — 实体标准化层 (1-3个月)
- [ ] 设计 ICE (IntegriRef Canonical Entity) 数据模型
- [ ] 实现跨ID桥接: DOI ↔ arXiv ↔ PMID ↔ OpenAlex ID ↔ S2 ID
- [ ] 标题+作者模糊消歧引擎
- [ ] 实体合并置信度评分

### Phase 2 — 第一个高价值垂直领域: 专利 (3-6个月)
- [ ] USPTO PatentsView API adapter (免费, 覆盖全部美国专利)
- [ ] EPO Open Patent Services adapter
- [ ] NPL (Non-Patent Literature) 引用提取 → 链接回学术子图
- [ ] 专利族映射 (同一发明的多国申请)
- [ ] Prior art 引用完整性评分
- **目标客户:** 专利事务所、企业IP部门 ($50-200/月/席位)

### Phase 3 — 引用图建模 (6-9个月)
- [ ] 利用 OpenAlex bulk data 构建二阶引用图
- [ ] 孤岛引用检测算法
- [ ] 时序反常检测
- [ ] 共引偏离分析
- [ ] 自引环/互引小团体检测 (CIDRE变体)

### Phase 4 — 语义对齐验证 (9-12个月)
- [ ] 引用意图分类模型 (SciBERT fine-tune)
- [ ] Claim 提取 pipeline (从引文上下文提取声明)
- [ ] Claim-Source embedding 对齐
- [ ] LLM精确验证 (对低置信度 case 调用)
- [ ] 数值/实验结果一致性检查

### Phase 5 — 法律+金融+标准 (12-18个月)
- [ ] CourtListener adapter (美国判例法)
- [ ] EDGAR/SEC adapter (金融披露)
- [ ] ISO OBP adapter (标准引用)
- [ ] 跨域引用边发现
- [ ] 领域特定风险评分权重

### Phase 6 — 假实验检测 (18-24个月)
- [ ] 方法-数据集-结果 三元组提取
- [ ] 跨论文数值一致性验证
- [ ] 论文工厂拓扑指纹检测
- [ ] 可复现性风险评分

---

## 商业模型

### SaaS 定价

| 层级 | 价格 | 目标 | 核心功能 |
|------|------|------|---------|
| Free | $0 | 学术开发者/口碑 | 5次/月, 仅学术库, L0验证 |
| Researcher | $14.99/月 | 个人研究者 | 无限次, 全格式, 图分析, 语义L1 |
| Professional | $49.99/月 | 专利律师/分析师 | +专利/法律库, 语义L2, API |
| Enterprise | 定制 | 出版社/专利局 | +私有部署, 批量API, SLA, 审计 |

### B2B API 定价
- $0.10/次 (L0 存在性验证)
- $0.50/次 (L1 + 图分析)
- $2.00/次 (L2 全语义 + 跨域)

### 目标客户优先级 (按付费意愿排序)
1. **专利事务所** — 每份 prior art search 收费 $3000-10000,工具费对他们是零头
2. **出版社** (Elsevier/Springer/Wiley) — 投稿审核流程集成
3. **合规部门** (银行/药企) — 监管文件引用合规
4. **法律科技平台** — 判例引用验证
5. **学术机构** — 价格敏感但量大,用于口碑

---

## 与现有 ref-check 的关系

```
refcheck-oss-v1.0.zip          → GitHub 开源, MIT License
  (纯CLI, .bib验证)               用于积累声望和社区

~/Tools/ref-check/app/         → 保留为 SaaS 演示原型
  (FastAPI Web 平台)               用于展示和早期获客

~/Projects/IntegriRef/         → 闭源商业核心
  (本项目)                        三层架构的完整实现
  refcheck.py                    暂时复用, 未来用 Rust 重写
  adapters/                      格式适配层, 持续扩展
  (未来) graph/                  引用图建模
  (未来) semantic/               语义对齐
  (未来) scoring/                风险评分
  (未来) registries/             各领域档案库适配器
```
