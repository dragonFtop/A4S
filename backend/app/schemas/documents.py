"""资料导入与管理的请求 / 响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import DocumentOut


class PasteIn(BaseModel):
    content: str = Field(min_length=1)
    title: str = ""
    topic_id: str = "uncategorized"
    tag_ids: list[str] = Field(default_factory=list)


class BatchIn(BaseModel):
    document_ids: list[str] = Field(min_length=1)
    topic_id: str | None = None
    add_tag_ids: list[str] = Field(default_factory=list)
    remove_tag_ids: list[str] = Field(default_factory=list)


class UploadError(BaseModel):
    name: str
    reason: str


class UploadOut(BaseModel):
    """多文件上传逐份返回结果：成功进 documents，失败进 errors，不整批回滚。"""

    documents: list[DocumentOut] = Field(default_factory=list)
    errors: list[UploadError] = Field(default_factory=list)


class ChunkOut(BaseModel):
    id: str
    index: int
    text: str
    page: int | None = None


class DocumentStatsOut(BaseModel):
    total: int
    ready: int
    processing: int
    failed: int
    chunks: int


class WebFetchIn(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class WebFetchOut(BaseModel):
    """抓取预览。markdown 供用户编辑确认后再走 /paste 入库。"""

    url: str
    title: str
    markdown: str
    chars: int
