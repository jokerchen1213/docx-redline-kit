# tests/test_anchors.py — 批注锚点体检（engine/anchors.py）
# 合成样本只用中性文字（甲方 / 乙方），不得放入真实客户文件。
import json

from lxml import etree

from engine import anchors, outline

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

ITEM3 = "（3）遵守保密约定，不得向任何第三方披露乙方提供的技术资料与报价信息。"
CL13 = "1.3 甲方应于每月10日前将上月服务费全额支付至乙方指定账户。"


def _p(text, cid=None):
    if cid is None:
        return '<w:p><w:r><w:t xml:space="preserve">%s</w:t></w:r></w:p>' % text
    return ('<w:p><w:commentRangeStart w:id="%s"/><w:r><w:t xml:space="preserve">%s</w:t></w:r>'
            '<w:commentRangeEnd w:id="%s"/><w:r><w:commentReference w:id="%s"/></w:r></w:p>'
            % (cid, text, cid, cid))


TABLE = ('<w:tbl><w:tr><w:tc><w:p><w:r><w:t>服务</w:t></w:r></w:p></w:tc>'
         '<w:tc><w:p><w:r><w:t>单价</w:t></w:r></w:p></w:tc></w:tr></w:tbl>')


def _contract(anchor_at):
    """anchor_at：要挂批注 id=0 的段落文字（必须是下列之一）。"""
    paras = ["第一条 合作内容", "1.1 乙方向甲方提供下列服务：", TABLE, "本表价格不含运输费用。",
             "1.2 服务费按月结算。", CL13,
             "第二条 甲方义务", "甲方应履行下列义务：", "（1）按约定付款；", "（2）提供必要的场地；", ITEM3,
             "第三条 其他", "3.1 本协议一式两份。", "3.2 本协议自签字之日起生效。"]
    assert anchor_at in paras
    return "".join(x if x.startswith("<w:tbl>") else _p(x, "0" if x == anchor_at else None)
                   for x in paras)


def _comment(text):
    return ('<w:comment w:id="0" w:author="T"><w:p><w:r><w:t xml:space="preserve">%s</w:t>'
            '</w:r></w:p></w:comment>' % text)


def _run(make_docx, anchor_at, body):
    res = anchors.check(make_docx(_contract(anchor_at), _comment(body)))
    assert len(res["comments"]) == 1
    return res, res["comments"][0]


def _codes(row, level):
    return sorted(f["code"] for f in row["findings"] if f["level"] == level)


# ---------------------------------------------------------------- W3 表后注释段

def test_w3_note_after_table(make_docx):
    res, row = _run(make_docx, "本表价格不含运输费用。", "【说明】价格口径按我方前稿。")
    assert row["location"] == "第一条 1.1 表后注释段"
    assert _codes(row, "warn") == ["W3"] and res["warn"] == ["0"]


def test_w3_not_on_clause_first_sentence(make_docx):
    res, row = _run(make_docx, "1.1 乙方向甲方提供下列服务：", "【说明】价格口径按我方前稿。")
    assert row["findings"] == [] and res["warn"] == []


# ---------------------------------------------------------------- W2 款级

def test_w2_clause_earlier_in_same_article(make_docx):
    res, row = _run(make_docx, CL13, "【说明】本稿调整结算方式（第1.2款）与付款期限（第1.3款）。")
    assert _codes(row, "warn") == ["W2"]
    assert row["location"] == "第一条 1.3"


def test_w2_clause_ok_when_anchor_is_first(make_docx):
    _, row = _run(make_docx, "1.2 服务费按月结算。", "【说明】本稿调整第1.2款与第1.3款。")
    assert row["findings"] == []


def test_w2_cross_article_not_compared(make_docx):
    _, row = _run(make_docx, "3.2 本协议自签字之日起生效。", "【说明】付款期限见第1.3款，本款沿用前稿（第3.2款）。")
    assert row["findings"] == []


def test_w2_citation_excluded(make_docx):
    _, row = _run(make_docx, CL13, "【说明】逾期的，按第1.2款处理。")
    assert row["findings"] == [] and row["cited"] == ["1.2"]
    _, row = _run(make_docx, CL13, "【说明】第1.1款、第1.2款所列服务均按月计费。")
    assert row["findings"] == [] and row["cited"] == ["1.1", "1.2"]


def test_w2_clause_downgraded_when_comment_quotes_anchor(make_docx):
    body = "【说明】我方前稿：甲方应于每月10日前将上月服务费全额支付。本稿另增第1.2款。"
    res, row = _run(make_docx, CL13, body)
    assert _codes(row, "warn") == [] and _codes(row, "hint") == ["W2"]
    assert res["hint"] == ["0"] and res["warn"] == []


