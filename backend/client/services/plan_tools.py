"""规划 Agent 的五个工具（M7）。

设计边界（见 docs/AGENT_DECISION.md §3.6）：

- 四个只读、一个写入。唯一写操作是 `save_plan`，不改笔记/错题本/题库。
- 出题不自己写，只调 `practice_service`——既有校验与重试不能绕过。
- 所有数字都从仓储/统计里取，工具不做任何推算。
- 返回值一律截断到 2000 字符：错题原文很容易把上下文撑爆。

工具本身不抛到接口层：失败抛 `ToolError`，由循环翻译成
`{"ok": false, "error": ...}` 交回模型，这是「失败能自己换一条路」的实现方式。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from shared.domain.models.entities import (
    UNCATEGORIZED_ID,
    UNCATEGORIZED_NAME,
    QuestionType,
    StudyPlan,
)
from shared.repositories.sqlalchemy_repo import (
    SqlAlchemyStudyPlanRepository,
    SqlAlchemyTopicRepository,
    SqlAlchemyWrongAnswerRepository,
)
from shared.repositories.stats_repo import StatsRepository
import client.services.practice_service as practice_service

# 工具返回值上限。单个错题原文、30 道题的快照都可能超过它，
# 超过之后模型只能看到被截断的 JSON——但结构还在，仍可据此写下一句。
MAX_RESULT_CHARS = 2000
MAX_SAMPLE_CHARS = 120
MAX_PLAN_CHARS = 8000
MIN_PLAN_CHARS = 200

TOOL_LABELS = {
    "get_progress": "查看学习进度",
    "list_weak_points": "分析薄弱知识点",
    "list_wrong_answers": "翻看错题",
    "generate_practice": "生成针对性练习",
    "save_plan": "写下计划",
}


class ToolError(ValueError):
    """工具调用失败。消息会原样交给模型，所以要写成人能读懂的一句话。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(minimum, min(maximum, number))


