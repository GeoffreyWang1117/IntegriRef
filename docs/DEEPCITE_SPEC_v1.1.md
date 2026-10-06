# deepcite 规格 v1.1（待批准，尚未实现）

状态：**设计已定稿，代码一行未写。** 等你批准后才开工。
日期：2026-10-05 · 对接方：另一个 session（负责 `/ref-check` 命令文件、bibguard、self-review 侧）

---

## 1. 这个功能要解决什么

现有五层（L0–L4）全部只管**元数据对不对**：DOI 是否存在、作者年份期刊是否一致。
它们完全不管**你对被引文献的描述对不对**。四个真实失败案例都属于后者：

| 案例 | 失败形态 |
|---|---|
| ROA-LLM | 写成 MQuAKE 的 "native metric is conjunction"；读 MQuAKE 原文，conjunction 只是它的 reference metric，主指标是 paraphrase 上的 disjunction |
| CIKM 真实审稿 | TALLRec 的 protocol 被归到了错误的论文名下 —— 唯一的 blocking 意见，内部 panel 完全没发现 |
| crystleLLM | 对照原始文献，8 处引用把被引工作描述错了 |
| ChroKnowBench | 反例：一份**二手摘要**（2010–2023）差点让论文把正确的 "13 snapshots" 改成错的 14。实际发布文件有 13 个年度字段 |

最后一个案例定了一条铁律：**必须读原始工件，不能读任何摘要或二手描述。**

## 2. 边界（我坚持的几条，和为什么）

1. **不接入 `core/pipeline.py` 的 risk tier，也不进 Bayesian fusion。**
   Table 5 的经验 LR 是在 8,817 校准池上标定的，camera-ready 又正好卡在 13 页且排版脆弱。
   任何新信号流进 tier 都会让已发表的数字失效。deepcite 是独立入口、独立输出。
2. **默认不装 torch。** NLI 只在 `--rank nli` 时启用，且只做排序，永不打标签。
   理由见 §6。
3. **不碰备份快照。** 验收测试的 fixture 先拷到临时目录再跑；无论参数怎么给，
   路径里含 `ProjectsBackup/snapshots` 一律硬拒绝写入。
   （原始 spec 的测试会把 `.cache/` 写进 2026-06 月度备份 —— 我查过那个目录是可写的。）
4. **三套代码库继续分开。** bibguard = 公开 L0 元数据（唯一真源）；
   IntegriRef = 研究层；`~/Tools/ref-check` = 冻结不动。
   `/ref-check` 仍然只调 pip 的 bibguard，`/ref-check --deep` 才走到 IntegriRef。
5. **不 import `core.discovery`。** 我实测过：它只拉 `requests`，不拉 pipeline/scoring/torch，
   所以技术上允许。但 bibguard 已经在解析这些 ID，再来一个解析器等于重复发请求 +
   两条可能悄悄打架的代码路径。只从 bibguard 拿 `resolved_ids`。

## 3. 流程

1. **选择** —— 只挑对被引工作做了**可核查断言**的引用，其余跳过。两族 pattern：
   - `using` 族：第一人称使用（"we follow X's protocol"）—— 从
     `semantic/intent_classifier.py:121-142` **拷贝**正则过来（不 import，见 §7 陷阱）
   - `attribution` 族：**新写，P0** —— 对被引工作属性的断言（"X's metric is…"、
     "X contains N…"、"in X, the setup is…"）
2. **解析 ID** —— `bibguard --json` → `resolved_ids`
3. **取原文** —— arXiv e-print，先定到 `<id>v<n>` 再抓；完整性检查靠内容嗅探分派
   （gzip 先试 tar，否则当单文件 .tex；`%PDF` 走 PDF 路径）；≥3s 间隔 + User-Agent
4. **检索** —— 对断言词项取 top-k 段落，默认词项重叠/BM25（确定性）
5. **输出 worklist，不是判决** —— 没有 SUPPORTED 标签（与 claim-verify 同约定）。
   若 LLM 给意见，必须引用被引论文的原文段落，且该段落**机械校验**：
   规范化后逐字节出现在缓存工件里，记录 file+line；对不上就整条拒绝
6. **缓存** —— self-review 只读不抓，契约见 §5

## 4. CLI

```
python -m deepcite run --paper <dir> --bib <refs.bib>
       [--main main.tex] [--artifact KEY=PATH_OR_URL ...]
       [--cache-dir DIR] [--rank lexical|nli]

python -m deepcite annotate --paper <dir> --record <record_id> --opinion-file F
```

