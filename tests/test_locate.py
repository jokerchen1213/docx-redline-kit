# 锚点定位：归一化命中 / 歧义检测 / occ / 条款过滤与兜底 / 失败信息
import zipfile

import pytest
from lxml import etree

from engine.locate import LocateError, live_text_of, locate, paragraphs
from engine.match_norm import norm_for_match, norm_simple


def _p(text):
    return '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % text


def _root(path):
    with zipfile.ZipFile(str(path)) as z:
        return etree.fromstring(z.read("word/document.xml"))


def test_norm_for_match_strips_space_and_maps_index():
    s = "甲方 应于　“十日”内\xa0支付\t。"
    n, imap = norm_for_match(s)
    assert n == '甲方应于"十日"内支付.'
    assert all(s[imap[k]] not in " 　\xa0\t" for k in range(len(n)))
    assert s[imap[n.index("支")]] == "支"


def test_norm_simple_fullwidth_punct_and_brackets():
    assert norm_simple("（一）【高风险】：，；。！？．、ＡＢ１") == "(一)[高风险]:,;.!?.,AB1"
    assert norm_simple("‘单’“双”") == "'单'\"双\""
    assert norm_simple("  \n ") == ""


def test_normalized_hit_maps_back_to_original_offsets(make_docx):
    d = make_docx(_p("前文。乙方 应当在 “收到发票” 后十日内付款。后文"))
    root = _root(d)
    r = locate(root, {"anchor": '乙方应当在"收到发票"后十日内付款', "range": "sentence"})
    text = live_text_of(r["p"])
    assert r["mode"] == "sentence" and r["p_index"] == 0
    assert text[r["start"]:r["end"]] == "乙方 应当在 “收到发票” 后十日内付款"
    assert r["matched_text"] == text[r["start"]:r["end"]]


def test_live_text_accepts_ins_excludes_del_tab_as_space(make_docx):
    body = ('<w:p><w:r><w:t>甲</w:t></w:r><w:r><w:tab/></w:r>'
            '<w:ins w:id="1" w:author="X" w:date="2026-01-01T00:00:00Z"><w:r><w:t>新增</w:t></w:r></w:ins>'
            '<w:del w:id="2" w:author="X" w:date="2026-01-01T00:00:00Z"><w:r><w:delText>删掉</w:delText></w:r></w:del>'
            '<w:r><w:t>尾</w:t></w:r></w:p>')
    root = _root(make_docx(body))
    assert live_text_of(paragraphs(root)[0]) == "甲 新增尾"


def test_textbox_paragraph_not_counted_in_outer(make_docx):
    body = ('<w:p><w:r><w:t>外层</w:t></w:r><w:r><w:pict><w:txbxContent>'
            '<w:p><w:r><w:t>框内</w:t></w:r></w:p></w:txbxContent></w:pict></w:r></w:p>')
    ps = paragraphs(_root(make_docx(body)))
    assert [live_text_of(p) for p in ps] == ["外层", "框内"]


def test_ambiguous_across_paragraphs_lists_candidates(make_docx, no_outline):
    root = _root(make_docx(_p("本合同一式两份。") + _p("附件：本合同一式两份。")))
    with pytest.raises(LocateError) as ei:
        locate(root, {"anchor": "本合同一式两份"})
    f = ei.value.failures[0]
    assert "歧义" in f["reason"] and [c["i"] for c in f["candidates"]] == [0, 1]
    assert f["candidates"][1]["text"].startswith("附件")


def test_ambiguous_within_one_paragraph(make_docx):
    root = _root(make_docx(_p("违约金为百分之十；违约金为百分之十。")))
    with pytest.raises(LocateError) as ei:
        locate(root, {"anchor": "违约金为百分之十"})
    assert len(ei.value.failures[0]["candidates"]) == 2
    # paragraph 模式按段去重，不算歧义
    r = locate(root, {"anchor": "违约金为百分之十", "range": "paragraph"})
    assert r["mode"] == "paragraph" and r["start"] is None


def test_occ_picks_nth(make_docx):
    root = _root(make_docx(_p("甲方签字。") + _p("乙方：甲方签字。")))
    r = locate(root, {"anchor": "甲方签字", "occ": 2})
    assert r["p_index"] == 1 and r["matched_on"] == "anchor+occ"
    with pytest.raises(LocateError) as ei:
        locate(root, {"anchor": "甲方签字", "occ": 3})
    assert "occ" in ei.value.failures[0]["reason"]


