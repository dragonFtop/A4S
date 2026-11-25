"""文档解析端口 + 解析结果结构。

M1 只定义接口并提供一个 dummy 实现用于验证注册表；
PDF/MD/TXT/DOCX 的真实解析在 M2 实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class ParsedDocument:
    text: str
    pages: list[tuple[int, str]] = field(default_factory=list)
    meta: dict = field(default_factory=dict)


class DocumentParser(ABC):
    name: str = "base"
    extensions: tuple[str, ...] = ()

    def supports(self, ext: str) -> bool:
        return ext.lower().lstrip(".") in self.extensions

    @abstractmethod
    def parse(self, path: Path) -> ParsedDocument:
        """把文件解析为纯文本。"""
