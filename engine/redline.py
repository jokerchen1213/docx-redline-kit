# engine/redline.py — 修订引擎，提炼自某项目 redline_engine.py
# （lxml 操作 OOXML，w:ins/w:del 留痕；支持跨 run 文本替换、整段删除、段落/表格插入、表行删除）
# 相对原引擎的改造：作者/日期/起始 id 参数化（实例级，id 扫描现有最大值防撞）；
# make_run/wrap_del/wrap_ins/make_para 由模块函数改为 Doc 方法（使用 self._attrs()）。
from datetime import datetime, timezone, date as _date_cls

import copy

from lxml import etree

WNS = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
XMLNS = 'http://www.w3.org/XML/1998/namespace'
def q(tag): return '{%s}%s' % (WNS, tag)

# ---------- 文本模型 ----------

def _in_del(el, stop):
    p = el.getparent()
    while p is not None and p is not stop:
        if p.tag == q('del'):
            return True
        p = p.getparent()
    return False

def _in_ins(el, stop):
    p = el.getparent()
    while p is not None and p is not stop:
        if p.tag == q('ins'):
            return True
        p = p.getparent()
    return False

def explode_run(r):
    """把含多个 t/tab/br 子节点的 run 拆成每节点一个 run（保持 rPr），返回新 run 列表。
    含 fldChar/drawing 等异质子节点的 run 不拆（避免破坏域代码）。"""
    allowed = (q('t'), q('tab'), q('br'), q('rPr'), q('lastRenderedPageBreak'),
               q('noBreakHyphen'), q('softHyphen'))
    if any(c.tag not in allowed for c in r):
        return [r]
    children = [c for c in r if c.tag in (q('t'), q('tab'), q('br'), q('rPr'))]
    toks = [c for c in children if c.tag != q('rPr')]
    if len(toks) <= 1:
        return [r]
    rPr = r.find(q('rPr'))
    parent = r.getparent()
    idx = list(parent).index(r)
    new_runs = []
    for tok in toks:
        nr = etree.Element(q('r'))
        if rPr is not None:
            nr.append(copy.deepcopy(rPr))
        nr.append(tok)  # 移动节点
        new_runs.append(nr)
    parent.remove(r)
    for i, nr in enumerate(new_runs):
        parent.insert(idx + i, nr)
    return new_runs

def live_segments(p):
    """返回 [(t_element, text, run, own_ins)]，仅统计未被删除的 w:t 文本。"""
    segs = []
    for r in list(p.iter(q('r'))):
        if _in_del(r, p):
            continue
        ts = r.findall(q('t'))
        if not ts:
            continue
        if len(ts) > 1 or any(c.tag in (q('tab'), q('br')) for c in r):
            new_runs = explode_run(r)
            if len(new_runs) == 1 and len(ts) > 1:
                # 异质 run 未拆分：每个 t 单列 segment，但标记不可拆（run=None 防误删整 run）
                for t in ts:
                    segs.append((t, t.text or '', r, _in_ins(r, p)))
            else:
                for nr in new_runs:
                    t = nr.find(q('t'))
                    if t is not None and not _in_del(nr, p):
                        segs.append((t, t.text or '', nr, _in_ins(nr, p)))
        else:
            t = ts[0]
            segs.append((t, t.text or '', r, _in_ins(r, p)))
    return segs

def live_text(p):
    return ''.join(s[1] for s in live_segments(p))

def reject_text(p):
    """拒绝全部修订后的文本（原文）：跳过 ins 内 w:t，计入 delText。"""
    out = []
    for el in p.iter():
        if el.tag == q('t'):
            if not _in_ins(el, p):
                out.append(el.text or '')
        elif el.tag == q('delText'):
            out.append(el.text or '')
    return ''.join(out)

def is_inserted_para(p):
    pPr = p.find(q('pPr'))
    if pPr is not None:
        rPr = pPr.find(q('rPr'))
        if rPr is not None and rPr.find(q('ins')) is not None:
            return True
    tr = p.getparent()
    while tr is not None:
        if tr.tag == q('tr'):
            trPr = tr.find(q('trPr'))
            if trPr is not None and trPr.find(q('ins')) is not None:
                return True
        tr = tr.getparent()
    return False

# ---------- 基本构件 ----------

def set_t(t, text):
    t.text = text
    if text != text.strip() or text == '':
        t.set('{%s}space' % XMLNS, 'preserve')
    elif '{%s}space' % XMLNS in t.attrib:
        del t.attrib['{%s}space' % XMLNS]

