"""回顾报告生成（FR-07）与推送编排（FR-08）。

生成流程分三段，任何一段失败都不应让报告整体失败：

1. **算**：`collect_window` 出数字快照，`wrong_summary` 出错题口径；
2. **写**：模型只写「知识点概括 / 薄弱点 / 建议」三段文字，数字部分由程序拼；
3. **降级**：模型不可用、输出不是 JSON、字段缺失时，用规则版分析顶上，
   并在 `metrics.analysis_source` 标记来源，报告照常保存。

「本期没有任何学习行为」走的是**提示性报告**而非报错：一份告诉你去哪开始的
报告，比一个红叉有用。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.domain.models.entities import (
    UNCATEGORIZED_ID,
    UNCATEGORIZED_NAME,
    ReviewReport,
)
from app.repositories.review_repo import (
    SqlAlchemyReviewRepository,
    collect_window,
    to_iso,
    utcnow_naive,
)
from app.repositories.sqlalchemy_repo import (
    SqlAlchemyTopicRepository,
    SqlAlchemyWrongAnswerRepository,
)
from app.services import review_prompts
from app.services.practice_service import wrong_summary

logger = logging.getLogger(__name__)

WINDOWS = (7, 14, 30)
# 题型中文名与 prompt 共用一份，避免报告里一份中文一份英文
TYPE_LABELS = review_prompts.TYPE_LABELS


class ReviewError(Exception):
    """报告生成的业务错误（窗口不合法等）。"""


class ReviewNotFound(ReviewError):
    def __init__(self, report_id: str) -> None:
        super().__init__(f"报告不存在：{report_id}")
        self.report_id = report_id


# ---------- 辅助 ----------


def _fmt_time(value: datetime) -> str:
    """库里存的是朴素 UTC，报告要给人看，转成本地时间。"""
    if value.tzinfo is None:
        from datetime import timezone

        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def _fmt_day(value: datetime) -> str:
    if value.tzinfo is None:
        from datetime import timezone

        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone().strftime("%Y-%m-%d")


def _top_wrong(db: Session, window_days: int, limit: int = 5) -> list[dict]:
    """窗口内错得最多的题，带主题名，供报告与邮件共用。"""
    items = SqlAlchemyWrongAnswerRepository(db).list(days=window_days, sort="count", limit=limit)
    topics = {topic.id: topic for topic in SqlAlchemyTopicRepository(db).list()}
    return [
        {
            "question_id": item.question.id,
            "stem": item.question.stem,
            "type": item.question.type.value,
            "type_label": TYPE_LABELS.get(item.question.type.value, item.question.type.value),
            "wrong_count": item.wrong_count,
            "answer": item.question.answer,
            "last_response": item.last_response,
            "topic_id": item.topic_id or UNCATEGORIZED_ID,
            "topic_name": (
                topics[item.topic_id].name
                if item.topic_id and item.topic_id in topics
                else UNCATEGORIZED_NAME
            ),
        }
        for item in items
    ]


def _has_activity(metrics: dict, wrong: dict) -> bool:
    return bool(
        metrics["documents"]["total"] or metrics["practice"]["sessions"] or wrong.get("total")
    )


# ---------- 规则版分析（模型不可用时的兜底） ----------


def _rule_based_analysis(metrics: dict, wrong: dict, top_wrong: list[dict]) -> dict:
    """不依赖模型的分析。

    写得不比模型差多少——它掌握的是同一批数字，只是措辞朴素。
    对单机应用来说这很重要：没配 Key 也不该拿不到报告。
    """
    weak: list[str] = []
    suggestions: list[str] = []

    practice_topics = [b for b in metrics["practice"]["by_topic"] if b["answered"] >= 3]
    worst = min(practice_topics, key=lambda b: b["accuracy"]) if practice_topics else None
    overall = metrics["practice"]["accuracy"]

    if worst and worst["accuracy"] < max(overall, 60):
        weak.append(
            f"「{worst['name']}」正确率 {worst['accuracy']}%，"
            f"低于你整体的 {overall}%，是当前最该补的主题。"
        )

    wrong_topics = [b for b in wrong.get("by_topic", []) if b["question_count"] >= 2]
    if wrong_topics:
        heaviest = wrong_topics[0]
        weak.append(
            f"「{heaviest['name']}」有 {heaviest['question_count']} 道题反复出错，"
            f"累计错 {heaviest['wrong_count']} 次，说明概念边界还没分清。"
        )

    if wrong.get("repeat_count"):
        weak.append(
            f"{wrong['repeat_count']} 道题错过两次以上，属于「看过但没真懂」，"
            "重做时不要凭印象选。"
        )

    if worst:
        suggestions.append(
            f"先把「{worst['name']}」的资料重读一遍，再针对性出 10 道题，做到正确率 80% 以上再往下走。"
        )
    if wrong_topics:
        suggestions.append(
            f"从错题本挑出「{wrong_topics[0]['name']}」的题做一次重练，重练时先写理由再选答案。"
        )
    untouched = [
        group["name"]
        for group in metrics["documents"]["by_topic"]
        if group["name"] not in {b["name"] for b in metrics["practice"]["by_topic"]}
    ]
    if untouched:
        suggestions.append(
            f"本期新增的「{'、'.join(untouched[:3])}」还没有练习记录，建议先出题检验是否真的读懂。"
        )
    if not suggestions:
        suggestions.append("本期数据还少，先导入一份资料并完成一套练习，下一份报告才有可分析的内容。")

    summaries = [
        {
            "topic": group["name"],
            "summary": f"新增 {group['count']} 份资料、{group['chunks']} 个知识片段。",
        }
        for group in metrics["documents"]["by_topic"]
    ]

    return {
        "topic_summaries": summaries,
        "weak_points": weak[:3],
        "suggestions": suggestions[:4],
    }


# ---------- 模型版分析 ----------


async def _analyze(metrics: dict, wrong: dict, top_wrong: list[dict]) -> tuple[dict, str]:
    """返回 (分析结果, 来源标记)。来源用于前端提示「这次是规则生成」。"""
    from app.config import settings

    if not settings.llm_configured:
        return _rule_based_analysis(metrics, wrong, top_wrong), "rule"

    try:
        from app.infrastructure.llm.openai_compat import build_llm

        llm = build_llm()
        raw = await llm.chat(
            [
                {"role": "system", "content": review_prompts.SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": review_prompts.build_user_prompt(metrics, wrong, top_wrong),
                },
            ],
            timeout=90,
        )
        parsed = review_prompts.parse_analysis(raw)
    except Exception as exc:  # noqa: BLE001  模型侧任何异常都降级，不阻断报告
        logger.warning("回顾报告模型分析失败，降级为规则版：%s", exc)
        return _rule_based_analysis(metrics, wrong, top_wrong), "rule"

    fallback = _rule_based_analysis(metrics, wrong, top_wrong)
    merged = {
        "topic_summaries": parsed["topic_summaries"] or fallback["topic_summaries"],
        "weak_points": parsed["weak_points"] or fallback["weak_points"],
        "suggestions": parsed["suggestions"] or fallback["suggestions"],
    }
    source = "llm" if parsed["weak_points"] or parsed["suggestions"] else "rule"
    return merged, source


# ---------- Markdown 组装 ----------


def _section_documents(metrics: dict, summaries: list[dict]) -> str:
    documents = metrics["documents"]
    if not documents["total"]:
        return "## 一、本期新增资料\n\n本期没有新增资料。\n"

    summary_map = {item["topic"]: item["summary"] for item in summaries}
    lines = [
        "## 一、本期新增资料",
        "",
        f"共 **{documents['total']}** 份，**{documents['chunks']}** 个知识片段。",
        "",
    ]
    for group in documents["by_topic"]:
        lines.append(f"### {group['name']}")
        lines.append("")
        note = summary_map.get(group["name"])
        if note:
            lines.append(f"{note}")
            lines.append("")
        for doc in group["documents"]:
            flag = "" if doc["status"] == "ready" else "（处理中或失败）"
            lines.append(f"- {doc['name']}{flag} · {doc['chunk_count']} 片段")
        lines.append("")
    return "\n".join(lines)


def _fmt_duration(seconds: int) -> str:
    """时长按量级换单位：0 秒 / 40 秒 / 12 分钟 / 1.5 小时。

    练习刚做完时如果写「0 分钟」，会让人以为统计坏了。
    """
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{round(seconds / 60)} 分钟"
    return f"{seconds / 3600:.1f} 小时"


def _section_practice(metrics: dict) -> str:
    practice = metrics["practice"]
    if not practice["sessions"]:
        return "## 二、练习完成情况\n\n本期没有完成任何练习。\n"

    minutes = _fmt_duration(practice["duration_seconds"])
    lines = [
        "## 二、练习完成情况",
        "",
        "| 指标 | 数值 |",
        "| --- | --- |",
        f"| 完成套数 | {practice['sessions']} |",
        f"| 题量 | {practice['questions']} |",
        f"| 实际作答 | {practice['answered']} |",
        f"| 答对 | {practice['correct']} |",
        f"| 正确率 | **{practice['accuracy']}%** |",
        f"| 用时 | {minutes} |",
        "",
    ]
    if practice["by_topic"]:
        lines += ["按主题：", "", "| 主题 | 作答 | 答对 | 正确率 |", "| --- | --- | --- | --- |"]
        for bucket in practice["by_topic"]:
            lines.append(
                f"| {bucket['name']} | {bucket['answered']} | {bucket['correct']} | {bucket['accuracy']}% |"
            )
        lines.append("")
    if practice["by_type"]:
        lines += ["按题型：", "", "| 题型 | 作答 | 正确率 |", "| --- | --- | --- |"]
        for bucket in practice["by_type"]:
            label = TYPE_LABELS.get(bucket["type"], bucket["type"])
            lines.append(f"| {label} | {bucket['answered']} | {bucket['accuracy']}% |")
        lines.append("")
    return "\n".join(lines)


def _section_wrong(wrong: dict, top_wrong: list[dict]) -> str:
    if not wrong.get("total"):
        return "## 三、错题整理\n\n本期没有新增错题。\n"

    lines = [
        "## 三、错题整理",
        "",
        f"错题 **{wrong['total']}** 道，累计答错 **{wrong['total_wrong_count']}** 次，"
        f"其中 {wrong['repeat_count']} 道重复错。",
        "",
    ]
    for bucket in wrong.get("by_topic", []):
        lines.append(f"### {bucket['name']}")
        lines.append("")
        lines.append(
            f"{bucket['question_count']} 道题，累计错 {bucket['wrong_count']} 次，"
            f"该主题错误率 {bucket.get('error_rate', 0)}%。"
        )
        lines.append("")

    if top_wrong:
        lines.append("高频错误：")
        lines.append("")
        for index, item in enumerate(top_wrong, start=1):
            lines.append(f"{index}. **{item['stem']}**")
            lines.append(
                f"   - 错 {item['wrong_count']} 次 · {item['topic_name']} · {item['type_label']}"
            )
            lines.append(f"   - 正确答案：{item['answer']}")
            if item.get("last_response"):
                lines.append(f"   - 你最近一次答：{item['last_response']}")
        lines.append("")
    return "\n".join(lines)


def _section_lists(title: str, items: list[str], fallback: str) -> str:
    lines = [f"## {title}", ""]
    if not items:
        lines.append(fallback)
        return "\n".join(lines)
    for index, item in enumerate(items, start=1):
        lines.append(f"{index}. {item}")
    return "\n".join(lines)


def _compose_markdown(
    metrics: dict,
    wrong: dict,
    top_wrong: list[dict],
    analysis: dict,
    source: str,
    period_start: datetime,
    period_end: datetime,
    window_days: int,
) -> str:
    head = [
        f"# 学习回顾 · 最近 {window_days} 天",
        f"> {_fmt_day(period_start)} → {_fmt_day(period_end)}　生成于 {_fmt_time(period_end)}",
    ]
    sections = [
        _section_documents(metrics, analysis["topic_summaries"]),
        _section_practice(metrics),
        _section_wrong(wrong, top_wrong),
        _section_lists(
            "四、薄弱点分析",
            analysis["weak_points"],
            "本期数据不足以判断薄弱点。完成更多练习后，这里会指出具体卡在哪。",
        ),
        _section_lists(
            "五、复习建议",
            analysis["suggestions"],
            "先导入一份资料并完成一套练习，下一份报告就能给出针对性建议。",
        ),
    ]
    # 用小节内 `.strip()` + 双换行拼接：各段函数自己收尾时留或不留空行都不会粘在一起
    body = "\n\n".join(section.strip() for section in sections)
    text = "\n\n".join(head) + "\n\n" + body
    if source == "rule":
        text += "\n\n---\n\n_本期分析由规则生成（模型未配置或调用失败），数字部分不受影响。_"
    return text + "\n"


# ---------- 对外 ----------


async def generate_report(
    db: Session,
    window_days: int = 7,
    *,
    trigger: str = "manual",
) -> ReviewReport:
    """生成并保存一份回顾报告。

    `trigger` 记录来源（manual / schedule），定时任务生成的报告在列表里要能区分。
    """
    if window_days not in WINDOWS:
        raise ReviewError(f"时间窗只能是 {', '.join(str(w) for w in WINDOWS)} 天")

    period_start, period_end, metrics = collect_window(db, window_days=window_days)
    wrong = wrong_summary(db, days=window_days)
    top_wrong = _top_wrong(db, window_days)

    active = _has_activity(metrics, wrong)
    if active:
        analysis, source = await _analyze(metrics, wrong, top_wrong)
    else:
        analysis, source = review_prompts.empty_analysis(), "empty"

    metrics["wrong"] = {
        "total": wrong.get("total", 0),
        "total_wrong_count": wrong.get("total_wrong_count", 0),
        "repeat_count": wrong.get("repeat_count", 0),
        "by_topic": wrong.get("by_topic", []),
        "by_type": wrong.get("by_type", []),
        "top": top_wrong,
    }
    metrics["analysis_source"] = source
    metrics["trigger"] = trigger
    metrics["has_activity"] = active
    metrics["push"] = {"status": "pending" if active else "skipped", "detail": "", "at": None}

    markdown = _compose_markdown(
        metrics, wrong, top_wrong, analysis, source, period_start, period_end, window_days
    )

    report = ReviewReport(
        id=str(uuid.uuid4()),
        window_days=window_days,
        period_start=period_start,
        period_end=period_end,
        markdown=markdown,
        metrics=metrics,
    )
    SqlAlchemyReviewRepository(db).save(report)
    return report


def list_reports(db: Session, limit: int = 50) -> list[ReviewReport]:
    return SqlAlchemyReviewRepository(db).list(limit=limit)


def get_report(db: Session, report_id: str) -> ReviewReport:
    report = SqlAlchemyReviewRepository(db).get(report_id)
    if report is None:
        raise ReviewNotFound(report_id)
    return report


def remove_report(db: Session, report_id: str) -> None:
    get_report(db, report_id)
    SqlAlchemyReviewRepository(db).delete(report_id)


def mark_push(db: Session, report: ReviewReport, ok: bool, detail: str) -> ReviewReport:
    """记录推送结果。

    失败也要落库：界面要能显示「上次推送失败 + 原因」，报告本身不能丢。
    """
    repo = SqlAlchemyReviewRepository(db)
    metrics = dict(report.metrics or {})
    metrics["push"] = {
        "status": "sent" if ok else "failed",
        "detail": detail,
        "at": to_iso(utcnow_naive()),
    }
    report.metrics = metrics
    repo.save(report)
    return report


def push_state(db: Session) -> dict[str, Any]:
    """最近一次推送的状态，给回顾页与概览做提示。"""
    latest = SqlAlchemyReviewRepository(db).latest()
    if latest is None:
        return {"status": "none", "detail": "", "at": None, "report_id": None}
    push = (latest.metrics or {}).get("push", {})
    return {
        "status": push.get("status", "none"),
        "detail": push.get("detail", ""),
        "at": push.get("at"),
        "report_id": latest.id,
    }
