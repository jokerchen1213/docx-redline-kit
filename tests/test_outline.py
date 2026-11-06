# tests/test_outline.py — 可见编号解析（engine/outline.py）
import json
import zipfile

from lxml import etree

from engine import outline, extract

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
MC = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'


def _x(s):
    return etree.fromstring(s.encode("utf-8"))


def doc(body):
    return _x('<w:document %s %s><w:body>%s</w:body></w:document>' % (W, MC, body))


def numbering(body):
    return _x('<w:numbering %s>%s</w:numbering>' % (W, body))


def styles(body):
    return _x('<w:styles %s>%s</w:styles>' % (W, body))


def P(text="", num=None, ilvl=None, style=None, raw_runs=None):
    ppr = ""
    if style:
        ppr += '<w:pStyle w:val="%s"/>' % style
    if num is not None:
        ppr += '<w:numPr>%s<w:numId w:val="%s"/></w:numPr>' % (
            '<w:ilvl w:val="%d"/>' % ilvl if ilvl is not None else "", num)
    runs = raw_runs if raw_runs is not None else (
        '<w:r><w:t xml:space="preserve">%s</w:t></w:r>' % text if text else "")
    return '<w:p>%s%s</w:p>' % ('<w:pPr>%s</w:pPr>' % ppr if ppr else "", runs)


def LVL(ilvl, fmt, text, start=1, extra=""):
    return ('<w:lvl w:ilvl="%d"><w:start w:val="%d"/><w:numFmt w:val="%s"/>%s'
            '<w:lvlText w:val="%s"/></w:lvl>' % (ilvl, start, fmt, extra, text))


def ABS(aid, *lvls):
    return '<w:abstractNum w:abstractNumId="%s">%s</w:abstractNum>' % (aid, "".join(lvls))


def NUM(nid, aid, overrides=""):
    return '<w:num w:numId="%s"><w:abstractNumId w:val="%s"/>%s</w:num>' % (nid, aid, overrides)


MULTI = ABS("0", LVL(0, "decimal", "%1."), LVL(1, "decimal", "%1.%2"),
            LVL(2, "decimal", "%1.%2.%3")) + NUM("1", "0")
ARTICLE = ABS("5", LVL(0, "chineseCounting", "第%1条"),
              LVL(1, "decimal", "%2."), LVL(2, "decimalEnclosedParen", "%3")) + NUM("7", "5")


def labels(res):
    return [r["label"] for r in res]


# ---------------------------------------------------------------- 格式化

def test_format_chinese_counting():
    f = outline.format_number
    assert [f(n, "chineseCounting") for n in (1, 3, 10, 11, 20, 21, 100, 101, 110, 999)] == \
        ["一", "三", "十", "十一", "二十", "二十一", "一百", "一百零一", "一百一十", "九百九十九"]
    assert f(15, "chineseCountingThousand") == "十五"


def test_format_legal_letters_roman_circle():
    f = outline.format_number
    assert f(10, "chineseLegalSimplified") == "壹拾"
    assert f(23, "chineseLegalSimplified") == "贰拾叁"
    assert f(1, "lowerLetter") == "a" and f(27, "lowerLetter") == "aa" and f(2, "upperLetter") == "B"
    assert f(4, "lowerRoman") == "iv" and f(14, "upperRoman") == "XIV"
    assert f(2, "decimalEnclosedParen") == "(2)" and f(3, "decimalEnclosedCircle") == "③"


def test_unknown_format_warns_once(capsys):
    assert outline.format_number(5, "fooBarFmt") == "5"
    outline.format_number(6, "fooBarFmt")
    err = capsys.readouterr().err
    assert err.count("fooBarFmt") == 1


def test_cn_to_int_roundtrip():
    for n in range(1, 1000):
        assert outline.cn_to_int(outline.format_number(n, "chineseCounting")) == n
    assert outline.cn_to_int("壹拾贰") == 12


# ---------------------------------------------------------------- 计数

