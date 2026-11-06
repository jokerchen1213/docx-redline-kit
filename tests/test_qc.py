from lxml import etree
from engine import ooxml, qc
from engine.redline import Doc, reject_text


def _root_after(sample_docx, tmp_path, fn):
    up = tmp_path / "u"
    ooxml.unpack_document(str(sample_docx), str(up))
    docxml = up / "word" / "document.xml"
    doc = Doc(str(docxml))
    original = "".join(reject_text(p) for p in doc.paras())   # 先记原文
    fn(doc)
    doc.save()
    root = etree.parse(str(docxml)).getroot()
    return root, original


def test_check_passes_for_clean_replace(sample_docx, tmp_path):
    root, original = _root_after(sample_docx, tmp_path, lambda d: d.R_ALL("乙集团", "甲公司"))
    r = qc.check(root, original, forbid=["乙集团"])
    assert r["ok"] is True
    assert r["reject_equals_original"] is True
    assert r["forbid_hits"]["乙集团"] == 0     # 接受后旧名已消失


def test_check_flags_forbid_residue(sample_docx, tmp_path):
    # 不替换，强行把"乙集团"列为 forbid → 接受文本里仍在 → 应失败
    root, original = _root_after(sample_docx, tmp_path, lambda d: None)
    r = qc.check(root, original, forbid=["乙集团"])
    assert r["forbid_hits"]["乙集团"] == 1
    assert r["ok"] is False
