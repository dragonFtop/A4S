"""修复员（M8 体检道）。

只提建议、不改数据：每条提案都挂在一个 Finding 上，说清「改什么、为什么、风险」。
真正的修复动作留给用户在知识库页手动执行，P2 不开放自动改资料。
"""

from __future__ import annotations

from shared.domain.models.agents import AgentRole, Finding, new_id

MAX_PROPOSALS = 40

_RULES: dict[str, tuple[str, str]] = {
    "parse_failed": (
        "reparse",
        "重新解析或换一份可复制文本的版本",
    ),
    "empty_chunks": (
        "reparse",
        "重新导出一份带文字层的 PDF 再上传",
    ),
    "too_short": (
        "replace",
        "补充完整资料，或与同主题资料合并后再问",
    ),
    "duplicate": (
        "merge",
        "只保留一份，删掉重复的那份",
    ),
    "uncategorized": (
        "assign_topic",
        "把它归到合适的学习主题，方便后续出题与统计",
    ),
    "topic_empty": (
        "add_document",
        "给这个主题补一份资料，否则它不会被检索到",
    ),
    "topic_sparse": (
        "add_document",
        "再补一份角度不同的资料，覆盖会更稳",
    ),
    "empty_library": (
        "import",
        "先导入一份资料，体检才有对象",
    ),
    "probe_missed": (
        "reindex",
        "重建索引后再体检一次；仍未召回就检查切分与 Embedding 模型是否一致",
    ),
}


class Fixer:
    def propose(self, findings: list[Finding]) -> list[dict]:
        proposals: list[dict] = []
        for finding in findings:
            check = str(finding.evidence.get("check") or "")
            rule = _RULES.get(check)
            if rule is None:
                continue
            action, label = rule
            target = [str(item) for item in finding.evidence.get("document_ids") or []]
            proposals.append(
                {
                    "id": new_id("x"),
                    "action": action,
                    "label": label,
                    "reason": finding.claim,
                    "target": target,
                    "topic_id": str(finding.evidence.get("topic_id") or ""),
                    "finding_id": finding.id,
                    "producer": AgentRole.FIXER.value,
                }
            )
            if len(proposals) >= MAX_PROPOSALS:
                break
        return proposals