from engine import ooxml, extract
from engine.comment import Document
from engine.redline import Doc


def _prep(sample_docx, tmp_path):
    up = tmp_path / "u"
    ooxml.unpack_document(str(sample_docx), str(up))
    docxml = up / "word" / "document.xml"
    d = Doc(str(docxml)); d.R_ALL("乙集团", "甲公司"); d.save()
    cm = Document(str(up)); p = cm.find_paragraph_by_text("第三条", allow_fallback=False)
    cm.add_comment(p, p, "[🔵 低] 测试批注"); cm.save()
    out = tmp_path / "x.docx"
    ooxml.pack_document(str(up), str(out))
    return out


def test_extract_all_layers(sample_docx, tmp_path):
    docx = _prep(sample_docx, tmp_path)
    data = extract.extract(str(docx))
    assert any("甲公司" == r["text"] for r in data["redlines"] if r["type"] == "ins")
    assert any("乙集团" == r["text"] for r in data["redlines"] if r["type"] == "del")
    assert any("测试批注" in c["text"] for c in data["comments"])
    assert len(data["structure"]["paragraphs"]) >= 4
    assert data["structure"]["tables"][0]["rows"] == 2


def test_to_markdown(sample_docx, tmp_path):
    data = extract.extract(str(_prep(sample_docx, tmp_path)))
    md = extract.to_markdown(data)
    assert "## 批注" in md and "## 修订痕迹" in md
