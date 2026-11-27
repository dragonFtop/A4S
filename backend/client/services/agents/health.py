"""体检道协调员（M8）。

Inspector 逐份巡检 → Fixer 出修复提案 → Verifier 核对提案是否都有事实依据。
不调模型，也不写业务表；只把过程与结论记进 `agent_runs` / `agent_steps`，
与教练道共用契约但互不读取内部状态。
"""

from __future__ import annotations

import time

from shared.domain.models.agents import (
    AgentKind,
    AgentRole,
    AgentRun,
    Finding,
    FindingKind,
    RunStatus,
    new_id,
)
from shared.repositories.agents import SqlAlchemyAgentRunRepository

from .fixer import Fixer
from .inspector import Inspector

SEVERITY_PENALTY = {"high": 20, "medium": 10, "low": 3}
_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def _counts(findings: list[Finding]) -> dict[str, int]:
    result = {"high": 0, "medium": 0, "low": 0}
    for finding in findings:
        severity = str(finding.evidence.get("severity") or "low")
        result[severity] = result.get(severity, 0) + 1
    return result


def health_score(findings: list[Finding]) -> int:
    """健康分。空库直接 0，其余按严重程度扣分，扣完为止。"""
    if any(item.evidence.get("check") == "empty_library" for item in findings):
        return 0
    penalty = sum(SEVERITY_PENALTY.get(str(item.evidence.get("severity")), 3) for item in findings)
    return max(0, 100 - penalty)


def verify_fixes(proposals: list[dict], findings: list[Finding]) -> tuple[list[dict], dict]:
    """确定性校验：提案必须挂在一张真实存在的 Finding 上，否则丢弃。"""
    known = {finding.id for finding in findings}
    kept = [item for item in proposals if item.get("finding_id") in known]
    dropped = len(proposals) - len(kept)
    warnings = [f"丢弃 {dropped} 条没有事实依据的修复提案"] if dropped else []
    verdict = {
        "passed": True,
        "reasons": [],
        "warnings": warnings,
        "checked": ["每条提案都能对回一条 Finding", "提案只描述改什么，不自动执行"],
    }
    return kept, verdict


def _sorted_findings(findings: list[Finding]) -> list[Finding]:
    return sorted(
        findings,
        key=lambda item: _SEVERITY_ORDER.get(str(item.evidence.get("severity") or "low"), 3),
    )


def run_inspection(db, *, space_id: str, trigger: str = "manual") -> AgentRun:
    """跑一次体检，返回完整运行记录（含步骤）。"""
    started = time.monotonic()
    repo = SqlAlchemyAgentRunRepository(db)
    inspector = Inspector(db, space_id)
    docs = inspector.documents()
    run_id = new_id()
    repo.start(
        AgentRun(
            id=run_id,
            space_id=space_id,
            kind=AgentKind.KB_INSPECTION.value,
            goal="知识库体检",
            trigger=trigger,
            budget_steps=max(8, len(docs) + 8),
        )
    )

    seq = 0
    findings: list[Finding] = []

    def _step(**kwargs) -> None:
        nonlocal seq
        seq += 1
        repo.append_step(run_id, seq=seq, **kwargs)

    try:
        topic_names = {topic.id: topic.name for topic in inspector.topics()}
        reports = []
        for doc in docs:
            begin = time.monotonic()
            report = inspector.inspect_document(doc, topic_names)
            reports.append(report)
            findings.extend(report.findings)
            _step(
                role=AgentRole.INSPECTOR.value,
                action="inspect",
                tool="inspect_document",
                args={"document_id": doc.id, "name": doc.name},
                ok=report.ok,
                brief=report.brief,
                result={
                    "chunks": report.chunks,
                    "chars": report.chars,
                    "findings": [item.claim for item in report.findings],
                },
                elapsed_ms=int((time.monotonic() - begin) * 1000),
            )

        begin = time.monotonic()
        cross = inspector.inspect_space(reports)
        findings.extend(cross)
        if not docs:
            findings.append(
                Finding(
                    kind=FindingKind.DOCUMENT,
                    claim="知识库还没有资料，体检没有可检查的对象",
                    evidence={"check": "empty_library", "severity": "low", "documents": 0},
                    producer=AgentRole.INSPECTOR.value,
                )
            )
        findings = _sorted_findings(findings)
        _step(
            role=AgentRole.INSPECTOR.value,
            action="inspect",
            tool="inspect_space",
            args={"documents": len(docs)},
            ok=not any(item.evidence.get("severity") == "high" for item in findings),
            brief=f"跨资料检查：{len(cross)} 条问题，合计 {len(findings)} 条待处理",
            result={"cross_findings": [item.as_dict() for item in cross][:10]},
            elapsed_ms=int((time.monotonic() - begin) * 1000),
        )

        proposals = Fixer().propose(findings)
        for item in proposals:
            _step(
                role=AgentRole.FIXER.value,
                action="propose_fix",
                tool="propose_fix",
                args={"action": item["action"], "target": item["target"]},
                brief=item["label"],
                result=item,
            )
        if not proposals:
            _step(
                role=AgentRole.FIXER.value,
                action="propose_fix",
                tool="propose_fix",
                ok=True,
                brief="没有需要修复的问题",
                result={"count": 0},
            )

        begin = time.monotonic()
        proposals, verdict = verify_fixes(proposals, findings)
        _step(
            role=AgentRole.VERIFIER.value,
            action="verdict",
            tool="verify_fixes",
            ok=True,
            brief=(
                f"通过：{len(proposals)} 条修复提案都有事实依据"
                if proposals
                else "通过：没有提案需要核对"
            ),
            result=verdict,
            elapsed_ms=int((time.monotonic() - begin) * 1000),
        )

        counts = _counts(findings)
        summary = {
            "score": health_score(findings),
            "documents": len(docs),
            "counts": counts,
            "findings": [item.as_dict() for item in findings],
            "proposals": proposals,
        }
        repo.finish(
            run_id,
            status=RunStatus.COMPLETED.value,
            steps_count=seq,
            llm_calls=0,
            handoffs=0,
            summary=summary,
            verdict=verdict,
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:
        repo.finish(
            run_id,
            status=RunStatus.FAILED.value,
            steps_count=seq,
            llm_calls=0,
            error=str(exc),
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
        raise

    run = repo.get(run_id, space_id)
    if run is None:  # pragma: no cover - 刚写入就查不到说明库出了问题
        raise RuntimeError("体检记录写入后无法读回")
    return run


def latest(db, space_id: str) -> AgentRun | None:
    repo = SqlAlchemyAgentRunRepository(db)
    for run in repo.list(space_id, limit=30):
        if run.kind == AgentKind.KB_INSPECTION.value:
            return repo.get(run.id, space_id)
    return None