def test_multilevel_decimal():
    d = doc(P("总则", 1, 0) + P("定义", 1, 1) + P("细则", 1, 2) + P("细则二", 1, 2)
            + P("第二节", 1, 1) + P("细则三", 1, 2) + P("分则", 1, 0) + P("正文"))
    res = outline.outline_from_parts(d, numbering(MULTI))
    assert labels(res) == ["1.", "1.1", "1.1.1", "1.1.2", "1.2", "1.2.1", "2.", ""]
    assert res[5]["path"] == "1. > 1.2 > 1.2.1"
    assert res[5]["level"] == 2 and res[7]["level"] == -1 and res[7]["path"] == ""


def test_chinese_article_label():
    d = doc(P("定义", 7, 0) + P("款一", 7, 1) + P("款二", 7, 1) + P("项", 7, 2) + P("价款", 7, 0))
    res = outline.outline_from_parts(d, numbering(ARTICLE))
    assert labels(res) == ["第一条", "1.", "2.", "(1)", "第二条"]
    assert res[3]["path"] == "第一条 > 2. > (1)"


def test_style_numbering_and_basedon():
    st = styles(
        '<w:style w:type="paragraph" w:styleId="H1"><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="H2"><w:pPr><w:numPr><w:ilvl w:val="1"/><w:numId w:val="1"/></w:numPr></w:pPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="H2x"><w:basedOn w:val="H2"/></w:style>')
    d = doc(P("一", style="H1") + P("二", style="H2x") + P("三", style="H2"))
    res = outline.outline_from_parts(d, numbering(MULTI), st)
    assert labels(res) == ["1.", "1.1", "1.2"]
    assert res[1]["style"] == "H2x"


def test_style_numid_only_uses_lvl_pstyle():
    # 样式只给 numId，级别由 abstractNum 的 lvl/pStyle 指明
    nb = ABS("0", LVL(0, "decimal", "%1."), LVL(1, "decimal", "%1.%2", extra='<w:pStyle w:val="Sub"/>')) + NUM("1", "0")
    st = styles('<w:style w:type="paragraph" w:styleId="Sub"><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>')
    d = doc(P("a", 1, 0) + P("b", style="Sub"))
    assert labels(outline.outline_from_parts(d, numbering(nb), st)) == ["1.", "1.1"]


def test_direct_numid_zero_disables_style_numbering():
    st = styles('<w:style w:type="paragraph" w:styleId="H1"><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>')
    d = doc(P("x", style="H1") + P("y", 0, 0, style="H1") + P("z", style="H1"))
    assert labels(outline.outline_from_parts(d, numbering(MULTI), st)) == ["1.", "", "2."]


def test_start_override_and_shared_abstract():
    nb = (ABS("0", LVL(0, "decimal", "%1)"))
          + NUM("1", "0") + NUM("2", "0")
          + NUM("3", "0", '<w:lvlOverride w:ilvl="0"><w:startOverride w:val="1"/></w:lvlOverride>'))
    d = doc(P("a", 1, 0) + P("b", 1, 0) + P("c", 2, 0) + P("d", 3, 0) + P("e", 3, 0) + P("f", 1, 0))
    assert labels(outline.outline_from_parts(d, numbering(nb))) == ["1)", "2)", "3)", "1)", "2)", "3)"]


def test_start_value():
    nb = ABS("0", LVL(0, "decimal", "%1.", start=5)) + NUM("1", "0")
    d = doc(P("a", 1, 0) + P("b", 1, 0))
    assert labels(outline.outline_from_parts(d, numbering(nb))) == ["5.", "6."]


def test_islgl_forces_decimal():
    nb = ABS("0", LVL(0, "chineseCounting", "第%1章"),
             LVL(1, "decimal", "%1.%2", extra="<w:isLgl/>")) + NUM("1", "0")
    d = doc(P("a", 1, 0) + P("b", 1, 0) + P("c", 1, 1))
    assert labels(outline.outline_from_parts(d, numbering(nb))) == ["第一章", "第二章", "2.1"]


