"""资料巡检员（M8 体检道）。

只读：查资料状态、片段长度、同主题重复与主题覆盖，产出带证据的 Finding。
所有判断都是确定性的——体检不做「模型觉得这份资料不好」，只报可核对的事实。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from shared.domain.models.agents import AgentRole, Finding, FindingKind
from shared.domain.models.entities import Document, DocumentStatus, Topic, TopicKind
from shared.repositories.chunks import SqlAlchemyChunkRepository
from shared.repositories.documents import SqlAlchemyDocumentRepository
from shared.repositories.topics import SqlAlchemyTopicRepository

# 正文少于这个字数基本问不出东西，提示补充而不是直接判失败
MIN_DOC_CHARS = 200
# 体检只看最近的这些资料，避免一次扫描把页面拖死
MAX_DOCS = 200
# 重复判定用的首段指纹长度
FINGERPRINT_CHARS = 120

_COPY_SUFFIX = re.compile(r"\s*[（(]\d+[)）]$")
_SEPARATORS = re.compile(r"[\s_\-—]+")


def _severity(check: str) -> str:
    if check in {"parse_failed", "empty_chunks"}:
        return "high"
    if check in {"too_short", "duplicate", "topic_empty"}:
        return "medium"
    return "low"


def _finding(check: str, claim: str, evidence: dict) -> Finding:
    return Finding(
        kind=FindingKind.DOCUMENT,
        claim=claim,
        evidence={"check": check, "severity": _severity(check), **evidence},
        producer=AgentRole.INSPECTOR.value,
    )


def _normalized_name(name: str) -> str:
    """去掉扩展名与「(1)」副本后缀，压掉空白与分隔符后比较。"""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = _COPY_SUFFIX.sub("", stem)
    return _SEPARATORS.sub("", stem).lower()


def _fingerprint(text: str) -> str:
    return re.sub(r"\s+", "", text or "")[:FINGERPRINT_CHARS]


@dataclass
class DocReport:
    """一份资料的巡检结果，协调员据此记一行步骤。"""

    document_id: str
    name: str
    topic_id: str
    ok: bool
    brief: str
    chunks: int = 0
    chars: int = 0
    fingerprint: str = ""
    findings: list[Finding] = field(default_factory=list)


class Inspector:
    def __init__(self, db, space_id: str) -> None:
        self.db = db
        self.space_id = space_id
        self.doc_repo = SqlAlchemyDocumentRepository(db)
        self.chunk_repo = SqlAlchemyChunkRepository(db)
        self.topic_repo = SqlAlchemyTopicRepository(db)

    def documents(self) -> list[Document]:
        return self.doc_repo.list(self.space_id, limit=MAX_DOCS)

    def topics(self) -> list[Topic]:
        return self.topic_repo.list(self.space_id, kind=TopicKind.TOPIC)

    def inspect_document(self, doc: Document, topic_names: dict[str, str]) -> DocReport:
        """逐份检查：0 片段、解析失败、正文过短、未分类。"""
        chunks = self.chunk_repo.list_by_document(doc.id)
        chars = sum(len(chunk.text or "") for chunk in chunks)
        report = DocReport(
            document_id=doc.id,
            name=doc.name,
            topic_id=doc.topic_id,
            ok=True,
            brief="",
            chunks=len(chunks),
            chars=chars,
            fingerprint=_fingerprint(chunks[0].text if chunks else ""),
        )

        if doc.status is DocumentStatus.FAILED:
            report.findings.append(
                _finding(
                    "parse_failed",
                    f"《{doc.name}》解析失败：{doc.error or '未给出原因'}",
                    {
                        "document_id": doc.id,
                        "document_ids": [doc.id],
                        "document": doc.name,
                        "error": doc.error or "",
                    },
                )
            )
        elif doc.status is DocumentStatus.READY and not chunks:
            report.findings.append(
                _finding(
                    "empty_chunks",
                    f"《{doc.name}》没有可检索片段，扫描件或纯图片 PDF 常见",
                    {
                        "document_id": doc.id,
                        "document_ids": [doc.id],
                        "document": doc.name,
                        "size": doc.size,
                    },
                )
            )
        elif doc.status is DocumentStatus.READY and chars < MIN_DOC_CHARS:
            report.findings.append(
                _finding(
                    "too_short",
                    f"《{doc.name}》正文只有 {chars} 字，可能不足以支撑问答",
                    {
                        "document_id": doc.id,
                        "document_ids": [doc.id],
                        "document": doc.name,
                        "chars": chars,
                        "threshold": MIN_DOC_CHARS,
                    },
                )
            )

        if doc.topic_id == "uncategorized":
            report.findings.append(
                _finding(
                    "uncategorized",
                    f"《{doc.name}》还在「未分类」主题里",
                    {
                        "document_id": doc.id,
                        "document_ids": [doc.id],
                        "document": doc.name,
                    },
                )
            )

        report.ok = not any(
            item.evidence["check"] in {"parse_failed", "empty_chunks"}
            for item in report.findings
        )
        if not report.ok:
            report.brief = report.findings[0].claim
        elif doc.status is not DocumentStatus.READY:
            report.brief = f"《{doc.name}》{doc.status.value}，本次跳过"
        else:
            report.brief = f"《{doc.name}》{len(chunks)} 片段 / {chars} 字，未发现严重问题"

        if doc.status is DocumentStatus.READY:
            topic = topic_names.get(doc.topic_id)
            if topic:
                report.brief += f"（主题：{topic}）"
        return report

    def inspect_space(self, reports: list[DocReport]) -> list[Finding]:
        """跨资料检查：同名重复、首段重复、主题覆盖。"""
        findings: list[Finding] = []
        findings.extend(self._duplicates(reports))
        findings.extend(self._topic_coverage(reports))
        return findings

    def _duplicates(self, reports: list[DocReport]) -> list[Finding]:
        by_name: dict[str, list[DocReport]] = {}
        by_fingerprint: dict[str, list[DocReport]] = {}
        for report in reports:
            by_name.setdefault(_normalized_name(report.name), []).append(report)
            if len(report.fingerprint) >= 40:
                by_fingerprint.setdefault(report.fingerprint, []).append(report)

        findings: list[Finding] = []
        seen: set[frozenset[str]] = set()

        def _add(group: list[DocReport], reason: str) -> None:
            ids = [item.document_id for item in group]
            key = frozenset(ids)
            if len(ids) < 2 or key in seen:
                return
            seen.add(key)
            names = "、".join(f"《{item.name}》" for item in group)
            findings.append(
                _finding(
                    "duplicate",
                    f"{names}疑似重复（{reason}），建议只保留一份",
                    {
                        "document_ids": ids,
                        "documents": [item.name for item in group],
                        "reason": reason,
                    },
                )
            )

        for group in by_name.values():
            _add(group, "同名")
        for group in by_fingerprint.values():
            _add(group, "首段内容相同")
        return findings

    def _topic_coverage(self, reports: list[DocReport]) -> list[Finding]:
        by_topic: dict[str, int] = {}
        for report in reports:
            if not report.ok:
                continue
            by_topic[report.topic_id] = by_topic.get(report.topic_id, 0) + 1

        findings: list[Finding] = []
        for topic in self.topics():
            if topic.id == "uncategorized":
                # 内置「未分类」不参与覆盖检查：它是兜底桶，不是用户建的主题
                continue
            count = by_topic.get(topic.id, 0)
            if count == 0:
                findings.append(
                    _finding(
                        "topic_empty",
                        f"主题「{topic.name}」还没有可用的资料",
                        {"topic_id": topic.id, "topic": topic.name, "documents": 0},
                    )
                )
            elif count == 1:
                findings.append(
                    _finding(
                        "topic_sparse",
                        f"主题「{topic.name}」只有 1 份资料，覆盖可能不足",
                        {"topic_id": topic.id, "topic": topic.name, "documents": 1},
                    )
                )
        return findings