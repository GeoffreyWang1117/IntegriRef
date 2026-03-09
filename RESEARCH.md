# IntegriRef — 调研报告汇总

## Part A: 免费公共 API 完整清单

### 学术 (13 API, 全部已实现 adapter)

| API | Base URL | Auth | Rate | Coverage |
|-----|----------|------|------|----------|
| CrossRef | `api.crossref.org` | None (mailto推荐) | 50 rps polite | 167M+ DOI works |
| arXiv | `export.arxiv.org/api/query` | None | 1 rps (建议3s间隔) | 2.4M+ preprints |
| DBLP | `dblp.org/search/publ/api` | None | HTTP 429 限制 | 7M+ CS pubs |
| Semantic Scholar | `api.semanticscholar.org/graph/v1` | 免费key可选 | 100/5min 无key; 1rps有key | 225M+ papers |
| OpenAlex | `api.openalex.org` | 免费account (credit制) | credit-based | 240M+ works |
| PubMed E-utils | `eutils.ncbi.nlm.nih.gov/entrez/eutils` | 免费key可选 | 3rps无key; 10rps有key | 37M+ citations |
| ORCID | `pub.orcid.org/v3.0` | OAuth (免费) | 日配额制 (2025改) | 20M+ profiles |
| DOAJ | `doaj.org/api/v4` | None | ~2 rps | 21K+ journals, 10M+ articles |
| Unpaywall | `api.unpaywall.org/v2` | Email参数 | 100K/day | 130M+ DOIs OA状态 |
| CORE | `api.core.ac.uk/v3` | 免费key必需 | 5 req/10s | 300M+ metadata |
| Europe PMC | `ebi.ac.uk/europepmc/webservices/rest` | None | ~10 rps | 44M+ records |
| DataCite | `api.datacite.org` | None | 3000/5min | 60M+ DOIs (数据集) |
| OpenCitations | `api.opencitations.net/index/v2` | 免费token可选 | 180/min | 2B+ citation links |

### 专利 (4 API, 全部已实现)

| API | Base URL | Auth | Rate | Coverage |
|-----|----------|------|------|----------|
| USPTO PatentsView | `search.patentsview.org/api/v1` | 免费key | 45/min | 全部US专利 |
| EPO OPS | `ops.epo.org/3.2/rest-services` | OAuth (免费注册) | 4GB/月 | 130M+ 全球专利 |
| WIPO PatentScope | SOAP API | 付费订阅 (限制) | 未公开 | 120M+ from 75+ offices |
| Lens.org | `api.lens.org` (POST) | 免费token (学术) | 按订阅 | 200M+学术 + 140M+专利 |

### 法律 (4 API + 2 发现, 已实现4个)

| API | Base URL | Auth | Rate | Coverage |
|-----|----------|------|------|----------|
| CourtListener | `courtlistener.com/api/rest/v4` | 免费token (v4.3必需) | 5000/hr | 12M+ US court opinions |
| GovInfo | `api.govinfo.gov` | api.data.gov key | 1000/hr | 3M+ US gov docs |
| EUR-Lex SPARQL | `publications.europa.eu/webapi/rdf/sparql` | None | 1M rows/query | EU法律全部 |
| Federal Register | `federalregister.gov/api/v1` | None | 未公开 | 1994至今全部 |
| CanLII (待实现) | `api.canlii.org/v1` | 免费key | 未公开 | 加拿大判例法 |
| HUDOC (无官方API) | URL-based JSON (非官方) | None | 未公开 | 10万+ ECHR文档 |

### 金融 (3 API, 已实现2个)

| API | Base URL | Auth | Rate | Coverage |
|-----|----------|------|------|----------|
| SEC EDGAR | `data.sec.gov` / `efts.sec.gov/LATEST` | User-Agent必需 | 10 rps | 21M+ filings |
| XBRL US (待实现) | `api.xbrl.us/api/v1` | 免费key | 未公开 | 全部SEC XBRL |
| CFPB (待实现) | `consumerfinance.gov/.../api/v1` | None | 未公开 | 5M+ complaints |

### 政府/国际组织 (8 API, 已实现4个)

| API | Base URL | Auth | Rate | Coverage |
|-----|----------|------|------|----------|
| World Bank | `api.worldbank.org/v2` | None | 无限制 | 16K+ indicators, 200+ countries |
| IMF | `data.imf.org/api` | None | 10/5s | 190+ countries 宏观经济 |
| WHO GHO | `ghoapi.azureedge.net/api` | None | 未公开 (2025底废弃) | 2300+ health indicators |
| UN Digital Library | OAI-PMH (无官方REST) | None | 未公开 | UN文档 |
| data.gov (US, 待实现) | `catalog.data.gov/api/3/action` | api.data.gov key | 1000/hr | 300K+ datasets |
| UK data.gov.uk (待实现) | `data.gov.uk/api/action` | None | 无限制 | 50K+ datasets |
| EU Open Data (待实现) | `data.europa.eu/api/hub/repo` | None | 未公开 | 1.7M+ datasets |
| OECD (待实现) | `sdmx.oecd.org/public/rest` | None | 60/hr | 2000+ datasets |

### 标准/知识库 (7 API, 已实现4个)

| API | Base URL | Auth | Rate | Coverage |
|-----|----------|------|------|----------|
| NIST NVD | `services.nvd.nist.gov/rest/json` | 免费key可选 | 5/30s无key; 50/30s有key | 250K+ CVEs |
| IETF Datatracker | `datatracker.ietf.org/api/v1` | None | 未公开 | 9K+ RFCs |
| Wikidata SPARQL | `query.wikidata.org/sparql` | None (UA必需) | 60s处理/min | 110M+ items |
| Open Library | `openlibrary.org` | None | 1-3 rps | 45M+ editions |
| W3C (待实现) | `api.w3.org` | None | 6000/10min | 全部W3C规范 |
| Library of Congress (待实现) | `loc.gov/?fo=json` / `api.congress.gov/v3` | api.data.gov key | 5000/hr | 全部国会文档 |
| Internet Archive (待实现) | `archive.org/services/search/v1/scrape` | None | HTTP 429限流 | 835B+ 网页 |

### 无API的重要数据源
- **ISO OBP**: 无公开API，仅网页浏览，全文收费
- **ITU**: 无开发者API
- **NIST CSRC**: 无REST API (仅网页)
- **Google Patents**: 无官方API (BigQuery替代，1TB/月免费)
- **HathiTrust**: Data API已于2024.7废弃，仅剩Bibliographic API

---

## Part B: 跨域实体图谱学术蓝海方法

### 1. 跨域实体对齐 (Entity Alignment)

| 论文 | 年份/会议 | 核心方法 | 对IntegriRef的意义 |
|------|----------|---------|-------------------|
| **EasyEA** | 2025 ACL Findings | 端到端LLM EA，零训练数据，三阶段(摘要→嵌入→候选) | **直接用于Phase 1**：零标注数据下跨库实体映射 |
| **LLM4EA** | 2024 NeurIPS | 主动学习+概率推理去噪LLM标注 | 优先消歧哪些模糊实体对 |
| **OneNet** | 2024 EMNLP | Few-shot实体链接(三模块: 缩减→链接→共识) | 扩展到新领域(法律/专利)时标注数据稀缺 |

