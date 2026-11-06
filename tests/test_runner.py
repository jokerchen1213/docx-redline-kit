import pytest
from engine import extract
from spec import runner


def test_run_redline_applies_and_passes_qc(sample_docx, tmp_path):
    spec = {"meta": {"author": "REVIEWER", "date": "2026-06-20T00:00:00Z", "forbid": ["乙集团"]},
            "edits": [
                {"op": "replace", "find": "乙集团", "with": "甲公司", "scope": "all"},
                {"op": "insert_after", "anchor": "本合同自双方签字之日起生效", "text": "，并以英文版本为准"},
                {"op": "delete_para", "anchor": "第十五条 保密义务"},
                {"op": "table_cell", "table": 0, "row": 0, "col": 1, "set": "PT SAMPLE CO"},
            ]}
    out = tmp_path / "out.docx"
    res = runner.run_redline(str(sample_docx), spec, str(out))
    assert res["ok"] is True and out.is_file()
    data = extract.extract(str(out))
    ins = {r["text"] for r in data["redlines"] if r["type"] == "ins"}
    assert "甲公司" in ins and "PT SAMPLE CO" in ins


def test_run_redline_refuses_on_qc_fail(sample_docx, tmp_path):
    # forbid 一个不会被替换的词 → 接受文本仍含 → QC 失败 → 不写文件、抛错
    spec = {"meta": {"forbid": ["利息"]}, "edits": [{"op": "replace", "find": "乙集团", "with": "X", "scope": "all"}]}
    out = tmp_path / "no.docx"
    with pytest.raises(RuntimeError):
        runner.run_redline(str(sample_docx), spec, str(out))
    assert not out.exists()


def test_run_comment(sample_docx, tmp_path):
    spec = {"meta": {"author": "REVIEWER"},
            "comments": [{"anchor": "第三条 利息", "text": "[🟡 中] 建议明确利率上限"}]}
    out = tmp_path / "c.docx"
    res = runner.run_comment(str(sample_docx), spec, str(out))
    assert res["count"] == 1 and out.is_file()
    assert res["items"][0]["mode"] == "sentence"
    assert any("利率上限" in c["text"] for c in extract.extract(str(out))["comments"])


# ---------- 批注：全量失败清单 / 条款 / 模式 / 标准化保护 ----------
import zipfile  # noqa: E402

from engine.locate import LocateError  # noqa: E402
from engine.normalize import normalize_field  # noqa: E402


def _p(text):
    return '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % text


def test_run_comment_collects_all_failures(make_docx, tmp_path, no_outline):
    d = make_docx(_p("本合同一式两份。") + _p("附件：本合同一式两份。") + _p("第三条 付款。"))
    spec = {"comments": [
        {"anchor": "本合同一式两份", "text": "歧义"},
        {"anchor": "第三条 付款", "text": "这条能命中"},
        {"anchor": "根本不存在的锚点", "text": "未命中"},
    ]}
    out = tmp_path / "fail.docx"
    with pytest.raises(LocateError) as ei:
        runner.run_comment(str(d), spec, str(out))
    fs = ei.value.failures
    assert [f["no"] for f in fs] == [1, 3]
    assert "歧义" in fs[0]["reason"] and len(fs[0]["candidates"]) == 2
    assert fs[1]["reason"] == "未命中"
    assert not out.exists()


def test_run_comment_modes_and_clause(make_docx, tmp_path, fake_outline):
    d = make_docx(_p("第一条 定义") + _p("费用按月结算。") + _p("第二条 付款") + _p("费用按月结算。"))
    fake_outline["paras"] = [{"i": 0, "label": "第一条", "level": 0, "path": "第一条"},
                             {"i": 2, "label": "第二条", "level": 0, "path": "第二条"}]
    spec = {"comments": [
        {"anchor": "费用按月结算", "clause": "第二条", "text": "句级"},
        {"anchor": "第一条 定义", "range": "paragraph", "text": "整段"},
        {"anchor": "不存在的话", "clause": "第一条", "text": "兜底"},
    ]}
    out = tmp_path / "ok.docx"
    res = runner.run_comment(str(d), spec, str(out))
    assert res["count"] == 3
    assert [(i["mode"], i["p_index"]) for i in res["items"]] == [
        ("sentence", 3), ("paragraph", 0), ("clause_fallback", 0)]
    xml = zipfile.ZipFile(str(out)).read("word/document.xml").decode("utf-8")
    assert xml.count("<w:commentRangeStart") == 3 and xml.count("<w:commentReference") == 3


def test_run_comment_rejects_empty_text(make_docx, tmp_path):
    d = make_docx(_p("甲方。"))
    with pytest.raises(LocateError) as ei:
        runner.run_comment(str(d), {"comments": [{"anchor": "甲方", "text": " "}]}, str(tmp_path / "x.docx"))
    assert "text" in ei.value.failures[0]["reason"]


def test_run_comment_keeps_existing_comments(make_docx, tmp_path):
    body = ('<w:p><w:commentRangeStart w:id="0"/><w:r><w:t>旧处</w:t></w:r><w:commentRangeEnd w:id="0"/>'
            '<w:r><w:commentReference w:id="0"/></w:r></w:p>' + _p("新处在此。"))
    comments = '<w:comment w:id="0" w:author="对方"><w:p><w:r><w:t>对方意见</w:t></w:r></w:p></w:comment>'
    d = make_docx(body, comments)
    out = tmp_path / "keep.docx"
    runner.run_comment(str(d), {"comments": [{"anchor": "新处在此", "text": "我方"}]}, str(out))
    cs = extract.extract(str(out), what=("comments",))["comments"]
    assert [(c["author"], c["text"]) for c in cs] == [("对方", "对方意见"), ("REVIEWER", "我方")]


def test_normalizer_keeps_bold_marks_and_brackets():
    for s in ["**重点提示**：第 3 条", "【高风险】**违约金** 过高", "[🟡 中] **注意**, 见第 5 条."]:
        out = normalize_field(s)
        assert out.count("**") == s.count("**")
        assert out.count("【") == s.count("【") and out.count("】") == s.count("】")
    assert normalize_field("**第 3 条**") == "**第3条**"


def test_run_comment_bold_survives_normalize(make_docx, tmp_path):
    d = make_docx(_p("甲方应付款。"))
    out = tmp_path / "b.docx"
    runner.run_comment(str(d), {"comments": [
        {"anchor": "甲方应付款", "text": "【高风险】**付款期限** 不明, 建议第 3 条补充"}]}, str(out))
    xml = zipfile.ZipFile(str(out)).read("word/comments.xml").decode("utf-8")
    assert "【高风险】" in xml and "**" not in xml
    assert ">付款期限</w:t>" in xml and '<w:u w:val="single"/>' in xml
    assert "不明，建议第3条补充" in xml