def test_paren_fullwidth_and_bullet_none():
    nb = (ABS("0", LVL(0, "chineseCounting", "（%1）")) + NUM("1", "0")
          + ABS("1", LVL(0, "bullet", "●")) + NUM("2", "1")
          + ABS("2", LVL(0, "none", "")) + NUM("3", "2")
          + ABS("3", LVL(0, "lowerLetter", "%1)")) + NUM("4", "3"))
    d = doc(P("a", 1, 0) + P("b", 1, 0) + P("c", 2, 0) + P("d", 3, 0) + P("e", 4, 0) + P("f", 4, 0))
    assert labels(outline.outline_from_parts(d, numbering(nb))) == ["（一）", "（二）", "●", "", "a)", "b)"]


def test_table_paragraphs_and_iteration_index():
    tbl = '<w:tbl><w:tr><w:tc>%s</w:tc><w:tc>%s</w:tc></w:tr></w:tbl>' % (P("单元1", 1, 1), P("单元2"))
    d = doc(P("一", 1, 0) + tbl + P("二", 1, 0))
    res = outline.outline_from_parts(d, numbering(MULTI))
    assert [r["i"] for r in res] == list(range(len(list(d.iter(outline.q("p"))))))
    assert [(r["label"], r["in_table"]) for r in res] == [
        ("1.", False), ("1.1", True), ("", True), ("2.", False)]
    md = outline.to_markdown(res)
    assert "1.1\t[表] 单元1" in md and "\t[表] 单元2" in md


def test_ins_del_tab_br_text():
    runs = ('<w:r><w:t>甲方</w:t></w:r>'
            '<w:del w:id="1" w:author="x"><w:r><w:delText>乙集团</w:delText></w:r></w:del>'
            '<w:ins w:id="2" w:author="x"><w:r><w:t>甲公司</w:t></w:r></w:ins>'
            '<w:r><w:tab/><w:t>A</w:t><w:br/><w:t>B</w:t></w:r>')
    ppr_tabs = '<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="100"/></w:tabs></w:pPr>%s</w:p>' % runs
    d = doc(ppr_tabs)
    res = outline.outline_from_parts(d)
    assert res[0]["text"] == "甲方甲公司 A\nB"


def test_textbox_paragraph_not_merged_into_parent():
    inner = P("框内文字")
    runs = ('<w:r><w:t>外层</w:t></w:r><w:r><mc:AlternateContent><mc:Choice Requires="wps">'
            '<w:txbxContent>%s</w:txbxContent></mc:Choice><mc:Fallback><w:txbxContent>%s</w:txbxContent>'
            '</mc:Fallback></mc:AlternateContent></w:r>' % (inner, inner))
    d = doc('<w:p>%s</w:p>' % runs)
    res = outline.outline_from_parts(d)
    assert [r["text"] for r in res] == ["外层", "框内文字", "框内文字"]


def test_no_numbering_part():
    d = doc(P("第一条 定义") + P("1、本合同……"))
    res = outline.outline_from_parts(d, None, None)
    assert labels(res) == ["", ""] and all(r["level"] == -1 for r in res)
    st = styles('<w:style w:type="paragraph" w:styleId="H1"><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr></w:pPr></w:style>')
    assert labels(outline.outline_from_parts(doc(P("x", style="H1")), None, st)) == [""]


# ---------------------------------------------------------------- resolve_clause

def _auto_contract():
    d = doc(P("定义", 7, 0) + P("款一", 7, 1) + P("款二", 7, 1) + P("价款", 7, 0) + P("款一'", 7, 1)
            + P("付款", 7, 0) + P("首付", 7, 1) + P("尾款", 7, 1) + P("（1）项", 7, 2))
    return outline.outline_from_parts(d, numbering(ARTICLE))


def test_resolve_exact_label():
    res = outline.outline_from_parts(doc(P("a", 1, 0) + P("b", 1, 1) + P("c", 1, 2)), numbering(MULTI))
    assert outline.resolve_clause(res, "1.1.1") == [2]
    assert outline.resolve_clause(res, "9.9") == []
    res2 = _auto_contract()
    assert outline.resolve_clause(res2, "（1）") == [8]


def test_resolve_article_auto_chinese_and_arabic():
    res = _auto_contract()
    assert outline.resolve_clause(res, "第三条") == [5]
    assert outline.resolve_clause(res, "第3条") == [5]
    assert outline.resolve_clause(res, "第九条") == []


