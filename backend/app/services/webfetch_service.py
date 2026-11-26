"""网页导入服务：抓取 → 预览 → 用户确认后走 paste 入库。

两步走的原因：正文提取不完美，必须让用户在预览里看到结果、
可编辑取舍后再决定是否入库——"确认"这一步不能省。
入库复用 ingest_service.create_from_text，因此解析、切分、向量化
与普通资料完全同一条路径，不产生特殊分支。
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.webfetch import PageContent, WebFetchError, fetch_page

MAX_URL_LEN = 2048

# 允许的正文长度下限：低于它大概率是提取失败而非真的短文章
MIN_MARKDOWN_CHARS = 200


class WebImportError(ValueError):
    """可展示给用户的导入错误。"""


def fetch_for_preview(url: str) -> dict:
    """抓取并返回预览载荷。错误一律转成带人话文案的 WebImportError。"""
    clean = _validate(url)
    try:
        page = fetch_page(clean)
    except WebFetchError as exc:
        raise WebImportError(str(exc)) from exc

    if len(page.markdown) < MIN_MARKDOWN_CHARS:
        raise WebImportError(
            "提取到的正文太短（可能页面主要靠脚本渲染），请改用「粘贴文本」导入"
        )
    return {
        "url": page.url,
        "title": page.title,
        "markdown": page.markdown,
        "chars": len(page.markdown),
    }


def _validate(url: str) -> str:
    clean = (url or "").strip()
    if not clean:
        raise WebImportError("请填写网页地址")
    if len(clean) > MAX_URL_LEN:
        raise WebImportError("网址过长")
    parsed = urlparse(clean)
    if parsed.scheme not in ("http", "https"):
        raise WebImportError("网址需以 http:// 或 https:// 开头")
    if not parsed.netloc:
        raise WebImportError("网址格式不正确")
    # netloc 出现 @ 说明带 userinfo，单机个人应用没必要放行这种形态
    if "@" in parsed.netloc:
        raise WebImportError("网址格式不正确（不应包含账号信息）")
    return clean
