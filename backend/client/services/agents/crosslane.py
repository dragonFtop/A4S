"""跨道协作：把体检道的结论交进教练道（P3）。

两条泳道共用一块黑板，但不共享内部状态：这里只读最近一次知识库体检的
运行记录，把其中的资料问题转成 Finding，在规划员动笔前挂到黑板上。
于是「资料本身有问题」会直接出现在计划的依据里，用户不必自己把知识库页
和计划页对照着看。

边界：

- 只读运行账本，不改体检结论，也不自动执行修复；
- 只带高危与中危问题，最多 5 条——计划不是修复清单；
- 体检从没跑过、记录损坏，都降级为空，教练道照常出计划。
"""

from __future__ import annotations

from shared.domain.models.agents import AgentRole, Finding, FindingKind

# 一次计划最多引用几条资料问题。再多会把计划正文挤成待办清单。
MAX_CROSS_FINDINGS = 5
# 空库没有可注入的体检结论：教练道自己会用 notice 声明，不必在这里重复一遍
SKIP_CHECKS = frozenset({"empty_library"})
_SEVERITY_ORDER = {"high": 0, "medium": 1}


def from_health_run(run) -> list[Finding]:
    """体检运行记录 → 黑板事实。只留高危/中危，按严重程度排序。"""
    items = (run.summary or {}).get("findings") or []
    picked: list[Finding] = []
    for item in items:
        evidence = item.get("evidence") or {}
        if str(evidence.get("check") or "") in SKIP_CHECKS:
            continue
        if str(evidence.get("severity") or "low") not in _SEVERITY_ORDER:
            continue
        picked.append(
            Finding(
                kind=FindingKind.DOCUMENT,
                claim=str(item.get("claim") or ""),
                evidence={
                    **evidence,
                    "source": "kb_inspection",
                    "run_id": run.id,
                },
                producer=AgentRole.INSPECTOR.value,
            )
        )
    picked.sort(
        key=lambda finding: _SEVERITY_ORDER.get(
            str(finding.evidence.get("severity") or "low"), 2
        )
    )
    return picked[:MAX_CROSS_FINDINGS]


def load(db, space_id: str) -> tuple[list[Finding], dict]:
    """返回（事实, 摘要）。任何异常都降级为空——体检道失败不能拖垮教练道。"""
    from . import health

    try:
        run = health.latest(db, space_id)
    except Exception:  # noqa: BLE001 - 跨道读取是增强项，不是必需项
        return [], {"found": 0, "run_id": None, "error": "读取体检记录失败"}
    if run is None:
        return [], {"found": 0, "run_id": None}

    findings = from_health_run(run)
    return findings, {
        "found": len(findings),
        "run_id": run.id,
        "score": (run.summary or {}).get("score"),
    }