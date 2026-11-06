from engine import ooxml
from engine.comment import Document


def test_add_comment_creates_comments_part(sample_docx, tmp_path):
    up = tmp_path / "u"
    ooxml.unpack_document(str(sample_docx), str(up))
    doc = Document(str(up), author="REVIEWER")
    para = doc.find_paragraph_by_text("第三条 利息", allow_fallback=False)
    cid = doc.add_comment(para, para, "[🟡 中] 利率上限建议明确约定")
    doc.save()
    assert cid >= 0
    assert (up / "word" / "comments.xml").is_file()
    text = (up / "word" / "comments.xml").read_text(encoding="utf-8")
    assert "利率上限建议明确约定" in text and "REVIEWER" in text
    # 打包不报错
    out = tmp_path / "commented.docx"
    assert ooxml.pack_document(str(up), str(out), validate=True) is True


def test_comment_runs_carry_explicit_cjk_font(sample_docx, tmp_path):
    """回归：批注 run 显式带宋体/zh-CN。此前无 rPr，遇 themeFontLang eastAsia=ja-JP 文档 WPS 回退成 MS 明朝（2026-09-09）"""
    up = tmp_path / "u"
    ooxml.unpack_document(str(sample_docx), str(up))
    doc = Document(str(up), author="REVIEWER")
    para = doc.find_paragraph_by_text("第三条 利息", allow_fallback=False)
    doc.add_comment(para, para, "第一行\n第二行")
    doc.save()
    text = (up / "word" / "comments.xml").read_text(encoding="utf-8")
    assert text.count("<w:r>") == 2
    # run rPr + 段落标记 rPr 各一份 → 每段 2 份
    assert text.count('w:eastAsia="宋体"') == 4
    assert text.count('w:eastAsia="zh-CN"') == 4
    assert text.count('w:hint="eastAsia"') == 4


def test_comment_paragraphs_carry_size_and_spacing(sample_docx, tmp_path):
    """回归：批注显式五号 + 单倍行距 + 脱离网格。此前无 sz/pPr，继承宿主 Normal（line=360/小四）呈 1.5 倍行距大字（2026-09-10）。
    pPr 子元素须按 CT_PPrBase 顺序（2026-10-13）。"""
    up = tmp_path / "u"
    ooxml.unpack_document(str(sample_docx), str(up))
    doc = Document(str(up), author="REVIEWER")
    para = doc.find_paragraph_by_text("第三条 利息", allow_fallback=False)
    doc.add_comment(para, para, "第一行\n第二行")
    doc.save()
    text = (up / "word" / "comments.xml").read_text(encoding="utf-8")
    assert text.count("<w:pPr>") == 2
    assert text.count('<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>') == 2
    assert text.count('<w:snapToGrid w:val="0"/>') == 2
    assert text.index("<w:snapToGrid") < text.index("<w:spacing") < text.index("<w:ind ")
    assert text.count('<w:sz w:val="21"/>') == 4 and text.count('<w:szCs w:val="21"/>') == 4
    # pPr 必须在 run 之前
    assert text.index("<w:pPr>") < text.index("<w:r>")


_ANNOT_STYLES = (
    '<w:style w:type="paragraph" w:styleId="4"><w:name w:val="annotation text"/>'
    '<w:basedOn w:val="1"/><w:link w:val="20"/><w:qFormat/>'
    '<w:rPr><w:rFonts w:eastAsia="等线 Light" w:asciiTheme="minorHAnsi" w:hAnsiTheme="minorHAnsi"/>'
    '<w:b/><w:color w:val="333333"/></w:rPr></w:style>'
    '<w:style w:type="character" w:customStyle="1" w:styleId="20"><w:name w:val="批注文字 字符"/>'
    '<w:link w:val="4"/></w:style>')


def test_annotation_text_style_normalized(sample_docx, tmp_path):
    """回归：宿主「批注文字」样式为等线 Light/主题字体时，WPS 手动新增批注呈黑体类字体（2026-10-09 直播推广合同实案）。
    save() 把 annotation text 及其链接字符样式改为宋体/TNR 五号，清除主题字体属性，其余属性不动。"""
    import re
    up = tmp_path / "u"
    ooxml.unpack_document(str(sample_docx), str(up))
    sp = up / "word" / "styles.xml"
    sp.write_text(sp.read_text(encoding="utf-8").replace("</w:styles>", _ANNOT_STYLES + "</w:styles>"),
                  encoding="utf-8")
    doc = Document(str(up), author="REVIEWER")
    para = doc.find_paragraph_by_text("第三条 利息", allow_fallback=False)
    doc.add_comment(para, para, "批注")
    doc.save()
    s = sp.read_text(encoding="utf-8")
    para_style = re.search(r'<w:style [^>]*w:styleId="4".*?</w:style>', s, re.S).group(0)
    char_style = re.search(r'<w:style [^>]*w:styleId="20".*?</w:style>', s, re.S).group(0)
    for st in (para_style, char_style):
        assert 'w:eastAsia="宋体"' in st and 'w:ascii="Times New Roman"' in st
        assert "Theme" not in st and "等线" not in st
        assert '<w:sz w:val="21"/>' in st and '<w:szCs w:val="21"/>' in st
    # 原有属性保留，且 CT_RPr 顺序 rFonts < b < color < sz
    assert para_style.index("<w:rFonts") < para_style.index("<w:b/>") \
        < para_style.index("<w:color") < para_style.index("<w:sz ")
    out = tmp_path / "styled.docx"
    assert ooxml.pack_document(str(up), str(out), validate=True) is True