# ---------- 核心操作 ----------

class Doc:
    def __init__(self, path, author="REVIEWER", date=None):
        self.path = path
        self.tree = etree.parse(path)
        self.root = self.tree.getroot()
        self.log = []
        self.author = author
        self.date = self._coerce_date(date)
        self._next_id = self._scan_max_id() + 1

    @staticmethod
    def _coerce_date(value):
        # YAML safe_load 会把 ISO 时间戳解析成 datetime/date 对象，lxml 属性值只收字符串
        if value is None:
            return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(value, str):
            return value
        if isinstance(value, datetime):
            return value.strftime("%Y-%m-%dT%H:%M:%SZ")
        if isinstance(value, _date_cls):
            return value.strftime("%Y-%m-%dT00:00:00Z")
        return str(value)

    def _scan_max_id(self):
        mx = 49999
        for el in self.root.iter():
            if el.tag in (q('ins'), q('del')):
                raw = el.get(q('id'))
                if raw and raw.isdigit():
                    mx = max(mx, int(raw))
        return mx

    def _nid(self):
        self._next_id += 1
        return str(self._next_id)

    def _attrs(self):
        return {q('id'): self._nid(), q('author'): self.author, q('date'): self.date}

    def _make_run(self, rpr_src, text, deltext=False):
        r = etree.Element(q('r'))
        if rpr_src is not None:
            r.append(copy.deepcopy(rpr_src))
        t = etree.SubElement(r, q('delText') if deltext else q('t'))
        set_t(t, text)
        return r

    def _wrap_del(self, run):
        """把 run 原位包入 w:del，w:t 改为 w:delText。"""
        parent = run.getparent()
        idx = list(parent).index(run)
        d = etree.Element(q('del'), self._attrs())
        parent.remove(run)
        for t in run.findall(q('t')):
            t.tag = q('delText')
        for it in run.findall(q('instrText')):
            it.tag = q('delInstrText')
        d.append(run)
        parent.insert(idx, d)
        return d

    def _wrap_ins(self, run):
        parent = run.getparent()
        idx = list(parent).index(run)
        i = etree.Element(q('ins'), self._attrs())
        parent.remove(run)
        i.append(run)
        parent.insert(idx, i)
        return i

    def paras(self):
        return list(self.root.iter(q('p')))

    def find_paras(self, anchor):
        return [p for p in self.paras() if anchor in live_text(p)]

    def _locate(self, anchor, occ=1, expect=None, op=''):
        ms = self.find_paras(anchor)
        if expect is not None and len(ms) != expect:
            raise AssertionError('%s 锚点[%s] 期望%d处，实得%d处' % (op, anchor[:40], expect, len(ms)))
        if expect is None and len(ms) != 1 and occ == 1:
            raise AssertionError('%s 锚点[%s] 不唯一/未找到：%d处' % (op, anchor[:40], len(ms)))
        if len(ms) < occ:
            raise AssertionError('%s 锚点[%s] occ=%d 超出%d处' % (op, anchor[:40], occ, len(ms)))
        return ms[occ - 1]

    # -- 段内替换 --
    def replace_in_para(self, p, old, new, count=1):
        done = 0
        guard = 0
        while done < count:
            guard += 1
            if guard > 200:
                raise AssertionError('replace 循环保护：[%s]' % old[:30])
            segs = live_segments(p)
            full = ''.join(s[1] for s in segs)
            pos = full.find(old)
            if pos < 0:
                raise AssertionError('段内未找到 [%s]（剩余%d次未替换）' % (old[:40], count - done))
            self._replace_span(p, segs, pos, pos + len(old), new)
            done += 1
        return done

    def _replace_span(self, p, segs, start, end, new):
        # 找受影响的 segment 区间
        cum = 0
        affected = []  # (seg, s_off, e_off)
        for seg in segs:
            t, txt, run, own_ins = seg
            s, e = cum, cum + len(txt)
            cum = e
            if e <= start or s >= end:
                continue
            affected.append((seg, max(start - s, 0), min(end - s, len(txt))))
        if not affected:
            raise AssertionError('span 定位失败')
        # 全部命中均在本人 ins 内 → 直接改文本
        if all(seg[3] for seg, _, _ in affected):
            first = True
            for (t, txt, run, _), so, eo in affected:
                if first:
                    set_t(t, txt[:so] + new + txt[eo:])
                    first = False
                else:
                    set_t(t, txt[:so] + txt[eo:])
            return
        rpr_src = None
        last_del = None
        for (t, txt, run, own_ins), so, eo in affected:
            pre, mid, post = txt[:so], txt[so:eo], txt[eo:]
            if own_ins:
                # 本人插入的文本直接截除
                set_t(t, pre + post)
                continue
            if rpr_src is None:
                rpr_src = run.find(q('rPr'))
            parent = run.getparent()
            idx = list(parent).index(run)
            if pre:
                parent.insert(idx, self._make_run(run.find(q('rPr')), pre))
                idx += 1
            set_t(t, mid)
            last_del = self._wrap_del(run)
            idx = list(parent).index(last_del)
            if post:
                parent.insert(idx + 1, self._make_run(rpr_for(run), post))
        if new:
            ins_run = self._make_run(rpr_src, new)
            i = etree.Element(q('ins'), self._attrs())
            i.append(ins_run)
            if last_del is not None:
                last_del.addnext(i)
            else:
                p.append(i)

    def R(self, anchor, old=None, new='', occ=1, expect=None, count=1):
        old = old if old is not None else anchor
        p = self._locate(anchor, occ=occ, expect=expect, op='R')
        n = self.replace_in_para(p, old, new, count=count)
        self.log.append(('R', anchor[:30], n))

    def R_ALL(self, old, new, expect=None):
        """所有段落中全部替换（含表格）。"""
        total = 0
        for p in self.paras():
            guard = 0
            while old in live_text(p):
                guard += 1
                if guard > 100:
                    raise AssertionError('R_ALL 循环保护 [%s]' % old[:30])
                segs = live_segments(p)
                full = ''.join(s[1] for s in segs)
                pos = full.find(old)
                self._replace_span(p, segs, pos, pos + len(old), new)
                total += 1
        if expect is not None and total != expect:
            raise AssertionError('R_ALL [%s] 期望%d处，实替换%d处' % (old[:30], expect, total))
        self.log.append(('R_ALL', old[:30], total))
        return total

    # -- 整段删除 --
    def delete_para(self, p, mark_pmark=True):
        for r in list(p.iter(q('r'))):
            if _in_del(r, p):
                continue
            if _in_ins(r, p):
                # 本人插入的直接移除
                ins = r.getparent()
                ins.getparent().remove(ins)
                continue
            self._wrap_del(r)
        if mark_pmark:
            pPr = p.find(q('pPr'))
            if pPr is None:
                pPr = etree.Element(q('pPr'))
                p.insert(0, pPr)
            rPr = pPr.find(q('rPr'))
            if rPr is None:
                rPr = etree.SubElement(pPr, q('rPr'))
            rPr.insert(0, etree.Element(q('del'), self._attrs()))

    def DP(self, anchor, occ=1, expect=None):
        p = self._locate(anchor, occ=occ, expect=expect, op='DP')
        self.delete_para(p)
        self.log.append(('DP', anchor[:30], 1))

    def DP_ALL(self, anchor, expect):
        ms = self.find_paras(anchor)
        if len(ms) != expect:
            raise AssertionError('DP_ALL [%s] 期望%d处，实得%d处' % (anchor[:30], expect, len(ms)))
        for p in ms:
            self.delete_para(p)
        self.log.append(('DP_ALL', anchor[:30], expect))

    def DP_REL(self, anchor, offsets, occ=1, expect=None):
        """删除锚点段落体序偏移处的段落（offset 0=锚点本身）。"""
        p0 = self._locate(anchor, occ=occ, expect=expect, op='DP_REL')
        allp = self.paras()
        i0 = allp.index(p0)
        for off in offsets:
            self.delete_para(allp[i0 + off])
        self.log.append(('DP_REL', anchor[:30], len(offsets)))

    # -- 整段重写 --
    def RP(self, anchor, new, occ=1, expect=None):
        p = self._locate(anchor, occ=occ, expect=expect, op='RP')
        segs = live_segments(p)
        rpr_src = segs[0][2].find(q('rPr')) if segs else None
        for r in list(p.iter(q('r'))):
            if _in_del(r, p):
                continue
            if _in_ins(r, p):
                ins = r.getparent()
                ins.getparent().remove(ins)
                continue
            self._wrap_del(r)
        ins_run = self._make_run(rpr_src, new)
        i = etree.Element(q('ins'), self._attrs())
        i.append(ins_run)
        p.append(i)
        self.log.append(('RP', anchor[:30], 1))

    # -- 插入段落 --
    def _make_para(self, style_src_p, text):
        newp = etree.Element(q('p'))
        pPr_src = style_src_p.find(q('pPr'))
        if pPr_src is not None:
            pPr = copy.deepcopy(pPr_src)
            for sp in pPr.findall(q('sectPr')):
                pPr.remove(sp)
        else:
            pPr = etree.Element(q('pPr'))
        rPr = pPr.find(q('rPr'))
        if rPr is None:
            rPr = etree.SubElement(pPr, q('rPr'))
        for d in rPr.findall(q('del')):
            rPr.remove(d)
        rPr.insert(0, etree.Element(q('ins'), self._attrs()))
        newp.append(pPr)
        segs = live_segments(style_src_p)
        rpr_src = None
        for t, txt, run, own in segs:
            if txt.strip():
                rpr_src = run.find(q('rPr'))
                break
        ins_run = self._make_run(rpr_src, text)
        i = etree.Element(q('ins'), self._attrs())
        i.append(ins_run)
        newp.append(i)
        return newp

    def IA(self, anchor, texts, style_anchor=None, occ=1):
        """在锚点段落之后插入若干新段落（样式取自 style_anchor 或锚点段落）。"""
        p = self._locate(anchor, occ=occ, op='IA')
        src = self._locate(style_anchor, op='IA-style') if style_anchor else p
        cur = p
        for txt in texts:
            np = self._make_para(src, txt)
            cur.addnext(np)
            cur = np
        self.log.append(('IA', anchor[:30], len(texts)))
        return cur

    # -- 行内插入 --
    def _insert_inline(self, anchor, text, marker, before, guard):
        marker = marker if marker is not None else anchor
        p = self._locate(anchor, op='INS')
        segs = live_segments(p)
        full = ''.join(s[1] for s in segs)
        pos = full.find(marker)
        if pos < 0:
            raise AssertionError('行内锚点未找到 [%s]' % marker[:30])
        split = pos if before else pos + len(marker)
        if guard and full[split:split + len(text)] == text:
            self.log.append(('INS-skip', marker[:20], 0))
            return
        cum = 0
        target = None
        off = 0
        rpr_src = None
        for (t, txt, run, own) in segs:
            if cum <= split <= cum + len(txt):
                target, off = (t, run), split - cum
                rpr_src = run.find(q('rPr'))
                break
            cum += len(txt)
        if target is None:
            raise AssertionError('split 定位失败 [%s]' % marker[:30])
        t_el, run = target
        pre, post = (t_el.text or '')[:off], (t_el.text or '')[off:]
        parent = run.getparent()
        idx = list(parent).index(run)
        if post:
            parent.insert(idx + 1, self._make_run(run.find(q('rPr')), post))
        set_t(t_el, pre)
        ins = etree.Element(q('ins'), self._attrs())
        ins.append(self._make_run(rpr_src, text))
        run.addnext(ins)
        self.log.append(('INS', marker[:20], 1))

    def insert_after_text(self, anchor, text, marker=None, guard=False):
        self._insert_inline(anchor, text, marker, before=False, guard=guard)

    def insert_before_text(self, anchor, text, marker=None, guard=False):
        self._insert_inline(anchor, text, marker, before=True, guard=guard)

    # -- 表格操作 --
    def tables(self):
        return list(self.root.iter(q('tbl')))

    def find_table(self, anchor):
        ms = [t for t in self.tables()
              if any(anchor in live_text(p) for p in t.iter(q('p')))]
        if len(ms) != 1:
            raise AssertionError('表锚点[%s] %d处' % (anchor[:30], len(ms)))
        return ms[0]

    def delete_row(self, tr):
        trPr = ensure_trPr(tr)
        trPr.append(etree.Element(q('del'), self._attrs()))
        for p in tr.iter(q('p')):
            self.delete_para(p, mark_pmark=False)

    def DR(self, anchor, occ=1):
        """删除包含锚点文本的表行。"""
        ms = []
        for t in self.tables():
            for tr in t.findall(q('tr')):
                if any(anchor in live_text(p) for p in tr.iter(q('p'))):
                    ms.append(tr)
        if len(ms) < occ:
            raise AssertionError('DR 锚点[%s] %d处' % (anchor[:30], len(ms)))
        if occ == 1 and len(ms) != 1:
            raise AssertionError('DR 锚点[%s] 不唯一：%d处' % (anchor[:30], len(ms)))
        self.delete_row(ms[occ - 1])
        self.log.append(('DR', anchor[:30], 1))

    def DT(self, anchor):
        """删除整张表（所有行标记删除）。"""
        t = self.find_table(anchor)
        for tr in t.findall(q('tr')):
            self.delete_row(tr)
        self.log.append(('DT', anchor[:30], 1))

    # -- 按索引操作表格 --
    def _cell_para(self, table, row, col):
        tbls = self.tables()
        if table >= len(tbls):
            raise AssertionError('表索引越界 %d' % table)
        rows = tbls[table].findall(q('tr'))
        if row >= len(rows):
            raise AssertionError('行索引越界 %d' % row)
        cells = rows[row].findall(q('tc'))
        if col >= len(cells):
            raise AssertionError('列索引越界 %d' % col)
        ps = cells[col].findall(q('p'))
        return ps[0] if ps else None

    def set_cell(self, table, row, col, new=None, old=None):
        """以 tracked-change 把单元格首段的 old（默认=现有全文）替换为 new。"""
        p = self._cell_para(table, row, col)
        if p is None:
            raise AssertionError('空单元格 (%d,%d,%d)' % (table, row, col))
        cur = live_text(p)
        old = old if old is not None else cur
        if old == '':
            raise AssertionError('单元格无文本可替换 (%d,%d,%d)' % (table, row, col))
        self.replace_in_para(p, old, new if new is not None else '')
        self.log.append(('SET_CELL', '%d,%d,%d' % (table, row, col), 1))

    def delete_row_by_index(self, table, row):
        tbls = self.tables()
        rows = tbls[table].findall(q('tr'))
        self.delete_row(rows[row])
        self.log.append(('DR_IDX', '%d,%d' % (table, row), 1))

    def insert_table_after(self, anchor_para_text, template_tbl_anchor, rows):
        """以模板表样式在锚点段落后插入新表（全表标记为插入）。rows=[[c1,c2],...]"""
        p = self._locate(anchor_para_text, op='IT')
        tpl = self.find_table(template_tbl_anchor)
        newt = etree.Element(q('tbl'))
        tblPr = tpl.find(q('tblPr'))
        if tblPr is not None:
            newt.append(copy.deepcopy(tblPr))
        grid = tpl.find(q('tblGrid'))
        if grid is not None:
            newt.append(copy.deepcopy(grid))
        tpl_rows = tpl.findall(q('tr'))
        for ri, row in enumerate(rows):
            src_tr = tpl_rows[0] if ri == 0 else tpl_rows[min(1, len(tpl_rows) - 1)]
            ntr = copy.deepcopy(src_tr)
            trPr = ensure_trPr(ntr)
            trPr.append(etree.Element(q('ins'), self._attrs()))
            cells = ntr.findall(q('tc'))
            for ci, tc in enumerate(cells):
                ps = tc.findall(q('p'))
                # 清空多余段落，仅留第一个
                first_p = ps[0]
                for extra in ps[1:]:
                    tc.remove(extra)
                # 拿原 run 样式
                segs = live_segments(first_p)
                rpr_src = segs[0][2].find(q('rPr')) if segs else None
                # 删除全部 run
                for r in list(first_p.iter(q('r'))):
                    r.getparent().remove(r)
                pPr = first_p.find(q('pPr'))
                if pPr is not None:
                    rPr = pPr.find(q('rPr'))
                    if rPr is None:
                        rPr = etree.SubElement(pPr, q('rPr'))
                    for d in rPr.findall(q('del')):
                        rPr.remove(d)
                    rPr.insert(0, etree.Element(q('ins'), self._attrs()))
                txt = row[ci] if ci < len(row) else ''
                ins_run = self._make_run(rpr_src, txt)
                i = etree.Element(q('ins'), self._attrs())
                i.append(ins_run)
                first_p.append(i)
            newt.append(ntr)
        p.addnext(newt)
        self.log.append(('IT', anchor_para_text[:30], len(rows)))
        return newt

    def save(self, path=None):
        self.tree.write(path or self.path, xml_declaration=True,
                        encoding='UTF-8', standalone=True)

def rpr_for(run):
    return run.find(q('rPr'))

def ensure_trPr(tr):
    """取得/创建 trPr，保证位于 tblPrEx 之后（OOXML 元素顺序）。"""
    trPr = tr.find(q('trPr'))
    if trPr is None:
        trPr = etree.Element(q('trPr'))
        idx = 0
        for i, c in enumerate(tr):
            if c.tag == q('tblPrEx'):
                idx = i + 1
        tr.insert(idx, trPr)
    return trPr
