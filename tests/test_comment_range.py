# 句级 / 整段批注注入：跨 run、跨 w:ins、跳过 w:del、pPr 顺序、加粗下划线、逐 id 校验、保留他人批注
import pytest
from lxml import etree

from engine import ooxml
from engine.comment import Document
from engine.locate import live_text_of, paragraphs
from engine.ooxml import q

RPR_B = '<w:rPr><w:b/></w:rPr>'
RPR_I = '<w:rPr><w:i/></w:rPr>'
INS = '<w:ins w:id="7" w:author="他人" w:date="2026-01-01T00:00:00Z">%s</w:ins>'
DEL = '<w:del w:id="8" w:author="他人" w:date="2026-01-01T00:00:00Z">%s</w:del>'


def _open(make_docx, tmp_path, body, comments=None, name="u"):
    up = tmp_path / name
    ooxml.unpack_document(str(make_docx(body, comments)), str(up))
    return Document(str(up)), up


def _children(p):
    return [etree.QName(c).localname for c in p]


def _txt(r):
    return "".join(t.text or "" for t in r.iter(q("t")))


def test_cross_run_range(make_docx, tmp_path):
    body = ('<w:p><w:pPr><w:jc w:val="both"/></w:pPr>'
            '<w:r>%s<w:t>甲方应当于</w:t></w:r><w:r>%s<w:t>十日内付款，</w:t></w:r>'
            '<w:r><w:t>否则违约。</w:t></w:r></w:p>' % (RPR_B, RPR_I))
    doc, _ = _open(make_docx, tmp_path, body)
    p = paragraphs(doc.doc_root)[0]
    before = live_text_of(p)
    s = before.index("于十日内")
    doc.add_comment_range(p, s, s + len("于十日内"), "意见")
    assert live_text_of(p) == before
    kids = _children(p)
    assert kids[0] == "pPr"
    i0, i1 = kids.index("commentRangeStart"), kids.index("commentRangeEnd")
    runs_in = [c for c in list(p)[i0 + 1:i1] if etree.QName(c).localname == "r"]
    assert "".join(_txt(r) for r in runs_in) == "于十日内"
    ref = list(p)[i1 + 1]
    assert ref.find(q("commentReference")) is not None
    assert ref.find(q("rPr")).find(q("i")) is not None  # 复制末个命中 run 的 rPr
    assert doc.verify_comments()["bad"] == []


def test_split_keeps_space_preserve(make_docx, tmp_path):
    body = '<w:p><w:r><w:t xml:space="preserve">前 后 尾</w:t></w:r></w:p>'
    doc, _ = _open(make_docx, tmp_path, body)
    p = paragraphs(doc.doc_root)[0]
    doc.add_comment_range(p, 2, 3, "x")
    ts = [t for t in p.iter(q("t"))]
    assert "".join(t.text for t in ts) == "前 后 尾"
    for t in ts:
        if t.text != t.text.strip():
            assert t.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"


def test_range_across_ins(make_docx, tmp_path):
    body = ('<w:p><w:r><w:t>原文甲</w:t></w:r>' + INS % '<w:r><w:t>插入乙</w:t></w:r><w:r><w:t>插入丙</w:t></w:r>'
            + '<w:r><w:t>尾</w:t></w:r></w:p>')
    doc, _ = _open(make_docx, tmp_path, body)
    p = paragraphs(doc.doc_root)[0]
    t = live_text_of(p)
    s, e = t.index("甲"), t.index("乙") + 1
    doc.add_comment_range(p, s, e, "跨插入")
    assert live_text_of(p) == t
    ins = p.find(q("ins"))
    # rangeEnd 在 ins 内（紧跟"插入乙"run），引用 run 挂出 ins 之外
    assert ins.find(q("commentRangeEnd")) is not None
    assert ins.find(".//" + q("commentReference")) is None
    assert ins.getnext().find(q("commentReference")) is not None
    # ins 内文本仍完整（插入丙 未被截走）
    assert "".join(x.text for x in ins.iter(q("t"))) == "插入乙插入丙"
    assert doc.verify_comments()["bad"] == []


