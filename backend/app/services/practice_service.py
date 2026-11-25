"""出题与草稿业务（FR-04）。

链路：定范围 → 取片段 → 分题型调用出题器 → 校验来源 → 存草稿。
与笔记起草同样的取舍：LLM 慢，所以接口只登记任务，生成在后台跑，前端轮询。

范围三条入口可叠加，一律取交集：
- 主题：该主题下全部资料（默认入口）
- 资料：手动勾选的文档
- 近期：最近 N 天导入的资料

资料不足时抛错而不是硬出题：`total * MIN_CHARS_PER_QUESTION` 是最低材料量，
低于它，模型只能靠编。凑不够数量的题型会降级并写进 warnings，
让用户知道「少的那几道」是材料的问题，不是系统坏了。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain.models.entities import (
    UNCATEGORIZED_ID,
    UNCATEGORIZED_NAME,
    AnswerRecord,
    Document,
    PracticeSession,
    PracticeStatus,
    Question,
    QuestionType,
    WrongAnswer,
)
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.generators.base import (
    GenerationChunk,
    GenerationContext,
    QuestionGenConfig,
)
from app.infrastructure.generators.llm_generator import LLMTypedGenerator
from app.infrastructure.llm.openai_compat import build_llm
from app.repositories.sqlalchemy_repo import (
    SqlAlchemyChunkRepository,
    SqlAlchemyDocumentRepository,
    SqlAlchemyPracticeRepository,
    SqlAlchemyTopicRepository,
    SqlAlchemyWrongAnswerRepository,
)
from app.services import grading, practice_prompts

logger = logging.getLogger(__name__)

MAX_QUESTIONS = 30
MAX_CHUNKS = 60
MAX_CHUNKS_PER_DOC = 8
MAX_MATERIAL_CHARS = 18_000
MIN_CHARS_PER_QUESTION = 100
SNIPPET_CHARS = 240

TYPE_ORDER = [
    QuestionType.SINGLE_CHOICE,
    QuestionType.MULTI_CHOICE,
    QuestionType.TRUE_FALSE,
    QuestionType.SHORT_ANSWER,
]


class PracticeError(ValueError):
    """组卷前置条件不满足（范围为空、资料不足、参数越界）。"""


class PracticeNotFound(PracticeError):
    pass


# ---------- 组卷任务 ----------

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_JOB_KEEP = 20


def plan_counts(raw: dict[QuestionType, int]) -> dict[QuestionType, int]:
    """清洗题型配比：丢掉 0 与未知项，总量限制在 1~30。"""
    counts: dict[QuestionType, int] = {}
    for question_type in TYPE_ORDER:
        value = int(raw.get(question_type, 0) or 0)
        if value > 0:
            counts[question_type] = min(value, MAX_QUESTIONS)
    total = sum(counts.values())
    if not counts or total < 1:
        raise PracticeError("至少选择一种题型并设置数量")
    if total > MAX_QUESTIONS:
        raise PracticeError(f"单次最多生成 {MAX_QUESTIONS} 道题，当前要求 {total} 道")
    return counts


def start_generation(
    db,
    *,
    counts: dict[QuestionType, int],
    topic_ids: list[str] | None = None,
    document_ids: list[str] | None = None,
    days: int | None = None,
    difficulty: str = "medium",
) -> dict[str, Any]:
    """校验范围与资料，登记任务并返回公开快照。生成在 `run_generation` 中执行。"""
    from app.config import settings

    planned = plan_counts(counts)
    total = sum(planned.values())

    if not topic_ids and not document_ids and not days:
        raise PracticeError("请选择出题范围：主题、资料，或最近 N 天新增")

    docs = _resolve_documents(db, topic_ids=topic_ids, document_ids=document_ids, days=days)
    if not docs:
        if days:
            raise PracticeError(f"最近 {days} 天没有已完成的资料，请先导入并等待索引完成")
        raise PracticeError("选定范围内没有已完成的资料，请先导入并等待索引完成")

    context = _build_context(db, docs)
    chars = sum(len(chunk.text) for chunk in context.chunks)
    required = total * MIN_CHARS_PER_QUESTION
    if chars < required:
        raise PracticeError(
            f"资料不足：{len(docs)} 份资料共约 {chars} 字，出 {total} 道题至少需要约 "
            f"{required} 字。可以减少题量，或把更多资料加入范围。"
        )

    if not settings.llm_configured:
        raise PracticeError("尚未配置大模型 API Key，请先在设置页填写")

    job_id = str(uuid.uuid4())
    job: dict[str, Any] = {
        "id": job_id,
        "state": "running",
        "session_id": None,
        "message": f"正在依据 {len(docs)} 份资料出题…",
        "warnings": [],
        "requested": total,
        "produced": 0,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        # 以下为内部载荷，不进接口快照
        "_context": context,
        "_counts": planned,
        "_difficulty": difficulty,
        "_meta": {
            "topic_ids": list(topic_ids or []),
            "document_ids": list(document_ids or []),
            "days": days,
            "counts": {key.value: value for key, value in planned.items()},
            "difficulty": difficulty,
            "requested": total,
            "documents": [{"id": doc.id, "name": doc.name} for doc in docs[:12]],
            "document_total": len(docs),
        },
    }
    with _LOCK:
        _JOBS[job_id] = job
        _trim_jobs()
    return _public(job)


def job_snapshot(job_id: str) -> dict[str, Any]:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise PracticeNotFound("出题任务不存在或已过期")
        return _public(job)


def _trim_jobs() -> None:
    if len(_JOBS) <= _JOB_KEEP:
        return
    for key in list(_JOBS)[: len(_JOBS) - _JOB_KEEP]:
        _JOBS.pop(key, None)


def _public(job: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in job.items() if not key.startswith("_")}


async def run_generation(job_id: str) -> None:
    """后台出题。自己开 Session；多个题型并发请求，单题型失败不拖垮整场。"""
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return
        payload = dict(job)

    db = SessionLocal()
    try:
        llm = build_llm()
        context: GenerationContext = payload["_context"]
        planned: dict[QuestionType, int] = payload["_counts"]
        difficulty = payload["_difficulty"]

        tasks = [
            LLMTypedGenerator(question_type, llm=llm).generate(
                context,
                QuestionGenConfig(total=count, types=[question_type], difficulty=difficulty),
            )
            for question_type, count in planned.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        questions: list[Question] = []
        warnings: list[str] = []
        seen_stems: set[str] = set()
        for (question_type, count), result in zip(planned.items(), results, strict=True):
            label = practice_prompts.TYPE_SPECS[question_type]["label"]
            if isinstance(result, Exception):
                warnings.append(f"{label}出题失败：{_explain(str(result) or result.__class__.__name__)}")
                continue
            if len(result) < count:
                warnings.append(f"{label}计划 {count} 道，材料只支持 {len(result)} 道")
            for question in result:
                key = "".join(char for char in question.stem if char.isalnum())
                if key in seen_stems:
                    continue
                seen_stems.add(key)
                questions.append(question)

        if not questions:
            detail = "；".join(warnings) or "模型没有返回可用题目"
            raise PracticeError(f"资料不足，没能出题：{detail}")

        meta = dict(payload["_meta"])
        meta.update(
            {
                "produced": len(questions),
                "warnings": warnings,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        session = PracticeSession(
            id=str(uuid.uuid4()),
            questions=questions,
            status=PracticeStatus.DRAFT,
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            meta=meta,
        )
        SqlAlchemyPracticeRepository(db).save(session)

        message = f"已生成 {len(questions)} 道题"
        if len(questions) < payload["requested"]:
            message += f"（计划 {payload['requested']} 道，部分题型材料不足）"
        message += "，可删掉不要的题再开始作答"

        with _LOCK:
            job.update(
                state="done",
                session_id=session.id,
                message=message,
                warnings=warnings,
                produced=len(questions),
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
        logger.info("出题完成：%s（%s 道）", session.id, len(questions))
    except Exception as exc:  # noqa: BLE001 - 失败原因要能显示给用户
        message = _explain(str(exc) or exc.__class__.__name__)
        logger.exception("出题失败")
        with _LOCK:
            job.update(
                state="failed",
                message=message,
                finished_at=datetime.now(timezone.utc).isoformat(),
            )
    finally:
        db.close()


def _explain(message: str) -> str:
    lowered = message.lower()
    if "401" in message or "unauthorized" in lowered or "api key" in lowered:
        return "模型鉴权失败（401）：请检查设置页的 API Key"
    if "timeout" in lowered or "timed out" in lowered:
        return "模型响应超时：可减少题量后重试"
    if "429" in message:
        return "模型额度不足或请求过快（429）：请稍后重试"
    return message[:400]


# ---------- 范围与材料 ----------


def _resolve_documents(
    db,
    *,
    topic_ids: list[str] | None,
    document_ids: list[str] | None,
    days: int | None,
) -> list[Document]:
    """三条入口取交集；只保留已就绪的资料。"""
    repo = SqlAlchemyDocumentRepository(db)
    docs = repo.list(limit=2000, topic_ids=list(topic_ids) if topic_ids else None)

    if document_ids:
        wanted = set(document_ids)
        docs = [doc for doc in docs if doc.id in wanted]

    if days:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=max(1, days))
        docs = [doc for doc in docs if doc.created_at >= cutoff]

    ready = [doc for doc in docs if doc.status.value == "ready" and doc.chunk_count > 0]
    # 多文档时按名称稳定排序，保证同一范围两次出题的取材一致
    return sorted(ready, key=lambda doc: (doc.name, doc.id))


def _build_context(db, docs: list[Document]) -> GenerationContext:
    """轮转取片段：先每份取第 1 段，再取第 2 段……避免排在前面的大文档吃掉全部配额。"""
    chunk_repo = SqlAlchemyChunkRepository(db)
    per_doc: list[tuple[Document, list]] = []
    for doc in docs:
        chunks = chunk_repo.list_by_document(doc.id)
        if chunks:
            per_doc.append((doc, _sample(chunks, MAX_CHUNKS_PER_DOC)))

    picked: list[GenerationChunk] = []
    depth = 0
    while len(picked) < MAX_CHUNKS and any(depth < len(chunks) for _, chunks in per_doc):
        for doc, chunks in per_doc:
            if depth >= len(chunks):
                continue
            chunk = chunks[depth]
            picked.append(
                GenerationChunk(
                    chunk_id=chunk.id,
                    text=chunk.text,
                    document_id=doc.id,
                    document_name=doc.name,
                    topic_id=doc.topic_id,
                    page=chunk.page,
                )
            )
            if len(picked) >= MAX_CHUNKS:
                break
        depth += 1

    blocks: list[str] = []
    kept: list[GenerationChunk] = []
    used = 0
    for chunk in picked:
        text = chunk.text.strip()
        if not text:
            continue
        if used + len(text) > MAX_MATERIAL_CHARS:
            break
        used += len(text)
        kept.append(chunk)
        page = f" 第 {chunk.page} 页" if chunk.page else ""
        blocks.append(f"[chunk:{chunk.chunk_id}] 《{chunk.document_name}》{page}\n{text}")

    topic_repo = SqlAlchemyTopicRepository(db)
    names = [
        topic.name
        for topic in topic_repo.list()
        if topic.id in {doc.topic_id for doc in docs}
    ]
    return GenerationContext(
        chunks=kept, material_text="\n\n---\n\n".join(blocks), topic_names=names
    )


def _sample(chunks: list, limit: int) -> list:
    """长文档等距抽样：只取开头几段会把整份资料的结论挡在门外。"""
    if len(chunks) <= limit:
        return chunks
    step = len(chunks) / limit
    return [chunks[min(int(index * step), len(chunks) - 1)] for index in range(limit)]


# ---------- 草稿管理 ----------


def get_session(db, session_id: str) -> PracticeSession:
    session = SqlAlchemyPracticeRepository(db).get(session_id)
    if session is None:
        raise PracticeNotFound("练习不存在或已删除")
    return session


def list_sessions(db, limit: int = 20) -> list[PracticeSession]:
    return SqlAlchemyPracticeRepository(db).list_recent(limit=limit)


def remove_question(db, session_id: str, question_id: str) -> PracticeSession:
    """草稿态删题。提交后题目快照是成绩的一部分，不再允许改。"""
    repo = SqlAlchemyPracticeRepository(db)
    session = repo.get(session_id)
    if session is None:
        raise PracticeNotFound("练习不存在或已删除")
    if session.status is not PracticeStatus.DRAFT:
        raise PracticeError("练习已开始作答，不能再删题")
    remaining = [question for question in session.questions if question.id != question_id]
    if len(remaining) == len(session.questions):
        raise PracticeNotFound("题目不存在或已删除")
    session.questions = remaining
    repo.save(session)
    return session


def delete_session(db, session_id: str) -> None:
    session = SqlAlchemyPracticeRepository(db).get(session_id)
    if session is None:
        raise PracticeNotFound("练习不存在或已删除")
    if session.status is PracticeStatus.SUBMITTED:
        # 已提交的记录要留给错题本与回顾统计，不提供删除
        raise PracticeError("已提交的练习不能删除")
    SqlAlchemyPracticeRepository(db).delete(session_id)


# ---------- 作答与判分（FR-05） ----------


def _now_naive() -> datetime:
    """库里统一存 naive UTC：写入与比较都在 UTC，只在展示层转本地时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ensure_session(db, session_id: str) -> PracticeSession:
    session = SqlAlchemyPracticeRepository(db).get(session_id)
    if session is None:
        raise PracticeNotFound("练习不存在或已删除")
    return session


