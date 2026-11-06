# engine/locate.py — 批注锚点定位：归一化命中 + 歧义检测 + 条款号过滤 / 兜底
#
# 文本模型（与 engine/comment.py 的字符偏移共用，务必一致）：
#   - 只取"本段自有"的 run（最近的 w:p 祖先就是本段；文本框内嵌段落另算）
#   - 接受修订后视图：计入 w:ins / w:moveTo 内文字，排除 w:del / w:moveFrom 内文字
#   - w:t 计原文；w:tab / w:ptab 记一个空格；w:br / w:cr 记一个换行
#
# outline 模块（engine/outline.py，由并行任务提供）接口：
#   outline.outline_from_parts(document_root, numbering_root, styles_root) -> list[dict]
#       每项含 i / label / level / text / path / in_table，i 与 paragraphs() 下标对齐
#   outline.resolve_clause(paras, label) -> list[int]
# 未就绪时：只有真正需要条款号（歧义消解 / 零命中兜底）才报"条款定位不可用"，不崩溃。
from engine.match_norm import norm_for_match, norm_simple, span_to_orig
from engine.ooxml import q

_EXCLUDE_WRAPPERS = (q("del"), q("moveFrom"))
_SPACE_TAGS = (q("tab"), q("ptab"))
_BREAK_TAGS = (q("br"), q("cr"))


class LocateError(Exception):
    """定位失败；failures 为失败条目清单（每项 dict）。"""

    def __init__(self, failures):
        self.failures = list(failures)
        super().__init__("定位失败 %d 条" % len(self.failures))


def paragraphs(document_root):
    return list(document_root.iter(q("p")))


def _owner_p(el):
    cur = el.getparent()
    while cur is not None and cur.tag != q("p"):
        cur = cur.getparent()
    return cur


def _excluded(run, p):
    cur = run.getparent()
    while cur is not None and cur is not p:
        if cur.tag in _EXCLUDE_WRAPPERS:
            return True
        cur = cur.getparent()
    return False


def own_runs(p):
    """本段自有、且在接受修订视图中可见的 run（文档序）。"""
    return [r for r in p.iter(q("r")) if _owner_p(r) is p and not _excluded(r, p)]


def run_pieces(r):
    """run 的文本片段 [(child, text)]；非文本子节点不出现。"""
    out = []
    for c in r:
        if c.tag == q("t"):
            out.append((c, c.text or ""))
        elif c.tag in _SPACE_TAGS:
            out.append((c, " "))
        elif c.tag in _BREAK_TAGS:
            out.append((c, "\n"))
    return out


def run_spans(p):
    """[(run, start, end)]：各可见 run 在段落 live 文本中的字符区间。"""
    spans = []
    cum = 0
    for r in own_runs(p):
        n = sum(len(t) for _, t in run_pieces(r))
        spans.append((r, cum, cum + n))
        cum += n
    return spans


def live_text_of(p):
    return "".join(t for r in own_runs(p) for _, t in run_pieces(r))


# ---------- outline 适配 ----------

_OUTLINE_ERR = []


def _outline_mod():
    try:
        from engine import outline  # noqa: WPS433
        return outline
    except Exception as exc:  # ImportError（未就绪）或其自身导入期异常，均不让定位崩溃
        _OUTLINE_ERR[:] = ["%s: %s" % (type(exc).__name__, exc)]
        return None


def _outline_entry(outline_paras, p_index):
    """该段所属 outline 条目：本段自身条目优先，否则取前方最近的带 label 条目。"""
    if not outline_paras:
        return None
    best = None
    for e in outline_paras:
        i = e.get("i")
        if i is None or i > p_index:
            continue
        if i == p_index:
            return e
        if e.get("label") and (best is None or i > best.get("i", -1)):
            best = e
    return best


def _candidate_info(paras, outline_paras, p_index):
    e = _outline_entry(outline_paras, p_index)
    return {
        "i": p_index,
        "label": e.get("label") if e else None,
        "path": e.get("path") if e else None,
        "text": live_text_of(paras[p_index]).strip()[:30],
    }