**关键发现**: EasyEA (2025) 在零训练数据下达到SOTA — IntegriRef的ICE实体标准化层可直接采用此方法，无需人工标注。

### 2. 引用图异常检测

| 论文 | 年份/会议 | 核心方法 | 对IntegriRef的意义 |
|------|----------|---------|-------------------|
| **PDCN** | 2025 JDIS | 异构图注意力+BERT+LGBM | Phase 6 论文工厂检测(81.85%准确率) |
| **CIDRE** | 2021 Sci Reports | 零模型+异常互引检测 | Phase 3 引用环检测(>50%被除名期刊) |
| **ACTION** | 2024 AI Review | NMF+网络表示学习 | 个体引用级别异常检测 |
| **GLAD** | 2022 IEEE TNNLS | GNN+文本语义挖掘 | 验证文本+图结构结合的有效性 |
| **DURENDAL** | 2025 ICLR | 时序异构图学习 | 引用图是动态演化的，需要时序建模 |
| **Deep GAD Survey** | 2025 IEEE TKDE | 13类图异常检测方法分类 | Phase 3 的方法选择参考 |

**关键发现**: PDCN+CIDRE组合代表当前最佳，但**没有系统将两者与时序建模和跨域分析结合**——这是IntegriRef的机会。

### 3. 语义对齐/声明验证

| 论文 | 年份/会议 | 核心方法 | 对IntegriRef的意义 |
|------|----------|---------|-------------------|
| **SemanticCite** | 2025 arXiv | 4阶段pipeline, 微调Qwen3(1.7B/4B) | **最接近L2的现有系统**，但是学术原型 |
| **CiteGuard** | 2025 NeurIPS | RAG验证+agent架构 | agent架构和成本模型($0.005/citation)参考 |
| **Valsci** | 2025 BMC Bioinfo | RAG+书目评分+CoT | 最具产品化潜力的开源声明验证 |
| **MedRAGChecker** | 2026 arXiv | 原子声明分解+NLI+KG一致性 | L2验证的方法论参考(原子声明+KG) |
| **SCitance** | 2024 EMNLP SDP | 零样本验证,用引用上下文替代全文 | **解决"没有全文怎么验证"的问题** |

**关键发现**: SCitance证明**不需要全文就能做L1验证**——用S2/scite的citation context作为全文代理。IntegriRef应优先实现此路径。

### 4. 跨域引用图

| 论文/数据集 | 年份/会议 | 内容 | 对IntegriRef的意义 |
|-----------|----------|------|-------------------|
| **PubMed KG 2.0** | 2025 Nature Sci Data | 36M论文+1.3M专利+0.48M临床试验统一KG | **唯一的产品级跨域引用图**，但仅限生物医学 |
| **USPTO Office Actions NPL Dataset** | 2026 Nature Sci Data | 85万条专利审查→论文引用 | Phase 2 专利→论文桥接的直接数据源 |
| **LeCNet** | 2025 JUST-NLP | 26K案例节点+67K引用边(印度司法) | Phase 5 法律引用图的参考架构 |
| **Legal Citation Prediction** | 2025 Springer | 异构图GNN法律引用预测 | 验证异构图方法在法律域的可行性 |

**关键发现**: **没有任何现有工作构建覆盖学术+专利+法律+标准+金融的统一图。** PubMed KG 2.0仅覆盖生物医学。这是IntegriRef的核心蓝海。

---

## Part C: 五大商业化蓝海

| # | 蓝海领域 | 学术成熟度 | 商业竞争 | IntegriRef Phase | 市场准备度 |
|---|---------|:---:|:---:|:---:|:---:|
| 1 | **声明-原文事实对齐** | 高 (SemanticCite, Valsci, F1>0.75) | **零** (scite只分类不验证) | Phase 4 | HIGH |
| 2 | **引用图拓扑异常检测** | 高 (PDCN, CIDRE, 验证有效) | **零** (仅文本模式) | Phase 3 | MED-HIGH |
| 3 | **跨域引用验证** | 中 (PubMed KG证可行) | **零** (无产品跨域) | Phase 2+5 | MEDIUM |
| 4 | **论文工厂SaaS检测** | 高 (PDCN 81.85%准确率) | **近零** (STM仅联盟内) | Phase 6 | HIGH (出版社急需) |
| 5 | **无全文声明验证** | 高 (SCitance零样本) | **零** | Phase 4 | HIGH |

### IntegriRef的架构定位

```
               现有竞品边界
                    │
  ┌─────────────────│──────────────────────────────┐
  │ L0: 存在性验证  │  CiteTrue / SwanRef / Citely │
  ├─────────────────│──────────────────────────────┤
  │ L1: 意图分类    │  scite.ai                    │
  ├─────────────────┼──────────────────────────────┤
  │ L2: 语义对齐    │  (无商业产品)  SemanticCite原型│ ← IntegriRef Phase 4
  ├─────────────────┼──────────────────────────────┤
  │ L3: 图结构分析  │  (无商业产品)  PDCN/CIDRE论文 │ ← IntegriRef Phase 3
  ├─────────────────┼──────────────────────────────┤
  │ L4: 跨域验证    │  (无任何存在)                 │ ← IntegriRef Phase 2+5
  ├─────────────────┼──────────────────────────────┤
  │ L5: 假实验检测  │  (无任何存在)                 │ ← IntegriRef Phase 6
  └─────────────────┴──────────────────────────────┘
```

**结论: IntegriRef的三层架构(验证接口→实体标准化→跨库映射)在L2-L5范围内面临零商业竞争。**

---

## 实施优先级建议

1. **Phase 1**: 用EasyEA(零标注)实现ICE实体消歧
2. **Phase 2**: 用USPTO NPL Dataset(2026)构建专利→论文桥
3. **Phase 3**: 先CIDRE(引用环), 后PDCN(论文工厂)
4. **Phase 4**: 用SCitance路径实现无全文L1验证; 用Valsci的RAG管道做有全文L2
5. **Phase 5**: 用LeCNet参考架构做法律子图

---

## Part D: 多语言/非英文公共数据库 API 清单

以下仅列出具有**真实程序化接口**(REST, SOAP, OAI-PMH, SPARQL)的数据源，不含仅提供网页抓取的数据库。

### 跨语言/全球性聚合 API (已有 adapter 可扩展)

这些已在 Part A 中列出的 API 本身就覆盖大量非英文文献，应作为多语言接入的**首选路径**：

| API | 非英文覆盖能力 | 备注 |
|-----|--------------|------|
| OpenAlex | 240M+ works，含 `language` 字段可按语言筛选 | 已收录 CNKI/J-STAGE/SciELO/HAL 等源的元数据 |
| Crossref | 167M+ works，含中日韩法德西葡等语言的 DOI 注册 | 直接通过 DOI 解析 |
| EPO OPS | 130M+ 全球专利文档(100+ 国家)，含中日韩德法俄等 | 已有 adapter，覆盖 CNIPA/JPO/KIPO/DPMA 等 |
| CORE | 300M+ 元数据，聚合全球 10,000+ OAI-PMH 仓库 | 含 DiVA/HAL/DergiPark 等非英文仓库 |
| DOAJ | 21K+ OA 期刊，含多语种期刊 | 已有 adapter |
| Unpaywall | 130M+ DOIs 的 OA 状态，语言无关 | 已有 adapter |
| EUR-Lex SPARQL | 全部 EU 法律，24种官方语言 | 已有 adapter |
| BASE (Bielefeld) | 400M+ 文档，聚合 10,000+ 全球仓库 | 见下方独立条目 |

