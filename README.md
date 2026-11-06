# cc-docx

Word（.docx）**编辑层**工具：编辑层提取 / 批注注入 / Redline 修订生成。由数据化 YAML「修改清单」驱动，每次任务不再现写 Python。

与 `cc-convert` 平行的**独立**工具（零共享代码、互不影响）：cc-convert 管"读正文"，cc-docx 管"改与审"。

## 环境

- Python：本机统一环境 `python3`（`~/.venvs/py`，3.14，已装 lxml / defusedxml / PyYAML / python-docx / pytest；依赖登记在 `~/claude-skills/_shared/python/requirements.in`）。不再使用自带 `.venv`（2026-11-04 退役，暂留未删）
- 测试：在本目录 `python3 -m pytest -q`
- 调用入口：`_工具/cc-docx/cc-docx`（bash wrapper → PATH 上的 python3）

## 六命令

```bash
# 1. 看一份 docx 的编辑层（批注 / 修订痕迹 / 段落表格结构，结构项含可见编号 label）
cc-docx extract X.docx [--what comments,redlines,structure] [--format md|json] [-o out]

# 2. 按 YAML 清单生成 Redline 修订（自动跑 QC，作者默认 REVIEWER；QC 不过拒绝输出 + 返回非零）
cc-docx redline X.docx --spec edits.yaml -o 修订版.docx

# 3. 按 YAML / JSON 清单注入批注（句级高亮；任一锚点未命中或歧义 → 全量失败清单、exit 2、不产出）
cc-docx comment X.docx --spec comments.yaml -o 批注版.docx

# 4. 校验：修订一致性（--forbid 禁碰词残留）/ 批注三件齐（--comments，逐 id 核 rangeStart·rangeEnd·reference）
cc-docx verify X.docx [--forbid 旧名1,旧名2] [--comments]

# 5. 导出带可见编号的段落结构（解析 numbering.xml + 样式挂接编号，自动编号合同的 1.1.1 / 第三条 也能还原）
cc-docx outline X.docx [--format md|json] [-o out]     # md 形态每行 label<TAB>text

# 6. 批注锚点体检：逐条列出批注挂在哪一条 / 款 / 项、正文提到哪些款项，报疑似错位（只警告，退出码恒 0）
cc-docx anchors X.docx [--format md|json] [-o out]
```

## 修改清单（spec）格式

### redline 清单

```yaml
meta:
  author: REVIEWER                # 可选，默认 REVIEWER
  date: 2026-06-20T00:00:00Z    # 可选，默认运行当日
  forbid: [乙集团, 乙方]         # 可选：QC 禁碰词，接受全部修订后若残留则拒绝输出
edits:
  - { op: replace,      find: 乙集团, with: 甲公司, scope: all, guard: dedup }
  - { op: insert_after, anchor: "本合同自双方签字之日起生效", text: "，并以英文版本为准" }
  - { op: delete_para,  anchor: "第十五条 保密义务" }
  - { op: table_cell,   table: 0, row: 0, col: 1, set: "PT SAMPLE CO" }
```

### op 词汇表

| op | 必填 | 可选 | 语义 |
|---|---|---|---|
| `replace` | find, with | scope, guard | 跨 run 文本替换，留 ins+del 痕迹 |
| `delete_text` | find | scope | 删除文本，留 del 痕迹 |
| `insert_after` | anchor, text | guard | 锚点串后插入，留 ins |
| `insert_before` | anchor, text | guard | 锚点串前插入，留 ins |
| `delete_para` | anchor | — | 整段删除（含段落标记） |
| `insert_para_after` | anchor, text | — | 锚点段后插入新段落 |
| `table_cell` | table, row, col | set 或 find | 表格单元格改写（按 0 起索引） |
| `table_row_delete` | table, row | — | 删表行（按 0 起索引） |

- `scope`：`all`（默认，全文）｜ 锚点串（仅含该串的段落内）。**`第N条` 范围 scope 暂未实现。**
- `guard: dedup`：防重——锚点后已紧跟相同插入文本则跳过。

### comment 清单（2026-09-26 起句级高亮）

