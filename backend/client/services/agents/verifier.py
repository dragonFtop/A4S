"""审核员：在写入之前核对「计划说的数字，事实里有没有」。

P0 只做确定性检查，不调模型——审核本身不能再引入一次幻觉：
1. 结构：必须有「依据 / 安排 / 完成标准」三段；
2. 长度：与 save_plan 的入库门槛一致；
3. 数字：「依据」里的数字必须在对应类型的事实里出现过；
   某类事实完全没观察到时只记 warning，不误杀。
"""

from __future__ import annotations

import re

import client.services.plan_tools as plan_tools
from shared.domain.models.agents import Finding, FindingKind, Proposal, Verdict

_SECTION = re.compile(r"^##\s*(依据|安排|完成标准)\s*$", re.MULTILINE)
_EVIDENCE_BLOCK = re.compile(r"^##\s*依据\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# 日期与「第 N 天」不是数据，先抹掉再抽数字
_NOT_DATA = [
    re.compile(r"\d{4}\s*[-/年]\s*\d{1,2}\s*[-/月]\s*\d{1,2}\s*日?"),
    re.compile(r"\d{1,2}\s*月\s*\d{1,2}\s*日"),
    re.compile(r"第\s*\d+\s*[天周]"),
    re.compile(r"\d+\s*天"),
]
_WEAK_WORDS = ("薄弱", "错误率", "最弱", "答错")
_WRONG_WORDS = ("错题",)
_DOC_WORDS = ("资料", "片段", "页")
_PRACTICE_WORDS = ("练习", "套", "平均")


def _numbers(value) -> set[float]:
    if isinstance(value, bool) or value is None:
        return set()
    if isinstance(value, (int, float)):
        return {round(float(value), 4)}
    if isinstance(value, dict):
        found: set[float] = set()
        for item in value.values():
            found |= _numbers(item)
        return found
    if isinstance(value, list):
        found = set()
        for item in value:
            found |= _numbers(item)
        return found
    if isinstance(value, str):
        found = set()
        for token in _NUMBER.findall(value):
            found.add(round(float(token), 4))
        return found
    return set()


def _mask(line: str) -> str:
    for pattern in _NOT_DATA:
        line = pattern.sub(" ", line)
    return line


def _category(prefix: str) -> str:
    window = prefix[-14:]
    if any(word in window for word in _WEAK_WORDS):
        return "weak"
    if any(word in window for word in _WRONG_WORDS):
        return "wrong"
    if any(word in window for word in _DOC_WORDS):
        return "documents"
    if any(word in window for word in _PRACTICE_WORDS):
        return "practice"
    return "any"


def _evidence_sets(findings: list[Finding]) -> dict[str, set[float]]:
    progress = [item for item in findings if item.kind == FindingKind.PROGRESS]
    weak = [item for item in findings if item.kind == FindingKind.WEAK_POINT]
    practice = [item for item in findings if item.kind == FindingKind.PRACTICE]

    documents = set()
    practices = set()
    wrong = set()
    for item in progress:
        documents |= _numbers(item.evidence.get("documents"))
        practices |= _numbers(item.evidence.get("practice"))
        wrong |= _numbers(item.evidence.get("wrong"))

    weak_numbers = set()
    for item in weak:
        weak_numbers |= _numbers(item.evidence)

    practice_numbers = set()
    for item in practice:
        practice_numbers |= _numbers(item.evidence)

    any_numbers = set()
    for item in findings:
        any_numbers |= _numbers(item.evidence)

    return {
        "documents": documents,
        "practice": practices,
        "wrong": wrong | {value for item in weak for value in _numbers(item.evidence.get("wrong_count"))},
        "weak": weak_numbers,
        "generated": practice_numbers,
        "any": any_numbers,
    }


def verify(proposal: Proposal, findings: list[Finding]) -> Verdict:
    reasons: list[str] = []
    warnings: list[str] = []
    markdown = proposal.markdown
    chars = len(markdown)

    if chars < plan_tools.MIN_PLAN_CHARS:
        reasons.append(
            f"计划太短了：至少写出一周内每天做什么，不少于 {plan_tools.MIN_PLAN_CHARS} 字"
            f"（当前 {chars} 字）"
        )
    elif chars > plan_tools.MAX_PLAN_CHARS:
        reasons.append(f"计划过长（{chars} 字），请压缩到 {plan_tools.MAX_PLAN_CHARS} 字以内")

    sections = {match.group(1) for match in _SECTION.finditer(markdown)}
    missing = [name for name in ("依据", "安排", "完成标准") if name not in sections]
    if missing:
        reasons.append(f"计划缺少段落：{'、'.join('## ' + name for name in missing)}")

    evidence = _evidence_sets(findings)
    block = _EVIDENCE_BLOCK.search(markdown)
    if not block:
        warnings.append("没有找到「## 依据」，数字未核对")
    else:
        allowed_by_category = {
            "weak": evidence["weak"],
            "wrong": evidence["wrong"],
            "documents": evidence["documents"],
            "practice": evidence["practice"],
            "any": evidence["any"],
        }
        for line in block.group(1).splitlines():
            if not line.strip():
                continue
            masked = _mask(line)
            for match in _NUMBER.finditer(masked):
                number = round(float(match.group(0)), 4)
                category = _category(masked[: match.start()])
                allowed = allowed_by_category[category]
                if not allowed:
                    label = {
                        "weak": "薄弱点",
                        "wrong": "错题",
                        "documents": "资料",
                        "practice": "练习",
                        "any": "学习数据",
                    }[category]
                    warnings.append(f"没有观察到{label}数据，未核对数字 {match.group(0)}")
                    continue
                if not any(abs(number - value) < 0.051 for value in allowed):
                    reasons.append(
                        f"「依据」里的数字 {match.group(0)} 在查询结果里找不到出处："
                        f"{line.strip()[:80]}"
                    )

    # 只把前三条打回理由交给规划员，避免一次塞回一大段
    deduped = list(dict.fromkeys(reasons))
    return Verdict(
        passed=not deduped,
        reasons=deduped[:3],
        warnings=warnings[:5],
        checked=["length", "structure", "evidence_numbers"],
    )