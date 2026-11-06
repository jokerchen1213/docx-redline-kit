from pathlib import Path
from engine import ooxml


def test_q_builds_namespaced_tag():
    assert ooxml.q("p") == "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"


def test_unpack_then_pack_roundtrip(sample_docx, tmp_path):
    unpacked = tmp_path / "unpacked"
    ooxml.unpack_document(str(sample_docx), str(unpacked))
    assert (unpacked / "word" / "document.xml").is_file()
    out = tmp_path / "rebuilt.docx"
    ok = ooxml.pack_document(str(unpacked), str(out), validate=True)
    assert ok is True and out.is_file()
    # 重新解包，document.xml 字节一致（纯搬运无改动）
    re = tmp_path / "re"
    ooxml.unpack_document(str(out), str(re))
    a = (unpacked / "word" / "document.xml").read_bytes()
    b = (re / "word" / "document.xml").read_bytes()
    assert a == b