# ---------------------------------------------------------------- W1 / W2 项级

def test_w1_item_not_mentioned(make_docx):
    _, row = _run(make_docx, ITEM3, "【说明】本条沿用我方前稿。")
    assert row["location"] == "第二条（3）"
    assert _codes(row, "warn") == ["W1"]


def test_w1_ok_when_item_mentioned(make_docx):
    _, row = _run(make_docx, ITEM3, "【说明】第（三）项为本稿新增。")
    assert row["findings"] == []


def test_w2_item_earlier(make_docx):
    _, row = _run(make_docx, ITEM3, "【说明】第（1）项沿用前稿，第（3）项为本稿新增。")
    assert _codes(row, "warn") == ["W2"]


def test_item_downgraded_when_comment_quotes_item(make_docx):
    _, row = _run(make_docx, ITEM3, "【说明】前稿第（二）项为不得向任何第三方披露乙方提供的技术资料。")
    assert _codes(row, "warn") == [] and _codes(row, "hint") == ["W1", "W2"]


def test_trunk_anchor_location(make_docx):
    _, row = _run(make_docx, "甲方应履行下列义务：", "【说明】第（1）项至第（3）项均沿用前稿。")
    assert row["location"] == "第二条 条首段" and row["findings"] == []


# ---------------------------------------------------------------- 其他

def test_range_start_outside_paragraph(make_docx):
    body = (_p("第一条 合作内容") + '<w:commentRangeStart w:id="0"/>'
            + '<w:p><w:r><w:t>1.1 乙方向甲方提供服务。</w:t></w:r><w:commentRangeEnd w:id="0"/>'
            + '<w:r><w:commentReference w:id="0"/></w:r></w:p>')
    res = anchors.check(make_docx(body, _comment("【说明】无")))
    assert res["comments"][0]["location"] == "第一条 1.1"


def test_mentions_parsing():
    mt = anchors.mentions("第三条第2款、第6.8款第（4）项；第六条第（三）项；按第4.5款处理")
    assert (3, 2) in mt["clauses"] and (6, 8) in mt["clauses"]
    assert mt["cited"] == [(4, 5)]
    assert (6, 8, 4) in mt["items"]
    assert all(n != 3 for _, _, n in mt["items"])   # 只带条号的项不计入


def test_no_comments_and_bad_file(make_docx, tmp_path, capsys):
    import cc_docx
    assert cc_docx.main(["anchors", str(make_docx(_p("第一条 合作内容")))]) == 0
    assert "无批注" in capsys.readouterr().out
    bad = tmp_path / "bad.docx"
    bad.write_text("not a zip")
    assert cc_docx.main(["anchors", str(bad)]) == 1


def test_cli_json(make_docx, capsys):
    import cc_docx
    path = make_docx(_contract(CL13), _comment("【说明】第1.2款、第1.3款一并调整。"))
    assert cc_docx.main(["anchors", str(path), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["warn"] == ["0"] and data["summary"].startswith("⚠ 警告 1 条")


def test_structure_auto_numbering():
    """自动编号合同：第N条 / 1.1（isLgl）/ （n）均来自 numbering.xml。"""
    def x(s):
        return etree.fromstring(s.encode("utf-8"))

    def P(text, ilvl):
        return ('<w:p><w:pPr><w:numPr><w:ilvl w:val="%d"/><w:numId w:val="1"/></w:numPr></w:pPr>'
                '<w:r><w:t>%s</w:t></w:r></w:p>' % (ilvl, text))
    lvls = ('<w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="chineseCounting"/>'
            '<w:lvlText w:val="第%1条"/></w:lvl>'
            '<w:lvl w:ilvl="1"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:isLgl/>'
            '<w:lvlText w:val="%1.%2"/></w:lvl>'
            '<w:lvl w:ilvl="2"><w:start w:val="1"/><w:numFmt w:val="decimal"/>'
            '<w:lvlText w:val="（%3）"/></w:lvl>')
    numbering = x('<w:numbering %s><w:abstractNum w:abstractNumId="0">%s</w:abstractNum>'
                  '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>' % (W, lvls))
    doc = x('<w:document %s><w:body>%s</w:body></w:document>'
            % (W, P("定义", 0) + P("首款", 1) + P("次款", 1) + P("某项", 2) + P("服务", 0) + P("第一款", 1)))
    st = anchors._structure(outline.outline_from_parts(doc, numbering))
    assert [anchors._location(s) for s in st] == [
        "第一条", "第一条 1.1", "第一条 1.2", "第一条 1.2（1）", "第二条", "第二条 2.1"]