def test_range_skips_del(make_docx, tmp_path):
    body = ('<w:p><w:r><w:t>甲乙</w:t></w:r>' + DEL % '<w:r><w:delText>被删</w:delText></w:r>'
            + '<w:r><w:t>丙丁</w:t></w:r></w:p>')
    doc, _ = _open(make_docx, tmp_path, body)
    p = paragraphs(doc.doc_root)[0]
    assert live_text_of(p) == "甲乙丙丁"
    doc.add_comment_range(p, 1, 3, "乙丙")  # 跨过删除段
    kids = _children(p)
    i0, i1 = kids.index("commentRangeStart"), kids.index("commentRangeEnd")
    assert "del" in kids[i0:i1]  # 删除段原样留在区间内
    d = p.find(q("del"))
    assert "".join(x.text for x in d.iter(q("delText"))) == "被删"
    assert live_text_of(p) == "甲乙丙丁"


def test_two_ranges_same_paragraph(make_docx, tmp_path):
    body = '<w:p><w:r><w:t>第一句话。第二句话。第三句。</w:t></w:r></w:p>'
    doc, _ = _open(make_docx, tmp_path, body)
    p = paragraphs(doc.doc_root)[0]
    t = live_text_of(p)
    a = doc.add_comment_range(p, t.index("第一句话"), t.index("第一句话") + 4, "一")
    b = doc.add_comment_range(p, t.index("第二句话"), t.index("第二句话") + 4, "二")
    assert a != b
    res = doc.verify_comments()
    assert res["total"] == 2 and sorted(res["ok_ids"]) == sorted([str(a), str(b)])
    assert live_text_of(p) == t


def test_range_inside_hyperlink_markers_are_siblings(make_docx, tmp_path):
    body = ('<w:p><w:r><w:t>见</w:t></w:r><w:hyperlink w:anchor="x"><w:r><w:t>附件一说明</w:t></w:r>'
            '</w:hyperlink><w:r><w:t>。</w:t></w:r></w:p>')
    doc, _ = _open(make_docx, tmp_path, body)
    p = paragraphs(doc.doc_root)[0]
    doc.add_comment_range(p, 1, 4, "链接内")
    h = p.find(q("hyperlink"))
    assert _children(h)[0] == "commentRangeStart" and "commentRangeEnd" in _children(h)
    assert doc.verify_comments()["bad"] == []


def test_paragraph_mode_start_after_ppr(make_docx, tmp_path):
    body = '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>整段</w:t></w:r></w:p><w:p><w:r><w:t>无pPr</w:t></w:r></w:p>'
    doc, _ = _open(make_docx, tmp_path, body)
    p0, p1 = paragraphs(doc.doc_root)
    doc.add_comment_paragraph(p0, "a")
    doc.add_comment_paragraph(p1, "b")
    assert _children(p0)[:2] == ["pPr", "commentRangeStart"]
    assert _children(p1)[0] == "commentRangeStart"
    assert _children(p0)[-2:] == ["commentRangeEnd", "r"]


def test_bold_underline_runs(make_docx, tmp_path):
    doc, up = _open(make_docx, tmp_path, '<w:p><w:r><w:t>甲</w:t></w:r></w:p>')
    p = paragraphs(doc.doc_root)[0]
    doc.add_comment_paragraph(p, "前文**重点**后文\n第二行")
    c = list(doc.comments_root.iter(q("comment")))[-1]
    runs = list(c.iter(q("r")))
    assert [_txt(r) for r in runs] == ["前文", "重点", "后文", "第二行"]
    bold = runs[1].find(q("rPr"))
    assert [etree.QName(x).localname for x in bold] == ["rFonts", "b", "bCs", "sz", "szCs", "u", "lang"]
    assert bold.find(q("u")).get(q("val")) == "single"
    assert runs[0].find(q("rPr")).find(q("b")) is None
    assert bold.find(q("rFonts")).get(q("eastAsia")) == "宋体"


def test_unclosed_bold_kept_raw_with_warning(make_docx, tmp_path, capsys):
    doc, _ = _open(make_docx, tmp_path, '<w:p><w:r><w:t>甲</w:t></w:r></w:p>')
    doc.add_comment_paragraph(paragraphs(doc.doc_root)[0], "注意**未闭合")
    c = list(doc.comments_root.iter(q("comment")))[-1]
    assert "".join(t.text for t in c.iter(q("t"))) == "注意**未闭合"
    assert "未闭合" in capsys.readouterr().err and doc.warnings


