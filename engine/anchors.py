# engine/anchors.py — 批注锚点体检（只读、只警告）
# 逐条列出"批注挂在哪一条 / 款 / 项"与"批注正文提到哪些款项"，按 README「批注锚点规则」报疑似错位：
#   W1 锚点在第（n）项，正文未提该项
#   W2 正文提到的最前一款（同条内）或一项（同一列举内）早于锚点
#   W3 锚点是表格后的非编号注释段
# 批注正文大段引述锚点段（最长连续重合达阈值）时，W1 / W2 降为"提示"。退出码恒 0（文件不可读除外）。
import json
import re
import zipfile
from difflib import SequenceMatcher
from pathlib import Path

from lxml import etree

from engine import outline
from engine.ooxml import WNS, q

_PARSER = etree.XMLParser(resolve_entities=False)

ITEM_QUOTE_MIN = 10     # 项级（W1 / W2 项）降级阈值：批注逐字引述锚点段 ≥ 10 字
CLAUSE_QUOTE_MIN = 18   # 款级（W2 款）降级阈值：多款批注常顺带引述锚点款，门槛更高

_NUM = "[0-9%s]+" % outline._CN_CHARS
_CL_DOTTED = re.compile(r"第(\d+)\.(\d+)(?:\.\d+)*[条款](?:第\((%s)\)项)?" % _NUM)
_CL_ART = re.compile(r"第(%s)条第\(?(%s)\)?款(?:第\((%s)\)项)?" % (_NUM, _NUM, _NUM))
_ITEM_BARE = re.compile(r"(?<![条款])第\((%s)\)项" % _NUM)
_GROUP_JOIN = re.compile(r"^[、,和及与或]$")
_CITE_PRE = ("按照", "依照", "根据", "参照", "适用", "按", "依")
_CITE_POST = ("所列", "所述", "所称", "约定的", "规定的", "项下")
_ITEM_RE = re.compile(r"^\((\d+)\)")
_CLAUSE_TEXT_RE = re.compile(r"^(\d+)\.(\d+)(?![\d.])")
_CLAUSE_LABEL_RE = re.compile(r"^(\d+)\.(\d+)\.?$")


# ---------------------------------------------------------------- 文档结构

def _structure(paras):
    """在 outline 条目上标注条 / 款 / 项归属：art、art_label、cl、item、kind（article/clause/item/body/table）、note。"""
    art, art_label, cl = None, "", None
    prev_in_table = False
    out = []
    for o in paras:
        lab = outline._canon(o.get("label") or "")
        txt = outline._canon((o.get("text") or "")[:16])
        kind, item, note = "body", None, False
        if o.get("in_table"):
            kind = "table"
        else:
            a, _ = outline._article_no(o)
            if a is not None:
                art, cl, kind = a, None, "article"
                mm = re.match(r"\s*(第\S+?条)", o.get("text") or "")
                art_label = o.get("label") or (mm.group(1) if mm else "第%d条" % a)
            else:
                m = (_CLAUSE_LABEL_RE.match(lab) if lab else _CLAUSE_TEXT_RE.match(txt))
                parts = [x for x in (o.get("path") or "").split(" > ") if x]
                if m and art is not None and int(m.group(1)) == art:
                    cl, kind = int(m.group(2)), "clause"
                elif (lab and art is not None and len(parts) == 2 and parts[0] == art_label
                      and not _ITEM_RE.match(lab) and outline._label_num(lab) is not None):
                    cl, kind = outline._label_num(lab), "clause"
                else:
                    mi = _ITEM_RE.match(lab or txt)
                    if mi:
                        kind, item = "item", int(mi.group(1))
                    elif not lab and prev_in_table:
                        note = True
        if (o.get("text") or "").strip():
            prev_in_table = bool(o.get("in_table"))
        out.append({"art": art, "art_label": art_label, "cl": cl, "kind": kind,
                    "item": item, "note": note})
    return out


def _location(s):
    if s["kind"] == "table":
        return "表格内"
    loc = s["art_label"] or "（条外）"
    if s["cl"] is not None:
        loc += " %d.%d" % (s["art"], s["cl"])
    if s["kind"] == "item":
        loc += "（%d）" % s["item"]
    elif s["note"]:
        loc += " 表后注释段"
    elif s["kind"] == "body" and s["cl"] is None and s["art"] is not None:
        loc += " 条首段"
    return loc


