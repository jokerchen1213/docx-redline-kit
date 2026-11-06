# engine/qc.py — 修订一致性 QC，提炼自 _verify.py 的 build_texts
# build_texts(root) → (accepted, original)：
#   accepted = 接受全部修订后的文本（保留非删除 w:t，含 ins 内）
#   original = 拒绝全部修订后的文本（保留非插入 w:t + delText）
from engine.ooxml import q


def build_texts(root):
    acc, orig = [], []

    def walk(el, in_ins, in_del):
        tag = el.tag
        if tag == q("ins"):
            in_ins = True
        elif tag == q("del"):
            in_del = True
        if tag == q("t"):
            txt = el.text or ""
            if not in_del:
                acc.append(txt)
            if not in_ins:
                orig.append(txt)
        elif tag == q("delText"):
            orig.append(el.text or "")
        for c in el:
            walk(c, in_ins, in_del)

    walk(root, False, False)
    return "".join(acc), "".join(orig)


def check(root, original_text, forbid=None):
    """QC 两不变量：
    1. reject_equals_original：拒绝全部修订后的文本 == 传入的原始文本（确保底稿未被篡改）
    2. forbid 残留：接受全部修订后，forbid 词出现次数必须为 0（如旧主体名漏改）
    """
    accepted, reject = build_texts(root)
    reject_ok = (reject == original_text)
    hits = {}
    for w in (forbid or []):
        hits[w] = accepted.count(w)
    ok = reject_ok and all(c == 0 for c in hits.values())
    return {"ok": ok, "reject_equals_original": reject_ok, "forbid_hits": hits}
