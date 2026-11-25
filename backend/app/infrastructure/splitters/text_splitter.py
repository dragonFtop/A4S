"""中文友好的文本切分。

策略（PRD FR-02 规则 2）：
1. 语义单元：先按空行切段落；段落超长再按中文句读（。！？；）切；仍超长则硬切。
2. 贪心合并：累加到接近目标长度；不足下限时允许继续吸收（不超过上限）。
3. 相邻片段重叠一段尾部文本，避免答案恰好被切在边界上。

纯函数、无 IO，便于单测与后续做"切分策略可视化调参"。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# 中文句读 + 英文句末
_SENTENCE_END = re.compile(r"(?<=[。！？；!?;])")
_PARAGRAPH = re.compile(r"\n{2,}")


@dataclass(slots=True)
class SplitConfig:
    min_size: int = 500
    target_size: int = 800
    max_size: int = 1000
    overlap: int = 150


@dataclass(slots=True)
class ChunkSlice:
    text: str
    page: int | None = None
    order: int = 0
    meta: dict = field(default_factory=dict)


def _split_long(text: str, max_size: int) -> list[str]:
    """把超长段落切成不超过 max_size 的片段，优先在句末断开。"""
    pieces: list[str] = []
    for sentence in _SENTENCE_END.split(text):
        if not sentence:
            continue
        while len(sentence) > max_size:
            pieces.append(sentence[:max_size])
            sentence = sentence[max_size:]
        if sentence:
            pieces.append(sentence)

    # 相邻短句再合并，避免产生一堆碎句
    merged: list[str] = []
    buf = ""
    for piece in pieces:
        if buf and len(buf) + len(piece) > max_size:
            merged.append(buf)
            buf = piece
        else:
            buf += piece
    if buf:
        merged.append(buf)
    return merged


def _units(text: str, max_size: int) -> list[str]:
    units: list[str] = []
    for para in _PARAGRAPH.split(text):
        para = para.strip()
        if not para:
            continue
        if len(para) <= max_size:
            units.append(para)
        else:
            # 单换行也算段落边界，进一步细化
            for line in para.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if len(line) <= max_size:
                    units.append(line)
                else:
                    units.extend(_split_long(line, max_size))
    return units


def split_text(text: str, config: SplitConfig | None = None, page: int | None = None) -> list[ChunkSlice]:
    """把一段文本切成若干片段。page 会原样带到每个片段上。"""
    cfg = config or SplitConfig()
    # 单元上限要扣掉重叠空间：新片 = 上一片尾部重叠 + 本单元，
    # 若单元本身就顶到 max_size，加上重叠必然超限。
    unit_limit = max(200, cfg.max_size - cfg.overlap)
    units = _units(text, unit_limit)
    if not units:
        return []

    slices: list[ChunkSlice] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        cleaned = buf.strip()
        if cleaned:
            slices.append(ChunkSlice(text=cleaned, page=page))
        buf = ""

    for unit in units:
        if not buf:
            buf = unit
            continue
        joined_len = len(buf) + 1 + len(unit)
        if joined_len <= cfg.target_size:
            buf = f"{buf}\n{unit}"
        elif len(buf) < cfg.min_size and joined_len <= cfg.max_size:
            # 还太短，宁可超目标也要凑够下限
            buf = f"{buf}\n{unit}"
        else:
            previous = buf
            flush()
            # 重叠不能把新片顶出上限：按剩余空间收缩重叠长度
            budget = cfg.max_size - len(unit) - 1
            tail = previous[-min(cfg.overlap, budget) :] if cfg.overlap > 0 and budget > 0 else ""
            buf = f"{tail}\n{unit}" if tail else unit

    flush()

    for index, item in enumerate(slices):
        item.order = index
        item.meta["chars"] = len(item.text)
    return slices


def split_document(pages: list[tuple[int, str]], text: str, config: SplitConfig | None = None) -> list[ChunkSlice]:
    """有分页信息时逐页切（便于标注来源页码），否则整篇切。"""
    cfg = config or SplitConfig()
    if not pages:
        return split_text(text, cfg)

    result: list[ChunkSlice] = []
    for page_no, page_text in pages:
        result.extend(split_text(page_text, cfg, page=page_no))
    for index, item in enumerate(result):
        item.order = index
    return result