# ---------------------------------------------------------------- 批注正文里的款项

def _int(s):
    return outline.cn_to_int(s)


def mentions(text):
    """返回 {"clauses": [(条, 款)…] 指向位置的款, "cited": [(条, 款)…] 援引内容的款,
    "items": [(条|None, 款|None, 项)…]}；条款号为 int。"""
    w = outline._width(text or "")
    hits = []
    for m in _CL_DOTTED.finditer(w):
        hits.append((m.start(), m.end(), int(m.group(1)), int(m.group(2)), m.group(3)))
    for m in _CL_ART.finditer(w):
        a, c = _int(m.group(1)), _int(m.group(2))
        if a is not None and c is not None:
            hits.append((m.start(), m.end(), a, c, m.group(3)))
    hits.sort()
    # 用"、和及与或"相连的几处款号为一组，组前"按 / 依…"或组后"所列 / 约定的…"→ 援引内容
    groups, cur = [], []
    for h in hits:
        if cur and _GROUP_JOIN.match(w[cur[-1][1]:h[0]]):
            cur.append(h)
        else:
            if cur:
                groups.append(cur)
            cur = [h]
    if cur:
        groups.append(cur)
    clauses, cited, items = [], [], []
    for g in groups:
        pre, post = w[:g[0][0]], w[g[-1][1]:]
        is_cite = pre.endswith(_CITE_PRE) or post.startswith(_CITE_POST)
        for _, _, a, c, it in g:
            (cited if is_cite else clauses).append((a, c))
            if it is not None and _int(it) is not None:
                items.append((a, c, _int(it)))
    for m in _ITEM_BARE.finditer(w):
        n = _int(m.group(1))
        if n is not None:
            items.append((None, None, n))
    return {"clauses": clauses, "cited": cited, "items": items}


# ---------------------------------------------------------------- 引述重合

def _bare(s):
    return re.sub(r"[\W_]", "", s or "")


def quote_overlap(para_text, comment_text):
    a, b = _bare(para_text), _bare(comment_text)
    if not a or not b:
        return 0
    return SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b)).size


# ---------------------------------------------------------------- 判定

def judge(s, para_text, comment_text):
    """返回 (mentions, findings)；finding = {code, level: warn|hint, msg}。"""
    mt = mentions(comment_text)
    found = []  # (code, msg, 降级阈值 or None)
    if s["note"]:
        found.append(("W3", "锚点是表格后的非编号注释段", None))
    if s["cl"] is not None and s["art"] is not None:
        same = [c for a, c in mt["clauses"] if a == s["art"]]
        if same and min(same) < s["cl"]:
            found.append(("W2", "正文提到 %d.%d，早于锚点所在 %d.%d" % (s["art"], min(same), s["art"], s["cl"]),
                          CLAUSE_QUOTE_MIN))
    if s["kind"] == "item":
        mine = [n for a, c, n in mt["items"]
                if a is None or (a == s["art"] and c == s["cl"])]
        if s["item"] not in mine:
            found.append(("W1", "锚点在第（%d）项，正文未提该项" % s["item"], ITEM_QUOTE_MIN))
        if mine and min(mine) < s["item"]:
            found.append(("W2", "正文提到第（%d）项，早于锚点所在第（%d）项" % (min(mine), s["item"]),
                          ITEM_QUOTE_MIN))
    ov = quote_overlap(para_text, comment_text) if found else 0
    out = []
    for code, msg, th in found:
        if th is not None and ov >= th:
            out.append({"code": code, "level": "hint",
                        "msg": "%s（批注引述锚点段 %d 字，降为提示）" % (msg, ov)})
        else:
            out.append({"code": code, "level": "warn", "msg": msg})
    return mt, out


# ---------------------------------------------------------------- 读取 docx

def _comment_texts(com_root):
    res = {}
    if com_root is None:
        return res
    for c in com_root.iter(q("comment")):
        paras = ["".join(t.text or "" for t in p.iter(q("t"))) for p in c.iter(q("p"))]
        res[c.get(q("id"))] = "\n".join(paras)
    return res


def _ranges(body):
    """id → 高亮文字（rangeStart 与 rangeEnd 之间的 w:t）。"""
    active, out = {}, {}
    for el in body.iter():
        if el.tag == q("commentRangeStart"):
            active[el.get(q("id"))] = []
        elif el.tag == q("commentRangeEnd"):
            cid = el.get(q("id"))
            if cid in active:
                out[cid] = "".join(active.pop(cid))
        elif el.tag == q("t"):
            for v in active.values():
                v.append(el.text or "")
    return out


