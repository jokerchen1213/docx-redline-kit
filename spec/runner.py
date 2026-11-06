# spec/runner.py — 读修改清单（dict）→ 调 engine 原语 → QC 闸 → 输出
import shutil
import sys
from pathlib import Path

from lxml import etree

from engine import ooxml, qc
from engine.comment import Document
from engine.locate import LocateError, locate
from engine.redline import Doc, reject_text
from engine.normalize import normalize_field, quote_warnings


def _apply_edit(doc, e):
    op = e["op"]
    if op == "replace":
        scope = e.get("scope", "all")
        if scope == "all":
            doc.R_ALL(e["find"], e["with"], expect=e.get("expect"))
        else:
            doc.R(scope, old=e["find"], new=e["with"])
    elif op == "delete_text":
        if e.get("scope", "all") == "all":
            doc.R_ALL(e["find"], "")
        else:
            doc.R(e["scope"], old=e["find"], new="")
    elif op == "insert_after":
        doc.insert_after_text(e["anchor"], e["text"], guard=(e.get("guard") == "dedup"))
    elif op == "insert_before":
        doc.insert_before_text(e["anchor"], e["text"], guard=(e.get("guard") == "dedup"))
    elif op == "delete_para":
        doc.DP(e["anchor"])
    elif op == "insert_para_after":
        doc.IA(e["anchor"], [e["text"]])
    elif op == "table_cell":
        doc.set_cell(e["table"], e["row"], e["col"], new=e.get("set"), old=e.get("find"))
    elif op == "table_row_delete":
        doc.delete_row_by_index(e["table"], e["row"])
    else:
        raise ValueError("未知 op: %s" % op)


# 写入文档的新文本字段（锚点类字段 find / anchor / scope / forbid 绝不碰）
_REDLINE_TEXT_FIELDS = ("with", "text", "set")


def _normalize_spec(spec):
    """按 meta.normalize（默认 True）标准化清单中的新文本；返回提示列表。"""
    meta = spec.get("meta", {})
    warns = []
    if not meta.get("normalize", True):
        return warns
    for i, e in enumerate(spec.get("edits", []), 1):
        for k in _REDLINE_TEXT_FIELDS:
            if isinstance(e.get(k), str):
                warns += quote_warnings(e[k], "edits[%d].%s" % (i, k))
                e[k] = normalize_field(e[k])
    for i, c in enumerate(spec.get("comments", []), 1):
        if isinstance(c.get("text"), str):
            warns += quote_warnings(c["text"], "comments[%d].text" % i)
            c["text"] = normalize_field(c["text"])
    return warns


def _print_warns(warns):
    for w in warns:
        print("提示：%s" % w, file=sys.stderr)


def run_redline(in_docx, spec, out_docx):
    meta = spec.get("meta", {})
    _print_warns(_normalize_spec(spec))
    tmp = Path(ooxml.mkdtemp_compat("ccdocx_"))
    try:
        ooxml.unpack_document(in_docx, str(tmp))
        docxml = tmp / "word" / "document.xml"
        doc = Doc(str(docxml), author=meta.get("author", "REVIEWER"), date=meta.get("date"))
        original = "".join(reject_text(p) for p in doc.paras())
        for e in spec.get("edits", []):
            _apply_edit(doc, e)
        doc.save()
        root = etree.parse(str(docxml)).getroot()
        res = qc.check(root, original, forbid=meta.get("forbid"))
        if not res["ok"]:
            raise RuntimeError("QC 未通过，拒绝输出：%s" % res)
        if not ooxml.pack_document(str(tmp), out_docx, validate=True):
            raise RuntimeError("打包校验失败")
        return res
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _load_outline(tmp, doc_root):
    """若 outline 模块可用，按解包目录算 outline；不可用或异常时返回 None（定位仅在需要条款号时报错）。"""
    try:
        from engine import outline
    except Exception:
        return None
    parser = etree.XMLParser(resolve_entities=False, huge_tree=True)

    def _opt(name):
        f = tmp / "word" / name
        return etree.parse(str(f), parser).getroot() if f.is_file() else None

    try:
        return outline.outline_from_parts(doc_root, _opt("numbering.xml"), _opt("styles.xml"))
    except Exception as exc:
        print("提示：outline 计算失败，条款号定位不可用：%s" % exc, file=sys.stderr)
        return None


def _comment_item(c):
    return {"anchor": c.get("anchor"), "clause": c.get("clause"),
            "occ": c.get("occ"), "range": c.get("range") or "sentence"}


def run_comment(in_docx, spec, out_docx):
    """全部条目先定位、收齐失败一次性抛 LocateError（不产出文件）；全部命中才注入。
    注入后逐 id 校验，新增完好批注数必须等于条目数，否则拒绝输出。"""
    meta = spec.get("meta", {})
    _print_warns(_normalize_spec(spec))
    comments = spec.get("comments") or []
    tmp = Path(ooxml.mkdtemp_compat("ccdocx_"))
    try:
        ooxml.unpack_document(in_docx, str(tmp))
        cm = Document(str(tmp), author=meta.get("author", "REVIEWER"),
                      initials=meta.get("author", "REVIEWER"), date=meta.get("date"))
        outline_paras = _load_outline(tmp, cm.doc_root)

        located, failures = [], []
        for n, c in enumerate(comments, 1):
            if not isinstance(c.get("text"), str) or not c["text"].strip():
                failures.append({"no": n, "anchor": c.get("anchor"), "clause": c.get("clause"),
                                 "reason": "text 缺失或为空", "candidates": []})
                continue
            try:
                located.append((c, locate(cm.doc_root, _comment_item(c), outline_paras)))
            except LocateError as e:
                for f in e.failures:
                    f["no"] = n
                    failures.append(f)
        if failures:
            raise LocateError(failures)

        before = cm.verify_comments()
        before_bad = {b["id"] for b in before["bad"]}
        new_ids, items = [], []
        for c, loc in located:
            if loc["mode"] == "sentence":
                cid = cm.add_comment_range(loc["p"], loc["start"], loc["end"], c["text"])
            else:
                cid = cm.add_comment_paragraph(loc["p"], c["text"])
            new_ids.append(str(cid))
            items.append({"anchor": c.get("anchor"), "mode": loc["mode"],
                          "p_index": loc["p_index"], "matched_on": loc["matched_on"],
                          "comment_id": cid})
        after = cm.verify_comments()
        new_bad = [b for b in after["bad"] if b["id"] not in before_bad]
        missing_ok = [i for i in new_ids if i not in after["ok_ids"]]
        if after["total"] - before["total"] != len(located) or new_bad or missing_ok:
            raise RuntimeError("批注逐 id 校验未通过，拒绝输出：新增 %d/%d，新增异常 %s，未通过 id %s"
                               % (after["total"] - before["total"], len(located), new_bad, missing_ok))
        cm.save()
        if not ooxml.pack_document(str(tmp), out_docx, validate=True):
            raise RuntimeError("打包校验失败")
        return {"count": len(items), "items": items}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
