# engine/extract.py — 编辑层提取（批注 / 修订痕迹 / 结构）
# 只取"编辑层"，不做正文阅读转换（正文阅读归 cc-convert，互不重叠）。
import zipfile

from lxml import etree

from engine.ooxml import q
from engine import outline as outline_mod

# 关闭外部实体展开，防 XXE / billion-laughs（输入虽为自有文档，仍硬化）
_PARSER = etree.XMLParser(resolve_entities=False)


def _text_of(el, tags):
    return "".join((t.text or "") for t in el.iter(*[q(x) for x in tags]))


def _read_xml(docx_path, member):
    with zipfile.ZipFile(docx_path) as z:
        if member not in z.namelist():
            return None
        return etree.fromstring(z.read(member), _PARSER)


def extract(docx_path, what=("comments", "redlines", "structure")):
    data = {}
    doc_root = _read_xml(docx_path, "word/document.xml")
    if "redlines" in what:
        rl = []
        for ins in doc_root.iter(q("ins")):
            rl.append({"type": "ins", "author": ins.get(q("author")),
                       "date": ins.get(q("date")), "text": _text_of(ins, ["t"])})
        for d in doc_root.iter(q("del")):
            rl.append({"type": "del", "author": d.get(q("author")),
                       "date": d.get(q("date")), "text": _text_of(d, ["delText"])})
        data["redlines"] = [r for r in rl if r["text"]]
    if "comments" in what:
        cm = []
        croot = _read_xml(docx_path, "word/comments.xml")
        if croot is not None:
            for c in croot.iter(q("comment")):
                cm.append({"id": c.get(q("id")), "author": c.get(q("author")),
                           "date": c.get(q("date")),
                           # 按批注内段落以换行连接，保留段落边界
                           "text": "\n".join(_text_of(p, ["t"]) for p in c.iter(q("p")))})
        data["comments"] = cm
    if "structure" in what:
        paras = []
        # 可见编号（自动编号还原），下标与 doc_root.iter(q("p")) 对齐
        labels = [o["label"] for o in outline_mod.outline_from_parts(
            doc_root, _read_xml(docx_path, "word/numbering.xml"),
            _read_xml(docx_path, "word/styles.xml"))]
        for i, p in enumerate(doc_root.iter(q("p"))):
            txt = _text_of(p, ["t"])
            if txt.strip():
                paras.append({"i": i, "label": labels[i], "text": txt})
        tables = []
        for i, tbl in enumerate(doc_root.iter(q("tbl"))):
            rows = tbl.findall(q("tr"))
            cols = len(rows[0].findall(q("tc"))) if rows else 0
            tables.append({"i": i, "rows": len(rows), "cols": cols})
        data["structure"] = {"paragraphs": paras, "tables": tables}
    return data


def to_markdown(data):
    out = []
    if "comments" in data:
        out.append("## 批注")
        for c in data["comments"]:
            out.append("- [%s] %s" % (c.get("author") or "", c.get("text") or ""))
    if "redlines" in data:
        out.append("\n## 修订痕迹")
        for r in data["redlines"]:
            sign = "＋" if r["type"] == "ins" else "－"
            out.append("- %s %s（%s）" % (sign, r["text"], r.get("author") or ""))
    if "structure" in data:
        out.append("\n## 结构")
        out.append("段落 %d 个，表格 %d 张" % (
            len(data["structure"]["paragraphs"]), len(data["structure"]["tables"])))
    return "\n".join(out)
