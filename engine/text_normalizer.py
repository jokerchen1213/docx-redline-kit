"""
text_normalizer.py
------------------
法律文书 Markdown→DOCX 转换工具 — 文本标准化模块

职责：
1. 标点标准化：中文语境中半角标点→全角（逗号、冒号、分号等）
2. 引号标准化：英文弯引号/ASCII直引号→中文引号（""''）
3. 圆括号标准化：中文语境中半角()→全角（），保留数字/编号内的半角
4. 句末句号标准化：中文句末英文句号.→中文句号。，不动小数点和编号点
5. 省略号标准化：...→……
6. 编号前缀剥离：识别并剥离 Markdown 中的文本编号前缀

公开 API：
  normalize_text(text)              → str
  strip_number_prefix(text, key)    → tuple[str, str | None]
  normalize_lines(lines)            → list[str]
"""

import re
from typing import Optional

# ---------------------------------------------------------------------------
# CJK 字符集判断
# ---------------------------------------------------------------------------

# CJK 统一汉字主区、扩展A区、兼容汉字、部首补充、CJK符号和标点
_CJK_RANGES = [
    (0x4E00, 0x9FFF),   # CJK Unified Ideographs
    (0x3400, 0x4DBF),   # CJK Extension A
    (0xF900, 0xFAFF),   # CJK Compatibility Ideographs
    (0x2E80, 0x2EFF),   # CJK Radicals Supplement
    (0x3000, 0x303F),   # CJK Symbols and Punctuation
]


def _is_cjk(ch: str) -> bool:
    """判断单个字符是否属于 CJK 范围。"""
    if not ch:
        return False
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def _is_digit_or_ascii_alnum(ch: str) -> bool:
    """判断字符是否为 ASCII 字母、数字或常见单位字符。"""
    if not ch:
        return False
    return ch.isascii() and (ch.isalnum() or ch in '._%+-/')


# ---------------------------------------------------------------------------
# 1. 省略号标准化：... → ……
# ---------------------------------------------------------------------------

def _normalize_ellipsis(text: str) -> str:
    """将三个及以上连续英文点号转为中文省略号……（两个，共6点）。"""
    return re.sub(r'\.{3,}', '……', text)


# ---------------------------------------------------------------------------
# 2. 引号标准化：英文引号 → 中文引号
# ---------------------------------------------------------------------------

def _normalize_quotes(text: str) -> str:
    """
    将英文弯引号和 ASCII 直引号转换为中文引号。

    策略：
    - 先处理弯引号（\u201c\u201d \u2018\u2019）→ 直接替换为对应中文引号
    - ASCII 直引号（"  '）使用奇偶配对逻辑确定开/闭
    """
    # 英文弯双引号
    text = text.replace('\u201c', '\u201c')  # " → "（保持不变，本已是全角）
    text = text.replace('\u201d', '\u201d')  # " → "（同上）

    # 实际上 \u201c/\u201d 就是 ""，下面统一处理 ASCII 直引号
    # ASCII 双引号 " (0x22) → "" 配对
    result = []
    double_open = True   # 下一个 " 作为开引号
    single_open = True   # 下一个 ' 作为开引号
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == '"':
            if double_open:
                result.append('\u201c')  # "
            else:
                result.append('\u201d')  # "
            double_open = not double_open
        elif ch == "'":
            if single_open:
                result.append('\u2018')  # '
            else:
                result.append('\u2019')  # '
            single_open = not single_open
        else:
            result.append(ch)
        i += 1
    return ''.join(result)


# ---------------------------------------------------------------------------
# 3. 逐字符标点转换（CJK 上下文判断）
# ---------------------------------------------------------------------------

# 需要在 CJK 上下文中转为全角的半角标点映射
_PUNCT_HALF_TO_FULL = {
    ',': '，',
    ':': '：',
    ';': '；',
    '!': '！',
    '?': '？',
}

# 句末句号映射（仅用于句末判断）
_PERIOD_FULL = '。'