def start_session(db, session_id: str) -> PracticeSession:
    """草稿 → 作答中。幂等：重复进入不会重置进度，这是断点续答的前提。"""
    repo = SqlAlchemyPracticeRepository(db)
    session = _ensure_session(db, session_id)
    if session.status is PracticeStatus.SUBMITTED:
        raise PracticeError("这套练习已经提交过了")
    if session.status is PracticeStatus.IN_PROGRESS:
        return session
    if not session.questions:
        raise PracticeError("这套练习没有题目，先重新生成一套")

    session.status = PracticeStatus.IN_PROGRESS
    session.meta = {**session.meta, "answer_started_at": _now_naive().isoformat()}
    repo.save(session)
    return session


def save_answer(
    db,
    session_id: str,
    question_id: str,
    *,
    response: str = "",
    duration_seconds: float = 0.0,
    self_grade: str | None = None,
) -> PracticeSession:
    """单题自动保存。

    每一次点选都落库，不是因为怕丢数据，而是「中途退出还能接着答」这条要求
    只能靠不断保存进度来实现；攒到提交才写，关页面就全没了。
    """
    repo = SqlAlchemyPracticeRepository(db)
    session = _ensure_session(db, session_id)
    if session.status is PracticeStatus.SUBMITTED:
        raise PracticeError("这套练习已经提交，不能再改答案")

    question = next((item for item in session.questions if item.id == question_id), None)
    if question is None:
        raise PracticeNotFound("题目不存在或已被删除")

    if session.status is PracticeStatus.DRAFT:
        session.status = PracticeStatus.IN_PROGRESS
        session.meta = {**session.meta, "answer_started_at": _now_naive().isoformat()}

    result = grading.grade(question, response, self_grade=self_grade)
    kept = [item for item in session.answers if item.question_id != question_id]
    if result.answered:
        kept.append(
            AnswerRecord(
                question_id=question_id,
                response=result.response,
                is_correct=result.is_correct,
                duration_seconds=max(0.0, float(duration_seconds or 0.0)),
            )
        )
    # 按题目顺序存放：前端回显进度时不必自己排序
    order = {item.id: index for index, item in enumerate(session.questions)}
    session.answers = sorted(kept, key=lambda item: order.get(item.question_id, 10**6))
    repo.save(session)
    return session


