# engine/normalize.py — 清单文本标准化闸（cc-docx 出口与 legal-docx 出口对齐）
#
# 只作用于「写入文档的新文本」（comment.text / redline 的 with、text、set），
# 绝不作用于锚点类字段（find / anchor / scope / forbid）——那些必须逐字匹配原文。
#
# 步骤（复用 engine/text_normalizer.py，该文件逐字节复制自
# ~/claude-skills/personal/legal-docx/scripts/text_normalizer.py）：
#   1. 省略号 ... → ……
#   2. ASCII 直引号 "" '' → 中文弯引号 “” ‘’（奇偶配对）
#   3. CJK 上下文中半角 , : ; ! ? . ( ) → 全角
#   4. 数字与汉字之间的半角空格删除（第 4.2 款 → 第4.2款；24 小时 → 24小时）
# 不做 legal-docx 的列举编号统一与 [X]→【X】 占位符转换（批注常用 [🟡 中] 等标签）。
#   5. 补两条 legal-docx 原版未覆盖的中文排版收尾（本文件自有，不改 text_normalizer.py）：
#      弯引号 / 全角括号书名号紧邻的半角 , : ; ! ? 转全角；全角标点前后残留的半角空格删除。
import re

from engine.text_normalizer import (
    _collapse_num_cjk_space,
    _normalize_ellipsis,
    _normalize_punctuation_chars,
    _normalize_quotes,
)

_H2F = {",": "，", ":": "：", ";": "；", "!": "！", "?": "？"}
_PUNCT_AFTER_CLOSE = re.compile(r'(?<=[”’）》」』])([,:;!?])')
_PUNCT_BEFORE_OPEN = re.compile(r'([,:;!?])(?=[“‘（《「『])')
_SPACE_AFTER_FW = re.compile(r'(?<=[，。；：！？、）》”’」』])[ ]+')
_SPACE_BEFORE_FW = re.compile(r'[ ]+(?=[，。；：！？、（《“‘「『])')


def _cjk_punct_tail(line):
    line = _PUNCT_AFTER_CLOSE.sub(lambda m: _H2F[m.group(1)], line)
    line = _PUNCT_BEFORE_OPEN.sub(lambda m: _H2F[m.group(1)], line)
    line = _SPACE_AFTER_FW.sub("", line)
    line = _SPACE_BEFORE_FW.sub("", line)
    return line


def normalize_field(text):
    """对一段将写入文档的新文本做标准化；非字符串原样返回。"""
    if not isinstance(text, str) or not text:
        return text
    lines = text.split("\n")
    out = []
    for line in lines:
        line = _normalize_ellipsis(line)
        line = _normalize_quotes(line)
        line = _normalize_punctuation_chars(line)
        line = _collapse_num_cjk_space(line)
        line = _cjk_punct_tail(line)
        out.append(line)
    return "\n".join(out)


def quote_warnings(text, where):
    """ASCII 双引号为奇数个时配对必错，返回提示（不阻断）。按行统计。"""
    warns = []
    if not isinstance(text, str):
        return warns
    for i, line in enumerate(text.split("\n"), 1):
        if line.count('"') % 2 == 1:
            warns.append("%s 第%d行 ASCII 双引号为奇数个，弯引号配对可能错位：%s"
                         % (where, i, line[:40]))
    return warns
