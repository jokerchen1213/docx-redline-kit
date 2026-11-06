import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _run(args, **kw):
    return subprocess.run([sys.executable, str(ROOT / "cc_docx.py"), *args],
                          capture_output=True, text=True, **kw)


def test_cli_extract_md(sample_docx, tmp_path):
    r = _run(["extract", str(sample_docx), "--format", "md"])
    assert r.returncode == 0 and "## 结构" in r.stdout


def test_cli_redline_with_spec(sample_docx, tmp_path):
    spec = {"meta": {"author": "REVIEWER", "forbid": ["乙集团"]},
            "edits": [{"op": "replace", "find": "乙集团", "with": "甲公司", "scope": "all"}]}
    sp = tmp_path / "e.yaml"
    sp.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    out = tmp_path / "o.docx"
    r = _run(["redline", str(sample_docx), "--spec", str(sp), "-o", str(out)])
    assert r.returncode == 0 and out.is_file()


def test_cli_redline_qc_fail_returns_nonzero(sample_docx, tmp_path):
    spec = {"meta": {"forbid": ["利息"]},
            "edits": [{"op": "replace", "find": "乙集团", "with": "X", "scope": "all"}]}
    sp = tmp_path / "bad.yaml"
    sp.write_text(yaml.safe_dump(spec, allow_unicode=True), encoding="utf-8")
    out = tmp_path / "no.docx"
    r = _run(["redline", str(sample_docx), "--spec", str(sp), "-o", str(out)])
    assert r.returncode != 0 and not out.exists()


# ---------- comment 失败清单 / 模式打印 / verify --comments / JSON 清单 ----------
import json  # noqa: E402
import zipfile  # noqa: E402


def _p(text):
    return '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % text


def test_cli_comment_failure_list_exit2(make_docx, tmp_path):
    d = make_docx(_p("本合同一式两份。") + _p("附件：本合同一式两份。"))
    sp = tmp_path / "c.yaml"
    sp.write_text(yaml.safe_dump({"comments": [
        {"anchor": "本合同一式两份", "text": "a"}, {"anchor": "查无此句", "text": "b"}]},
        allow_unicode=True), encoding="utf-8")
    out = tmp_path / "o.docx"
    r = _run(["comment", str(d), "--spec", str(sp), "-o", str(out)])
    assert r.returncode == 2 and not out.exists()
    assert "[1]" in r.stderr and "歧义" in r.stderr and "[2]" in r.stderr and "未命中" in r.stderr
    assert "候选 i=0" in r.stderr and "候选 i=1" in r.stderr


def test_cli_comment_json_spec_and_verify(make_docx, tmp_path):
    d = make_docx(_p("甲方应于十日内付款。"))
    sp = tmp_path / "c.json"
    sp.write_text(json.dumps({"comments": [{"anchor": "十日内付款", "text": "意见**加粗**"}]},
                             ensure_ascii=True, indent=2), encoding="utf-8")
    out = tmp_path / "o.docx"
    r = _run(["comment", str(d), "--spec", str(sp), "-o", str(out)])
    assert r.returncode == 0, r.stderr
    assert "sentence" in r.stdout and "OK 注入批注 1 条" in r.stdout
    v = _run(["verify", str(out), "--comments"])
    assert v.returncode == 0 and json.loads(v.stdout)["bad"] == []


def test_cli_verify_comments_bad_exit1(make_docx, tmp_path):
    body = '<w:p><w:commentRangeStart w:id="0"/><w:r><w:t>甲</w:t></w:r></w:p>'
    comments = '<w:comment w:id="0" w:author="x"><w:p><w:r><w:t>c</w:t></w:r></w:p></w:comment>'
    v = _run(["verify", str(make_docx(body, comments)), "--comments"])
    assert v.returncode == 1 and json.loads(v.stdout)["bad"][0]["id"] == "0"


def test_cli_prints_clause_fallback_flag(monkeypatch, capsys, tmp_path):
    sys.path.insert(0, str(ROOT))
    import cc_docx
    fake = {"count": 2, "items": [
        {"anchor": "甲", "mode": "sentence", "p_index": 1, "matched_on": "anchor"},
        {"anchor": "乙", "mode": "clause_fallback", "p_index": 5, "matched_on": "clause:第五条"}]}
    monkeypatch.setattr(cc_docx.runner, "run_comment", lambda *a, **k: fake)
    sp = tmp_path / "s.yaml"
    sp.write_text("comments: []\n", encoding="utf-8")
    assert cc_docx.main(["comment", "x.docx", "--spec", str(sp), "-o", str(tmp_path / "o.docx")]) == 0
    out = capsys.readouterr().out
    assert "⚠ 整段兜底 clause_fallback" in out and "⚠" not in out.splitlines()[0]