def submit_session(
    db, session_id: str, answers: list[dict]
) -> tuple[PracticeSession, grading.SessionScore]:
    """判分并落库，同时把答错的题写进错题本。

    合并策略：以本次提交的作答为准，提交里缺失但之前自动保存过的沿用旧记录。
    这样「答到一半关页面 → 重开只补了两题就交卷」不会把前面的答案判成未答。
    """
    repo = SqlAlchemyPracticeRepository(db)
    session = _ensure_session(db, session_id)
    if session.status is PracticeStatus.SUBMITTED:
        raise PracticeError("这套练习已经提交过了")
    if not session.questions:
        raise PracticeError("这套练习没有题目")

    question_map = {item.id: item for item in session.questions}
    incoming = {
        item.get("question_id"): item
        for item in answers
        if item.get("question_id") in question_map
    }
    saved = {item.question_id: item for item in session.answers}

    records: list[AnswerRecord] = []
    results: list[grading.GradeResult] = []
    wrongs: list[tuple[Question, str]] = []

    for question in session.questions:
        raw = incoming.get(question.id)
        if raw is None and question.id in saved:
            previous = saved[question.id]
            results.append(
                grading.GradeResult(
                    question_id=question.id,
                    response=previous.response,
                    correct_answer=grading.normalize(question.type, question.answer),
                    is_correct=previous.is_correct,
                    answered=True,
                    self_graded=not grading.auto_graded(question.type),
                )
            )
            records.append(previous)
            if not previous.is_correct:
                wrongs.append((question, previous.response))
            continue

        response = str(raw.get("response") or "") if raw else ""
        self_grade = raw.get("self_grade") if raw else None
        duration = max(0.0, float(raw.get("duration_seconds") or 0.0)) if raw else 0.0
        result = grading.grade(question, response, self_grade=self_grade)
        results.append(result)
        if not result.answered:
            continue
        records.append(
            AnswerRecord(
                question_id=question.id,
                response=result.response,
                is_correct=result.is_correct,
                duration_seconds=duration,
            )
        )
        if not result.is_correct:
            wrongs.append((question, result.response))

    elapsed = _elapsed_seconds(session, records)
    score = grading.summarize(session.questions, results, elapsed)
    finished_at = _now_naive()

    session.answers = records
    session.score = score.accuracy
    session.status = PracticeStatus.SUBMITTED
    session.finished_at = finished_at
    session.meta = {
        **session.meta,
        "result": {
            "total": score.total,
            "answered": score.answered,
            "correct": score.correct,
            "accuracy": score.accuracy,
            "duration_seconds": score.duration_seconds,
        },
    }
    repo.save(session)

    if wrongs:
        wrong_repo = SqlAlchemyWrongAnswerRepository(db)
        for question, response in wrongs:
            wrong_repo.upsert(
                WrongAnswer(question=question, last_response=response, last_wrong_at=finished_at)
            )
    return session, score


