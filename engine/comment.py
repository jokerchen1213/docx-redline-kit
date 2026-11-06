# engine/comment.py — 批注引擎（lxml 实现）
#
# 沿革：原为 contract-review/scripts/document.py 的 minidom 分叉；2026-09 重写为 lxml，
# 与 engine/redline.py 共用 OOXML 操作方式。部件创建（comments.xml / rels / Content_Types）
# 与批注字体字号规格保持原样。
#
# 能力：
#   - add_comment_range：按字符偏移句级高亮（跨 run、跨 w:ins；w:del 内 run 不计偏移、原样跳过）
#   - add_comment_paragraph：整段高亮（commentRangeStart 置于 w:pPr 之后）
#   - 批注体：按行成段；行内 **…** 转加粗 + 下划线 run
#   - verify_comments：逐 id 核 comment / rangeStart / rangeEnd / commentReference 齐全且顺序正确
# 字符偏移的文本模型见 engine/locate.py（两者必须一致）。
from __future__ import annotations

import copy
import re
import sys
from datetime import date as _date_cls, datetime, timezone
from pathlib import Path

from lxml import etree

from engine.locate import live_text_of, own_runs, run_pieces, run_spans
from engine.ooxml import WNS, q
from engine.redline import set_t

COMMENTS_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
)
COMMENTS_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
)
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"

_PARSER = etree.XMLParser(resolve_entities=False, huge_tree=True)
_MARKERS = ("commentRangeStart", "commentRangeEnd", "commentReference")
# 复制 rPr 给引用 run 时剔除的修订标记（避免复制出重复的修订 id）
_REV_IN_RPR = (q("ins"), q("del"), q("moveFrom"), q("moveTo"), q("rPrChange"))
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
# CT_RPr 子元素顺序（ECMA-376 §17.3.2.28），插入 rFonts / sz / szCs 时按此定位
_RPR_ORDER = (
    "rStyle", "rFonts", "b", "bCs", "i", "iCs", "caps", "smallCaps", "strike", "dstrike",
    "outline", "shadow", "emboss", "imprint", "noProof", "snapToGrid", "vanish", "webHidden",
    "color", "spacing", "w", "kern", "position", "sz", "szCs", "highlight", "u", "effect",
    "bdr", "shd", "fitText", "vertAlign", "rtl", "cs", "em", "lang", "eastAsianLayout",
    "specVanish", "oMath")
# CT_PPrBase 子元素顺序（ECMA-376 §17.3.1.26），写样式 pPr 的 snapToGrid / spacing / ind 时按此定位
_PPR_ORDER = (
    "pStyle", "keepNext", "keepLines", "pageBreakBefore", "framePr", "widowControl", "numPr",
    "suppressLineNumbers", "pBdr", "shd", "tabs", "suppressAutoHyphens", "kinsoku", "wordWrap",
    "overflowPunct", "topLinePunct", "autoSpaceDE", "autoSpaceDN", "bidi", "adjustRightInd",
    "snapToGrid", "spacing", "ind", "contextualSpacing", "mirrorIndents", "suppressOverlap", "jc",
    "textDirection", "textAlignment", "textboxTightWrap", "outlineLvl", "divId", "cnfStyle",
    "rPr", "sectPr", "pPrChange")
# CT_Style 中位于 rPr 之后的子元素（样式缺 rPr 时插在它们之前）
_STYLE_AFTER_RPR = ("tblPr", "trPr", "tcPr", "tblStylePr")
# CT_Style 中位于 pPr 之后的子元素（样式缺 pPr 时插在它们之前）
_STYLE_AFTER_PPR = ("rPr",) + _STYLE_AFTER_RPR


def _set_ordered_child(parent, local, attrs, order):
    """parent 下设置子元素 local：已有则整体替换属性，没有则按 order 顺序插入。"""
    el = parent.find(q(local))
    if el is None:
        el = etree.Element(q(local))
        idx = order.index(local)
        later = next((c for c in parent
                      if isinstance(c.tag, str) and etree.QName(c).localname in order
                      and order.index(etree.QName(c).localname) > idx), None)
        if later is not None:
            later.addprevious(el)
        else:
            parent.append(el)
    el.attrib.clear()
    for k, v in attrs.items():
        el.set(k, v)
    return el


