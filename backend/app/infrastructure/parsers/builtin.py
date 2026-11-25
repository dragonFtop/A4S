"""内置文档解析器：PDF / DOCX / Markdown / 纯文本。

统一约定：
- 返回 ParsedDocument.text 为全文（用于切分与检索）；
- pages 保留分页信息（仅 PDF 有），供 chunk 标注来源页码；
- 解析失败一律抛出异常，由上层服务转成文档的 failed 状态与原因。
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.infrastructure.parsers.base import DocumentParser, ParsedDocument

logger = logging.getLogger(__name__)

# 中文 TXT 常见 GBK/GB18030，按顺序探测，最后兜底忽略错误
_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")


def read_text_with_fallback(path: Path) -> str:
    for enc in _ENCODINGS:
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    logger.warning("文本编码无法确定，按 utf-8 忽略错误读取：%s", path.name)
    return path.read_text(encoding="utf-8", errors="ignore")


class PdfParser(DocumentParser):
    name = "pdf"
    extensions = ("pdf",)

    def parse(self, path: Path) -> ParsedDocument:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:
                raise ValueError("PDF 已加密，无法解析") from exc

        pages: list[tuple[int, str]] = []
        for index, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as exc:  # 个别损坏页不应毁掉整份文档
                logger.warning("第 %s 页解析失败：%s", index, exc)
                text = ""
            if text.strip():
                pages.append((index, text))

        full = "\n\n".join(text for _, text in pages)
        if not full.strip():
            raise ValueError("未能从 PDF 中提取到文本（可能是扫描版，v0.1 不支持 OCR）")
        return ParsedDocument(text=full, pages=pages, meta={"parser": self.name, "page_count": len(reader.pages)})


class DocxParser(DocumentParser):
    name = "docx"
    extensions = ("docx",)

    def parse(self, path: Path) -> ParsedDocument:
        from docx import Document as DocxDocument

        doc = DocxDocument(str(path))
        blocks: list[str] = []
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                blocks.append(text)
        # 表格内容也是资料的一部分，按行拼接
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    blocks.append(" | ".join(cells))

        full = "\n".join(blocks)
        if not full.strip():
            raise ValueError("DOCX 中没有可提取的文本")
        return ParsedDocument(text=full, meta={"parser": self.name, "blocks": len(blocks)})


class MarkdownParser(DocumentParser):
    name = "markdown"
    extensions = ("md", "markdown")

    def parse(self, path: Path) -> ParsedDocument:
        text = read_text_with_fallback(path)
        if not text.strip():
            raise ValueError("文件内容为空")
        # Markdown 保留原样：标题层级本身对切分和出题有用
        return ParsedDocument(text=text, meta={"parser": self.name})


class TextParser(DocumentParser):
    name = "text"
    extensions = ("txt", "text")

    def parse(self, path: Path) -> ParsedDocument:
        text = read_text_with_fallback(path)
        if not text.strip():
            raise ValueError("文件内容为空")
        return ParsedDocument(text=text, meta={"parser": self.name})


# 所有可用解析器。顺序决定同扩展名时的优先级，新增格式在这里加一行即可。
PARSERS: tuple[DocumentParser, ...] = (
    PdfParser(),
    DocxParser(),
    MarkdownParser(),
    TextParser(),
)
