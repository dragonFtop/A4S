"""可问答性探针（P2.1）。

用资料自身的片段当问题，走一次真实检索，看这份资料会不会出现在召回结果里。
它回答的是「资料在库里，但学生提问时能不能被找到」——片段数、字符数这些静态
指标看不出来的问题（向量没写进去、索引维度不一致、索引被清空）都长这样。

三个约束
--------
- **只读**：不写业务表、不改资料，未召回只产出一张 Finding 交给修复员。
- **尽力而为**：Embedding 模型或向量索引不可用时整体跳过，把原因写进步骤结果，
  绝不让体检失败。其余检查都是纯 SQL，不能因为探针把整份报告拖没。
- **一次批量**：所有资料在同一步里跑完，不按资料拆分步骤，避免撑爆步数预算。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shared.domain.models.agents import AgentRole, Finding, FindingKind
from shared.domain.models.entities import Document, DocumentStatus
from shared.repositories.chunks import SqlAlchemyChunkRepository
from shared.services import retrieval

# 抽测的正文下限：太短的片段本来就难以支撑问答，由巡检员的 too_short 负责
MIN_PROBE_CHARS = 60
# 拿多少字的原文当问题。取首段即可：同一段文字的向量必然离它自己最近，
# 又不至于把整份资料塞进一次查询。
PROBE_CHARS = 240
# 一次体检最多抽测多少份资料。探针要跑 Embedding，多了会拖慢手动体检。
MAX_PROBES = 10
# 证据里最多记几条「实际召回的是谁」，够排查即可
MAX_EVIDENCE_HITS = 3


@dataclass
class ProbeReport:
    """一次探针抽测的结果，协调员据此记一行步骤。"""

    checked: int = 0
    hits: int = 0
    missed: int = 0
    skipped: int = 0
    reason: str = ""
    findings: list[Finding] = field(default_factory=list)
    missed_documents: list[str] = field(default_factory=list)

    @property
    def brief(self) -> str:
        if self.reason:
            return self.reason
        if not self.checked:
            return "没有可抽测的资料"
        detail = f"抽测 {self.checked} 份资料：召回 {self.hits} 份"
        if self.missed:
            detail += f"，{self.missed} 份没有被召回"
        return detail


def _probe_finding(doc: Document, probe: str, hit_documents: list[str]) -> Finding:
    claim = (
        f"《{doc.name}》没有被检索到：用原文片段提问，"
        f"召回的 {len(hit_documents)} 份资料里没有它"
    )
    return Finding(
        kind=FindingKind.DOCUMENT,
        claim=claim,
        evidence={
            "check": "probe_missed",
            "severity": "medium",
            "document_id": doc.id,
            "document_ids": [doc.id],
            "document": doc.name,
            "probe": probe[:120],
            "probe_chars": len(probe),
            "hits": hit_documents[:MAX_EVIDENCE_HITS],
        },
        producer=AgentRole.INSPECTOR.value,
    )


def probe_recall(db, *, space_id: str, docs: list[Document]) -> ProbeReport:
    """对每份可检索资料抽一段原文当问题，验证它能否被召回。"""
    report = ProbeReport()
    chunk_repo = SqlAlchemyChunkRepository(db)

    candidates: list[tuple[Document, str]] = []
    for doc in docs:
        if doc.status is not DocumentStatus.READY:
            continue
        chunks = chunk_repo.list_by_document(doc.id)
        longest = max(chunks, key=lambda item: len(item.text or ""), default=None)
        text = (longest.text or "").strip() if longest else ""
        if len(text) < MIN_PROBE_CHARS:
            # 缺片段 / 正文过短已由巡检员报出，这里不重复告警
            report.skipped += 1
            continue
        candidates.append((doc, text))

    if not candidates:
        report.reason = "没有可抽测的资料（缺片段或正文过短）"
        return report

    for doc, text in candidates[:MAX_PROBES]:
        probe = text[:PROBE_CHARS]
        try:
            citations, _, _ = retrieval.retrieve(db, probe, space_id=space_id)
        except Exception as exc:  # noqa: BLE001 - 检索不可用时跳过，不让体检失败
            report.reason = f"检索不可用，探针已跳过：{exc}"
            return report

        report.checked += 1
        hit_ids = [str(item.get("document_id") or "") for item in citations]
        if doc.id in hit_ids:
            report.hits += 1
            continue

        report.missed += 1
        report.missed_documents.append(doc.name)
        hit_names = list(dict.fromkeys(str(item.get("document_name") or "") for item in citations))
        report.findings.append(
            _probe_finding(doc, probe, [name for name in hit_names if name])
        )

    return report