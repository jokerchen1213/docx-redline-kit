# 标准化闸：新文本转全角/弯引号/去数字汉字空格；锚点字段不动；meta.normalize=false 可关
import sys
from pathlib import Path

import pytest
from lxml import etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import extract as extract_mod  # noqa: E402
from engine.normalize import normalize_field, quote_warnings  # noqa: E402
from spec import runner  # noqa: E402


def test_normalize_field_basic():
    assert normalize_field('第 4.2 款, 24 小时内"单独同意":见 1.1 条...') == \
        '第4.2款，24小时内“单独同意”：见1.1条……'
    assert normalize_field("[🟡 中] 利率上限建议明确约定") == "[🟡 中] 利率上限建议明确约定"
    assert normalize_field("依《民法典》第 585 条, 见“单独同意” 之约定") == "依《民法典》第585条，见“单独同意”之约定"
    assert normalize_field("English, 2 items; a.b") == "English, 2 items; a.b"  # 纯西文不动
    assert normalize_field(None) is None


def test_quote_warnings_odd_only():
    assert quote_warnings('a "b" c', "x") == []
    w = quote_warnings('第一行\n他说"未完', "comments[1].text")
    assert len(w) == 1 and "第2行" in w[0]


def test_comment_text_normalized(sample_docx, tmp_path):
    out = tmp_path / "c.docx"
    spec = {"comments": [{"anchor": "第三条 利息", "text": '建议第 3.1 款明确"上限", 30 日内'}]}
    runner.run_comment(str(sample_docx), spec, str(out))
    data = extract_mod.extract(str(out), what=("comments",))
    txt = "".join(c["text"] for c in data["comments"])
    assert "第3.1款明确“上限”，30日内" in txt
    assert '"' not in txt and "第 3.1" not in txt


def test_redline_with_normalized_find_untouched(sample_docx, tmp_path):
    out = tmp_path / "r.docx"
    spec = {"meta": {"forbid": ["乙集团"]},
            "edits": [{"op": "replace", "find": "乙集团", "with": '甲 公司("SAMPLE")',
                       "scope": "all"}]}
    runner.run_redline(str(sample_docx), spec, str(out))
    data = extract_mod.extract(str(out), what=("redlines",))
    ins = "".join(r["text"] for r in data["redlines"] if r["type"] == "ins")
    assert "甲 公司（“SAMPLE”）" in ins  # 字母↔汉字空格不动，仅数字↔汉字


def test_normalize_can_be_disabled(sample_docx, tmp_path):
    out = tmp_path / "c2.docx"
    spec = {"meta": {"normalize": False},
            "comments": [{"anchor": "第三条 利息", "text": '第 3 条 "原样"'}]}
    runner.run_comment(str(sample_docx), spec, str(out))
    data = extract_mod.extract(str(out), what=("comments",))
    txt = "".join(c["text"] for c in data["comments"])
    assert '第 3 条 "原样"' in txt