`run` 完全确定性，自己不调用任何 LLM。

退出码：

| 码 | 含义 |
|---|---|
| 0 | 跑完了（**包括"没选中任何引用"** —— 照样写缓存，`records: []`） |
| 2 | bib 缺失或解析不了（纯调用错误） |
| 3 | bibguard < 0.5.0 —— 响亮失败，**绝不**退回自己解析 |
| 4 | records > 0 且全部失败（单条取不到只是该条的 status，不是进程退出） |
| 5 | `annotate` 的引文校验不通过（硬失败） |
| 1 | 预料之外的错误（保留给未捕获异常） |

工件磁盘缓存：`${XDG_CACHE_HOME:-~/.cache}/integriref/deepcite/eprints/<id>/<version>/`
—— 工件按版本不可变，可跨论文共享，不放论文目录里（否则每篇重下且有被提交的风险）。

`<paper>/.cache/` 只放那个小 JSON。若 `git check-ignore` 显示它没被忽略，
打印警告并给出该加的那一行，**不替你改 `.gitignore`**。

## 5. 缓存契约 `<paper>/.cache/ref_check_deep.json`

```
{
  "format_version": 1,                   # 文件格式版本，独立于工具版本
  "tool": "integriref-deepcite", "tool_version": "x.y.z",
  "selection_regex_sha256": "<排序后 pattern 源串的哈希>",
  "bibguard_version": "0.5.0",
  "generated_at": "<ISO8601>",
  "paper": {"root": "<abs>", "main_tex": "main.tex",
            "files_sha256": {"sections/05_audit.tex": "<sha256>", ...}},
  "records": [{
    "record_id": "<sha256(bib_key|cited_id_or_empty|citing_file_relpath|context_sha256)[:16]>",
    "bib_key": "...", "cited_id": "arXiv:2305.14795v3" | "doi:..." | null,
    "artifact": {"kind": "arxiv_source|arxiv_pdf|pdf|data_file|url|none",
                 "ref": "<path or url>", "sha256": "<工件哈希>" | null},
    "citing": {"file": "...", "line": N, "byte_start": N, "byte_end": M,
               "sentence": "<raw>", "context_sha256": "..."},
    "selection": {"family": "using|attribution", "pattern_id": "U3|A2|...",
                  "claim_type": "protocol|metric|setup|dataset|split|number|other"},
    "status": "CANDIDATE_EVIDENCE|CLAIM_ABSENT_FROM_ARTIFACT|NO_FULLTEXT|WRONG_ARTIFACT_KIND|UNRESOLVED",
    "search_terms": ["..."],
    "passages": [{"file": "...", "line_start": N, "line_end": M,
                  "text": "<展示用规范化，<=60 词>", "rank_score": float}],
    "llm_opinion": null | {"text": "...", "quotes": [{"text": "...", "file": "...",
                                                      "line": N, "verified": true}]},
    "error": null | "..."
  }]
}
```

`files_sha256` 记录**每一个被扫过**的 .tex（完整的选择输入集），
这样读方遇到"引用所在文件不在表里"能判为未扫描，而不是误判为干净。

`CLAIM_ABSENT_FROM_ARTIFACT` 必须带上试过的 `search_terms` —— 这是 CIKM 那类
"protocol 归错论文"的形态，**人工优先级最高的一桶**，因为它是真的 block 过投稿的。

### 规范化规则（契约的一部分，两边必须一致）

`context_sha256 = sha256(norm(sentence).encode("utf-8")).hexdigest()`

`norm`：① NFKC ② 去掉每行未转义 `%` 之后的内容 ③ 删
`\\[Cc]ite[a-zA-Z]*\*?(\[[^\]]*\]){0,2}\{[^}]*\}` ④ `~`→空格
⑤ 所有空白压成单空格 ⑥ 去首尾空白 ⑦ 转小写

两条要紧的：
- **注释在"组装句子之前"从原始文件里剥掉**，`norm()` 的第②步只是防御性的空操作。
  否则跨行句子里一个行尾 `%` 会把后半句吃掉，两边哈希当场分叉。
- 这条 cite 正则让 `\citet`↔`\citep` 的改写**不改变哈希** —— 重排引用格式不会让缓存失效，
  这是有意设计。

### 失效（staleness）由读方判定