---

### 一、中文 (Chinese)

#### 1. 学术数据库

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **CNKI (中国知网)** | `https://api.cnki.net/` / `https://oversea.cnki.net/restapi/` | OAuth token (机构订阅) | 未公开 | 期刊/学位论文/会议/报纸/年鉴，中国最大学术库 | 有 REST API，但**非公开免费**；需机构合作 | 中文为主 |

**注意**: CNKI、万方 (Wanfang)、维普 (CQVIP) 三大中文学术数据库均**无免费公开 API**。程序化访问需签订机构协议。**推荐替代路径**: 通过 OpenAlex/Crossref 获取已注册 DOI 的中文文献元数据。

#### 2. 专利

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **CNIPA (国家知识产权局)** | 无公开 REST API；数据通过 EPO OPS 可获取 | N/A | N/A | 中国全部专利 | **无独立公开 API**。通过 EPO OPS (`ops.epo.org`) 查询 CNIPA 数据 | 中/英 |

#### 3. 法律

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **裁判文书网 (China Judgments Online)** | `https://wenshu.court.gov.cn/` | N/A | N/A | 1.05亿+ 裁判文书 | **无公开 API**；2023年后大量文书被下架 | 中文 |

#### 4. 政府数据

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **国家统计局 (NBS/NBSC)** | `https://data.stats.gov.cn/` | 无 | 未公开 | 经济/人口/能源指标 | 有数据接口，Python 库 `nbsc` 可用 | 中/英 |

---

### 二、日文 (Japanese)

#### 1. 学术数据库

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **CiNii Research** | `https://cir.nii.ac.jp/opensearch/articles` | 免费 Application ID (注册获取) | 未公开 | 日本最大学术检索，整合 CiNii Books (2026年1月) | **真实 API**: OpenSearch (RSS/Atom/JSON-LD/HTML) | 日/英 |
| **J-STAGE** | `https://api.jstage.jst.go.jp/searchapi/do` | 无 (需同意使用条款) | 未公开 | 日本学术期刊电子化平台，数千种期刊 | **真实 API**: REST，返回 XML | 日/英 |

#### 2. 专利

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **JPO Patent Info API (试行)** | `https://www.jpo.go.jp/e/system/laws/koho/internet/api-patent_info.html` (文档页) | 注册制 | 访问量限制 (试行阶段) | 日本专利/外观/商标 (J-PlatPat 数据) | **真实 API** (2022年1月起试行) | 日/英 |
| **EPO OPS (JPO 数据)** | `https://ops.epo.org/3.2/rest-services` | OAuth (免费注册) | 4GB/月 | 含日本专利数据 | **真实 REST API** (已有 adapter) | 多语言 |

#### 3. 政府数据

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **e-Stat** | `http://api.e-stat.go.jp/rest/<ver>/app/getStatsData` | 无需注册 | 未公开 | 约6,000指标，日本政府统计门户 | **真实 REST API** (XML/JSON/CSV) | 日/英 |

#### 4. 法律

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **e-Gov Law API** | `https://laws.e-gov.go.jp/api/1/` | 无 | 未公开 | 日本全部法律/法规/条例 | **真实 REST API** (JSON/XML) | 日文 (英译: `japaneselawtranslation.go.jp`) |

---

### 三、韩文 (Korean)

#### 1. 学术数据库

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **KCI (Korea Citation Index)** | `data.go.kr/data/15085323/openapi.do` | data.go.kr注册 | 100K请求 | 6000+期刊, 260M+论文, 10K+机构 | **真实 API** (via Korea Open Data Portal) | 韩/英 |

#### 2. 专利

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **KIPRIS Plus** | `http://plus.kipris.or.kr/` | 免费 key (申请后1-2天) | 1,000件/月 (免费) | 韩国专利/实用新型/商标/外观，约126种 Open API | **真实 REST API** + LOD | 韩文为主 |

#### 3. 政府数据

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **data.go.kr** | `https://apis.data.go.kr/` | 免费 service key | 按服务不同 | 韩国政府公共数据门户 | **真实 REST API** | 韩/英 |

#### 4. 法律

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **Korean CLIS (종합법률정보)** | `https://portal.scourt.go.kr/` | XML数据库 | 未公开 | 约8万最高法院判例 + 6万下级法院判例 + 5千宪法法院裁定 | 有 Open API 服务，但**部分判例需付费** | 韩文 |
| **国家法令信息中心** | `https://open.law.go.kr/` | 注册制 | 未公开 | 韩国全部法律/总统令/部令/地方法/判例 | **真实 Open API** | 韩文 (英译: `elaw.klri.re.kr`) |

---

### 四、俄文 (Russian)

#### 1. 学术数据库

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **CyberLeninka** | OAI-PMH: `https://cyberleninka.ru/oai` | 无 | 未公开 | 俄罗斯最大开放获取科学电子图书馆 | **真实 OAI-PMH**，支持 Highwire Press/Eprints 标签 | 俄文为主 |
| **eLIBRARY.RU / RSCI** | 无公开 API | N/A | N/A | 3070万+ 篇文章，3000+ OA 期刊，RSCI 引用索引 | **无公开 API** | 俄文 |

#### 2. 专利

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **Rospatent/FIPS** | `https://www.fips.ru/` (检索系统) | FTP (外国专利局用) | N/A | 俄罗斯发明/实用新型/商标/外观 | **无公开 REST API**；仅 FTP 服务器 + 网页检索。通过 EPO OPS 可获取部分数据 | 俄/英 |

#### 3. 法律

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **Garant / ConsultantPlus** | 无公开 API | 付费订阅 | N/A | Garant: 联邦法规全文；ConsultantPlus: 1亿+ 文档 | **无公开 API**；仅网页/订阅访问 | 俄文 |

---

### 五、法文 (French)

#### 1. 学术数据库

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **HAL (Archives Ouvertes)** | `https://api.archives-ouvertes.fr/search/` | 无 | 未公开 | 4.3M+ 记录，法国多学科开放档案 (CCSD/CNRS 运营) | **真实 REST API** (Apache Solr)；JSON/XML/TEI/BibTeX/CSV；SWORD 协议存储 | 法/英/多语 |
| **theses.fr** | REST: `theses.fr/api/v1/recherche/` + `theses.fr/api/v1/diffusion/` | 无 | 未公开 | 法国全部电子博士论文 (1985至今, ABES/STAR) | **真实 REST API** (JSON)；开源: `github.com/abes-esr/theses-api-*` | 法文为主 |
| **Persée** | SPARQL: `data.persee.fr/` ; OAI-PMH | 无 | 未公开 | 400+馆藏, 1M+文档, 30M+ RDF triples | **真实 SPARQL + OAI-PMH**；DC/METS/MODS/TEI/FRBR | 法文为主 |

#### 2. 专利

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **INPI France (Data INPI)** | `https://data.inpi.fr/` (API 端点) | 免费注册 | 每周更新 | 2010年起法国专利公告(XML)；1981年起全文(XML) | **真实 REST API + FTP** | 法文 |

