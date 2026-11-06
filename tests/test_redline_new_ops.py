from engine import ooxml
from engine.redline import Doc, live_text, reject_text


def _doc(sample_docx, tmp_path):
    up = tmp_path / "u"
    ooxml.unpack_document(str(sample_docx), str(up))
    return Doc(str(up / "word" / "document.xml"), date="2026-06-20T00:00:00Z")


def _live(doc):
    return "".join(live_text(p) for p in doc.paras())


def _rej(doc):
    return "".join(reject_text(p) for p in doc.paras())


def test_insert_after_text_inline(sample_docx, tmp_path):
    doc = _doc(sample_docx, tmp_path)
    orig = _rej(doc)
    doc.insert_after_text("本合同自双方签字之日起生效", "，并以英文版本为准")
    assert "生效，并以英文版本为准" in _live(doc)
    assert _rej(doc) == orig                      # 插入文本在 reject 下不出现


def test_insert_after_text_guard_dedup(sample_docx, tmp_path):
    doc = _doc(sample_docx, tmp_path)
    doc.insert_after_text("生效", "（补充）", guard=True)
    doc.insert_after_text("生效", "（补充）", guard=True)   # 第二次应跳过
    assert _live(doc).count("（补充）") == 1


def test_set_cell_by_index(sample_docx, tmp_path):
    doc = _doc(sample_docx, tmp_path)
    orig = _rej(doc)
    doc.set_cell(0, 0, 1, new="PT SAMPLE CO")     # 借款人那一格 PT EXAMPLE→PT SAMPLE CO
    assert "PT SAMPLE CO" in _live(doc)
    assert "PT EXAMPLE" in _rej(doc)
    assert _rej(doc) == orig


def test_delete_row_by_index(sample_docx, tmp_path):
    doc = _doc(sample_docx, tmp_path)
    orig = _rej(doc)
    doc.delete_row_by_index(0, 1)                     # 删"金额 100"行
    assert "金额" not in _live(doc)
    assert _rej(doc) == orig