def _char_before_after(text: str, idx: int):
    """返回 idx 位置前一个和后一个非空白字符（不含自身）。"""
    before = ''
    for j in range(idx - 1, -1, -1):
        if not text[j].isspace():
            before = text[j]
            break
    after = ''
    for j in range(idx + 1, len(text)):
        if not text[j].isspace():
            after = text[j]
            break
    return before, after


def _normalize_punctuation_chars(text: str) -> str:
    """
    逐字符扫描，对 CJK 上下文中的半角标点执行全角替换。

    规则：
    - 逗号/冒号/分号/叹号/问号：前后至少一侧为 CJK 字符时转全角
    - 英文句号(.)：
        * 前后均为数字/字母时保留（小数点、编号点）
        * 前为 CJK 且后面不是数字/字母时（即句末）转。
    - 圆括号()：
        * 括号内容为纯数字/字母（如编号(1)(a)）且整行无 CJK 时保留半角（纯技术/代码场景）
        * 整行含 CJK 字符（中文语境）时，一律转全角（），
          避免连续列举 (1)(2)(3) 因邻字为 ')' 或 '(' 被漏改。
    """
    line_has_cjk = any(_is_cjk(c) for c in text)
    chars = list(text)
    n = len(chars)
    result = []
    i = 0

    while i < n:
        ch = chars[i]

        # --- 省略号已在上游处理，此处跳过 ---

        # --- 逗号、冒号、分号、叹号、问号 ---
        if ch in _PUNCT_HALF_TO_FULL:
            before, after = _char_before_after(text, i)
            if _is_cjk(before) or _is_cjk(after):
                result.append(_PUNCT_HALF_TO_FULL[ch])
            else:
                result.append(ch)

        # --- 英文句号 ---
        elif ch == '.':
            before, after = _char_before_after(text, i)
            # 小数点：前后均为数字
            if before.isdigit() and after.isdigit():
                result.append(ch)
            # 编号点：前为数字，后为数字或空（如"1."列表）
            elif before.isdigit() and (after.isdigit() or after == ''):
                result.append(ch)
            # 句末句号：前为 CJK，后不是数字/字母
            elif _is_cjk(before) and not _is_digit_or_ascii_alnum(after):
                result.append(_PERIOD_FULL)
            else:
                result.append(ch)

        # --- 圆括号 ( ) ---
        elif ch == '(':
            if line_has_cjk:
                result.append('（')
            else:
                end = text.find(')', i + 1)
                if end != -1:
                    inner = text[i + 1:end]
                    before, _ = _char_before_after(text, i)
                    _, after_paren = _char_before_after(text, end)
                    is_enum = bool(re.fullmatch(r'\d+|[a-z]', inner))
                    if is_enum and not _is_cjk(before):
                        result.append(ch)
                    elif _is_cjk(before) or _is_cjk(after_paren):
                        result.append('（')
                    else:
                        result.append(ch)
                else:
                    before, _ = _char_before_after(text, i)
                    if _is_cjk(before):
                        result.append('（')
                    else:
                        result.append(ch)

        elif ch == ')':
            if line_has_cjk:
                result.append('）')
            else:
                start = text.rfind('(', 0, i)
                if start != -1:
                    inner = text[start + 1:i]
                    before_paren, _ = _char_before_after(text, start)
                    _, after = _char_before_after(text, i)
                    is_enum = bool(re.fullmatch(r'\d+|[a-z]', inner))
                    if is_enum and not _is_cjk(before_paren):
                        result.append(ch)
                    elif _is_cjk(before_paren) or _is_cjk(after):
                        result.append('）')
                    else:
                        result.append(ch)
                else:
                    _, after = _char_before_after(text, i)
                    if _is_cjk(after):
                        result.append('）')
                    else:
                        result.append(ch)

        else:
            result.append(ch)

        i += 1

    return ''.join(result)


# ---------------------------------------------------------------------------
# 数字与中文汉字之间的半角空格折叠
# ---------------------------------------------------------------------------

