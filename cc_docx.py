#!/usr/bin/env python3
# cc-docx CLI — Word 编辑层工具：extract / comment / redline / verify
import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lxml import etree

from engine import extract as extract_mod, ooxml, qc
from engine.comment import Document as CommentDoc
from engine.locate import LocateError
from engine.redline import Doc, reject_text
from spec import runner


def _load_spec(p):
    return yaml.safe_load(Path(p).read_text(encoding="utf-8"))


def cmd_extract(a):
    what = tuple(a.what.split(",")) if a.what else ("comments", "redlines", "structure")
    data = extract_mod.extract(a.docx, what=what)
    text = (json.dumps(data, ensure_ascii=False, indent=2)
            if a.format == "json" else extract_mod.to_markdown(data))
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


def cmd_redline(a):
    try:
        res = runner.run_redline(a.docx, _load_spec(a.spec), a.out)
    except RuntimeError as e:
        print("redline 失败：%s" % e, file=sys.stderr)
        return 1
    print("OK 修订完成，QC 通过：%s" % res)
    return 0


def _print_locate_failures(e):
    print("comment 失败：%d 条锚点定位失败，未产出文件" % len(e.failures), file=sys.stderr)
    for f in e.failures:
        print("  [%s] 锚点「%s」：%s" % (f.get("no", "?"), (f.get("anchor") or "")[:30],
                                      f.get("reason")), file=sys.stderr)
        for cd in f.get("candidates") or []:
            print("      候选 i=%s  %s  %s" % (cd.get("i"), cd.get("path") or cd.get("label") or "-",
                                             cd.get("text") or ""), file=sys.stderr)


def cmd_comment(a):
    try:
        res = runner.run_comment(a.docx, _load_spec(a.spec), a.out)
    except LocateError as e:
        _print_locate_failures(e)
        return 2
    except RuntimeError as e:
        print("comment 失败：%s" % e, file=sys.stderr)
        return 1
    for k, it in enumerate(res["items"], 1):
        flag = "⚠ 整段兜底 " if it["mode"] == "clause_fallback" else ""
        print("  [%d] %s%s  p=%s  %s  「%s」" % (k, flag, it["mode"], it["p_index"],
                                              it["matched_on"], (it["anchor"] or "")[:30]))
    print("OK 注入批注 %d 条 → %s" % (res["count"], a.out))
    return 0


def cmd_verify_comments(a):
    import shutil
    tmp = Path(ooxml.mkdtemp_compat("ccdocx_"))
    try:
        ooxml.unpack_document(a.docx, str(tmp))
        res = CommentDoc(str(tmp)).verify_comments(strict=True)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 1 if res["bad"] else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def cmd_verify(a):
    import shutil
    if a.comments:
        return cmd_verify_comments(a)
    tmp = Path(ooxml.mkdtemp_compat("ccdocx_"))
    try:
        ooxml.unpack_document(a.docx, str(tmp))
        docxml = tmp / "word" / "document.xml"
        doc = Doc(str(docxml))
        original = "".join(reject_text(p) for p in doc.paras())
        root = etree.parse(str(docxml)).getroot()
        forbid = a.forbid.split(",") if a.forbid else None
        res = qc.check(root, original, forbid=forbid)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    p = argparse.ArgumentParser(prog="cc-docx")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="导出编辑层（批注/修订/结构）")
    e.add_argument("docx")
    e.add_argument("--what", help="逗号分隔：comments,redlines,structure")
    e.add_argument("--format", default="md", choices=["md", "json"])
    e.add_argument("-o", "--out")
    e.set_defaults(fn=cmd_extract)

    r = sub.add_parser("redline", help="按 YAML 清单生成修订（自动 QC）")
    r.add_argument("docx")
    r.add_argument("--spec", required=True)
    r.add_argument("-o", "--out", required=True)
    r.set_defaults(fn=cmd_redline)

    c = sub.add_parser("comment", help="按 YAML 清单注入批注")
    c.add_argument("docx")
    c.add_argument("--spec", required=True)
    c.add_argument("-o", "--out", required=True)
    c.set_defaults(fn=cmd_comment)

    v = sub.add_parser("verify", help="修订一致性校验")
    v.add_argument("docx")
    v.add_argument("--forbid", help="逗号分隔的禁碰词")
    v.add_argument("--comments", action="store_true",
                   help="改为逐 id 校验批注（comment / rangeStart / rangeEnd / reference 齐全且有序）")
    v.set_defaults(fn=cmd_verify)

    try:  # outline 子命令由 engine/outline.py 提供（未就绪时跳过）
        from engine import outline
        outline.add_cli(sub)
    except ImportError:
        pass

    from engine import anchors  # 批注锚点体检（只警告）
    anchors.add_cli(sub)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
