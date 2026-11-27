"""把工具返回值翻译成黑板上的 Finding。

观察员决定「查什么」，这里只负责「查到的结果怎么变成可交接的事实」。
规则很硬：工具失败不产出 Finding；没有证据的数字不写进 claim。
"""

from __future__ import annotations

import json

from shared.domain.models.agents import Finding, FindingKind

# 观察员能用的工具：全部只读
READ_TOOLS = frozenset({"get_progress", "list_weak_points", "list_wrong_answers"})
# 规划员才能做的动作
PLANNER_ACTIONS = frozenset({"generate_practice", "propose_plan", "save_plan"})

MAX_EVIDENCE_CHARS = 700
MAX_FACTS_CHARS = 5000


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def from_tool_result(tool: str, result: dict) -> list[Finding]:
    """一个工具结果 → 一到多条 Finding。失败或空结果返回空列表。"""
    if not isinstance(result, dict) or result.get("ok") is False or result.get("error"):
        return []

    if tool == "get_progress":
        documents = result.get("documents") or {}
        practice = result.get("practice") or {}
        wrong = result.get("wrong") or {}
        return [
            Finding(
                kind=FindingKind.PROGRESS,
                claim=(
                    f"资料 {documents.get('ready', 0)} 份（片段 {documents.get('chunks', 0)}）、"
                    f"练习 {practice.get('finished', 0)} 套、"
                    f"平均 {practice.get('avg_score', 0)} 分、错题 {wrong.get('total', 0)} 道"
                ),
                evidence=result,
            )
        ]

    if tool == "list_weak_points":
        items = result.get("items") or []
        findings: list[Finding] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            findings.append(
                Finding(
                    kind=FindingKind.WEAK_POINT,
                    claim=(
                        f"主题「{item.get('topic') or '未分类'}」错误率 {item.get('error_rate', 0)}%，"
                        f"错题 {item.get('wrong_questions', 0)} 道，"
                        f"累计答错 {item.get('wrong_count', 0)} 次"
                    ),
                    evidence=item,
                )
            )
        return findings

    if tool == "list_wrong_answers":
        items = result.get("items") or []
        if not items:
            return []
        first = items[0] if isinstance(items[0], dict) else {}
        return [
            Finding(
                kind=FindingKind.WRONG_ANSWER,
                claim=(
                    f"错题明细 {len(items)} 条；错得最多的是"
                    f"「{_clip(str(first.get('stem') or ''), 80)}」"
                    f"（错 {first.get('wrong_count', 1)} 次）"
                ),
                evidence=result,
            )
        ]

    if tool == "generate_practice":
        return [
            Finding(
                kind=FindingKind.PRACTICE,
                claim=(
                    f"已生成 {result.get('produced', 0)} 道题，"
                    f"练习草稿 {result.get('session_id') or '（未返回编号）'}"
                ),
                evidence=result,
            )
        ]

    return []


def facts_digest(findings: list[Finding]) -> str:
    """交给规划员的证据清单。每条 Finding 保留 claim + 截断后的证据。"""
    blocks: list[str] = []
    for index, finding in enumerate(findings, 1):
        evidence = json.dumps(finding.evidence, ensure_ascii=False)
        if len(evidence) > MAX_EVIDENCE_CHARS:
            evidence = evidence[: MAX_EVIDENCE_CHARS - 1] + "…"
        blocks.append(f"[F{index}] {finding.claim}\n证据：{evidence}")
    text = "\n\n".join(blocks) or "（观察员没有查到任何可用数据）"
    return text[:MAX_FACTS_CHARS]