#### 3. 法律

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **Legifrance API** | `https://api.gouv.fr/les-api/DILA_api_Legifrance` (PISTE 门户) | OAuth 2.0 Client Credentials (免费) | 未公开 | 法国宪法/法律/法规/75部法典/官方公报/集体协议 | **真实 REST API** (Swagger 文档)；已有 MCP Server 集成 | 法文 |

#### 4. 政府数据

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **data.gouv.fr** | `https://tabular-api.data.gouv.fr/api` (表格API); CKAN API v3 | 无 (只读) | 未公开 | 法国政府开放数据平台 | **真实 REST API** | 法文 |

---

### 六、德文 (German)

#### 1. 学术/文化数据库

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **Deutsche Digitale Bibliothek (DDB)** | `https://api.deutsche-digitale-bibliothek.de/` | 免费 API key (注册 DDB 账户) | 未公开 | 德国数字图书馆，文化遗产元数据 | **真实 REST API** (OpenAPI 3.0)；JSON 返回 | 德/英 |

#### 2. 专利

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **DPMA DPMAconnectPlus** | SSL REST 接口 (需签协议) | 用户名/密码 (签协议后) | 未公开 | 德国专利/实用新型/商标/外观 | **真实 REST API**，但需签约 + 一次性连接费 200 EUR；DPMAregister 检索免费 | 德/英 |
| **EPO OPS (DPMA 数据)** | `https://ops.epo.org/3.2/rest-services` | OAuth (免费注册) | 4GB/月 | 含德国专利数据 | **真实 REST API** (已有 adapter) | 多语言 |

#### 3. 法律

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **Juris** | 无公开 API | 付费订阅 | N/A | 德国法律全文数据库 | **无公开 API** | 德文 |
| **Open Legal Data** | `https://de.openlegaldata.io/api/` | 免费key (注册) | 未公开 | 德国法律全文 + BGH判例 | **真实 REST API**；Python/JS/Java/PHP SDK | 德文 |

---

### 七、西班牙文 (Spanish)

#### 1. 学术数据库

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **Redalyc** | OAI-PMH 端点 (Dublin Core 格式) | 无 | 未公开 | 15国 620+ 学术期刊，拉丁美洲/伊比利亚 | **真实 OAI-PMH** | 西/葡/英 |
| **Dialnet** | OAI-PMH: `http://dialnet.unirioja.es/oai/OAIHandler` (技术文档: `dialnet.unirioja.es/info/ayuda/oai_tecnica`) | 无 | 未公开 | 西班牙/拉美学术文献，期刊/论文/书籍 | **真实 OAI-PMH** (条款中明确允许 OAI-PMH 元数据下载) | 西文为主 |

#### 2. 专利

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **OEPM (西班牙专利商标局)** | 无独立公开 API | N/A | N/A | 西班牙专利/商标/外观 | **无公开 API**；通过 EPO OPS 获取西班牙专利数据 | 西/英 |

#### 3. 法律

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **CENDOJ** | `https://www.poderjudicial.es/` (搜索引擎) | 无 | N/A | 900万+ 司法裁决 | **无公开 API**；仅网页搜索 | 西文 |

---

### 八、葡萄牙文/巴西 (Portuguese/Brazilian)

#### 1. 学术数据库

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **SciELO** | ArticleMeta: `http://articlemeta.scielo.org/api/v1/` ; CitedBy: `https://github.com/scieloorg/citedby` | 无 | 未公开 | 拉美/加勒比/伊比利亚/南非 OA 期刊平台 | **真实 REST API** (JSON)；Python 库 `scieloapi` 可用；批量数据: `static.scielo.org/articlemeta/articles.json.zip` | 葡/西/英 |

#### 2. 专利

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **INPI Brazil** | 无公开 REST API | N/A | N/A | 巴西专利/商标/外观 | **无公开 API**；通过 EPO OPS 获取巴西专利数据 | 葡文 |

---

### 九、阿拉伯文 (Arabic)

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **QScience** | `https://www.qscience.com/` | 未知 | 未知 | 医学/生物/社科/伊斯兰研究/工程，OA 期刊 | **未确认有公开 API**；摘要支持英/阿双语 | 英/阿 |

**注意**: 阿拉伯语区域缺乏独立的公开学术 API。**推荐路径**: 通过 OpenAlex/Crossref/DOAJ 获取阿拉伯地区机构发表的文献元数据。KAUST 通过 Lens.org API 提供学术数据访问 (已有 Lens adapter)。

---

### 十、印度 (Indian)

#### 1. 法律

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **Indian Kanoon** | `https://api.indiankanoon.org/` | 公钥/私钥 加密 或 Token 认证 | 按信用额度 (注册送 Rs 500；非商业每月 Rs 10,000 免费) | 印度最大法律文档数据库 | **真实 REST API** (JSON/XML)；Python/Java 工具库 | 英/印地语等 |

#### 2. 学术

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **Shodhganga** | OAI-PMH: `shodhganga.inflibnet.ac.in/oai/request` | 无 | 未公开 | 535K+ 印度博士论文 (INFLIBNET/DSpace) | **真实 OAI-PMH** | 英/印地语等 |

#### 2. 专利

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **Indian Patent Office (InPASS)** | `https://iprsearch.ipindia.gov.in/` | 无 | N/A | 仅印度专利申请 | **无公开 API**；仅网页搜索。通过 EPO OPS 获取印度专利数据 | 英文 |

---

### 十一、北欧 (Nordic/Scandinavian)

#### 1. 学术数据库

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **DiVA Portal** | OAI-PMH: `http://<domain>.diva-portal.org/dice/oai` | 无 | 未公开 | 约50所瑞典大学/研究所/博物馆的数字仓库 | **真实 OAI-PMH** (oai_dc + swepub_mods 格式) | 瑞典语/英语 |

#### 2. 专利

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **PRH (芬兰专利注册局)** | `http://avoindata.prh.fi/` (开放数据API) | 无 | 未公开 | 芬兰专利/商标/外观 + 企业信息 | **真实 REST API**；Document Service API 可下载专利文档 | 芬/瑞/英 |

---

### 十二、土耳其文 (Turkish)

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **DergiPark (TUBITAK ULAKBIM)** | REST: `dergipark.org.tr/api/public/v1/` ; OAI-PMH | 无 | 未公开 | 土耳其学术期刊国家平台，OA 数字出版基础设施 | **真实 REST API + OAI-PMH** | 土耳其语/英语 |

---

### 十三、印尼文 (Indonesian)

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **GARUDA (Garba Rujukan Digital)** | OAI-PMH v2.0 | 无 | 未公开 | 印尼学术出版物聚合器 (教育科技部运营) | **真实 OAI-PMH** (用于收集数千条期刊元数据) | 印尼语/英语 |

---

### 十四、非洲 (African)

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **AJOL (African Journals Online)** | 基于 PKP OJS 平台 (标准支持 OAI-PMH) | 无 | 未公开 | 31个非洲国家 500+ 同行评审期刊 | **可能有 OAI-PMH** (PKP/OJS 平台标准支持，但未确认端点 URL) | 英/法/多语 |

---

### 十五、欧洲跨国 (European Cross-national)