# 数字 ↔ CJK 统一汉字（U+4E00-9FFF）之间的半角空格（双向）
_NUM_BEFORE_CJK_RE = re.compile(r'(?<=[0-9])[ ]+(?=[一-鿿])')
_CJK_BEFORE_NUM_RE = re.compile(r'(?<=[一-鿿])[ ]+(?=[0-9])')

# 行首十进制编号前缀（如 "1.1 "、"1.1.1 "，可带 ATX 标题标记 "## "）
# 及其后的分隔空格。该空格须保留——否则 md_parser 的 decimal2/decimal3
# 前缀正则（要求 \s+）无法识别编号标题。
_LEADING_DECIMAL_PREFIX_RE = re.compile(
    r'^(\s*(?:#{1,6}[ \t]+)?\d+(?:\.\d+)+[ \t]+)(.*)$')


def _collapse_num_cjk_space(text: str) -> str:
    """删除 ASCII 数字与 CJK 统一汉字之间的半角空格（双向）。

    仅针对「数字↔汉字」，不动数字↔字母、数字↔数字、数字↔CJK标点；
    仅删半角空格，不动 tab 与全角空格。法律文书惯例为数字与中文紧贴无空格。

    例外：行首十进制编号前缀（如 "1.1 定义"）后的分隔空格予以保留——
    该空格是 md_parser 识别 decimal2/decimal3 编号标题所必需。
    """
    m = _LEADING_DECIMAL_PREFIX_RE.match(text)
    if m:
        return m.group(1) + _collapse_num_cjk_space(m.group(2))
    text = _NUM_BEFORE_CJK_RE.sub('', text)
    text = _CJK_BEFORE_NUM_RE.sub('', text)
    return text


# ---------------------------------------------------------------------------
# 列举编号统一（contract preset 专用）
# ---------------------------------------------------------------------------

# 中文小写数字 → 阿拉伯数字映射（仅覆盖一~十，超出范围保留中文）
_CN_NUM_TO_ARABIC = {
    '一': '1', '二': '2', '三': '3', '四': '4', '五': '5',
    '六': '6', '七': '7', '八': '8', '九': '9', '十': '10',
}

# 行首中文数字括号（仅一~十）
_LEAD_WIDE_PAREN_CN_RE = re.compile(r'^（([一二三四五六七八九十])）')

# 行首全角括号编号（阿拉伯/字母）后的多余空格
_LEAD_WIDE_PAREN_TRAIL_SPACE_RE = re.compile(r'^(（[\dA-Za-z]+）)[ \t]+')


def _normalize_list_prefix(text: str, preset: str = 'general') -> str:
    """contract preset 下，统一列举子项编号风格：

    1. 行首中文数字括号 `（一）（二）...（十）` → `（1）（2）...（10）`
       — 避免被 md_parser.cn_paren 识别为 ilvl=1 顶级，污染 X.X 编号链
    2. 行首全角括号编号 `（1） / （a）` 后的空格删除
       — 与中文排版习惯一致

    general / premium preset 下不做转换（cn_paren 在 general 是合法二级标题）。
    """
    if preset != 'contract':
        return text

    # 1. 中文数字 → 阿拉伯
    m = _LEAD_WIDE_PAREN_CN_RE.match(text)
    if m:
        arabic = _CN_NUM_TO_ARABIC[m.group(1)]
        text = '（' + arabic + '）' + text[m.end():]

    # 2. 删除括号后空格
    text = _LEAD_WIDE_PAREN_TRAIL_SPACE_RE.sub(r'\1', text)

    return text


# ---------------------------------------------------------------------------
# 方括号占位符全角化
# ---------------------------------------------------------------------------

# 匹配 [X]，但跳过 markdown 链接 [text](url) / [text]（url）
# 用 [^\[\]] 保证不跨嵌套；后置否定前瞻同时排除半/全角左括号
# （上游 _normalize_punctuation_chars 可能已把 ( 转为全角（，需同时检测）
_PLACEHOLDER_BRACKET_RE = re.compile(r'\[([^\[\]]*)\](?![(（])')