def _clause_range(outline_paras, clause):
    """条款号 → (i0, i_end)；i_end 为下一个同级或更高级条款段（不含）。失败返回 (None, 原因)。"""
    mod = _outline_mod()
    if mod is None or not hasattr(mod, "resolve_clause"):
        return None, "条款定位不可用：outline 模块未就绪%s" % (
            "（%s）" % _OUTLINE_ERR[0] if _OUTLINE_ERR and mod is None else "")
    if outline_paras is None:
        return None, "条款定位不可用：未提供 outline 数据"
    try:
        hits = list(mod.resolve_clause(outline_paras, clause))
    except Exception as exc:  # outline 模块自身异常不应让整批崩溃
        return None, "条款定位异常：%s" % exc
    if len(hits) != 1:
        return None, "条款号[%s]解析到 %d 处（需唯一）" % (clause, len(hits))
    i0 = hits[0]
    lvl0 = None
    for e in outline_paras:
        if e.get("i") == i0:
            lvl0 = e.get("level")
            break
    i_end = None
    for e in sorted(outline_paras, key=lambda x: x.get("i", -1)):
        i = e.get("i", -1)
        if i <= i0 or not e.get("label"):
            continue
        lv = e.get("level")
        if lvl0 is None or lv is None or lv <= lvl0:
            i_end = i
            break
    return (i0, i_end), None


# ---------- 定位主函数 ----------

def _fail(item, reason, candidates=None):
    return LocateError([{
        "anchor": item.get("anchor"),
        "clause": item.get("clause"),
        "reason": reason,
        "candidates": candidates or [],
    }])


def locate(document_root, item, outline_paras=None):
    anchor = item.get("anchor") or ""
    clause = item.get("clause")
    occ = item.get("occ")
    rng = item.get("range") or "sentence"
    if rng not in ("sentence", "paragraph"):
        raise _fail(item, "range 取值非法：%s（应为 sentence / paragraph）" % rng)
    na = norm_simple(anchor)
    if not na:
        raise _fail(item, "锚点为空")

    paras = paragraphs(document_root)
    # 候选 = 全部命中位置（段号, 归一化起点）；paragraph 模式按段去重
    cands = []
    norm_cache = {}
    for i, p in enumerate(paras):
        text = live_text_of(p)
        if not text:
            continue
        nt, imap = norm_for_match(text)
        pos = nt.find(na)
        if pos < 0:
            continue
        norm_cache[i] = (text, nt, imap)
        while pos >= 0:
            cands.append((i, pos))
            if rng == "paragraph":
                break
            pos = nt.find(na, pos + 1)

    matched_on = "anchor"
    if not cands:
        if clause:
            r, why = _clause_range(outline_paras, clause)
            if r is None:
                raise _fail(item, "未命中；条款兜底失败：%s" % why)
            i0 = r[0]
            return {"p_index": i0, "p": paras[i0], "start": None, "end": None,
                    "mode": "clause_fallback", "matched_on": "clause:%s" % clause,
                    "matched_text": live_text_of(paras[i0])}
        raise _fail(item, "未命中")

    if len(cands) > 1 and clause:
        r, why = _clause_range(outline_paras, clause)
        if r is None and not occ:
            raise _fail(item, "歧义（%d 处）且条款过滤失败：%s" % (len(cands), why),
                        [_candidate_info(paras, outline_paras, i) for i, _ in cands])
        if r is not None:
            i0, i_end = r
            filtered = [c for c in cands if c[0] >= i0 and (i_end is None or c[0] < i_end)]
            if not filtered:
                raise _fail(item, "条款[%s]范围内无命中（全文 %d 处）" % (clause, len(cands)),
                            [_candidate_info(paras, outline_paras, i) for i, _ in cands])
            cands = filtered
            matched_on = "anchor+clause"
        # 条款标签解析失败（如「首部」不是条款号）但给了 occ：跳过条款过滤，交给下方 occ 消歧

    if len(cands) > 1:
        if occ:
            if not isinstance(occ, int) or occ < 1 or occ > len(cands):
                raise _fail(item, "occ=%s 超出命中数 %d" % (occ, len(cands)),
                            [_candidate_info(paras, outline_paras, i) for i, _ in cands])
            cands = [cands[occ - 1]]
            matched_on += "+occ"
        else:
            raise _fail(item, "歧义：命中 %d 处" % len(cands),
                        [_candidate_info(paras, outline_paras, i) for i, _ in cands])

    i, npos = cands[0]
    text, nt, imap = norm_cache[i]
    if rng == "paragraph":
        return {"p_index": i, "p": paras[i], "start": None, "end": None,
                "mode": "paragraph", "matched_on": matched_on, "matched_text": text}
    start, end = span_to_orig(imap, npos, npos + len(na))
    return {"p_index": i, "p": paras[i], "start": start, "end": end,
            "mode": "sentence", "matched_on": matched_on,
            "matched_text": text[start:end]}