def _elapsed_seconds(session: PracticeSession, records: list[AnswerRecord]) -> float:
    """优先用「开始作答 → 提交」的真实时长；没有记录（旧数据/异常）时退化为逐题耗时求和。"""
    started = session.meta.get("answer_started_at")
    if isinstance(started, str) and started:
        try:
            delta = _now_naive() - datetime.fromisoformat(started)
            if delta.total_seconds() > 0:
                return delta.total_seconds()
        except ValueError:
            pass
    return sum(item.duration_seconds for item in records)


# ---------- 错题本与重练（FR-06） ----------


def list_wrong_answers(
    db,
    *,
    topic_id: str | None = None,
    document_id: str | None = None,
    question_type: str | None = None,
    days: int | None = None,
    min_wrong: int | None = None,
    sort: str = "recent",
    limit: int = 200,
) -> list[WrongAnswer]:
    return SqlAlchemyWrongAnswerRepository(db).list(
        topic_id=topic_id,
        document_id=document_id,
        question_type=question_type,
        days=days,
        min_wrong=min_wrong,
        sort=sort,
        limit=limit,
    )


def remove_wrong_answer(db, wrong_id: str) -> None:
    """「这题我会了」——手动移出。自动移出没有可信依据，交给用户判断。"""
    if not SqlAlchemyWrongAnswerRepository(db).delete(wrong_id):
        raise PracticeNotFound("错题不存在或已移出")