def _normalize_placeholder_brackets(text: str) -> str:
    """半角方括号占位符 [X] → 全角【X】

    - 律师文书中 [____]、[7]日、[肆]份等均为占位符，统一改为更醒目的【】
    - 跳过 markdown 链接 [text](url)，通过 ] 后紧跟 ( 检测
    - 跳过空 []（无内容时也应保留）
    """
    def _repl(m: re.Match) -> str:
        inner = m.group(1)
        if not inner:
            return m.group(0)
        return '【' + inner + '】'
    return _PLACEHOLDER_BRACKET_RE.sub(_repl, text)


# ---------------------------------------------------------------------------
# 主入口：normalize_text
# ---------------------------------------------------------------------------

def normalize_text(
    text: str,
    collapse_num_cjk_space: bool = True,
    *,
    preset: str = 'general',
) -> str:
    """
    对单行文本执行全部标点标准化。

    执行顺序：
    1. 省略号（... → ……）
    2. 引号（英文 → 中文）
    3. 逐字符标点转换（CJK 上下文中半角→全角，含句末句号、圆括号）
    4. 数字与中文汉字间半角空格折叠（collapse_num_cjk_space=True 时）
    5. 列举编号统一（仅 contract preset：（一）→（1）；删括号后空格）
    6. 方括号占位符全角化（[X] → 【X】，跳过 markdown 链接）

    collapse_num_cjk_space：fenced code block 内的行应传 False，
    以保护 ASCII 图/代码中「数字 空格 中文」图案的对齐。

    preset：影响第 5 步行为；general/premium 不做列举编号转换。
    """
    if not text:
        return text
    text = _normalize_ellipsis(text)
    text = _normalize_quotes(text)
    text = _normalize_punctuation_chars(text)
    if collapse_num_cjk_space:
        text = _collapse_num_cjk_space(text)
    text = _normalize_list_prefix(text, preset=preset)
    text = _normalize_placeholder_brackets(text)
    return text


# ---------------------------------------------------------------------------
# 编号前缀剥离
# ---------------------------------------------------------------------------

# 编号前缀正则模式表
NUMBER_PREFIX_PATTERNS: dict[str, re.Pattern] = {
    'tiao':      re.compile(r'^第[一二三四五六七八九十百千]+条\s*'),
    'decimal3':  re.compile(r'^\d+\.\d+\.\d+\s+'),
    'decimal2':  re.compile(r'^\d+\.\d+\s+'),
    'cn_paren':  re.compile(r'^（[一二三四五六七八九十百千]+）\s*'),
    'cn_count':  re.compile(r'^[一二三四五六七八九十百千]+、\s*'),
    'paren_d':   re.compile(r'^\(\d+\)\s*'),
    'paren_l':   re.compile(r'^\([a-z]\)\s*'),
}

# 匹配优先顺序（更具体的在前）
_PATTERN_ORDER = ['tiao', 'decimal3', 'decimal2', 'cn_paren', 'cn_count', 'paren_d', 'paren_l']


def strip_number_prefix(
    text: str,
    pattern_key: Optional[str] = None
) -> tuple[str, Optional[str]]:
    """
    剥离编号前缀，返回 (剥离后文本, 匹配的模式key)。

    参数：
        text        — 原始文本（单行）
        pattern_key — 指定模式key（如 'tiao'）；None 时自动按优先顺序匹配

    返回：
        (stripped_text, matched_key)
        若未匹配任何前缀，返回 (text, None)
    """
    if pattern_key is not None:
        pattern = NUMBER_PREFIX_PATTERNS.get(pattern_key)
        if pattern is None:
            raise ValueError(f"未知的 pattern_key: {pattern_key!r}")
        m = pattern.match(text)
        if m:
            return text[m.end():], pattern_key
        return text, None

    # 自动匹配
    for key in _PATTERN_ORDER:
        m = NUMBER_PREFIX_PATTERNS[key].match(text)
        if m:
            return text[m.end():], key
    return text, None


