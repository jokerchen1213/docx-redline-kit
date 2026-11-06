# engine/outline.py — 可见编号解析（按 numbering.xml + styles.xml 还原 Word 自动编号）
# 自动编号（w:numPr）的"第三条 / 1.1.1"不在段落文字里，本模块按 Word 计数规则算出每段显示编号。
# 只读：不修改任何 XML 树。
import json
import re
import sys
import zipfile
from pathlib import Path

from lxml import etree

from engine.ooxml import q

_PARSER = etree.XMLParser(resolve_entities=False)
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"

# ---------------------------------------------------------------- 数字格式化

_CN_LOWER = "零一二三四五六七八九"
_CN_UPPER = "零壹贰叁肆伍陆柒捌玖"
_CN_UNITS_LOWER = ("", "十", "百", "千")
_CN_UNITS_UPPER = ("", "拾", "佰", "仟")
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
_TIANGAN = "甲乙丙丁戊己庚辛壬癸"
_DIZHI = "子丑寅卯辰巳午未申酉戌亥"
_warned = set()


def _warn_once(fmt):
    if fmt not in _warned:
        _warned.add(fmt)
        print("提示：未支持的编号格式 %s，按 decimal 显示" % fmt, file=sys.stderr)


def _cn_number(n, upper=False):
    """1–9999 中文读数：10→十、11→十一、101→一百零一；大写 10→壹拾。"""
    digits = _CN_UPPER if upper else _CN_LOWER
    units = _CN_UNITS_UPPER if upper else _CN_UNITS_LOWER
    if n == 0:
        return "〇" if not upper else "零"
    if n >= 10000:
        return str(n)
    s = str(n)
    out = []
    zero_pending = False
    L = len(s)
    for idx, ch in enumerate(s):
        d = int(ch)
        pos = L - 1 - idx
        if d == 0:
            zero_pending = bool(out)
            continue
        if zero_pending:
            out.append(digits[0])
            zero_pending = False
        out.append(digits[d] + units[pos])
    r = "".join(out)
    # 小写 10–19 省略"一"：一十一 → 十一
    if not upper and 10 <= n <= 19:
        r = r[1:]
    return r