#### 1. 文化遗产

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **Europeana** | `https://api.europeana.eu/` (Search/Record/Entity/IIIF API) | 免费 API key | 未公开 | 欧洲数字文化遗产，多国博物馆/图书馆/档案馆 | **真实 REST API** (JSON/JSON-LD/RDF)；EDM 元数据模型 | 多语言 (30+) |

#### 2. 学术聚合

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **BASE (Bielefeld)** | OAI-PMH: `http://oai.base-search.net/oai` | IP 白名单 (需申请) | 未公开 | 400M+ 文档，10,000+ 全球仓库聚合 | **真实 OAI-PMH** (oai_dc + base_dc 格式) | 多语言 |

#### 3. 法律

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **EUR-Lex / CELLAR** | SPARQL: `https://publications.europa.eu/webapi/rdf/sparql` ; REST: CELLAR RESTful API | 无 | 100万行/查询 | 全部 EU 法律 (条约/法规/指令/判例) | **真实 SPARQL + REST API** (CDM/FRBR 本体)；R 包 `eurlex` 可用。**已有 adapter** | 24种 EU 官方语言 |

---

### 十六、全球专利聚合 (补充 Part A)

| 名称 | API URL / 端点 | 认证方式 | 速率限制 | 覆盖/规模 | API 真实性 | 语言 |
|------|---------------|---------|---------|----------|----------|------|
| **WIPO API Catalog** | `https://apicatalog.wipo.int/` | 按服务不同 | 按服务不同 | 全球 IP API 目录，PATENTSCOPE: 2.4M PCT + 99M 专利 | **真实 API 目录**；PATENTSCOPE 原为 SOAP，部分迁移至 REST | 9种语言 |
| **WIPO PEARL** | `https://wipopearl.wipo.int/en/api` | 未知 | 未知 | 专利术语数据库 | **真实 API** | 多语言 |

---

### 实施优先级建议 (多语言扩展)

基于 API 成熟度、免费可用性、和 IntegriRef 现有架构:

#### Tier 1: 立即可集成 (真实免费 API，高价值)

| 优先级 | 数据源 | 类型 | 所需工作 |
|--------|-------|------|---------|
| 1 | **HAL** | 学术 (法文) | 新 adapter: REST/Solr 查询 |
| 2 | **CiNii Research** | 学术 (日文) | 新 adapter: OpenSearch |
| 3 | **J-STAGE** | 学术 (日文) | 新 adapter: REST XML |
| 4 | **SciELO** | 学术 (葡/西) | 新 adapter: REST JSON |
| 5 | **Legifrance** | 法律 (法文) | 新 adapter: OAuth2 REST |
| 6 | **Indian Kanoon** | 法律 (印度) | 新 adapter: Token REST |
| 7 | **KIPRIS Plus** | 专利 (韩文) | 新 adapter: REST API |
| 8 | **e-Stat Japan** | 政府 (日文) | 新 adapter: REST XML/JSON |
| 9 | **Europeana** | 文化遗产 (多语) | 新 adapter: REST JSON |
| 10 | **INPI France** | 专利 (法文) | 新 adapter: REST + FTP |
| 11 | **Korean Law Center** | 法律 (韩文) | 新 adapter: Open API |
| 12 | **e-Gov Japan Law** | 法律 (日文) | 新 adapter: REST JSON/XML |
| 13 | **Open Legal Data DE** | 法律 (德文) | 新 adapter: REST JSON |
| 14 | **KCI** | 学术 (韩文) | 新 adapter: via data.go.kr |
| 15 | **DergiPark** | 学术 (土耳其) | 新 adapter: REST JSON |
| 16 | **Persée** | 学术 (法文) | 新 adapter: SPARQL |
| 17 | **theses.fr** | 学术 (法文) | 新 adapter: REST JSON |

#### Tier 2: OAI-PMH 批量收割 (需实现通用 OAI-PMH 客户端)

| 优先级 | 数据源 | 覆盖 |
|--------|-------|------|
| 1 | **DiVA Portal** | 瑞典/北欧学术 |
| 2 | **theses.fr** | 法国博士论文 |
| 3 | **Dialnet** | 西班牙/拉美学术 |
| 4 | **Redalyc** | 拉美学术 |
| 5 | **CyberLeninka** | 俄罗斯 OA 学术 |
| 6 | **DergiPark** | 土耳其学术 |
| 7 | **GARUDA** | 印尼学术 |
| 8 | **BASE** | 全球聚合 (需 IP 白名单) |

**建议**: 实现一个通用 `OaiPmhRegistry` 基类，所有 OAI-PMH 源共享 harvesting 逻辑，仅配置端点 URL 和元数据前缀。

#### Tier 3: 通过现有 adapter 间接覆盖

| 现有 Adapter | 间接覆盖的非英文数据 |
|-------------|-------------------|
| **EPO OPS** | CNIPA (中国), JPO (日本), KIPO (韩国), DPMA (德国), INPI-BR (巴西), Rospatent (俄国), OEPM (西班牙) 专利 |
| **OpenAlex** | 全球 240M+ works 含多语种元数据 |
| **Crossref** | 全球 167M+ DOI 含多语种元数据 |
| **CORE** | 聚合 10,000+ 全球仓库含非英文源 |
| **Lens.org** | KAUST 等中东机构数据 |
| **EUR-Lex** | 24种 EU 官方语言法律文本 |

#### Tier 4: 无法集成 (无公开 API)

| 数据源 | 原因 |
|-------|------|
| CNKI/万方/维普 | 需机构订阅，无公开免费 API |
| 裁判文书网 | 无 API，且数据在缩减中 |
| eLIBRARY.RU/RSCI | 无公开 API |
| CENDOJ (西班牙法院) | 无 API |
| Juris (德国法律) | 付费订阅，无公开 API |
| Garant/ConsultantPlus | 付费订阅，无公开 API |
| AJOL | OAI-PMH 端点未确认 |
| QScience | API 未确认 |

---

## Part E: 新增英语高价值 API 清单

除 Part A 已实现的31个 adapter 外，以下为新发现的高价值免费公开 API。

### Tier 1: 立即集成 (高价值、易接入)

| # | API | Base URL | Auth | Rate | Coverage | IntegriRef用途 |
|---|-----|----------|------|------|----------|---------------|
| 1 | **bioRxiv/medRxiv** | `api.biorxiv.org` / `api.medrxiv.org` | None | 无公开限制 | 250K+ 生物/医学预印本 | 预印本引用验证+期刊发表追踪 |
| 2 | **ClinicalTrials.gov v2** | `clinicaltrials.gov/api/v2/studies` | None | 10 rps | 530K+ 临床试验 | NCT ID验证 |
| 3 | **Retraction Watch** | Crossref API `filter=update-type:retraction` | None (mailto) | 同Crossref | ~50K 撤稿记录 | **研究诚信核心**: 标记已撤稿论文 |
| 4 | **NASA ADS** | `api.adsabs.harvard.edu/v1` | Bearer token (免费) | 5000/day | 16M+ 天体物理/物理记录 | 天文物理引用验证 |
| 5 | **INSPIRE-HEP** | `inspirehep.net/api` | None | 15/5s | 1.7M 高能物理文献 | HEP引用验证 |
| 6 | **Zenodo** | `zenodo.org/api` | OAuth可选 | 30/min | 3M+ 研究输出(数据集/软件) | 数据集/软件DOI验证 |
| 7 | **FRED** | `api.stlouisfed.org/fred` | 免费key | 120/min | 840K+ 经济时间序列 | 经济数据引用验证 |