def test_resolve_article_clause_auto():
    res = _auto_contract()
    assert outline.resolve_clause(res, "第三条第2款") == [7]
    assert outline.resolve_clause(res, "第三条第二款") == [7]
    assert outline.resolve_clause(res, "第二条第一款") == [4]
    assert outline.resolve_clause(res, "第二条第二款") == []  # 不越过下一条
    assert outline.resolve_clause(res, "第三条第一款") == [6]  # 三级"(1)"不算款


def test_resolve_decimal_top_level_article():
    # "%1." 顶级 + "%1.%2" 二级：第3条 → 顶级 3.；第3条第2款 → 3.2
    d = doc(P("a", 1, 0) + P("b", 1, 0) + P("c", 1, 0) + P("c1", 1, 1) + P("c2", 1, 1) + P("d", 1, 0) + P("d2", 1, 1))
    res = outline.outline_from_parts(d, numbering(MULTI))
    assert outline.resolve_clause(res, "第三条") == [2]
    assert outline.resolve_clause(res, "第3条第2款") == [4]


def test_resolve_text_numbered_contract():
    d = doc(P("第一条 定义") + P("1、甲方指……") + P("第二条 价款") + P("1、总价") + P("2、付款") + P("（3）其他")
            + P("2.5亿元整") + P("第三条 违约") + P("2、违约金"))
    res = outline.outline_from_parts(d)
    assert outline.resolve_clause(res, "第二条") == [2]
    assert outline.resolve_clause(res, "第二条第二款") == [4]
    assert outline.resolve_clause(res, "第2条第3款") == [5]
    assert outline.resolve_clause(res, "第三条第2款") == [8]


# ---------------------------------------------------------------- docx 路径 / 输出 / CLI / extract

def _write_docx(tmp_path, d, nb=None, st=None):
    out = tmp_path / "t.docx"
    with zipfile.ZipFile(str(out), "w") as z:
        z.writestr("word/document.xml", etree.tostring(d))
        if nb is not None:
            z.writestr("word/numbering.xml", etree.tostring(nb))
        if st is not None:
            z.writestr("word/styles.xml", etree.tostring(st))
    return out


def test_build_outline_and_json(tmp_path):
    p = _write_docx(tmp_path, doc(P("定义", 7, 0) + P("款", 7, 1)), numbering(ARTICLE))
    res = outline.build_outline(p)
    assert labels(res) == ["第一条", "1."]
    back = json.loads(outline.to_json(res))
    assert set(back[0]) == {"i", "label", "level", "text", "style", "in_table", "path"}


def test_cli_outline(tmp_path):
    import argparse
    p = _write_docx(tmp_path, doc(P("定义", 7, 0) + P("款", 7, 1)), numbering(ARTICLE))
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    outline.add_cli(sub)
    o = tmp_path / "o.md"
    a = ap.parse_args(["outline", str(p), "-o", str(o)])
    assert a.fn(a) == 0
    assert o.read_text(encoding="utf-8") == "第一条\t定义\n1.\t款"
    oj = tmp_path / "o.json"
    a = ap.parse_args(["outline", str(p), "--format", "json", "-o", str(oj)])
    a.fn(a)
    assert json.loads(oj.read_text(encoding="utf-8"))[1]["path"] == "第一条 > 1."


def test_extract_structure_has_label_and_comment_paragraphs(tmp_path):
    p = _write_docx(tmp_path, doc(P("定义", 7, 0) + P("正文")), numbering(ARTICLE))
    cm = ('<w:comments %s><w:comment w:id="0" w:author="A"><w:p><w:r><w:t>第一行</w:t></w:r></w:p>'
          '<w:p><w:r><w:t>第二行</w:t></w:r></w:p></w:comment></w:comments>' % W)
    with zipfile.ZipFile(str(p), "a") as z:
        z.writestr("word/comments.xml", cm)
    data = extract.extract(str(p))
    assert [(x["label"], x["text"]) for x in data["structure"]["paragraphs"]] == [("第一条", "定义"), ("", "正文")]
    assert data["comments"][0]["text"] == "第一行\n第二行"