# ---------------------------------------------------------------------------
# 多行标准化
# ---------------------------------------------------------------------------

def normalize_lines(lines: list[str], *, preset: str = 'general') -> list[str]:
    """
    对多行文本列表执行标准化。

    规则：
    - 空行（strip 后为空）直接保留，不处理
    - 非空行调用 normalize_text

    preset 透传给 normalize_text，影响列举编号统一行为。
    """
    result = []
    for line in lines:
        if line.strip() == '':
            result.append(line)
        else:
            result.append(normalize_text(line, preset=preset))
    return result


# ---------------------------------------------------------------------------
# 自测代码块
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    import sys

    PASS = '\033[32mPASS\033[0m'
    FAIL = '\033[31mFAIL\033[0m'
    errors = 0

    def check(label: str, got, expected):
        global errors
        if got == expected:
            print(f'  {PASS}  {label}')
        else:
            print(f'  {FAIL}  {label}')
            print(f'         got:      {got!r}')
            print(f'         expected: {expected!r}')
            errors += 1

    print('=== 1. 省略号标准化 ===')
    check('三点省略号',    normalize_text('如此...'),        '如此……')
    check('六点省略号',    normalize_text('如此......'),     '如此……')
    check('保留小数点',    normalize_text('3.14'),           '3.14')

    print('\n=== 2. 引号标准化 ===')
    check('ASCII双引号',  normalize_text('"合同"'),         '\u201c合同\u201d')
    check('ASCII单引号',  normalize_text("'条款'"),          '\u2018条款\u2019')
    check('混合引号',     normalize_text('"甲方\'s"'),       '\u201c甲方\u2018s\u201d')

    print('\n=== 3. 逗号/冒号/分号 ===')
    check('中文逗号',     normalize_text('甲方,乙方'),       '甲方，乙方')
    check('中文冒号',     normalize_text('如下:说明'),       '如下：说明')
    check('中文分号',     normalize_text('甲方;乙方'),       '甲方；乙方')
    check('保留CSV逗号',  normalize_text('a,b,c'),           'a,b,c')

    print('\n=== 4. 句末句号 ===')
    check('句末点→句号',  normalize_text('本合同终止.'),     '本合同终止。')
    check('保留小数点',   normalize_text('利率为3.5%'),      '利率为3.5%')
    check('保留编号点',   normalize_text('1.1 定义'),        '1.1 定义')

    print('\n=== 5. 圆括号 ===')
    check('中文括号',     normalize_text('甲方(以下简称甲)'), '甲方（以下简称甲）')
    check('中文语境编号括号→全角', normalize_text('(1)付款'), '（1）付款')
    check('中文语境字母编号→全角', normalize_text('(a)条件'), '（a）条件')
    check('连续编号全部转全角', normalize_text('第(5)(6)(7)项'), '第（5）（6）（7）项')
    check('纯ASCII行保留半角', normalize_text('func(x) = y'),    'func(x) = y')

    print('\n=== 5b. 列举编号统一（仅 contract preset）===')
    # contract preset：中文数字 → 阿拉伯
    check('（一）→（1） [contract]',
          normalize_text('（一）甲方违约', preset='contract'),
          '（1）甲方违约')
    check('（十）→（10） [contract]',
          normalize_text('（十）杂项', preset='contract'),
          '（10）杂项')
    # contract preset：删除括号后空格
    check('（1） 空格→（1） [contract]',
          normalize_text('(1) 甲方违约', preset='contract'),
          '（1）甲方违约')
    check('（a） 空格→（a） [contract]',
          normalize_text('(a) 子项', preset='contract'),
          '（a）子项')
    # general preset：cn_paren 是合法二级标题，不动
    check('（一）保留 [general]',
          normalize_text('（一）适用范围', preset='general'),
          '（一）适用范围')
    # contract preset：行中（非行首）的（一）不动
    check('行中（一）不动 [contract]',
          normalize_text('按第（一）项处理', preset='contract'),
          '按第（一）项处理')

    print('\n=== 5c. 方括号占位符全角化 ===')
    check('[____]→【____】',     normalize_text('金额[____]元'),       '金额【____】元')
    check('[7]→【7】',            normalize_text('[7]日内支付'),         '【7】日内支付')
    check('[肆]→【肆】',          normalize_text('一式[肆]份'),          '一式【肆】份')
    check('多占位符',             normalize_text('[A]和[B]'),            '【A】和【B】')
    # 注：URL 中的半角括号会被上游 _normalize_punctuation_chars 转为全角，
    # 这是既有行为；本测试只关心 [官网] 部分不被误转为【官网】
    check('markdown链接保留方括号',
          normalize_text('见[官网](https://x)'),
          '见[官网]（https://x）')
    check('空[]保留',             normalize_text('空括号[]'),            '空括号[]')

    print('\n=== 6. 编号前缀剥离 ===')
    s, k = strip_number_prefix('第一条 本合同自签署之日起生效。')
    check('tiao',        (s, k), ('本合同自签署之日起生效。', 'tiao'))

    s, k = strip_number_prefix('1.1 定义')
    check('decimal2',    (s, k), ('定义', 'decimal2'))

    s, k = strip_number_prefix('1.1.1 术语')
    check('decimal3',    (s, k), ('术语', 'decimal3'))

    s, k = strip_number_prefix('（一）适用范围')
    check('cn_paren',    (s, k), ('适用范围', 'cn_paren'))

    s, k = strip_number_prefix('一、总则')
    check('cn_count',    (s, k), ('总则', 'cn_count'))

    s, k = strip_number_prefix('(1)付款条件')
    check('paren_d',     (s, k), ('付款条件', 'paren_d'))

    s, k = strip_number_prefix('(a)选项')
    check('paren_l',     (s, k), ('选项', 'paren_l'))

    s, k = strip_number_prefix('无编号文本')
    check('无匹配',       (s, k), ('无编号文本', None))

    s, k = strip_number_prefix('(1)付款条件', pattern_key='paren_d')
    check('指定key命中',  (s, k), ('付款条件', 'paren_d'))

    s, k = strip_number_prefix('一、总则', pattern_key='tiao')
    check('指定key未命中', (s, k), ('一、总则', None))

    print('\n=== 7. 多行标准化 ===')
    lines = ['', '甲方,乙方', '  ', '利率3.5%']
    result = normalize_lines(lines)
    check('空行保留',     result[0], '')
    check('非空行转换',   result[1], '甲方，乙方')
    check('空白行保留',   result[2], '  ')
    check('小数点不变',   result[3], '利率3.5%')

    print('\n=== 8. 数字-中文空格折叠 ===')
    check('数字前后空格',     normalize_text('赔偿 500 元'),     '赔偿500元')
    check('双向折叠',         normalize_text('2024 年 1 月'),    '2024年1月')
    check('汉字→数字',        normalize_text('共 3 名'),         '共3名')
    check('英文↔数字不动',    normalize_text('clause 5 of'),     'clause 5 of')
    check('小数版本号',       normalize_text('版本 2.0 发布'),   '版本2.0发布')
    check('关闭时透传',
          normalize_text('赔偿 500 元', collapse_num_cjk_space=False),
          '赔偿 500 元')
    check('汉字↔汉字空格保留', normalize_text('劳 动 争 议'),     '劳 动 争 议')
    check('保留十进制编号前缀空格', normalize_text('1.1 定义'),   '1.1 定义')
    check('保留三级编号前缀空格', normalize_text('1.1.1 术语说明'), '1.1.1 术语说明')
    check('保留ATX标题的编号前缀空格', normalize_text('## 1.1 事实认定'),
          '## 1.1 事实认定')
    check('编号前缀后正文仍折叠', normalize_text('2.3 共 5 项'),  '2.3 共5项')

    print()
    if errors == 0:
        print(f'所有测试通过 ✓')
    else:
        print(f'{errors} 个测试失败')
        sys.exit(1)