```yaml
meta: { author: REVIEWER }
comments:
  - anchor: "乙方不退还该事项已付的全部费用"     # 必填：12–30 字连续原文，只高亮这一句
    clause: "第三条第2款"                          # 可选：歧义时缩到该条范围；未命中时整段兜底
    occ: 1                                          # 可选：锚点多处出现时取第 n 处
    range: sentence                                 # 默认 sentence；paragraph = 整段高亮
    text: |
      【高风险】风险提示：本款约定……
      修改建议：建议将本款改为：**因对方原因导致服务未完成的，对方应按比例退还已收费用**
```

- **定位**：锚点与段落文字（接受修订后视图：含 `w:ins`、排除 `w:del`）按"归一化子串"匹配——两侧同步去空白、统一弯 / 直引号与全 / 半角标点，命中后高亮的仍是原文精确范围。候选唯一 → 注入；多个 → 先用 `clause`（经 `outline.resolve_clause`）缩范围，再看 `occ`，否则报歧义并列出各候选的 i / 条款 label / 前 30 字；零候选且 `clause` 唯一 → 整段高亮并标 `⚠ 整段兜底`。
- **fail-closed**：所有条目先定位、收齐失败后一次性打印（`❌ n/m 条批注无法定位`），exit 2，不写文件；注入后逐 id 校验三件齐、批注增量 = 条目数，否则拒绝输出。
- **批注体**：`text` 每行成一段；行内 `**…**` → 加粗 + 下划线 run（不跨行）；`.json` 清单同样可用（`yaml.safe_load` 兼容）。
- 高亮范围跨 run、跨 `w:ins`，`w:del` 内文字跳过；整段模式下 `commentRangeStart` 插在 `w:pPr` 之后（旧实现插在 pPr 之前，违反结构约束，已修）。
- 引擎为 lxml 实现（`engine/comment.py` + `engine/locate.py` + `engine/match_norm.py`），与 redline 同一 XML 栈；旧 minidom 版（借自 contract-review）已退役，contract-review 不再自带任何 OOXML 脚本。
- 批注统一规格（不随宿主文档变）：宋体 / Times New Roman、五号（`sz=21`）、单倍行距、段前后 0、`snapToGrid=0`，run 与段落标记均显式带 `rFonts`+`w:hint=eastAsia`+`w:lang eastAsia=zh-CN`。缘起：2026-09-09 不写 rPr 时遇 `themeFontLang eastAsia="ja-JP"` 文档 WPS 渲染成 MS 明朝；2026-09-10 不写 sz/pPr 时继承宿主 Normal（line=360 + 小四）呈 1.5 倍行距大字。已知 WPS 特性（2026-10-13 实测）：批注框不按段落行距排版，静态显示固定约 1.15 倍，编辑中的段落临时变窄、重开文件即恢复，与 `line` 取值无关（240 / 276 均如此），无法从 XML 修正。该规格现只在本工具一处维护（contract-review 的同源脚本已于 2026-09-26 删除）。
- 同一规格写入宿主「批注文字」样式（`annotation text` 及其 `w:link` 字符样式：改 `rFonts`/`sz`/`szCs` 并清除主题字体属性；段落样式另写 `snapToGrid=0`、单倍行距、段前后 0、缩进 0，其余属性不动；样式不存在则新建 `CommentText`，基于默认段落样式），使律师在 WPS / Word 里手动新增的批注、批注内回车或粘贴生成的新段落也是宋体五号单倍行距。缘起：2026-10-09 某直播推广合同模板的该样式为「等线 Light」+ 主题字体，手动新增批注呈黑体类字体；2026-10-12 某战略合作框架协议无该样式，WPS 现场新建并继承 Normal（段后 8 磅、1.16 倍、11 号），手动新增批注行距变松、字大一号。

## 批注锚点规则（2026-11-06 律师裁定；contract-gen / contract-review 批注共同真源）

批注挂在哪一句，按批注针对的范围定：

1. 只针对某一项（（1）（2）…）→ 挂该项首句。
2. 针对整条（含其下（1）（2）（3）列举）→ 挂该条第一句主干句，如“甲方应履行下列义务：”。
3. 涉及连续几款 → 挂第一款首句。
4. 不挂表格下方的注释段；针对表格所在款的，挂表格上方该款首句。
5. 首句取到第一个“。”为止；款首有“开发风险。”这类不超过 8 字的小标题时，延到下一句；首句超过 80 字的，截到第一个“；”或“：”。

上游写法：contract-gen 的 `>` 说明块紧跟所针对条款（首款）的第一段，用「片段」框定首句（引用块归其上方最近正文段，写在一组条款末尾会挂到最后一个子项）；contract-review 报告的锚点句按上述范围选句。注入后跑 `cc-docx anchors` 体检。