def _letters(n, upper=False):
    if n <= 0:
        return str(n)
    ch = chr(ord("a") + (n - 1) % 26) * ((n - 1) // 26 + 1)
    return ch.upper() if upper else ch


def _roman(n, upper=False):
    if n <= 0 or n >= 4000:
        return str(n)
    vals = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
            (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]
    out = []
    for v, sym in vals:
        while n >= v:
            out.append(sym)
            n -= v
    r = "".join(out)
    return r if upper else r.lower()


def _ordinal(n):
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return "%d%s" % (n, suf)


def format_number(n, fmt):
    """按 w:numFmt 把计数值 n 格式化为显示文本（bullet/none 由调用方处理）。"""
    if fmt in (None, "", "decimal"):
        return str(n)
    if fmt in ("chineseCounting", "chineseCountingThousand", "taiwaneseCounting",
               "taiwaneseCountingThousand", "japaneseCounting", "koreanCounting"):
        return _cn_number(n)
    if fmt in ("chineseLegalSimplified", "japaneseLegal"):
        return _cn_number(n, upper=True)
    if fmt in ("ideographDigital", "taiwaneseDigital", "japaneseDigitalTenThousand"):
        return "".join("〇一二三四五六七八九"[int(c)] for c in str(n))
    if fmt == "lowerLetter":
        return _letters(n)
    if fmt == "upperLetter":
        return _letters(n, upper=True)
    if fmt == "lowerRoman":
        return _roman(n)
    if fmt == "upperRoman":
        return _roman(n, upper=True)
    if fmt == "decimalEnclosedParen":
        return "(%d)" % n
    if fmt == "decimalEnclosedFullstop":
        return "%d." % n
    if fmt in ("decimalEnclosedCircle", "decimalEnclosedCircleChinese"):
        return _CIRCLED[n - 1] if 1 <= n <= len(_CIRCLED) else str(n)
    if fmt == "decimalZero":
        return "%02d" % n if n < 10 else str(n)
    if fmt == "decimalFullWidth":
        return "".join(chr(ord(c) + 0xFEE0) for c in str(n))
    if fmt == "ideographTraditional":
        return _TIANGAN[(n - 1) % 10] if n >= 1 else str(n)
    if fmt == "ideographZodiac":
        return _DIZHI[(n - 1) % 12] if n >= 1 else str(n)
    if fmt == "ordinal":
        return _ordinal(n)
    _warn_once(fmt)
    return str(n)


# ---------------------------------------------------------------- 段落文本（接受修订视图，只读）

def _owner_para(el):
    p = el.getparent()
    while p is not None and p.tag != q("p"):
        p = p.getparent()
    return p


def _in_removed(el, stop):
    """el 是否位于 w:del / w:moveFrom 内（接受修订后消失）。"""
    p = el.getparent()
    while p is not None and p is not stop:
        if p.tag in (q("del"), q("moveFrom")):
            return True
        p = p.getparent()
    return False


def para_text(p):
    """接受修订后的段落文本：含 w:ins，排除 w:del；tab→空格，br/cr→换行；不含嵌套文本框段落。"""
    out = []
    t_tag, tab_tag, br_tag, cr_tag = q("t"), q("tab"), q("br"), q("cr")
    for el in p.iter(t_tag, tab_tag, br_tag, cr_tag):
        if _owner_para(el) is not p:
            continue
        # pPr/rPr 里没有这些标签；w:tab 也出现在 w:tabs（段落制表位定义）中，须排除
        par = el.getparent()
        if par is None or par.tag != q("r"):
            continue
        if _in_removed(el, p):
            continue
        if el.tag == t_tag:
            out.append(el.text or "")
        elif el.tag == tab_tag:
            out.append(" ")
        else:
            out.append("\n")
    return "".join(out)


# ---------------------------------------------------------------- numbering / styles 模型

def _val(el, tag):
    if el is None:
        return None
    c = el.find(q(tag))
    return None if c is None else c.get(q("val"))


def _int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


class _Styles:
    def __init__(self, root):
        self.by_id = {}
        self.default_para = None
        if root is None:
            return
        for s in root.iter(q("style")):
            sid = s.get(q("styleId"))
            if sid is None:
                continue
            self.by_id[sid] = s
            if s.get(q("type")) == "paragraph" and s.get(q("default")) in ("1", "true", "on"):
                self.default_para = sid

    def num_pr(self, style_id):
        """沿 basedOn 链找样式的 (numId, ilvl)；返回 (numId|None, ilvl|None)。"""
        seen = set()
        num_id = ilvl = None
        sid = style_id
        while sid and sid not in seen and sid in self.by_id:
            seen.add(sid)
            s = self.by_id[sid]
            pPr = s.find(q("pPr"))
            np_ = pPr.find(q("numPr")) if pPr is not None else None
            if np_ is not None:
                if num_id is None:
                    num_id = _val(np_, "numId")
                if ilvl is None:
                    ilvl = _val(np_, "ilvl")
                if num_id is not None:
                    break
            sid = _val(s, "basedOn")
        return num_id, ilvl


class _Numbering:
    def __init__(self, root, styles):
        self.abstract = {}   # abstractNumId -> element
        self.nums = {}       # numId -> element
        self.styles = styles
        if root is None:
            return
        for a in root.findall(q("abstractNum")):
            self.abstract[a.get(q("abstractNumId"))] = a
        for n in root.findall(q("num")):
            self.nums[n.get(q("numId"))] = n

    def resolve_abstract(self, num_id, depth=0):
        """numId → 实际承载 lvl 定义的 abstractNumId（处理 numStyleLink）。"""
        n = self.nums.get(num_id)
        if n is None or depth > 5:
            return None
        aid = _val(n, "abstractNumId")
        a = self.abstract.get(aid)
        if a is None:
            return None
        if a.find(q("lvl")) is None:
            link = _val(a, "numStyleLink")
            if link:
                nid2, _ = self.styles.num_pr(link)
                if nid2 and nid2 != num_id:
                    return self.resolve_abstract(nid2, depth + 1)
        return aid

    def lvl_def(self, num_id, aid, ilvl):
        """取 lvl 定义：优先 num 的 lvlOverride/lvl，否则 abstractNum 的 lvl。"""
        n = self.nums.get(num_id)
        if n is not None:
            for ov in n.findall(q("lvlOverride")):
                if _int(ov.get(q("ilvl"))) == ilvl:
                    lv = ov.find(q("lvl"))
                    if lv is not None:
                        return lv
        a = self.abstract.get(aid)
        if a is None:
            return None
        for lv in a.findall(q("lvl")):
            if _int(lv.get(q("ilvl"))) == ilvl:
                return lv
        return None

    def start_overrides(self, num_id):
        n = self.nums.get(num_id)
        res = {}
        if n is None:
            return res
        for ov in n.findall(q("lvlOverride")):
            so = ov.find(q("startOverride"))
            if so is not None:
                res[_int(ov.get(q("ilvl")), 0)] = _int(so.get(q("val")), 0)
        return res

    def ilvl_for_style(self, aid, style_id):
        """abstractNum 中 w:lvl/w:pStyle 指向该样式的级别。"""
        a = self.abstract.get(aid)
        if a is None or not style_id:
            return None
        for lv in a.findall(q("lvl")):
            if _val(lv, "pStyle") == style_id:
                return _int(lv.get(q("ilvl")))
        return None


def _lvl_info(lv):
    fmt = _val(lv, "numFmt") or "decimal"
    return {
        "fmt": fmt,
        "text": _val(lv, "lvlText") if lv.find(q("lvlText")) is not None else "",
        "start": _int(_val(lv, "start"), 0),
        "isLgl": lv.find(q("isLgl")) is not None and _val(lv, "isLgl") not in ("0", "false", "off"),
        "restart": _int(_val(lv, "lvlRestart")),
    }


# ---------------------------------------------------------------- 主流程

def _para_num(p, styles, numbering):
    """段落的 (numId, ilvl, style)；无编号返回 (None, -1, style)。"""
    pPr = p.find(q("pPr"))
    style = _val(pPr, "pStyle") or ""
    d_num = d_ilvl = None
    if pPr is not None:
        np_ = pPr.find(q("numPr"))
        if np_ is not None:
            d_num = _val(np_, "numId")
            d_ilvl = _val(np_, "ilvl")
    s_num = s_ilvl = None
    sid = style or styles.default_para
    if sid:
        s_num, s_ilvl = styles.num_pr(sid)
    num_id = d_num if d_num is not None else s_num
    if num_id is None or num_id == "0" or num_id not in numbering.nums:
        return None, -1, style
    ilvl = _int(d_ilvl) if d_ilvl is not None else _int(s_ilvl)
    if ilvl is None:
        aid = numbering.resolve_abstract(num_id)
        ilvl = numbering.ilvl_for_style(aid, sid)
    if ilvl is None:
        ilvl = 0
    return num_id, ilvl, style


def _in_table(p):
    a = p.getparent()
    while a is not None:
        if a.tag == q("tc"):
            return True
        a = a.getparent()
    return False


def _in_fallback(p):
    """mc:Fallback 内的段落是 mc:Choice 的重复副本，不参与计数。"""
    a = p.getparent()
    while a is not None:
        if a.tag == "{%s}Fallback" % MC_NS:
            return True
        a = a.getparent()
    return False


def _mark_deleted(p):
    pPr = p.find(q("pPr"))
    if pPr is None:
        return False
    rPr = pPr.find(q("rPr"))
    return rPr is not None and rPr.find(q("del")) is not None


def outline_from_parts(document_root, numbering_root=None, styles_root=None):
    styles = _Styles(styles_root)
    numbering = _Numbering(numbering_root, styles)
    counters = {}      # aid -> {ilvl: 当前值}
    stacks = {}        # aid -> {ilvl: label}（祖先链）
    pending = {}       # aid -> {ilvl: startOverride 值}
    seen_nums = set()
    out = []
    for i, p in enumerate(document_root.iter(q("p"))):
        text = para_text(p)
        num_id, ilvl, style = _para_num(p, styles, numbering)
        label, path, level = "", "", -1
        aid = numbering.resolve_abstract(num_id) if num_id is not None else None
        lv = numbering.lvl_def(num_id, aid, ilvl) if aid is not None else None
        # 接受修订后整段消失（段落标记已删且无存活文字）→ 不计数
        skip = _in_fallback(p) or (_mark_deleted(p) and not text.strip())
        if lv is not None and not skip:
            level = ilvl
            ctr = counters.setdefault(aid, {})
            stk = stacks.setdefault(aid, {})
            pend = pending.setdefault(aid, {})
            if num_id not in seen_nums:
                seen_nums.add(num_id)
                for L, v in numbering.start_overrides(num_id).items():
                    pend[L] = v
            info = _lvl_info(lv)
            # 本级递增
            if ilvl in pend:
                ctr[ilvl] = pend.pop(ilvl)
            elif ctr.get(ilvl) is None:
                ctr[ilvl] = info["start"]
            else:
                ctr[ilvl] += 1
            # 更深级按 lvlRestart 重置（默认：任一更高级出现即重置）
            for M in list(ctr.keys()):
                if M <= ilvl:
                    continue
                mlv = numbering.lvl_def(num_id, aid, M)
                rs = _lvl_info(mlv)["restart"] if mlv is not None else None
                rs = M if rs is None else rs
                if rs != 0 and ilvl < rs:
                    ctr[M] = None
            for M in list(stk.keys()):
                if M > ilvl:
                    del stk[M]
            label = _render(info, ctr, numbering, num_id, aid, ilvl)
            stk[ilvl] = label
            path = " > ".join(stk[k] for k in sorted(stk) if k <= ilvl and stk[k] != "")
        out.append({
            "i": i,
            "label": label,
            "level": level,
            "text": text,
            "style": style,
            "in_table": _in_table(p),
            "path": path,
        })
    return out


def _render(info, ctr, numbering, num_id, aid, ilvl):
    fmt = info["fmt"]
    tmpl = info["text"] or ""
    if fmt == "bullet":
        return tmpl
    if fmt == "none" and "%" not in tmpl:
        return tmpl

    def rep(m):
        n = int(m.group(1)) - 1
        lv = numbering.lvl_def(num_id, aid, n)
        li = _lvl_info(lv) if lv is not None else {"fmt": "decimal", "start": 1}
        v = ctr.get(n)
        if v is None:
            v = li["start"]
        f = li["fmt"]
        if f == "none":
            return ""
        if info["isLgl"]:
            f = "decimal"
        return format_number(v, f)

    return re.sub(r"%([1-9])", rep, tmpl)


def build_outline(docx_path):
    parts = {}
    with zipfile.ZipFile(str(docx_path)) as z:
        names = set(z.namelist())
        for key, member in (("document", "word/document.xml"),
                            ("numbering", "word/numbering.xml"),
                            ("styles", "word/styles.xml")):
            parts[key] = etree.fromstring(z.read(member), _PARSER) if member in names else None
    return outline_from_parts(parts["document"], parts["numbering"], parts["styles"])


# ---------------------------------------------------------------- 条款定位

_CN_DIGIT = {c: i for i, c in enumerate("零一二三四五六七八九")}
_CN_DIGIT.update({"〇": 0, "两": 2, "壹": 1, "贰": 2, "叁": 3, "肆": 4, "伍": 5,
                  "陆": 6, "柒": 7, "捌": 8, "玖": 9})
_CN_UNIT = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CN_CHARS = "".join(_CN_DIGIT) + "".join(_CN_UNIT)
_NUM_RE = "[0-9%s]+" % _CN_CHARS


def cn_to_int(s):
    """中文或阿拉伯数字串 → int；无法解析返回 None。"""
    if s is None or s == "":
        return None
    if s.isdigit():
        return int(s)
    total, cur = 0, None
    has_unit = any(c in _CN_UNIT for c in s)
    if not has_unit:
        # 逐位读法：一〇一
        try:
            return int("".join(str(_CN_DIGIT[c]) for c in s))
        except KeyError:
            return None
    for c in s:
        if c in _CN_DIGIT:
            cur = _CN_DIGIT[c]
        elif c in _CN_UNIT:
            total += (1 if cur is None else cur) * _CN_UNIT[c]
            cur = None
        else:
            return None
    if cur:
        total += cur
    return total


def _width(s):
    """全角→半角、去空白。"""
    out = []
    for ch in s or "":
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:
            ch = chr(o - 0xFEE0)
        elif ch == "　":
            ch = " "
        out.append(ch)
    return re.sub(r"\s+", "", "".join(out))


def _canon(s):
    """宽度归一 + 中文数字串转阿拉伯数字。"""
    s = _width(s)
    return re.sub("[%s]+" % _CN_CHARS, lambda m: str(cn_to_int(m.group(0)))
                  if cn_to_int(m.group(0)) is not None else m.group(0), s)


def _label_num(label):
    """标签中的末级数值：'3.' / '3、' / '(3)' / '1.3' / '第3条' → 3。"""
    c = _canon(label)
    m = re.fullmatch(r"第(\d+)[条款项]", c)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"\(?(?:\d+\.)*(\d+)[\.、)]?", c)
    return int(m.group(1)) if m else None