def _parse(path):
    return etree.parse(str(path), _PARSER)


def _write(tree, path):
    tree.write(str(path), xml_declaration=True, encoding="UTF-8", standalone=True)


def _coerce_date(value):
    if value is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, _date_cls):
        return value.strftime("%Y-%m-%dT00:00:00Z")
    return str(value)


class Document:
    """编辑已解包的 .docx 目录，注入批注。"""

    # 批注 run / 段落必须显式带全套属性（字体、字号、语言、行距），不能交给宿主文档回退：
    #  - 不写 rFonts/lang：遇 settings.xml themeFontLang eastAsia="ja-JP" 的文档，WPS 把批注渲染成 MS 明朝（2026-09-09 实案）；
    #  - 不写 sz / pPr：批注段落继承宿主 Normal 样式（如 line=360 + 小四），批注框里呈 1.5 倍行距大字（2026-09-10 实案）。
    # 统一规格：宋体 / Times New Roman，五号（sz=21 半磅），单倍行距，段前后 0，脱离文档网格。
    #  - WPS 批注框不按段落行距排版：静态显示固定约 1.15 倍，编辑中的段落临时变窄、重开文件恢复，
    #    与 line 取值无关（2026-10-13 实测 240 / 276 均如此），无法从 XML 修正；Word 按本值显示。
    COMMENT_FONT_EASTASIA = "宋体"
    COMMENT_FONT_LATIN = "Times New Roman"
    COMMENT_FONT_SIZE = "21"  # half-points = 10.5pt（五号）
    COMMENT_LINE = "240"  # 单倍

    def __init__(self, unpacked_path, author="REVIEWER", initials="REVIEWER", date=None):
        self.unpacked_path = Path(unpacked_path)
        if not self.unpacked_path.exists():
            raise FileNotFoundError(f"Unpacked path not found: {self.unpacked_path}")
        self.word_path = self.unpacked_path / "word"
        self.document_path = self.word_path / "document.xml"
        self.comments_path = self.word_path / "comments.xml"
        self.rels_path = self.word_path / "_rels" / "document.xml.rels"
        self.content_types_path = self.unpacked_path / "[Content_Types].xml"

        self.author = author
        self.initials = initials
        self.date = _coerce_date(date)
        self.warnings = []

        self.doc_tree = _parse(self.document_path)
        self.doc_root = self.doc_tree.getroot()
        self._dirty = set()
        self._rev_next = None  # 拆分 run 时给 rPr 拷贝件内修订标记重新分配 w:id（懒初始化）
        self._ensure_comments_part()
        self.next_comment_id = self._get_next_comment_id()

    # ---------- 部件 ----------

    def _ensure_comments_part(self):
        if self.comments_path.exists():
            self.comments_tree = _parse(self.comments_path)
        else:
            root = etree.Element(q("comments"), nsmap={"w": WNS})
            self.comments_tree = etree.ElementTree(root)
            self._dirty.add("comments")
        self.comments_root = self.comments_tree.getroot()
        self._ensure_comments_relationship()
        self._ensure_comments_content_type()

    def _ensure_comments_relationship(self):
        if not self.rels_path.exists():
            raise FileNotFoundError(f"Missing rels file: {self.rels_path}")
        self.rels_tree = _parse(self.rels_path)
        root = self.rels_tree.getroot()
        rels = list(root.iter("{%s}Relationship" % PKG_REL_NS))
        if any(r.get("Type") == COMMENTS_REL_TYPE for r in rels):
            return
        max_id = 0
        for r in rels:
            rid = r.get("Id") or ""
            if rid.startswith("rId") and rid[3:].isdigit():
                max_id = max(max_id, int(rid[3:]))
        etree.SubElement(root, "{%s}Relationship" % PKG_REL_NS, {
            "Id": "rId%d" % (max_id + 1), "Type": COMMENTS_REL_TYPE, "Target": "comments.xml"})
        self._dirty.add("rels")

    def _ensure_comments_content_type(self):
        if not self.content_types_path.exists():
            raise FileNotFoundError(f"Missing content types file: {self.content_types_path}")
        self.ct_tree = _parse(self.content_types_path)
        root = self.ct_tree.getroot()
        for ov in root.iter("{%s}Override" % CT_NS):
            if ov.get("PartName") == "/word/comments.xml":
                return
        etree.SubElement(root, "{%s}Override" % CT_NS, {
            "PartName": "/word/comments.xml", "ContentType": COMMENTS_CONTENT_TYPE})
        self._dirty.add("ct")

    def _get_next_comment_id(self):
        max_id = -1
        for c in self.comments_root.iter(q("comment")):
            raw = c.get(q("id"))
            if raw and raw.lstrip("-").isdigit():
                max_id = max(max_id, int(raw))
        for tag in _MARKERS:
            for m in self.doc_root.iter(q(tag)):
                raw = m.get(q("id"))
                if raw and raw.lstrip("-").isdigit():
                    max_id = max(max_id, int(raw))
        return max_id + 1

    def _take_id(self, comment_id):
        if comment_id is None:
            comment_id = self.next_comment_id
        self.next_comment_id = max(self.next_comment_id, int(comment_id) + 1)
        return int(comment_id)

    @staticmethod
    def _style_child(style, local, after_locals):
        """取样式下的 pPr / rPr；没有则新建并插在 after_locals 所列子元素之前。"""
        el = style.find(q(local))
        if el is None:
            el = etree.Element(q(local))
            after = next((c for c in style if isinstance(c.tag, str)
                          and etree.QName(c).localname in after_locals), None)
            if after is not None:
                after.addprevious(el)
            else:
                style.append(el)
        return el

    def _new_annotation_text_style(self, root, by_id):
        """宿主无「批注文字」样式时新建一个（基于默认段落样式）；属性由调用方统一写入。"""
        sid = "CommentText"
        n = 1
        while sid in by_id:
            n += 1
            sid = f"CommentText{n}"
        style = etree.SubElement(root, q("style"), {q("type"): "paragraph", q("styleId"): sid})
        etree.SubElement(style, q("name"), {q("val"): "annotation text"})
        normal = next((s for s in root.iter(q("style"))
                       if s.get(q("type")) == "paragraph" and s.get(q("default")) in ("1", "true", "on")),
                      None)
        if normal is not None and normal.get(q("styleId")):
            etree.SubElement(style, q("basedOn"), {q("val"): normal.get(q("styleId"))})
        etree.SubElement(style, q("uiPriority"), {q("val"): "99"})
        etree.SubElement(style, q("unhideWhenUsed"))
        by_id[sid] = style
        return style

    def _normalize_comment_styles(self):
        """把宿主文档「批注文字」样式（annotation text 及其链接字符样式）改为批注统一规格。

        注入的批注 run / 段落自带显式格式，不受样式影响；但律师在 WPS / Word 里手动新增批注、
        或在批注内回车 / 粘贴生成新段落时，按该样式取格式：
          - 字体：宿主模板设成「等线 Light」+ 主题字体时，新增批注呈黑体类字体（2026-10-09 直播推广合同实案）；
          - 行距：宿主无该样式时 WPS 现场新建、继承 Normal（after=160 / line=278 / 11 号），
            新增批注比注入批注行距松、字大一号（2026-10-12 战略合作框架协议实案）。
        处理：样式不存在则新建；段落样式写入 snapToGrid=0 / 单倍行距 / 段前后 0 / 缩进 0（其余 pPr 子元素不动），
        段落样式与链接字符样式的 rFonts / sz / szCs 改为统一规格（主题字体属性一并清除，其余 rPr 不动）。
        """
        styles_path = self.word_path / "styles.xml"
        if not styles_path.exists():
            return 0
        tree = _parse(styles_path)
        root = tree.getroot()
        by_id = {s.get(q("styleId")): s for s in root.iter(q("style"))}
        para_styles = [s for s in root.iter(q("style"))
                       if s.find(q("name")) is not None
                       and s.find(q("name")).get(q("val"), "").strip().lower() == "annotation text"]
        if not para_styles:
            para_styles = [self._new_annotation_text_style(root, by_id)]
        targets = []
        for s in para_styles:
            targets.append(s)
            link = s.find(q("link"))
            if link is not None and link.get(q("val")) in by_id:
                targets.append(by_id[link.get(q("val"))])
        for s in para_styles:
            ppr = self._style_child(s, "pPr", _STYLE_AFTER_PPR)
            _set_ordered_child(ppr, "snapToGrid", {q("val"): "0"}, _PPR_ORDER)
            _set_ordered_child(ppr, "spacing", {
                q("before"): "0", q("after"): "0", q("line"): self.COMMENT_LINE, q("lineRule"): "auto"},
                _PPR_ORDER)
            _set_ordered_child(ppr, "ind", {
                q("left"): "0", q("right"): "0", q("firstLine"): "0", q("firstLineChars"): "0"}, _PPR_ORDER)
        for s in targets:
            rpr = s.find(q("rPr"))
            if rpr is None:
                rpr = etree.Element(q("rPr"))
                after = next((c for c in s if isinstance(c.tag, str)
                              and etree.QName(c).localname in _STYLE_AFTER_RPR), None)
                if after is not None:
                    after.addprevious(rpr)
                else:
                    s.append(rpr)
            _set_ordered_child(rpr, "rFonts", {
                q("ascii"): self.COMMENT_FONT_LATIN, q("hAnsi"): self.COMMENT_FONT_LATIN,
                q("eastAsia"): self.COMMENT_FONT_EASTASIA, q("cs"): self.COMMENT_FONT_LATIN,
            }, _RPR_ORDER)
            for tag in ("sz", "szCs"):
                _set_ordered_child(rpr, tag, {q("val"): self.COMMENT_FONT_SIZE}, _RPR_ORDER)
        if targets:
            _write(tree, styles_path)
        return len(targets)

    def save(self, validate=False):
        _ = validate
        self._normalize_comment_styles()
        _write(self.doc_tree, self.document_path)
        _write(self.comments_tree, self.comments_path)
        if "rels" in self._dirty:
            _write(self.rels_tree, self.rels_path)
        if "ct" in self._dirty:
            _write(self.ct_tree, self.content_types_path)

    # ---------- 注入 ----------

    @staticmethod
    def _marker(tag, cid):
        return etree.Element(q(tag), {q("id"): str(cid)})

    @staticmethod
    def _reference_run(cid, rpr_src):
        r = etree.Element(q("r"))
        if rpr_src is not None:
            rpr = copy.deepcopy(rpr_src)
            for c in list(rpr):
                if c.tag in _REV_IN_RPR:
                    rpr.remove(c)
            r.append(rpr)
        etree.SubElement(r, q("commentReference"), {q("id"): str(cid)})
        return r

    def _next_rev_id(self):
        """全文档 w:id 最大值 + 1 起递增（同 redline.Doc._scan_max_id/_nid 思路），保证修订 id 不重复。"""
        if self._rev_next is None:
            mx = 0
            for el in self.doc_root.iter():
                raw = el.get(q("id"))
                if raw and raw.isdigit():
                    mx = max(mx, int(raw))
            self._rev_next = mx + 1
        rid = self._rev_next
        self._rev_next += 1
        return str(rid)

    def _copy_rpr(self, rpr):
        """深拷贝 rPr；拷贝件内凡带 w:id 的修订标记（rPrChange 等）重新分配唯一 id。"""
        dup = copy.deepcopy(rpr)
        for el in dup.iter():
            if el.get(q("id")) is not None:
                el.set(q("id"), self._next_rev_id())
        return dup

    def _split_run(self, r, k):
        """在 run 文本偏移 k（0 < k < len）处拆为两个 run，后半段插在原 run 之后；复制 run 属性与 rPr。"""
        new = etree.Element(q("r"), dict(r.attrib))
        rpr = r.find(q("rPr"))
        if rpr is not None:
            new.append(self._copy_rpr(rpr))
        pieces = run_pieces(r)  # 持有引用，保证 lxml 代理对象身份稳定
        texts = {c: t for c, t in pieces}
        cum = 0
        moving = False
        for c in list(r):
            if c.tag == q("rPr"):
                continue
            if moving:
                new.append(c)
                continue
            if c in texts:
                n = len(texts[c])
                if cum + n <= k:
                    cum += n
                    continue
                if cum >= k:
                    moving = True
                    new.append(c)
                    continue
                off = k - cum  # 只有 w:t 会落到此处（tab/br 长度为 1）
                txt = texts[c]
                t2 = etree.SubElement(new, q("t"))
                set_t(t2, txt[off:])
                set_t(c, txt[:off])
                moving = True
            elif cum >= k:  # 零长度子节点（lastRenderedPageBreak 等）按位置归属
                moving = True
                new.append(c)
        r.addnext(new)
        return new

    def _split_at(self, p, k):
        for r, s, e in run_spans(p):
            if s < k < e:
                self._split_run(r, k - s)
                return

    def add_comment_range(self, p, start, end, text, comment_id=None):
        total = len(live_text_of(p))
        if start is None or end is None or not (0 <= start < end <= total):
            raise ValueError("批注区间非法：[%s, %s)，段落文本长 %d" % (start, end, total))
        self._split_at(p, start)
        self._split_at(p, end)
        hit = [r for r, s, e in run_spans(p) if e > s and s < end and e > start]
        if not hit:
            raise ValueError("批注区间未覆盖任何 run：[%d, %d)" % (start, end))
        first, last = hit[0], hit[-1]
        cid = self._take_id(comment_id)
        first.addprevious(self._marker("commentRangeStart", cid))
        rend = self._marker("commentRangeEnd", cid)
        last.addnext(rend)
        # 引用 run 挂到 w:ins / w:moveTo 之外，避免成为他人修订内容（拒绝修订时被连带删除）；
        # rangeEnd 则留在 ins 内紧贴末个命中 run：schema 允许区间标记位于 ins 内，且这样高亮区间最精确
        anchor = rend
        parent = rend.getparent()
        while parent is not p and parent.tag in (q("ins"), q("moveTo")):
            anchor = parent
            parent = parent.getparent()
        anchor.addnext(self._reference_run(cid, last.find(q("rPr"))))
        self._append_comment_entry(cid, text)
        return cid

    def add_comment_paragraph(self, p, text, comment_id=None):
        cid = self._take_id(comment_id)
        rs = self._marker("commentRangeStart", cid)
        first = p[0] if len(p) else None
        if first is not None and first.tag == q("pPr"):
            first.addnext(rs)
        else:
            p.insert(0, rs)
        runs = own_runs(p)
        rpr_src = runs[-1].find(q("rPr")) if runs else None
        p.append(self._marker("commentRangeEnd", cid))
        p.append(self._reference_run(cid, rpr_src))
        self._append_comment_entry(cid, text)
        return cid

    # ---------- 批注体 ----------

    def _comment_run_properties(self, bold=False):
        rpr = etree.Element(q("rPr"))
        etree.SubElement(rpr, q("rFonts"), {
            q("ascii"): self.COMMENT_FONT_LATIN,
            q("hAnsi"): self.COMMENT_FONT_LATIN,
            q("eastAsia"): self.COMMENT_FONT_EASTASIA,
            q("cs"): self.COMMENT_FONT_LATIN,
            q("hint"): "eastAsia",
        })
        if bold:  # CT_RPr 子元素顺序：rFonts, b, bCs, …, sz, szCs, …, u, …, lang
            etree.SubElement(rpr, q("b"))
            etree.SubElement(rpr, q("bCs"))
        for tag in ("sz", "szCs"):
            etree.SubElement(rpr, q(tag), {q("val"): self.COMMENT_FONT_SIZE})
        if bold:
            etree.SubElement(rpr, q("u"), {q("val"): "single"})
        etree.SubElement(rpr, q("lang"), {q("val"): "en-US", q("eastAsia"): "zh-CN"})
        return rpr

    def _comment_paragraph_properties(self):
        ppr = etree.Element(q("pPr"))  # CT_PPrBase 顺序：snapToGrid < spacing < ind < rPr
        etree.SubElement(ppr, q("snapToGrid"), {q("val"): "0"})
        etree.SubElement(ppr, q("spacing"), {
            q("before"): "0", q("after"): "0", q("line"): self.COMMENT_LINE, q("lineRule"): "auto"})
        etree.SubElement(ppr, q("ind"), {q("left"): "0", q("right"): "0", q("firstLine"): "0"})
        # 段落标记 rPr：WPS 以其决定空段/段尾的字体字号
        ppr.append(self._comment_run_properties())
        return ppr

    def _split_bold(self, line, lineno):
        parts = []
        pos = 0
        for m in _BOLD_RE.finditer(line):
            if m.start() > pos:
                parts.append((line[pos:m.start()], False))
            parts.append((m.group(1), True))
            pos = m.end()
        if pos < len(line):
            parts.append((line[pos:], False))
        if any("**" in t for t, b in parts if not b):
            msg = "批注第%d行存在未闭合的 **，已原样输出：%s" % (lineno, line[:40])
            self.warnings.append(msg)
            print("提示：%s" % msg, file=sys.stderr)
        return parts or [("", False)]

    def _append_comment_entry(self, cid, text):
        comment = etree.SubElement(self.comments_root, q("comment"), {
            q("id"): str(cid), q("author"): self.author,
            q("initials"): self.initials, q("date"): self.date})
        for n, line in enumerate(text.splitlines() or [""], 1):
            para = etree.SubElement(comment, q("p"))
            para.append(self._comment_paragraph_properties())
            for seg, bold in self._split_bold(line, n):
                run = etree.SubElement(para, q("r"))
                run.append(self._comment_run_properties(bold=bold))
                set_t(etree.SubElement(run, q("t")), seg)

    # ---------- 校验 ----------

    def _story_roots(self):
        """document.xml（内存态）+ 其他可能挂批注标记的部件（脚注 / 尾注 / 页眉页脚，磁盘态）。"""
        roots = [("document.xml", self.doc_root)]
        for pat in ("footnotes.xml", "endnotes.xml", "header*.xml", "footer*.xml"):
            for f in sorted(self.word_path.glob(pat)):
                try:
                    roots.append((f.name, _parse(f).getroot()))
                except etree.XMLSyntaxError:
                    continue
        return roots

    def verify_comments(self, strict=True):
        """逐 id 核验。strict=True 要求 rangeStart / rangeEnd / reference 三件齐；
        False 时只要求 reference（点批注），但区间标记存在时仍核顺序。"""
        ids = [c.get(q("id")) for c in self.comments_root.iter(q("comment"))]
        pos = {}  # id -> kind -> [(part, idx)]
        for part, root in self._story_roots():
            idx = 0
            for el in root.iter(*[q(t) for t in _MARKERS]):
                kind = etree.QName(el).localname
                pos.setdefault(el.get(q("id")), {}).setdefault(kind, []).append((part, idx))
                idx += 1
        required = _MARKERS if strict else ("commentReference",)
        ok_ids, bad = [], []
        for cid in ids:
            got = pos.get(cid, {})
            missing = [k for k in required if not got.get(k)]
            for k, lst in got.items():
                if len(lst) > 1:
                    missing.append("duplicate:%s" % k)
            s, e = got.get("commentRangeStart"), got.get("commentRangeEnd")
            if s and e and not (s[0][0] == e[0][0] and s[0][1] < e[0][1]):
                missing.append("order")
            if missing:
                bad.append({"id": cid, "missing": missing})
            else:
                ok_ids.append(cid)
        idset = set(ids)
        for cid in pos:
            if cid not in idset:
                bad.append({"id": cid, "missing": ["comment"]})
        return {"total": len(ids), "ok_ids": ok_ids, "bad": bad}

    # ---------- 兼容层（旧 API；runner / CLI 不再使用） ----------

    def find_paragraph_by_text(self, search_text, allow_fallback=False):
        """旧 API：返回首个含子串的段落。已取消"找不到就回退到前 20 段"的兜底，找不到一律抛错。"""
        _ = allow_fallback
        keys = [search_text] if isinstance(search_text, str) else list(search_text)
        for key in keys:
            for p in self.doc_root.iter(q("p")):
                if key in live_text_of(p):
                    return p
        raise ValueError(f"Paragraph not found for: {search_text}")

    def add_comment(self, start, end, text):
        """旧 API：整段批注（end 忽略）。"""
        _ = end
        return self.add_comment_paragraph(start, text)
