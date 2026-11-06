from engine import ooxml
from engine.redline import Doc, live_text, reject_text


def _doc(sample_docx, tmp_path):
    up = tmp_path / "u"
    ooxml.unpack_document(str(sample_docx), str(up))
    return Doc(str(up / "word" / "document.xml"), author="REVIEWER", date="2026-06-20T00:00:00Z")


def _all_text(doc, which):
    f = live_text if which == "live" else reject_text
    return "".join(f(p) for p in doc.paras())


def test_R_ALL_replaces_with_tracked_change(sample_docx, tmp_path):
    doc = _doc(sample_docx, tmp_path)
    orig = _all_text(doc, "reject")
    n = doc.R_ALL("乙集团", "甲公司")
    assert n == 1
    assert "甲公司" in _all_text(doc, "live")        # 接受修订后＝新名
    assert "乙集团" in _all_text(doc, "reject")          # 拒绝修订后＝旧名
    assert _all_text(doc, "reject") == orig                # 不变量：reject == 原文


def test_DP_marks_paragraph_deleted(sample_docx, tmp_path):
    doc = _doc(sample_docx, tmp_path)
    orig = _all_text(doc, "reject")
    doc.DP("第十五条 保密义务")
    assert "保密义务" not in _all_text(doc, "live")        # 接受后该段消失
    assert _all_text(doc, "reject") == orig                # 拒绝后还原


def test_IA_inserts_new_paragraph(sample_docx, tmp_path):
    doc = _doc(sample_docx, tmp_path)
    orig = _all_text(doc, "reject")
    doc.IA("本合同自双方签字之日起生效。", ["补充条款：以英文版本为准。"])
    assert "以英文版本为准" in _all_text(doc, "live")
    assert _all_text(doc, "reject") == orig                # 插入段在 reject 下不计入