### Tier 2: 高价值、中等接入难度

| # | API | Base URL | Auth | Rate | Coverage | IntegriRef用途 |
|---|-----|----------|------|------|----------|---------------|
| 8 | **UniProt** | `rest.uniprot.org` | None | 极高 (303M req/月) | 250M+ 蛋白质序列 | 蛋白质/基因登录号验证 |
| 9 | **ChEMBL** | `ebi.ac.uk/chembl/api/data` | None | 1 rps | 2.4M化合物, 20M生物活性 | CHEMBL ID验证 |
| 10 | **openFDA** | `api.fda.gov` | 可选key | 240/min (无key); 120K/day (有key) | 药物/器械/食品监管数据 | FDA引用验证 |
| 11 | **GitHub API** | `api.github.com` | Token推荐 | 60/hr(匿名); 5000/hr(认证) | 630M+ 仓库 | 软件仓库引用验证 |
| 12 | **Software Heritage** | `archive.softwareheritage.org/api/1` | Keycloak可选 | 120/hr(匿名); 1200/hr(认证) | 27B+ 源文件, 421M项目 | SWHID持久标识符验证 |
| 13 | **zbMATH Open** | `api.zbmath.org/v1` | None | 无公开限制 | 4.9M数学出版物 (1755至今) | 数学引用验证 |
| 14 | **ERIC** | `api.ies.ed.gov/eric` | None | 无公开限制 | 1.9M教育研究记录 | 教育文献验证 |
| 15 | **GBIF** | `api.gbif.org/v1` | None (下载需账户) | 1000/page | 3.1B+ 物种记录 | 生物多样性数据验证 |

### Tier 3: 专项领域

| # | API | Base URL | Auth | Rate | Coverage | IntegriRef用途 |
|---|-----|----------|------|------|----------|---------------|
| 16 | **NASA CMR** | `cmr.earthdata.nasa.gov/search` | None (搜索) | HTTP 429节流 | NASA地球科学数据 | 数据集引用验证 |
| 17 | **US Census** | `api.census.gov/data` | 可选key | 500/day(无key) | ACS/十年人口普查等 | 人口统计数据引用 |
| 18 | **BLS** | `api.bls.gov/publicAPI/v2` | 免费注册 (v2) | 500/day | 就业/物价/生产力数据 | 劳动统计引用验证 |
| 19 | **NOAA CDO** | `ncei.noaa.gov/cdo-web/api/v2` | 免费token | 5/s, 10K/day | 全球历史气候数据 | 气候数据引用验证 |
| 20 | **EPA Envirofacts** | `enviro.epa.gov/enviro/efservice` | api.data.gov key | 15min查询超时 | 空气/水/废物/毒物数据 | 环境数据引用验证 |
| 21 | **DailyMed** | `dailymed.nlm.nih.gov/dailymed/services/v2` | None | 合理使用 | 140K+药品标签 | 药物标签引用验证 |
| 22 | **PubPeer** | `pubpeer.com/api` | 私有key (需申请) | 未公开 | 数十万条同行评议评论 | 研究诚信: 标记问题论文 |
| 23 | **PhilPapers** | `philpapers.org/philpapers/raw` | API key (免费) | 未公开 | 500K+哲学文献 | 哲学引用验证 |
| 24 | **ChemRxiv** | Cambridge Open Engage API | None (读取) | 未公开 | ~25K化学预印本 | 化学预印本验证 |

### 不推荐 (无免费API)

| 数据源 | 原因 |
|--------|------|
| MathSciNet | AMS付费订阅, 无免费API (用zbMATH替代) |
| PsycINFO | APA/EBSCO付费订阅, 无免费API |
| SSRN | Elsevier所有, 无公开API |
| DrugBank | 仅非商用免费 (CC BY-NC 4.0) |

---

## Part F: 全局统计与架构总结

### 数据源总览

| 类别 | Part A (已有) | Part E (新增英语) | Part D (多语言) | 合计 |
|------|:---:|:---:|:---:|:---:|
| 学术 | 13 | 10 | 14 | **37** |
| 专利 | 4 | 0 | 4 | **8** |
| 法律 | 4+2 | 0 | 7 | **13** |
| 金融/经济 | 2+1 | 4 | 0 | **7** |
| 政府/国际 | 4+4 | 3 | 3 | **14** |
| 标准/知识库 | 4+3 | 3 | 1 | **11** |
| 研究诚信 | 0 | 2 | 0 | **2** |
| **合计** | **41** | **22** | **29** | **92** |

### 架构扩展建议

```
IntegriRef Registry Architecture (扩展后)
═══════════════════════════════════════════

已实现 (31 adapters)          待实现 Tier 1 (17 adapters)         待实现 Tier 2 (8 OAI-PMH)
────────────────────          ─────────────────────────           ──────────────────────────
CrossRef, arXiv, DBLP...     bioRxiv, ClinicalTrials,           DiVA, theses.fr, Dialnet,
USPTO, EPO, WIPO...          NASA ADS, INSPIRE-HEP,             Redalyc, CyberLeninka,
CourtListener, GovInfo...    Zenodo, FRED, zbMATH...            DergiPark, GARUDA, BASE
World Bank, IMF, WHO...      HAL, CiNii, J-STAGE,
NIST, IETF, Wikidata...     SciELO, Legifrance...
                              Retraction Watch (Crossref扩展)

新增基础设施:
┌──────────────────────────────────────┐
│  OaiPmhRegistry (通用基类)           │  ← 8个OAI-PMH源共享harvesting逻辑
│  RetractedPaperIndex (Crossref扩展)  │  ← 研究诚信核心功能
│  MultilingualNormalizer              │  ← CJK/Cyrillic/Arabic标题标准化
└──────────────────────────────────────┘
```

### 多语言能力矩阵

| 语言 | 学术 | 专利 | 法律 | 政府 | 覆盖路径 |
|------|:---:|:---:|:---:|:---:|---------|
| 英语 | ★★★ | ★★★ | ★★★ | ★★★ | 31+22 adapters |
| 中文 | ★★☆ | ★★★ | ☆☆☆ | ★☆☆ | OpenAlex/Crossref + EPO OPS |
| 日文 | ★★★ | ★★★ | ★★☆ | ★★☆ | CiNii+J-STAGE + EPO OPS + e-Stat + e-Gov Law |
| 韩文 | ★★☆ | ★★☆ | ★★☆ | ★☆☆ | KCI + KIPRIS + Korean Law Center + data.go.kr |
| 俄文 | ★★☆ | ★★☆ | ☆☆☆ | ☆☆☆ | CyberLeninka(OAI) + EPO OPS |
| 法文 | ★★★ | ★★★ | ★★★ | ★★☆ | HAL+theses.fr + INPI + Legifrance |
| 德文 | ★★☆ | ★★★ | ★★☆ | ★☆☆ | DDB + EPO OPS + Open Legal Data |
| 西文 | ★★☆ | ★★☆ | ☆☆☆ | ☆☆☆ | Dialnet+Redalyc(OAI) + EPO OPS |
| 葡文 | ★★★ | ★★☆ | ☆☆☆ | ☆☆☆ | SciELO + EPO OPS |
| 阿拉伯 | ★☆☆ | ☆☆☆ | ☆☆☆ | ☆☆☆ | OpenAlex/DOAJ间接 |
| 印地语 | ☆☆☆ | ★☆☆ | ★★☆ | ☆☆☆ | Indian Kanoon + EPO OPS |
| 北欧 | ★★☆ | ★★☆ | ☆☆☆ | ☆☆☆ | DiVA(OAI) + PRH |
| 土耳其 | ★★☆ | ★☆☆ | ☆☆☆ | ☆☆☆ | DergiPark(REST+OAI) |
| 印尼 | ★☆☆ | ☆☆☆ | ☆☆☆ | ☆☆☆ | GARUDA(OAI) |