### anchors 体检

逐条输出 `#id  锚点位置  「高亮前 20 字」  提到 …  判定`，末行 `⚠ 警告 n 条：…；ℹ 提示 m 条：…`。**只警告、不阻断**：⚠ 逐条人工复核（确属错位的回上游改说明块 / 锚点后重跑），ℹ 看一眼即可。

| 代号 | 判定 |
|---|---|
| W1 | 锚点在第（n）项，正文没有提到该项 |
| W2 | 正文提到的最前一款早于锚点所在款（只比同一条内的款）；或正文提到的最前一项早于锚点所在项（只比同一列举） |
| W3 | 锚点是表格后的非编号注释段 |

- 结构识别：条 = 编号标签或正文开头“第N条”；款 = 标签或正文开头 `A.B`（A 为当前条号），或自动编号中条下一级；项 = 标签或正文以（n）开头（中文数字亦可），归属其上方最近的款，无款的归条首段。
- 正文提及：`第A.B款`、`第X条第Y款`、`第（n）项`、`第A.B款第（n）项`。款号前紧挨“按 / 依 / 依照 / 按照 / 根据 / 参照 / 适用”或后接“所列 / 所述 / 所称 / 约定的 / 规定的 / 项下”的视为援引内容，不当作位置（用“、和及与或”相连的一组共同判断，输出里列为“援引”）；只带条号的提及（“按第四条执行”“第六条第（三）项”）不参与比较。
- 引述降级：批注正文与锚点段落去掉空白和标点后最长连续重合 ≥ 10 字（W1、项级 W2）或 ≥ 18 字（款级 W2）的，降为 ℹ 提示——批注逐字引述了锚点段，锚点大概率就在所讨论的条款上。W3 不降级。
- 回归（2026-11-06 某合作协议 25 条批注，真实样本只在本机）：改前稿 ⚠ 14 条，与律师认定的 14 处错位一致，另 11 条无警告；律师改定稿 ⚠ 0 条、ℹ 2 条。
- 已知边界：① 降级阈值来自这一份样本（错位批注与锚点段最长重合 15 字，正确批注 22 字），余量小；批注大段引述后一款而实应挂前一款的，只显示为 ℹ。② 跨条提及不比较（挂 8.2、正文提 6.4 与 8.1，只比 8.1）。③ 正文不写款号、只能凭语义判断的错位抓不到。④ 编号体例不是“第N条 / A.B / （n）”的外部合同，款级规则跳过，W1、W3 照常。

## 文本标准化闸（comment / redline 共用，2026-09-16）

清单里**写入文档的新文本**（`comments[].text`、`edits[].with` / `text` / `set`）在注入前统一做中文排版标准化，与 legal-docx 出口一致：

| 步骤 | 例 |
|---|---|
| ASCII 直引号 → 中文弯引号（奇偶配对） | `"单独同意"` → `“单独同意”` |
| CJK 上下文半角 `, : ; ! ? . ( )` → 全角 | `第三条:` → `第三条：` |
| 数字与汉字之间的半角空格删除 | `第 4.2 款`、`24 小时` → `第4.2款`、`24小时` |
| `...` → `……`；全角标点前后残留半角空格删除 | |

- **锚点类字段（`find` / `anchor` / `scope` / `forbid`）绝不标准化**，必须逐字匹配原文。
- 字母与汉字之间的空格、纯西文行不动；不做 legal-docx 的 `[X]→【X】`（批注常用 `[🟡 中]` 标签）。
- 关闭：`meta: { normalize: false }`。
- 某行 ASCII 双引号为奇数个时配对必错，stderr 打「提示」但不阻断，人工核对该行。
- 实现：`engine/normalize.py`（薄封装）+ `engine/text_normalizer.py`（逐字节复制自 `~/claude-skills/personal/legal-docx/scripts/text_normalizer.py`，不改；legal-docx 侧升级后 `cp` 覆盖即可）。
- 缘起：模型按 Markdown 技术文档习惯在数字前后加空格、用直引号，legal-docx 路径有 normalizer 兜底而 cc-docx 原样透传，导致批注 / 修订里出现「第 4.2 款」。上游提示词种子（contract-review `第 X 条："…"`、contract-gen / CONTRACT_INPUT_SPEC「第 7.5 款保留半角空格」）已同日改掉。

