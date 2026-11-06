import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))            # 让 engine/ spec/ 可被 import


@pytest.fixture
def sample_docx(tmp_path):
    """造一份含若干段落与一张 2x2 表格的简单 .docx。"""
    from docx import Document as Docx
    d = Docx()
    d.add_paragraph("本合同由乙集团与对方签署。")
    d.add_paragraph("第三条 利息：年利率为基准利率。")
    d.add_paragraph("第十五条 保密义务：双方应保密。")
    d.add_paragraph("本合同自双方签字之日起生效。")
    t = d.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "借款人"; t.cell(0, 1).text = "PT EXAMPLE"
    t.cell(1, 0).text = "金额";   t.cell(1, 1).text = "100"
    out = tmp_path / "sample.docx"
    d.save(str(out))
    return out


# ---------- 批注定位 / 句级高亮测试用：直接按 body XML 造最小 .docx ----------
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_CT = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
       '<Default Extension="xml" ContentType="application/xml"/>'
       '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
       'officedocument.wordprocessingml.document.main+xml"/>%s</Types>')
_CT_COMMENTS = ('<Override PartName="/word/comments.xml" ContentType="application/vnd.'
                'openxmlformats-officedocument.wordprocessingml.comments+xml"/>')
_PKG_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
             '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
             'relationships/officeDocument" Target="word/document.xml"/></Relationships>')
_DOC_RELS = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s'
             '</Relationships>')
_REL_COMMENTS = ('<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/'
                 '2006/relationships/comments" Target="comments.xml"/>')


def build_docx(path, body, comments=None):
    """body：<w:body> 内的 XML（前缀 w）；comments：<w:comments> 内的 XML（可选）。"""
    import zipfile
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="%s"><w:body>%s</w:body></w:document>' % (W_NS, body))
    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("[Content_Types].xml", _CT % (_CT_COMMENTS if comments is not None else ""))
        z.writestr("_rels/.rels", _PKG_RELS)
        z.writestr("word/_rels/document.xml.rels",
                   _DOC_RELS % (_REL_COMMENTS if comments is not None else ""))
        z.writestr("word/document.xml", doc)
        if comments is not None:
            z.writestr("word/comments.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<w:comments xmlns:w="%s">%s</w:comments>' % (W_NS, comments))
    return path


@pytest.fixture
def make_docx(tmp_path):
    counter = [0]

    def _make(body, comments=None):
        counter[0] += 1
        return build_docx(tmp_path / ("built%d.docx" % counter[0]), body, comments)
    return _make


@pytest.fixture
def fake_outline(monkeypatch):
    """用假的 engine.outline 替身（并行任务的真模块未就绪或有变动时，测试不受影响）。
    state["paras"] 设为 outline 条目列表：[{i, label, level, text, path, in_table}]。"""
    import types
    import engine
    state = {"paras": []}
    mod = types.ModuleType("engine.outline")
    mod.outline_from_parts = lambda doc_root, numbering_root=None, styles_root=None: state["paras"]
    mod.resolve_clause = lambda paras, label: [e["i"] for e in paras if e.get("label") == label]
    monkeypatch.setitem(sys.modules, "engine.outline", mod)
    monkeypatch.setattr(engine, "outline", mod, raising=False)
    return state


@pytest.fixture
def no_outline(monkeypatch):
    """模拟 outline 模块不可用（import 抛 ImportError）。"""
    import engine
    monkeypatch.setitem(sys.modules, "engine.outline", None)
    monkeypatch.delattr(engine, "outline", raising=False)