### 实施路线图 (扩展)

**Phase 0.5 (立即)**: Retraction Watch集成 — 在现有CrossRef adapter中添加撤稿标记功能
**Phase 1a**: 7个Tier 1英语API (bioRxiv, ClinicalTrials, NASA ADS, INSPIRE, Zenodo, FRED, zbMATH)
**Phase 1b**: 17个Tier 1多语言API (HAL, CiNii, J-STAGE, SciELO, Legifrance, Indian Kanoon, KIPRIS, e-Stat, Europeana, INPI, Korean Law, e-Gov Japan Law, Open Legal Data DE, KCI, DergiPark, Persée, theses.fr)
**Phase 1c**: OaiPmhRegistry通用基类 + 7个OAI-PMH源 (DiVA, Dialnet, Redalyc, CyberLeninka, GARUDA, Shodhganga, BASE)
**Phase 2+**: Tier 2/3 英语API按需集成

---

## Part G: 语义验证方法调研 (Claim-Abstract Alignment)

### 核心问题

> 如果目标文章写 "Smith (2020) 证明了 X"，但 Smith (2020) 的摘要说的是 Y 甚至 ¬X — 自动标记为高风险。

这是 IntegriRef 的 L2 核心差异化功能。当前**零个商业产品**实现此能力。

---

### 1. 现有系统深度分析

#### SCitance (2024 EMNLP SDP, Allen AI)

**核心洞察**: 引用句本身就是天然的 claim — 无需人工标注。

| 阶段 | 做什么 | 技术细节 |
|------|-------|---------|
| 1. Citance提取 | 从论文中提取包含引用标记的句子 | 正则匹配 `[1]`, `(Smith, 2020)` 等 |
| 2. 否定生成 | 用LLM生成引用句的否定版本 | 创建合成"反驳"样本，无需人工标注 |
| 3. 证据配对 | 将引用句+否定句与被引论文摘要配对 | 形成 (claim, evidence, label) 三元组 |
| 4. ICL推理 | GPT-4 few-shot分类 | SUPPORTS / REFUTES |

**关键结果**: GPT-4 + SCitance ICL 样本 → 与 SciFact 微调模型差距仅 1 F1 点。
**IntegriRef意义**: 直接可用。引用句就是 claim，配上被引摘要做 NLI 即可。

#### SemanticCite (2025.11 arXiv, 开源)

**4阶段 pipeline**:

| 阶段 | 做什么 | 技术细节 |
|------|-------|---------|
| 1. 文本预处理 | PDF提取+分块 | PyMuPDF → 512字符块 (50字符overlap) |
| 2. Claim提取 | 引用句标准化为独立声明 | 去除引用标记、作者署名，保留数值和事实断言 |
| 3. 混合检索 | 从被引论文找相关证据段 | 密集语义搜索 (向量嵌入) + 稀疏关键词匹配 (BM25) |
| 4. 重排序+分类 | 交叉编码器重排序 → LLM分类 | QLoRA微调 Qwen3 (1.7B/4B) |

**4类分类法** (优于二分法):
- **SUPPORTED** — 被引论文明确支持该声明
- **PARTIALLY SUPPORTED** — 部分支持，需要修正
- **UNSUPPORTED** — 被引论文未涉及该声明
- **UNCERTAIN** — 无法确定

**性能**: 4B模型 → 83.64% 加权准确率, 90.01% 字符相似度
**开源**: `github.com/sebhaan/semanticcite`, HuggingFace 模型+Demo

#### CiteGuard — 两个版本

**版本1 (arXiv 2510.17853) — Agent-based 引用归属**:
- LLM agent + 工具调用 (Semantic Scholar 搜索)
- 迭代搜索→精炼查询→评估候选
- DeepSeek-R1: 65.4% 准确率 (接近人类 69.7%), **$0.005/引用**
- GPT-4o: $0.12/引用 — 24x 更贵

**版本2 (NeurIPS 2025) — 同行评审引用验证**:
- BM25 + SPECTER2 混合检索
- 可解释对齐分数: DOI一致性 + 标题相似度 + SPECTER2语义 + 出版信息
- 等温回归校准为概率
- **仅18%不确定引用升级到LLM** — 其余82%纯模型判定
- 论文级 F1=0.95, 引用级 F1=0.89
- **中位成本 $0.0028/篇论文**, 中位延迟 11.7s/篇

#### Valsci (2025 BMC Bioinformatics, 开源)

| 步骤 | 做什么 |
|------|-------|
| 1. 查询扩展 | LLM 从声明生成扩展搜索词 |
| 2. 证据检索 | 搜索 Semantic Scholar 获取相关论文/摘录 |
| 3. 相关性评分 | 每段证据得 0-1 置信分，过滤低相关性 |
| 4. 文献计量评分 | 作者 H-index + 被引数 + 期刊影响力 |
| 5. CoT 综合 | LLM 必须引用实际验证过的摘录，输出透明报告 |

**性能**: GPT-4o → F1=0.761; GPT-4o-mini → F1=0.720
**部署**: 兼容任何 OpenAI-style API (含 LLaMA, DeepSeek-R1, Mistral)
**开源**: `github.com/bricee98/Valsci`

#### MedRAGChecker (2026.01 arXiv)

**原子声明分解 (Atomic Claim Decomposition)**:

```
输入: "Drug A treats condition B and has side effect C"
         ↓ LLM分解
输出: Claim 1: "Drug A treats condition B"
      Claim 2: "Drug A has side effect C"
         ↓ 逐条验证
NLI: Claim 1 vs 证据 → Supported
     Claim 2 vs 证据 → Contradicted
KG:  Drug A --treats--> B ✓ (DRKG中存在)
     Drug A --side_effect--> C ✗ (DRKG中不存在)
         ↓ 信号融合
最终: Claim 2 标记为矛盾
```

**关键设计**: 教师模型 (GPT-4) 蒸馏到紧凑生物医学学生模型，集成模型用F1加权。

---

### 2. NLI 模型选型

| 模型 | 参数量 | 训练数据 | 核心优势 | CPU可行? |
|------|-------|---------|---------|:-------:|
| **DeBERTa-v3-large-mnli-fever-anli-ling-wanli** | 304M | 885K NLI对 | HuggingFace最佳零样本NLI | GPU推荐 |
| **DeBERTa-v3-base-mnli-fever-anli** | 86M | 764K NLI对 | 接近大模型性能，推理快 | **是** |
| **cross-encoder/nli-deberta-v3-base** | 86M | 标准NLI | 适合交叉编码器重排序 | **是** |
| **SciFact微调 DeBERTa** | 86-304M | SciFact (1.4K科学声明) | 学术领域特化, 88% F1 | **是** |