def test_verify_comments_per_id(make_docx, tmp_path):
    doc, _ = _open(make_docx, tmp_path, '<w:p><w:r><w:t>甲乙丙</w:t></w:r></w:p>')
    p = paragraphs(doc.doc_root)[0]
    a = doc.add_comment_range(p, 0, 1, "a")
    b = doc.add_comment_range(p, 2, 3, "b")
    for el in list(p.iter(q("commentRangeEnd"))):
        if el.get(q("id")) == str(b):
            el.getparent().remove(el)
    res = doc.verify_comments()
    assert res["ok_ids"] == [str(a)]
    assert res["bad"] == [{"id": str(b), "missing": ["commentRangeEnd"]}]
    assert doc.verify_comments(strict=False)["bad"] == []


def test_verify_detects_order_and_orphan(make_docx, tmp_path):
    body = ('<w:p><w:commentRangeEnd w:id="0"/><w:r><w:t>甲</w:t></w:r><w:commentRangeStart w:id="0"/>'
            '<w:r><w:commentReference w:id="0"/></w:r><w:r><w:commentReference w:id="5"/></w:r></w:p>')
    comments = '<w:comment w:id="0" w:author="他人"><w:p><w:r><w:t>旧</w:t></w:r></w:p></w:comment>'
    doc, _ = _open(make_docx, tmp_path, body, comments)
    bad = {b["id"]: b["missing"] for b in doc.verify_comments()["bad"]}
    assert bad == {"0": ["order"], "5": ["comment"]}


def test_existing_comments_preserved_and_ids_not_reused(make_docx, tmp_path):
    body = ('<w:p><w:commentRangeStart w:id="3"/><w:r><w:t>旧批注处</w:t></w:r><w:commentRangeEnd w:id="3"/>'
            '<w:r><w:commentReference w:id="3"/></w:r><w:r><w:t>新批注处</w:t></w:r></w:p>')
    comments = '<w:comment w:id="3" w:author="对方"><w:p><w:r><w:t>对方意见</w:t></w:r></w:p></w:comment>'
    doc, up = _open(make_docx, tmp_path, body, comments)
    p = paragraphs(doc.doc_root)[0]
    t = live_text_of(p)
    cid = doc.add_comment_range(p, t.index("新"), len(t), "我方意见")
    doc.save()
    assert cid == 4
    doc2 = Document(str(up))
    res = doc2.verify_comments()
    assert res["total"] == 2 and sorted(res["ok_ids"]) == ["3", "4"]
    authors = [c.get(q("author")) for c in doc2.comments_root.iter(q("comment"))]
    assert authors == ["对方", "REVIEWER"]


def test_invalid_range_rejected(make_docx, tmp_path):
    doc, _ = _open(make_docx, tmp_path, '<w:p><w:r><w:t>甲乙</w:t></w:r></w:p>')
    p = paragraphs(doc.doc_root)[0]
    with pytest.raises(ValueError):
        doc.add_comment_range(p, 1, 1, "空区间")
    with pytest.raises(ValueError):
        doc.add_comment_range(p, 0, 9, "越界")


REV_TAGS = ("ins", "del", "moveFrom", "moveTo", "rPrChange", "pPrChange")


def _rev_ids(root):
    return [el.get(q("id")) for t in REV_TAGS for el in root.iter(q(t))]


def test_split_renumbers_rprchange_ids(make_docx, tmp_path):
    body = ('<w:p><w:r><w:rPr><w:b/><w:rPrChange w:id="5" w:author="他人" w:date="2026-01-01T00:00:00Z">'
            '<w:rPr/></w:rPrChange></w:rPr><w:t>甲乙丙丁戊</w:t></w:r>'
            + INS % '<w:r><w:t>尾</w:t></w:r>' + '</w:p>')
    doc, _ = _open(make_docx, tmp_path, body)
    p = paragraphs(doc.doc_root)[0]
    assert len(list(doc.doc_root.iter(q("rPrChange")))) == 1
    doc.add_comment_range(p, 1, 3, "中段")  # 拆成 甲 | 乙丙 | 丁戊 → 多出 2 份
    assert len(list(doc.doc_root.iter(q("rPrChange")))) == 1 + 2
    ids = _rev_ids(doc.doc_root)
    assert len(ids) == len(set(ids)) == 4
    assert min(int(i) for i in ids if i not in ("5", "7")) > 7  # 新 id 取全文档 max + 1 起
    assert live_text_of(p) == "甲乙丙丁戊尾"
    ref = p.find(".//" + q("commentReference")).getparent()
    assert ref.find(q("rPr")).find(q("rPrChange")) is None
