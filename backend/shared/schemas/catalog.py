"""公共目录（catalog）的响应模型（FR-2.1 ~ FR-2.4）。

catalog 不落表，由「已生效的共享授权」实时派生，因此这里的字段都是投影，
不承担存储职责。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from shared.schemas.common import UtcDateTime


class CatalogEntryOut(BaseModel):
    space_id: str
    space_name: str
    owner_id: str
    owner_name: str
    # 代表主题：ready 资料最多的那个主题名（无资料时退到空间首个主题）
    topic: str = ""
    # 该空间 ready 资料数（与 /documents/stats 的 ready 口径一致）
    document_count: int = 0
    updated_at: UtcDateTime
    # 空间内向量版本：以 ready 资料的更新时间派生，供请求方判断是否需要重拉
    vector_version: str = ""
    # unknown（P2 占位）/ online / offline；联邦节点注册落地后填真实值
    online: str = "unknown"
    # 配额余量（P2 占位常量；P3 接入审计后按节点日配额递减）
    quota_remaining: int = 0


class CatalogListOut(BaseModel):
    items: list[CatalogEntryOut] = Field(default_factory=list)
    total: int = 0