def _owner_p(el):
    p = el.getparent()
    while p is not None and p.tag != q("p"):
        p = p.getparent()
    if p is not None:
        return p
    nxt = el.xpath("following::w:p[1]", namespaces={"w": WNS})
    return nxt[0] if nxt else None


def _uniq(xs):
    return list(dict.fromkeys(xs))


def check(docx_path):
    with zipfile.ZipFile(str(docx_path)) as z:
        names = set(z.namelist())

        def part(member):
            return etree.fromstring(z.read(member), _PARSER) if member in names else None
        doc = part("word/document.xml")
        if doc is None:
            raise ValueError("缺少 word/document.xml")
        numbering, styles, com = part("word/numbering.xml"), part("word/styles.xml"), part("word/comments.xml")
    paras = outline.outline_from_parts(doc, numbering, styles)
    struct = _structure(paras)
    plist = list(doc.iter(q("p")))  # 持有引用：lxml 代理对象只在被引用期间保持同一 id
    pidx = {id(p): i for i, p in enumerate(plist)}
    ctext = _comment_texts(com)
    body = doc.find(q("body"))
    ranges = _ranges(body) if body is not None else {}
    rows = []
    for st in (body.iter(q("commentRangeStart")) if body is not None else []):
        cid = st.get(q("id"))
        p = _owner_p(st)
        i = pidx.get(id(p)) if p is not None else None
        if i is None:
            continue
        s = struct[i]
        mt, found = judge(s, paras[i]["text"], ctext.get(cid, ""))
        rows.append({"id": cid, "p": i, "location": _location(s),
                     "range": (ranges.get(cid) or "")[:20],
                     "mentions": _uniq(["%d.%d" % x for x in mt["clauses"]]
                                       + ["%s（%d）" % ("%d.%d" % (a, c) if a is not None else "", n)
                                          for a, c, n in mt["items"]]),
                     "cited": _uniq(["%d.%d" % x for x in mt["cited"]]),
                     "findings": found})
    warn = [r["id"] for r in rows if any(f["level"] == "warn" for f in r["findings"])]
    hint = [r["id"] for r in rows if r["id"] not in warn and r["findings"]]
    return {"comments": rows, "warn": warn, "hint": hint}


# ---------------------------------------------------------------- 输出

def _summary(res):
    if not res["comments"]:
        return "无批注"
    return "⚠ 警告 %d 条：%s；ℹ 提示 %d 条：%s" % (
        len(res["warn"]), " ".join("#" + i for i in res["warn"]) or "无",
        len(res["hint"]), " ".join("#" + i for i in res["hint"]) or "无")


def to_markdown(res):
    lines = []
    for r in res["comments"]:
        ref = " ".join(r["mentions"]) or "—"
        if r["cited"]:
            ref += "（援引 %s）" % " ".join(r["cited"])
        if not r["findings"]:
            verdict = "✓"
        else:
            verdict = "；".join(("⚠ " if f["level"] == "warn" else "ℹ ") + f["code"] + " " + f["msg"]
                               for f in r["findings"])
        lines.append("#%s\t%s\t「%s」\t提到 %s\t%s" % (r["id"], r["location"], r["range"], ref, verdict))
    lines.append(_summary(res))
    return "\n".join(lines)


def _cmd_anchors(a):
    import sys
    try:
        res = check(a.docx)
    except (zipfile.BadZipFile, etree.XMLSyntaxError, ValueError, OSError) as e:
        print("anchors 失败：%s" % e, file=sys.stderr)
        return 1
    if a.format == "json":
        text = json.dumps(dict(res, summary=_summary(res)), ensure_ascii=False, indent=2)
    else:
        text = to_markdown(res)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(_summary(res))
    else:
        print(text)
    return 0


def add_cli(subparsers):
    """注册 anchors 子命令：cc-docx anchors X.docx [--format md|json] [-o out]"""
    o = subparsers.add_parser("anchors", help="批注锚点体检（只警告，需人工复核）")
    o.add_argument("docx")
    o.add_argument("--format", default="md", choices=["md", "json"])
    o.add_argument("-o", "--out")
    o.set_defaults(fn=_cmd_anchors)
    return o