def _clip_text(text: str, limit: int = MAX_SAMPLE_CHARS) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _fit(data: dict) -> dict:
    """把返回结构压到上限以内，但保留字段名。

    先裁列表（模型最需要的是「有哪几项」，不是每一项的全部细节），
    再截字符串，最后兜底。截断只改变信息量，不改变结构——
    结构一变，模型就会开始瞎猜字段。
    """
    text = json.dumps(data, ensure_ascii=False)
    if len(text) <= MAX_RESULT_CHARS:
        return data

    trimmed = dict(data)
    for key, value in trimmed.items():
        if isinstance(value, list) and len(value) > 1:
            trimmed[key] = value[: max(1, len(value) // 2)]
    if len(json.dumps(trimmed, ensure_ascii=False)) <= MAX_RESULT_CHARS:
        trimmed["truncated"] = True
        return trimmed

    for key, value in list(trimmed.items()):
        if isinstance(value, str) and len(value) > 200:
            trimmed[key] = value[:200] + "…"
        elif isinstance(value, list):
            trimmed[key] = value[:2]
    trimmed["truncated"] = True

    if len(json.dumps(trimmed, ensure_ascii=False)) > MAX_RESULT_CHARS:
        return {"truncated": True, "note": "结果过长，已省略明细，请据此判断方向"}
    return trimmed


# ---------- 只读工具 ----------


def get_progress(db, space_id: str) -> dict:
    """概览统计。数字全部原样取自 StatsRepository，工具不做加减乘除。"""
    summary = StatsRepository(db, space_id).summary()
    documents = summary["documents"]
    practice = summary["practice"]
    wrong = summary["wrong"]
    return _fit(
        {
            "documents": {
                "total": documents["total"],
                "ready": documents["ready"],
                "processing": documents["processing"],
                "failed": documents["failed"],
                "chunks": documents["chunks"],
                "added_last_7d": documents["added_last_7d"],
            },
            "practice": {
                "sessions": practice["sessions"],
                "finished": practice["finished"],
                "avg_score": round(float(practice["avg_score"]), 1),
                "finished_last_7d": practice["finished_last_7d"],
            },
            "wrong": {
                "total": wrong["total"],
                "added_last_7d": wrong["added_last_7d"],
            },
            "notes": summary["notes"],
            "as_of": _now_iso(),
        }
    )


def list_weak_points(db, space_id: str, limit: Any = 5) -> dict:
    """按主题聚合薄弱点。

    错误率来自 `wrong_summary`（分母是该主题历史作答数），不是拿错题条数直接排——
    只练过一个主题的人，那个主题必然排第一，这种排序没有信息量。
    """
    count = _as_int(limit, 5, 1, 10)
    summary = practice_service.wrong_summary(db, space_id=space_id)
    buckets = summary.get("by_topic") or []
    if not buckets:
        return {"items": [], "note": "错题本还是空的，没有可分析的薄弱点"}

    topics = {topic.id: topic for topic in SqlAlchemyTopicRepository(db).list(space_id)}
    samples: dict[str, list[dict]] = {}
    for item in SqlAlchemyWrongAnswerRepository(db).list(space_id, sort="count", limit=300):
        key = item.topic_id or UNCATEGORIZED_ID
        bucket = samples.setdefault(key, [])
        if len(bucket) < 2:
            bucket.append(
                {
                    "stem": _clip_text(item.question.stem),
                    "wrong_count": item.wrong_count,
                    "knowledge_point": item.question.knowledge_point,
                }
            )

    items = []
    for bucket in buckets[:count]:
        key = bucket["topic_id"]
        topic = topics.get(key)
        items.append(
            {
                "topic": bucket["name"] if bucket.get("name") else (topic.name if topic else UNCATEGORIZED_NAME),
                "topic_id": key,
                "wrong_questions": bucket["question_count"],
                "wrong_count": bucket["wrong_count"],
                "answered": bucket["answered"],
                "error_rate": bucket["error_rate"],
                "samples": samples.get(key, []),
            }
        )
    return _fit({"items": items, "total_weak_topics": len(buckets)})


def list_wrong_answers(db, space_id: str, limit: Any = 10) -> dict:
    """待复习错题：题干、你的答案、正确答案、错误次数。"""
    count = _as_int(limit, 10, 1, 20)
    rows = SqlAlchemyWrongAnswerRepository(db).list(space_id, sort="count", limit=count)
    if not rows:
        return {"items": [], "note": "错题本还是空的"}

    topics = {topic.id: topic.name for topic in SqlAlchemyTopicRepository(db).list(space_id)}
    items = []
    for row in rows:
        question = row.question
        items.append(
            {
                "stem": _clip_text(question.stem, 160),
                "type": question.type.value,
                "your_answer": _clip_text(row.last_response or "（未作答）", 60),
                "correct_answer": _clip_text(question.answer, 60),
                "wrong_count": row.wrong_count,
                "knowledge_point": question.knowledge_point,
                "topic": topics.get(row.topic_id or UNCATEGORIZED_ID, UNCATEGORIZED_NAME),
                "last_wrong_at": row.last_wrong_at.isoformat() if row.last_wrong_at else None,
            }
        )
    return _fit({"items": items, "total": len(rows)})


# ---------- 动作工具 ----------


async def generate_practice(
    db,
    *,
    space_id: str,
    topic_id: str | None = None,
    counts: dict[str, int] | None = None,
    difficulty: str = "medium",
) -> dict:
    """出一套针对薄弱点的题。

    直接 await `run_generation`，不经过 job 轮询：Agent 这一轮必须拿到题号才能写计划，
    轮询只会把「一步」拆成两条异步链路。出题失败（材料不够）抛 ToolError，
    模型据此改走「只给复习建议，不出题」。
    """
    raw_counts = counts or {"single_choice": 3, "multi_choice": 1, "true_false": 2}
    planned: dict[QuestionType, int] = {}
    for key, value in raw_counts.items():
        try:
            question_type = QuestionType(key)
        except ValueError:
            continue
        planned[question_type] = _as_int(value, 0, 0, 30)
    if not any(planned.values()):
        raise ToolError("题型配比无效：可选 single_choice / multi_choice / true_false / short_answer")

    if difficulty not in ("easy", "medium", "hard"):
        difficulty = "medium"

    try:
        if topic_id:
            job = practice_service.start_generation(
                db,
                space_id=space_id,
                counts=planned,
                topic_ids=[topic_id],
                difficulty=difficulty,
            )
        else:
            # 没给主题时用最近 30 天的资料：宁可范围宽一点，也不要因为「没指定范围」直接失败
            job = practice_service.start_generation(
                db, space_id=space_id, counts=planned, days=30, difficulty=difficulty
            )
    except practice_service.PracticeError as exc:
        raise ToolError(str(exc)) from exc

    await practice_service.run_generation(job["id"])
    snapshot = practice_service.job_snapshot(job["id"])

    if snapshot["state"] == "failed":
        raise ToolError(snapshot.get("message") or "出题失败")
    if not snapshot.get("produced"):
        raise ToolError("这次没能出题，材料可能不够")

    return _fit(
        {
            "session_id": snapshot["session_id"],
            "produced": snapshot["produced"],
            "requested": snapshot["requested"],
            "warnings": snapshot.get("warnings") or [],
            "note": "题目已存为草稿，用户可以在练习页作答",
        }
    )


def save_plan(
    db, *, space_id: str, markdown: str, goal: str = "", meta: dict | None = None
) -> dict:
    """落库。整轮唯一写操作，位置显眼、参数唯一，便于审计。"""
    text = (markdown or "").strip()
    if len(text) < MIN_PLAN_CHARS:
        raise ToolError("计划太短了：至少写出一周内每天做什么，不少于 200 字")
    if len(text) > MAX_PLAN_CHARS:
        raise ToolError(f"计划过长（{len(text)} 字），请压缩到 {MAX_PLAN_CHARS} 字以内")

    plan = StudyPlan(
        id=_plan_id(),
        markdown=text,
        goal=(goal or "")[:200],
        meta=meta or {},
        space_id=space_id,
    )
    saved = SqlAlchemyStudyPlanRepository(db).save(plan)
    return {"plan_id": saved.id, "chars": len(text)}


def _plan_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]


# 名称 → 调用。只读工具全在表里，统一 async 包装，循环里不必判断同步/异步。
# `save_plan` 不在表里：它要附带过程快照（meta），由循环直接调用，见 plan_agent.py。
TOOLS = {
    "get_progress": lambda db, **kw: _sync(get_progress, db, **kw),
    "list_weak_points": lambda db, **kw: _sync(list_weak_points, db, **kw),
    "list_wrong_answers": lambda db, **kw: _sync(list_wrong_answers, db, **kw),
    "generate_practice": lambda db, **kw: generate_practice(db, **kw),
}


async def _sync(fn, db, **kwargs):
    return fn(db, **kwargs)