- 文件未变 → 按 `byte_start/byte_end` 切片再哈希
- 文件变了 → **先重试存下来的偏移量**：命中 = `FRESH`（别处被改过，本句原地未动）
- 偏移量没命中 → 在新规范化全文里**子串搜索**规范化句子：
  命中 = `MOVED`（句子只是挪了位，不算 stale）；没命中 = `STALE`

这样**句子切分完全是我这边的实现细节**。原始 spec 要求读方"重算该文件里引用该
bib_key 的句子的哈希"，那等于要求两套独立实现在 "et al."、"Fig. 3"、"…是 0.95。"
这种边界上完全一致 —— 必然做不到，结果是每条记录都被误判 STALE。这是 v1 里最严重的一处。

`annotate` 在**上下文已 stale 时也要拒绝**：哈希对不上现文件就不许挂意见，
否则一条意见会悄悄比它所评判的那个句子活得更久 —— 换个马甲的 ChroKnowBench 失败模式。

`selection_regex_sha256` 对读方只是 provenance：它手里没有正则副本，无法校验，
不要基于它做检查。

### 契约交叉验证结果（2026-10-05，两轮，已收敛）

对方手写了 fixture 缓存与生成器（`~/Tools/self-review/tests/fixtures/citations/`，
生成器 `make_fixture.py` 可复现），哈希全部自己独立算。我按 §5 从零实现 `norm()` 对照，
两轮结果：

**第二轮（最终）：5 组测试向量、7 条记录的 `context_sha256`、7 个 `record_id`、
字节偏移切片、以及三态失效判定，全部逐位一致。**
唯一不一致的 `sections/c.tex` 的 `files_sha256` 是对方故意做的 stale fixture，属预期。
三态判定也独立复现：`bao2023tallrec`→FRESH、`other2020`→STALE、
`other2021`→MOVED（偏移量故意后移 5 字节）、`95\%` 那条→FRESH。
对方的 reader 测试 13/13 通过。

注意这证明了什么、没证明什么：两套实现独立编码，但读的是**同一份 §5 散文**，
所以它抓实现分歧，抓不住"两边一起读错"。第一轮正是靠这个发现了下面第 2 条的洞。

**第一轮发现并已修掉的三件事：**

1. **字段名** `char_start/char_end` → **`byte_start`/`byte_end`**，语义保持 UTF-8 字节。
   证据：ChroKnowBench 那条记录跨度 `103`，而句子是 **100 码点 / 103 字节** ——
   叫 `char_*` 的字段按字符切片会静默少 3 字节且切错，且只在非 ASCII 行上错。
   读方现在切 `stripped.encode("utf-8")[a:b]`，代码里有注释写明不切 str。
2. **"未转义的 `%`" 当时既无定义也无测试**（三个源文件里没有任何 `\%`）。
   现已钉为"前面连续反斜杠个数为**偶数**"，并补了两条向量：
   `MQuAKE reports 95\% accuracy …`（95 活着进哈希）与
   `… is fixed\\% a comment …`（`\\` 之后的注释被剥掉）。
   这条要紧：一句 `95\%` 若被从 `%` 处截断，哈希里就丢了那个数字，
   而**那个数字就是断言本身** —— 正是本功能针对的 `claim_type: number`。
3. **失效判定改成三态**：文件变了先重试偏移量，命中记 `FRESH` 而不是 `MOVED`，
   再退到子串搜索（MOVED / STALE）。比直接搜索便宜，语义也更准。

**三条已确认的约定：**

- `files_sha256` 算文件**原始字节**（剥注释之前）。能用 `sha256sum` 直接核，
  且只改注释最多让读方多做一次子串搜索，不会误判 STALE。
- 注释剥离删到换行符**之前**为止，换行保留 —— 行号不漂。这条承重：
  向量 1 之所以哈希正确，就是因为换行活下来并在第⑤步成了 `we report accuracy` 里那个空格。
- 偏移量是 **UTF-8 字节**偏移。

**两条已记录、不修的限制**（两边一致，不是 drift；写在对方生成器的 docstring 里）：

- `\verb|%|` 和 `verbatim`/`lstlisting` 里的 `%` 是字面量，两边都会当注释误剥。
  引用句都在正文散文里，可接受。
- TeX 行尾 `%` 是吞掉换行的**续行符**，`the main met%\nric is…` 渲染成 `metric`，
  而 norm 得到 `met ric`。规范化文本会与渲染结果不同，切句也可能切错。正文散文里罕见。

