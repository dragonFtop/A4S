"""网页抓取与正文提取（Markdown 化）。

为什么用 trafilatura
--------------------
正文提取的难点是"去掉页面里的一切非正文"（导航、侧栏、广告、页脚）。
trafilatura 的启发式提取在中文文档站、博客、教程站上效果最好，且
2.x 直接支持 output_format='markdown'，省掉 HTML→MD 的二次转换。

边界约定
--------
- 只处理 http/https 的 HTML 纯网页；PDF 等让用户直接走文件上传。
- 抓取上限 5 MB、超时 20s：教程页没有比这更大的合理正文。
- 提取失败或正文过短不硬撑——大概率是 JS 渲染页或需要登录，
  返回明确错误让用户改走"粘贴文本"路径。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

MAX_BYTES = 5 * 1024 * 1024
TIMEOUT = 20
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_HTML_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
_TITLE_TAG = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


class WebFetchError(ValueError):
    """可展示给用户的抓取错误（文案直接进 UI）。"""


@dataclass(slots=True)
class PageContent:
    url: str  # 重定向后的最终 URL
    title: str
    markdown: str
    content_type: str


def fetch_page(url: str) -> PageContent:
    """抓取网页并提取正文为 Markdown。失败抛 WebFetchError。"""
    headers = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    try:
        response = httpx.get(
            url, headers=headers, timeout=TIMEOUT, follow_redirects=True
        )
    except httpx.TimeoutException as exc:
        raise WebFetchError("网页响应超时，稍后重试或改用「粘贴文本」导入") from exc
    except httpx.ProxyError as exc:
        raise WebFetchError("连接失败（可能与本机代理有关），请检查网络或代理设置") from exc
    except httpx.ConnectError as exc:
        raise WebFetchError("无法连接到该网址，请检查地址是否可访问") from exc
    except httpx.HTTPError as exc:
        raise WebFetchError(f"网页请求失败：{type(exc).__name__}") from exc

    if response.status_code != 200:
        raise WebFetchError(f"网页返回了 {response.status_code}，无法抓取")

    content_type = response.headers.get("content-type", "").lower()
    if "pdf" in content_type or url.lower().rstrip("/").endswith(".pdf"):
        raise WebFetchError("这是一个 PDF 文件，请改用「上传文件」导入")
    if not any(kind in content_type for kind in _HTML_TYPES):
        raise WebFetchError(
            f"链接内容类型是 {content_type.split(';')[0] or '未知'}，暂不支持抓取"
        )

    raw = response.content
    if len(raw) > MAX_BYTES:
        raise WebFetchError("网页超过 5 MB 上限，请改用「粘贴文本」导入需要的部分")

    html = response.text
    markdown = _extract_markdown(html, str(response.url))
    if not markdown:
        raise WebFetchError(
            "未能从页面提取到正文：可能是动态渲染页或需要登录，请改用「粘贴文本」导入"
        )
    return PageContent(
        url=str(response.url),
        title=_pick_title(html, markdown),
        markdown=markdown,
        content_type=content_type.split(";")[0],
    )


def _extract_markdown(html: str, url: str) -> str:
    import trafilatura

    try:
        markdown = trafilatura.extract(
            html,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_links=True,
            include_formatting=True,
            favor_recall=True,
        )
    except Exception as exc:  # noqa: BLE001 - 提取器的任何异常都不该崩掉接口
        logger.warning("正文提取失败 %s：%s", url, exc)
        return ""
    return (markdown or "").strip()


def _pick_title(html: str, markdown: str) -> str:
    """标题优先取正文首个一级标题（更贴近内容），退回 <title> 标签。"""
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and len(stripped) > 2:
            return stripped[2:].strip()[:200]
    match = _TITLE_TAG.search(html)
    if match:
        raw = re.sub(r"\s+", " ", match.group(1)).strip()
        if raw:
            return raw[:200]
    return ""