_ANNOT_STYLE_LOOSE = (
    '<w:style w:type="paragraph" w:styleId="2"><w:name w:val="annotation text"/>'
    '<w:basedOn w:val="1"/><w:uiPriority w:val="99"/>'
    '<w:pPr><w:spacing w:after="160" w:line="278" w:lineRule="auto"/><w:jc w:val="both"/></w:pPr>'
    '</w:style>')


def _annot_para_style(styles_text):
    import re
    styles = re.findall(r'<w:style [^>]*>.*?</w:style>', styles_text, re.S)
    hits = [s for s in styles if '<w:name w:val="annotation text"/>' in s]
    assert len(hits) == 1
    return hits[0]


def _assert_comment_ppr(style):
    assert '<w:snapToGrid w:val="0"/>' in style
    assert '<w:spacing w:before="0" w:after="0" w:line="240" w:lineRule="auto"/>' in style
    assert 'w:firstLine="0"' in style and 'w:firstLineChars="0"' in style
    # CT_PPrBase 顺序 snapToGrid < spacing < ind；CT_Style 顺序 pPr < rPr
    assert style.index("<w:snapToGrid") < style.index("<w:spacing") < style.index("<w:ind ")
    assert style.index("<w:pPr>") < style.index("<w:rPr>")


def test_annotation_text_style_paragraph_normalized(sample_docx, tmp_path):
    """回归：WPS 新建的「批注文字」样式继承 Normal（after=160 / line=278），手动新增批注行距变松
    （2026-10-12 战略合作框架协议实案）。save() 改写其段落行距，其余 pPr 子元素保留。"""
    up = tmp_path / "u"
    ooxml.unpack_document(str(sample_docx), str(up))
    sp = up / "word" / "styles.xml"
    sp.write_text(sp.read_text(encoding="utf-8").replace("</w:styles>", _ANNOT_STYLE_LOOSE + "</w:styles>"),
                  encoding="utf-8")
    doc = Document(str(up), author="REVIEWER")
    para = doc.find_paragraph_by_text("第三条 利息", allow_fallback=False)
    doc.add_comment(para, para, "批注")
    doc.save()
    st = _annot_para_style(sp.read_text(encoding="utf-8"))
    _assert_comment_ppr(st)
    assert 'w:after="160"' not in st and 'w:line="278"' not in st
    assert '<w:jc w:val="both"/>' in st and st.index("<w:ind ") < st.index("<w:jc ")
    assert '<w:sz w:val="21"/>' in st
    out = tmp_path / "loose.docx"
    assert ooxml.pack_document(str(up), str(out), validate=True) is True


def test_missing_annotation_style_created(sample_docx, tmp_path):
    """回归：宿主无「批注文字」样式时新建（2026-10-12 战略合作框架协议实案：原先不新建，WPS 现场建的样式继承 Normal）。"""
    import re
    up = tmp_path / "u"
    ooxml.unpack_document(str(sample_docx), str(up))
    sp = up / "word" / "styles.xml"
    before = sp.read_text(encoding="utf-8")
    assert '<w:name w:val="annotation text"/>' not in before
    doc = Document(str(up), author="REVIEWER")
    para = doc.find_paragraph_by_text("第三条 利息", allow_fallback=False)
    doc.add_comment(para, para, "批注")
    doc.save()
    after = sp.read_text(encoding="utf-8")
    st = _annot_para_style(after)
    _assert_comment_ppr(st)
    assert 'w:eastAsia="宋体"' in st and 'w:ascii="Times New Roman"' in st and '<w:sz w:val="21"/>' in st
    sid = re.search(r'w:styleId="([^"]+)"', st).group(1)
    assert len(re.findall(r'w:styleId="%s"' % re.escape(sid), after)) == 1
    normal = re.search(r'<w:style [^>]*w:type="paragraph"[^>]*w:default="1"[^>]*w:styleId="([^"]+)"', after) \
        or re.search(r'<w:style [^>]*w:default="1"[^>]*w:styleId="([^"]+)"', after)
    if normal:
        assert '<w:basedOn w:val="%s"/>' % normal.group(1) in st
    # 只追加一个样式，原有样式数不变
    assert after.count("<w:style ") == before.count("<w:style ") + 1
    out = tmp_path / "created.docx"
    assert ooxml.pack_document(str(up), str(out), validate=True) is True