def test_clause_filter_disambiguates(make_docx, fake_outline):
    body = (_p("第一条 定义") + _p("费用按月结算。") + _p("第二条 付款") + _p("费用按月结算。")
            + _p("第三条 其他"))
    root = _root(make_docx(body))
    fake_outline["paras"] = [
        {"i": 0, "label": "第一条", "level": 0, "text": "第一条 定义", "path": "第一条", "in_table": False},
        {"i": 2, "label": "第二条", "level": 0, "text": "第二条 付款", "path": "第二条", "in_table": False},
        {"i": 4, "label": "第三条", "level": 0, "text": "第三条 其他", "path": "第三条", "in_table": False},
    ]
    r = locate(root, {"anchor": "费用按月结算", "clause": "第二条"}, fake_outline["paras"])
    assert r["p_index"] == 3 and r["matched_on"] == "anchor+clause" and r["mode"] == "sentence"
    # 未给 clause 时歧义，候选带 outline label/path
    with pytest.raises(LocateError) as ei:
        locate(root, {"anchor": "费用按月结算"}, fake_outline["paras"])
    assert [c["label"] for c in ei.value.failures[0]["candidates"]] == ["第一条", "第二条"]


def test_clause_filter_stops_at_next_same_level(make_docx, fake_outline):
    body = _p("第一条 甲") + _p("1.1 细则：保密。") + _p("第二条 乙") + _p("保密。")
    root = _root(make_docx(body))
    fake_outline["paras"] = [
        {"i": 0, "label": "第一条", "level": 0, "path": "第一条"},
        {"i": 1, "label": "1.1", "level": 1, "path": "第一条 > 1.1"},
        {"i": 2, "label": "第二条", "level": 0, "path": "第二条"},
    ]
    r = locate(root, {"anchor": "保密", "clause": "第一条"}, fake_outline["paras"])
    assert r["p_index"] == 1


def test_clause_fallback_whole_paragraph(make_docx, fake_outline):
    root = _root(make_docx(_p("第一条 定义") + _p("第二条 付款方式")))
    fake_outline["paras"] = [{"i": 0, "label": "第一条", "level": 0, "path": "第一条"},
                             {"i": 1, "label": "第二条", "level": 0, "path": "第二条"}]
    r = locate(root, {"anchor": "原文里不存在的句子", "clause": "第二条"}, fake_outline["paras"])
    assert r["mode"] == "clause_fallback" and r["p_index"] == 1 and r["start"] is None


def test_miss_without_clause(make_docx):
    root = _root(make_docx(_p("甲方。")))
    with pytest.raises(LocateError) as ei:
        locate(root, {"anchor": "不存在"})
    assert ei.value.failures[0]["reason"] == "未命中"


def test_empty_anchor(make_docx):
    root = _root(make_docx(_p("甲方。")))
    with pytest.raises(LocateError) as ei:
        locate(root, {"anchor": " 　\n"})
    assert ei.value.failures[0]["reason"] == "锚点为空"


def test_clause_needed_but_outline_missing_fails_cleanly(make_docx, no_outline):
    root = _root(make_docx(_p("甲方。")))
    with pytest.raises(LocateError) as ei:
        locate(root, {"anchor": "不存在", "clause": "第一条"})
    assert "outline" in ei.value.failures[0]["reason"]
    # 锚点唯一时即便给了 clause 也不需要 outline
    r = locate(root, {"anchor": "甲方", "clause": "第一条"})
    assert r["p_index"] == 0


def test_clause_unresolvable_but_occ_given_falls_through(make_docx, no_outline):
    # 条款标签解析不了（如「首部」）时，若给了 occ，不应报"条款过滤失败"，应按 occ 消歧
    root = _root(make_docx(_p("甲公司，一家有限合伙企业。") + _p("乙公司，一家有限合伙企业。")))
    r = locate(root, {"anchor": "一家有限合伙企业", "clause": "首部", "occ": 2})
    assert r["p_index"] == 1 and r["mode"] == "sentence"
    with pytest.raises(LocateError) as ei:
        locate(root, {"anchor": "一家有限合伙企业", "clause": "首部"})
    assert "条款过滤失败" in ei.value.failures[0]["reason"]