## QC 不变量（redline 强制闸）

每次 `redline` 输出前自动校验，**违反即拒绝写文件、返回非零**：

1. **reject==原文**：拒绝全部修订后的文本逐字符等于改前原文（确保只是留痕、未篡改底稿）
2. **forbid 残留**：接受全部修订后，`meta.forbid` 词出现次数必须为 0（如旧主体名漏改）

> `verify` 命令独立运行时，reject==原文 恒真（原文从同一文档反推），其实用价值在 forbid 残留检查。reject==原文 的真正护栏作用在 redline 流程内（改前先捕获原文）。

## 与现有 skill / 工具的分工

| 任务 | 走哪 |
|---|---|
| 读 docx 正文（省 token） | **cc-convert**（原生解析→正文 MD） |
| 看 docx 有哪些批注/修订/结构 | **cc-docx extract** |
| 中文法律合同生成 Redline | **cc-docx redline** |
| 给 docx 加批注（句级高亮） | **cc-docx comment** |
| 取合同条款结构（可见编号） | **cc-docx outline**（contract-review Phase A 以此替代 pandoc） |
| 批注注入后查锚点是否挂错位置 | **cc-docx anchors**（contract-gen 导出、contract-review Phase B 注入后各跑一道） |
| 完整合同审查（判断+报告+批注） | **contract-review**：Phase A 出报告，Phase B 用 `scripts/report_to_spec.py` 把报告翻成本工具 comment 清单后调用 `cc-docx comment`；skill 内无 OOXML 代码 |
| 起草合同的说明批注 | **contract-gen** → legal-docx `--notes-out` 把 `>` 引用块导出为 comment 清单 → `cc-docx comment` |
| MD 法律文书 → Word | **legal-docx** |
| MD 通用 → Word | **md2word** |
| 冷门 OOXML 操作 | **docx skill**（Anthropic，兜底/参考） |

## 借用代码来源（provenance）

- `engine/redline.py`：提炼自某项目 `redline_engine.py`（作者/日期/id 参数化、构件函数方法化）
- `engine/qc.py`：提炼自 `主体名称替换/.../​_verify.py` 的 `build_texts`
- `engine/ooxml.py` 的 unpack/pack/validate：源自 `contract-review/scripts/{ooxml_unpack,ooxml_pack}.py`
- `engine/comment.py`：2026-06 借自 `contract-review/scripts/document.py`（minidom），2026-09-26 以 lxml 重写并接管全部批注注入；`engine/locate.py` / `engine/match_norm.py` / `engine/outline.py` 同日新写
- `engine/text_normalizer.py`：逐字节复制自 legal-docx（见「文本标准化闸」）

## 测试

```bash
python3 -m pytest -q     # 2026-11-06：115 passed（anchors 17）；2026-11-04（3.14）：98 passed；2026-09-26：93 passed（outline 25 / locate·range·runner·cli 37 / 既有 31）
```

## 设计与计划文档

`docs/2026-06-20-cc-docx-design.md`（设计）、`docs/2026-06-20-cc-docx-implementation-plan.md`（实现计划）。

## 实战验收（2026-06-20 真实样例冒烟）

在某银团贷款项目真实合同上验证（只读复制到 `/tmp`，原件未动）：

- **extract**（已有修订的"修订版"合同，2199 段 / 19 表）：正确抽出 359 处插入 + 1602 处删除（作者 REVIEWER）+ 0 批注，修订内容为真实合同要素（金额、主体名、日期）。
- **redline 往返**（clean V1，2292 段）：按清单 `甲公司 → SAMPLE CO`（19 处），QC 通过（reject==原文 ✓、forbid「甲公司」残留 0 ✓），输出有效 .docx。
- **verify** 输出文件：`ok=true`。
- **extract** 输出文件：确认 19 处插入「SAMPLE CO」+ 19 处删除「甲公司」，作者 REVIEWER。

> 冒烟中发现并修复一个真 bug：YAML `meta.date` 被 `safe_load` 解析为 datetime 对象、lxml 属性拒收——`Doc._coerce_date` 已统一强制为 ISO 字符串（回归测试 `test_meta_date_accepts_datetime_from_yaml`）。这正是真实样例冒烟相对合成单测的价值。

**待用户人工验收**：在 Word 中打开 redline 输出，确认修订痕迹（接受=新版 / 拒绝=原版）与作者显示正常。