## 6. 为什么判决环节不用 NLI

rebuttal Exp1 的两个结论都指向不用：

- FPR 0.68 是 {contradicted ∪ unsupported} 的 firing；contradicted-only 是
  recall 0.77 / FPR 0.36。**最好的配置也不够当自动判决。**
- 更致命的是 **template 污染**：把断言包进引用模板会让 supported→unsupported。
  而这个功能里的断言**不可避免**就是一句引用句（"Following Bai et al., we adopt…"），
  正好是当初测出污染的那个形态。`claim_extractor.py:87 _clean_claim` 只删
  `\cite{}` 标记，删标记 ≠ 删 "Following X," 这个句法框架，别指望它救场。

所以：判决走"LLM 必须引用 + 机械校验引文"。NLI 只留在第 4 步**排序**上 ——
不产出任何标签，出错只影响段落顺序，不继承 FPR 问题。
另外 GPU 上的 NLI 不是逐位可复现的，所以 `rank_score` 不进任何哈希。

## 7. 复用现有代码的四个陷阱（已实测，不是推测）

1. `IntentClassifier()` 虽然所有 torch import 都是惰性的，但 `__init__` 默认
   `use_model=True, use_contrast_model=True` 并立刻加载 —— 而 `models/intent_classifier`
   和 `models/contrast_detector` 都在盘上，所以真的会加载。
   要用得写 `IntentClassifier(use_model=False, use_contrast_model=False)`。
2. 公开的三分类输出把 **USING 合并进了 SUPPORTING**（~381-386 行），
   读 `classify()` 的标签正好丢掉要选的那个信号。
3. `semantic/__init__.py` 会连带 import `abstract_fetcher`/`semantic_pipeline` →
   `core.discovery` → `core.registry` → `requests`，所以拿不到干净的叶子 import。
   —— 这三条合起来的结论就是 §2.5 和"拷正则而不是 import"。
4. `~/Tools/final-reviewer/fixtures/calibrate.py:56` 的 `_archive_ok` 只认 gzip+tar，
   单文件 `.tex` e-print 和纯 PDF e-print 都返回 False，`_fetch` 接着按
   `--max-time 300/600/900` 重试三次才放弃 —— 一个纯 PDF 的引用要烧 ~15 分钟然后被
   错标成 NO_FULLTEXT。嗅探必须放在完整性检查**内部**。

（这四条已存进 memory：`reference_l1_reuse_traps.md`）

## 8. 验收测试

正例：
- `zhong2023mquake`（ROA-LLM 的 ACL 论文引的）：必须被 `attribution` 族选中，
  且 top-5 段落里要有一条是 MQuAKE 自己说主指标是什么的
- CIKM v17 的 TALLRec 归属：
  `/data/ProjectsBackup/snapshots/monthly/monthly.2026-06/GraphLLMRec/submissions/cikm2026`
  （对方核对过 `main.pdf` md5 `02c2274d…`）。**先拷到临时目录再跑**，不碰快照。
  期望落在 `CANDIDATE_EVIDENCE` 或 `CLAIM_ABSENT_FROM_ARTIFACT`，段落里要显示
  TALLRec 的 protocol 实际是什么

负例：普通背景引用（没有 using / attribution 措辞）必须**不**被选中。

另外要交三组 `输入→哈希` 测试向量给对方，两边测试用同一组，谁的实现漂了测试就红。

## 9. 分工

| 谁 | 做什么 | 额外审批 |
|---|---|---|
| 我（IntegriRef） | `deepcite/` 模块 | **就是本文档在等的那个批准** |
| 对方 | bibguard v0.5.0 `resolved_ids{doi, arxiv_id, s2_id, openalex_id}` + CHANGELOG —— **已本地提交** `~/Engineering/ref-check` `bef1a86`，未推未发布 | 推 GitHub / 发 PyPI 要他们的 user 另外批 |
| 对方 | `/ref-check --deep` 命令文件段落（声明三方分工、指向我的模块） | — |
| 对方 | `self_review.py citations --paper`：只读、标 STALE、永不抓取 | — |

选择正则**只存在于我的模块里**，对方的命令文件只指过来、不复述 ——
两份 pattern 列表必然漂移，而缓存键依赖它。

## 10. 我自己仍然担心的（请你一并权衡）

1. **成败全在选择质量。** `attribution` 族是新写的、未验证的。
   precision 低 → worklist 噪声大到没人看；recall 低 → 假安心。
   建议：先只在 ROA-LLM / crystleLLM 上量一次 precision/recall 再决定要不要做全量。