_ART_RE = re.compile(r"^第(\d+)条")


def _article_no(p):
    """段落作为"条"的编号及命中层级：(n, tier)；非条段返回 (None, None)。
    tier 1 = 显式"第N条"（自动编号 label 或文字编号正文开头）；tier 2 = 自动编号顶级纯数字。"""
    lab = p.get("label") or ""
    if lab:
        m = re.fullmatch(r"第(\d+)条", _canon(lab))
        if m:
            return int(m.group(1)), 1
        if p.get("level") == 0 and re.fullmatch(r"\d+[\.、]?", _canon(lab)):
            return int(re.match(r"\d+", _canon(lab)).group(0)), 2
        return None, None
    m = _ART_RE.match(_canon((p.get("text") or "")[:20]))
    if m:
        return int(m.group(1)), 1
    return None, None


def _find_articles(paras, n):
    t1 = [k for k, p in enumerate(paras) if _article_no(p) == (n, 1)]
    if t1:
        return t1, 1
    return [k for k, p in enumerate(paras) if _article_no(p) == (n, 2)], 2


_PARA_TEXT_RE = re.compile(r"^(?:\((\d+)\)|(\d+)[、．.](?!\d))")


def _is_clause(p, art_no, k, art_label=""):
    lab = p.get("label") or ""
    c = _canon(lab)
    parts = [x for x in (p.get("path") or "").split(" > ") if x]
    # 与"条"同属一个自动编号列表时，只认紧邻下一级（避免把"项"当"款"）
    if art_label and len(parts) >= 2 and parts[0] == art_label and len(parts) != 2:
        return False
    if lab:
        if c in {str(k), "%d." % k, "%d.%d" % (art_no, k), "(%d)" % k, "%d、" % k,
                 "%d.%d." % (art_no, k), "%d)" % k}:
            return True
        if len(parts) == 2 and _label_num(parts[1]) == k:
            return True
        return False
    m = _PARA_TEXT_RE.match(_canon((p.get("text") or "")[:12]))
    if m:
        return int(m.group(1) or m.group(2)) == k
    return False