**NLI 使用方式**:

```
前提 (Premise):  [被引论文的摘要]
假设 (Hypothesis): [从引用句提取的声明]
                    ↓
模型输出: {entailment: 0.85, neutral: 0.10, contradiction: 0.05}
```

**判定阈值**:

| 条件 | 标签 | 风险等级 |
|------|-----|---------|
| contradiction > 0.5 | **CONTRADICTED** | **高风险** — 引文歪曲了被引工作 |
| entailment > 0.7 | SUPPORTED | 低风险 |
| neutral占主导, entailment < 0.4 | UNSUPPORTED | 中风险 — 摘要未涉及该声明 |
| 摘要不可获取 | UNVERIFIABLE | 信息缺失 |

---

### 3. IntegriRef L2 实施方案

#### 完整 pipeline

```
用户文档
   │
   ▼
┌──────────────────────────────┐
│ Step 1: 引用上下文提取        │
│  解析文档 → 找到引用标记      │
│  提取引用句 ± 1句上下文       │
│  去除标记 → 干净的 claim     │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Step 2: 被引文献摘要获取      │
│  DOI → Crossref (免费,无auth)│
│  ↓ 无摘要                    │
│  → Semantic Scholar (225M+)  │
│  ↓ 无摘要                    │
│  → OpenAlex (240M+)         │
│  ↓ 无摘要                    │
│  → PubMed (生物医学)         │
│  ↓ 无摘要                    │
│  → 标记为 UNVERIFIABLE       │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Step 3: NLI 快速预筛         │
│  DeBERTa-v3-base (CPU可用)   │
│  premise = 摘要              │
│  hypothesis = claim          │
│  → 三类概率                   │
│                              │
│  contradiction > 0.5 → 高风险 │
│  entailment > 0.7 → 通过     │
│  max_prob < 0.6 → 升级到LLM  │
└──────────────┬───────────────┘
               │ (仅~18%升级)
               ▼
┌──────────────────────────────┐
│ Step 4: LLM 深度分析 (可选)   │
│  DeepSeek-R1: ~$0.005/引用   │
│  GPT-4o-mini: ~$0.01/引用    │
│  → 4类: SUPPORTED /          │
│    PARTIALLY_SUPPORTED /     │
│    UNSUPPORTED / CONTRADICTED│
│  + 自然语言解释               │
└──────────────┬───────────────┘
               │
               ▼
┌──────────────────────────────┐
│ Step 5: 汇总评分              │
│  每个引用 → alignment_score   │
│  CONTRADICTED → 自动高风险    │
│  全文档 → 语义对齐度总分      │
└──────────────────────────────┘
```

#### 成本估算

| 场景 | 引用数 | NLI成本 | LLM升级 (18%) | 总成本 |
|------|:-----:|:------:|:------------:|:-----:|
| 学术论文 | 50 | $0 (自托管) | 9引用 × $0.005 | **$0.045** |
| 专利文件 | 100 | $0 | 18引用 × $0.005 | **$0.09** |
| 法律文书 | 200 | $0 | 36引用 × $0.005 | **$0.18** |
| 纯NLI (无LLM) | 50 | $0 | $0 | **$0** |

#### 硬件要求

| 部署方式 | 最低配置 | 性能 |
|---------|---------|------|
| 纯CPU (DeBERTa-base) | 4核 / 8GB RAM | 500ms-2s/引用, 足够批处理 |
| 单GPU (DeBERTa-large) | RTX 3060 12GB | 100-500ms/引用, 可实时 |
| API模式 (无本地模型) | 任意 | 依赖外部API延迟 |

---

### 4. L0/L1 竞品基线 (必须持平)

#### L0: 存在性验证 — 竞品做了什么

| 竞品 | 检查内容 | 数据源 |
|------|---------|-------|
| **CiteTrue** | DOI解析, 标题匹配, 作者验证, AI幻觉引用检测 | 15+库 (Crossref, arXiv, CORE, OpenAlex, PubMed, S2...) |
| **SwanRef** | DOI解析, 标题匹配 | 2库 (Crossref, Google Scholar) |
| **Citely** | DOI解析, 95%准确率 | 3库 (Crossref, PubMed, arXiv) |

**IntegriRef L0 必须实现**:
- [x] DOI/arXiv/PMID → 官方记录解析 (已有31个adapter)
- [x] 标题+作者+年份模糊匹配 (已有ICEntity.normalize)
- [ ] **元数据交叉验证** — 多库返回结果的一致性检查
- [ ] **撤稿检测** — Retraction Watch via Crossref
- [ ] **AI幻觉引用检测** — 不存在的DOI + 不匹配的标题/作者组合

#### L1: 意图分类 — scite.ai 做了什么

scite.ai 的核心:
- 处理全文PDF提取引用语句 + 上下文
- 深度学习分类器: **Supporting** / **Mentioning** / **Contrasting**
- 规模: 880M+ 已分类引用语句, 25M+ 全文
- 提供每个分类的置信百分比

**L1 不做什么**: 不验证分类是否*准确*。如果论文A说"Smith (2020) supports our hypothesis"，scite 标记为 "supporting" — 但不检查 Smith (2020) 是否真的支持。

**L1 → L2 的鸿沟**:
- L0: "这篇引文存在吗？" (书目检查)
- L1: "引用论文如何使用这篇引文？" (意图分类)
- L2: "引用论文对被引文献的描述是否准确？" (**语义验证 — 无竞品**)

---

### 5. 关键决策建议

1. **NLI 模型优先**: DeBERTa-v3-base 作为基线 — CPU可用, $0成本, 88% F1
2. **LLM 作为升级路径**: 仅对 NLI 不确定的 ~18% 引用调用 LLM
3. **4类分类优于2类**: 采用 SemanticCite 的 SUPPORTED/PARTIALLY/UNSUPPORTED/CONTRADICTED
4. **摘要优先**: SCitance 证明摘要足够做 L2 验证 — 不需要全文
5. **原子声明分解**: 对复合声明 (一句话多个断言)，参考 MedRAGChecker 做拆分
6. **CONTRADICTION = 自动高风险**: 这是用户的核心需求，必须最高优先级实现

### 参考实现 (开源)

| 项目 | 链接 | 许可 | 可直接复用 |
|------|------|------|:--------:|
| SemanticCite | `github.com/sebhaan/semanticcite` | MIT | 4阶段pipeline |
| Valsci | `github.com/bricee98/Valsci` | 开源 | RAG+CoT架构 |
| SCitance | `github.com/larchlab/scitance` | 开源 | Citance提取+否定生成 |
| SciFact | `github.com/allenai/scifact` | Apache 2.0 | 训练数据 (1.4K科学声明) |
| DeBERTa NLI | `huggingface.co/MoritzLaurer/DeBERTa-v3-*` | MIT | 即用NLI模型 |
| FlashDeBERTa | `github.com/Knowledgator/FlashDeBERTa` | 开源 | 50%+加速 (512+ tokens) |