2. **没有 SUPPORTED 标签意味着输出永远是人工活。** 一篇 40 refs 的论文如果出 15 条记录、
   每条 5 段要人读，这是实打实的时间。动工前值得先估一下这个数。
3. **自动覆盖只有 3/4。** ChroKnowBench 那条的事实在**发布的数据文件**里，不在论文正文里，
   只能靠手工 `--artifact` 指过去。四个动机案例里，这条不是自动的。
4. **机会成本。** 你 memory 里记的 next steps 是：crawler 扩数据集（P0）、
   4 个 detector 接线（P1）、GEM 2026 论文（P0）。deepcite 是**新增范围**，不在那张单子上。
   正面理由是：这正好是 L0–L4 全都不做的事，可能是 GEM 的实质贡献点。
   但这个取舍是你的，不是我的。

---

## 11. 实际建成（as built，2026-10-05）

模块在 `deepcite/`，48 个离线测试 + 一组联网验收测试（`DEEPCITE_NETWORK=1` 才跑）。
`contract.py` 只用标准库，这样读写两侧都能实现。

**五处设计必须偏离或补充 spec，都是实测撞出来的：**

1. **bibguard 的 `arxiv_id` 对"按正式会议引用"的文献一律是 null。**
   实测 MQuAKE（booktitle=EMNLP）只拿到 `doi` 和 `openalex_id`；读源码确认 bibguard
   只在 bib 条目**本身带** arXiv id 时才查 arXiv（`core.py:147`），从不按标题搜。
   而 deepcite 整条取原文路径靠 arXiv id —— 验收测试那条会直接卡死。
   **因此 deepcite 自己做一次 arXiv 标题查找**，但严格限定为**定位工件**，
   不是第二个元数据解析器：匹配要求标题近乎相等（arXiv 相关性搜索会返回别的论文，
   取错工件比取不到更糟），且只接受带版本号的 id。
2. **检索器第一版不合格，已重写。** 朴素的 `1/sqrt(len)` 长度归一化把"只含
   benchmark 自己名字的单词窗口"顶到第一，真正定义主指标的段落掉出 top-5。
   改成正经 BM25（k1=1.2, b=0.75）+ 窗口最少 12 个实词（滤掉标题碎片）+
   按 `claim_type` 给相关线索词加权 + 同文件重叠窗口去重。
   改完 MQuAKE 验收通过：top-3 里出现
   "Multi-hop accuracy … **If any of the three questions is correctly answered**"
   —— 即 disjunction，正是 ROA-LLM 写错的那一点。
3. **A6 收紧**：原来 `(native|main|primary) … <artifact>` 会在
   `Primary metric: NDCG@10~\cite{k}` 上误报 —— 那只是给指标标出处，
   没对被引工作断言任何东西。现在要求后面跟 `is/are/uses/…` 等断言动词。
4. **默认跳过草稿与归档**（`archive/`、`old/`、`draft_*.tex` 等，可用 `include_all` 关掉）。
   在真实论文上实测：不跳的话 19 条里有 7 条来自 `draft_theorem_extension.tex`
   这类不会投出去的文件。
5. **`annotate` 也支持手工 `--artifact`**（data_file / url），不只 arXiv 缓存。
   原 spec 只描述了 arXiv 路径。

**worklist 规模实测（回答 §10.2）：** CIKM v17 真实论文，56 条参考文献、12 个 .tex 文件
→ **9 条记录**（每条 ≤5 段）。这是人读得完的量级。
其中 `experimental_setup.tex:61` 一句产生 2 条记录（protocol 同时归给
`hou2024large` 和 `sun2023chatgpt`，各自需单独核对）—— 正是 CIKM 那条 blocking 意见的形状。
`related_work.tex:9` 也被选中："LLMRank 同时评估 oracle (Section 3.1) 和 realistic
(Section 3.3)" —— 带节号的精确可核查断言。
`bao2023tallrec` 在这篇里只出现在背景罗列中，**正确地未被选中**。

**精度初判（§10.1 的小规模验证，尚未做成正式指标）：** 人工看了 5 条，4 条是真可核查断言，
1 条假阳性（已修，即上面第 3 条）。这不构成 precision/recall 数字 ——
要给出数字还需要在 ROA-LLM 和 crystleLLM 上标注，属未完成项。