def start_retry(
    db, *, wrong_ids: list[str] | None = None, limit: int = 10
) -> PracticeSession:
    """从错题组卷重练。

    不调模型：题目本身已经有了，重练的意义是「同一道题再做一遍」。
    重新生成只会换一题、换一套说法，反而验不出是不是真会了。
    """
    repo = SqlAlchemyWrongAnswerRepository(db)
    if wrong_ids:
        items = repo.list_by_ids(wrong_ids)
        order = {wrong_id: index for index, wrong_id in enumerate(wrong_ids)}
        items.sort(key=lambda item: order.get(item.id, 10**6))
    else:
        items = repo.list(sort="count", limit=max(1, min(limit, MAX_QUESTIONS)))

    if not items:
        raise PracticeError("没有可重练的错题")
    items = items[:MAX_QUESTIONS]

    now = _now_naive()
    questions = [item.question for item in items]
    session = PracticeSession(
        id=str(uuid.uuid4()),
        questions=questions,
        status=PracticeStatus.IN_PROGRESS,
        started_at=now,
        meta={
            "kind": "retry",
            "source": "wrong_answers",
            "wrong_ids": [item.id for item in items],
            "requested": len(questions),
            "produced": len(questions),
            "answer_started_at": now.isoformat(),
            "topic_ids": [],
            "document_ids": [],
            "days": None,
            "counts": {question.type.value: 0 for question in questions},
            "difficulty": None,
            "warnings": [],
        },
    )
    for question in questions:
        session.meta["counts"][question.type.value] += 1
    SqlAlchemyPracticeRepository(db).save(session)
    return session