def resolve_clause(paras, label):
    """按条款写法定位段落，返回段落 i 列表（文档顺序）。"""
    want = _canon(label)
    # 1) 精确等于 label（先严格宽度归一比较，再中文/阿拉伯互认）
    exact = [p["i"] for p in paras if p.get("label") and _width(p["label"]) == _width(label)]
    m = re.fullmatch(r"第(\d+)条(?:第(\d+)[款项])?", want)
    if not m:
        if exact:
            return sorted(exact)
        return sorted(p["i"] for p in paras if p.get("label") and _canon(p["label"]) == want)
    art_no = int(m.group(1))
    idxs, tier = _find_articles(paras, art_no)
    if m.group(2) is None:
        return sorted(paras[k]["i"] for k in idxs)
    k_no = int(m.group(2))
    hits = []
    for k0 in idxs:
        j = k0 + 1
        while j < len(paras):
            n, t = _article_no(paras[j])
            if n is not None and t == tier:
                break
            if _is_clause(paras[j], art_no, k_no, paras[k0].get("label") or ""):
                hits.append(paras[j]["i"])
            j += 1
    return sorted(set(hits))


# ---------------------------------------------------------------- 输出

def to_markdown(paras):
    lines = []
    for p in paras:
        if not p["label"] and not p["text"].strip():
            continue
        text = p["text"].replace("\n", " ")
        if p["in_table"]:
            text = "[表] " + text
        lines.append("%s\t%s" % (p["label"], text))
    return "\n".join(lines)


def to_json(paras):
    return json.dumps(paras, ensure_ascii=False, indent=2)


def _cmd_outline(a):
    paras = build_outline(a.docx)
    text = to_json(paras) if a.format == "json" else to_markdown(paras)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


def add_cli(subparsers):
    """注册 outline 子命令：cc-docx outline X.docx [--format md|json] [-o out]"""
    o = subparsers.add_parser("outline", help="导出段落可见编号（自动编号还原）")
    o.add_argument("docx")
    o.add_argument("--format", default="md", choices=["md", "json"])
    o.add_argument("-o", "--out")
    o.set_defaults(fn=_cmd_outline)
    return o
