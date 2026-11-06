# engine/match_norm.py — 锚点匹配用的归一化（只用于"找"，绝不写回文档）
#
# 目的：清单里的锚点常与原文在空白、引号、全半角标点上有出入（模型复制时改了引号、
# 原文有全角空格 / NBSP / 换行），逐字 in 判断会漏命中。归一化后再比，命中区间
# 经 index_map 映射回原文字符偏移，供句级高亮使用。
#
# 规则：
#   1. 去掉全部空白（str.isspace：半角/全角空格、NBSP、tab、换行等）及零宽字符
#   2. 引号统一：“ ” " → "；‘ ’ ' → '
#   3. 括号统一：（）→ ()；【】→ []
#   4. 全角标点 → 半角：：，；。！？．、 等（、 视同 ,）；全角 ASCII 区 FF01–FF5E 统一减 0xFEE0

_ZERO_WIDTH = {"​", "‌", "‍", "⁠", "﻿"}

_CHAR_MAP = {
    "“": '"', "”": '"', "„": '"', "‟": '"', "＂": '"',
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "＇": "'",
    "（": "(", "）": ")",
    "【": "[", "】": "]",
    "：": ":", "，": ",", "；": ";", "。": ".", "！": "!",
    "？": "?", "．": ".", "、": ",",
}


def _map_char(ch):
    if ch in _CHAR_MAP:
        return _CHAR_MAP[ch]
    o = ord(ch)
    if 0xFF01 <= o <= 0xFF5E:  # 全角 ASCII（含全角数字字母）
        return chr(o - 0xFEE0)
    return ch


def norm_for_match(s):
    """返回 (norm_str, index_map)：index_map[k] = 归一化串第 k 个字符在原串中的下标。"""
    out = []
    idx = []
    for i, ch in enumerate(s or ""):
        if ch.isspace() or ch in _ZERO_WIDTH:
            continue
        out.append(_map_char(ch))
        idx.append(i)
    return "".join(out), idx


def norm_simple(s):
    return norm_for_match(s)[0]


def span_to_orig(index_map, ns, ne):
    """归一化区间 [ns, ne) → 原串区间 [start, end)（ne > ns）。"""
    return index_map[ns], index_map[ne - 1] + 1