def wrong_summary(db, *, days: int | None = None) -> dict[str, Any]:
    """错题聚合。

    「错得最多的主题」用两个数字回答：错题里有多少条，以及该主题历史作答的错误率。
    只给错题条数会骗人——只练过一个主题的人，那个主题必然排第一。
    """
    items = SqlAlchemyWrongAnswerRepository(db).list(days=days, limit=2000)
    topics = {topic.id: topic for topic in SqlAlchemyTopicRepository(db).list()}

    answered: dict[str, int] = {}
    correct: dict[str, int] = {}
    cutoff = _now_naive() - timedelta(days=days) if days else None
    for practice in SqlAlchemyPracticeRepository(db).list_recent(limit=500):
        if practice.status is not PracticeStatus.SUBMITTED:
            continue
        if cutoff is not None and practice.finished_at and practice.finished_at < cutoff:
            continue
        question_map = {question.id: question for question in practice.questions}
        for record in practice.answers:
            question = question_map.get(record.question_id)
            if question is None:
                continue
            key = question.topic_id or UNCATEGORIZED_ID
            answered[key] = answered.get(key, 0) + 1
            if record.is_correct:
                correct[key] = correct.get(key, 0) + 1

    by_topic: dict[str, dict[str, Any]] = {}
    for item in items:
        key = item.topic_id or UNCATEGORIZED_ID
        bucket = by_topic.setdefault(
            key, {"topic_id": key, "wrong_count": 0, "question_count": 0}
        )
        bucket["wrong_count"] += item.wrong_count
        bucket["question_count"] += 1

    for key, bucket in by_topic.items():
        topic = topics.get(key)
        bucket["name"] = topic.name if topic else UNCATEGORIZED_NAME
        bucket["color"] = topic.color if topic else "#8a8f98"
        total = answered.get(key, 0)
        right = correct.get(key, 0)
        bucket["answered"] = total
        bucket["correct"] = right
        bucket["error_rate"] = round((total - right) / total * 100, 1) if total else 0.0

    by_type: dict[str, int] = {}
    for item in items:
        key = item.question.type.value
        by_type[key] = by_type.get(key, 0) + 1

    return {
        "total": len(items),
        "total_wrong_count": sum(item.wrong_count for item in items),
        "repeat_count": sum(1 for item in items if item.wrong_count >= 2),
        "by_topic": sorted(
            by_topic.values(), key=lambda bucket: (-bucket["wrong_count"], bucket["name"])
        ),
        "by_type": [
            {
                "type": question_type.value,
                "label": practice_prompts.TYPE_SPECS[question_type]["label"],
                "count": count,
            }
            for question_type, count in sorted(
                ((QuestionType(key), count) for key, count in by_type.items()),
                key=lambda pair: TYPE_ORDER.index(pair[0])
                if pair[0] in TYPE_ORDER
                else len(TYPE_ORDER),
            )
        ],
    }
