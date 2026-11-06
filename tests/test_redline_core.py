from engine import ooxml
from engine.redline import Doc, live_text, reject_text


def _unpack_doc(sample_docx, tmp_path):
    up = tmp_path / "u"
    ooxml.unpack_document(str(sample_docx), str(up))
    return up / "word" / "document.xml"


def test_meta_is_instance_level(sample_docx, tmp_path):
    docxml = _unpack_doc(sample_docx, tmp_path)
    doc = Doc(str(docxml), author="A-TEST", date="2026-06-20T00:00:00Z")
    assert doc.author == "A-TEST"
    a = doc._attrs()
    assert a[ooxml.q("author")] == "A-TEST"
    assert a[ooxml.q("date")] == "2026-06-20T00:00:00Z"


def test_meta_date_accepts_datetime_from_yaml(sample_docx, tmp_path):
    # YAML safe_load 会把 "2026-06-20T00:00:00Z" 解析为 datetime 对象，
    # lxml 属性值只收字符串——Doc 必须把 datetime 强制成 ISO 字符串
    from datetime import datetime, timezone
    docxml = _unpack_doc(sample_docx, tmp_path)
    dt = datetime(2026, 6, 20, 0, 0, 0, tzinfo=timezone.utc)
    doc = Doc(str(docxml), date=dt)
    a = doc._attrs()
    assert isinstance(a[ooxml.q("date")], str)
    assert a[ooxml.q("date")] == "2026-06-20T00:00:00Z"


def test_nid_is_unique_and_monotonic(sample_docx, tmp_path):
    doc = Doc(str(_unpack_doc(sample_docx, tmp_path)))
    ids = {doc._nid() for _ in range(5)}
    assert len(ids) == 5


def test_reject_text_equals_live_text_when_no_changes(sample_docx, tmp_path):
    doc = Doc(str(_unpack_doc(sample_docx, tmp_path)))
    for p in doc.paras():
        assert reject_text(p) == live_text(p)   # 未改前两者相等
