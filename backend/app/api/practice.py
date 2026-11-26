"""练习接口（FR-04）。

出题与笔记起草同构：POST 立刻返回 job，前端轮询 /practice/jobs/{id}。
一次性长请求在模型慢的时候只会让界面卡死，还会被浏览器或代理掐断。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.models import (
    UNCATEGORIZED_ID,
    UNCATEGORIZED_NAME,
    PracticeSession,
    Question,
    WrongAnswer,
)
from app.db import get_db
from app.store import Topics
from app.schemas.practice import (
    AnswerIn,
    AnswerOut,
    GenerateIn,
    GenerationJobOut,
    PracticeMetaOut,
    QuestionOut,
    QuestionTypeOut,
    RetryIn,
    SessionBriefOut,
    SessionOut,
    SessionStatsOut,
    SourceOut,
    SubmitIn,
    WrongOut,
    WrongSummaryOut,
)
from app.services import practice_prompts, practice_service
from app.services.practice_service import PracticeError, PracticeNotFound

router = APIRouter(prefix="/practice", tags=["practice"])

_TYPE_HINTS = {
    "single_choice": "四选一，自动判分",
    "multi_choice": "多选少选都不得分",
    "true_false": "一句话判对错",
    "short_answer": "对照参考答案自评",
}

_DIFFICULTIES = [
    {"key": "easy", "label": "偏易", "hint": "概念与直接结论"},
    {"key": "medium", "label": "中等", "hint": "理解与辨析"},
    {"key": "hard", "label": "偏难", "hint": "应用与边界条件"},
]


def _source_out(question: Question) -> list[SourceOut]:
    return [
        SourceOut(
            document_id=source.document_id,
            document_name=source.document_name,
            chunk_id=source.chunk_id,
            snippet=source.snippet,
            page=source.page,
        )
        for source in question.sources
    ]


def _question_out(question: Question) -> QuestionOut:
    return QuestionOut(
        id=question.id,
        type=question.type,
        stem=question.stem,
        options=question.options,
        answer=question.answer,
        explanation=question.explanation,
        knowledge_point=question.knowledge_point,
        document_id=question.document_id,
        topic_id=question.topic_id,
        sources=_source_out(question),
    )


def _session_out(session: PracticeSession) -> SessionOut:
    stored = session.meta.get("result")
    stats = SessionStatsOut(**stored) if isinstance(stored, dict) and "total" in stored else None
    return SessionOut(
        id=session.id,
        status=session.status.value,
        score=session.score,
        questions=[_question_out(question) for question in session.questions],
        answers=[
            AnswerOut(
                question_id=answer.question_id,
                response=answer.response,
                is_correct=answer.is_correct,
                duration_seconds=answer.duration_seconds,
            )
            for answer in session.answers
        ],
        stats=stats,
        started_at=session.started_at,
        finished_at=session.finished_at,
        meta=session.meta,
    )


def _brief_out(session: PracticeSession) -> SessionBriefOut:
    return SessionBriefOut(
        id=session.id,
        status=session.status.value,
        score=session.score,
        question_count=len(session.questions),
        answered_count=len(session.answers),
        started_at=session.started_at,
        finished_at=session.finished_at,
        meta=session.meta,
    )


@router.get("/meta", response_model=PracticeMetaOut)
def practice_meta() -> PracticeMetaOut:
    """题型与限制由服务端给出，前端不硬编码题型列表。"""
    return PracticeMetaOut(
        types=[
            QuestionTypeOut(
                key=question_type,
                label=spec["label"],
                # 简答在 v0.1 由用户自评，系统不判分
                auto_graded=question_type.value != "short_answer",
                hint=_TYPE_HINTS.get(question_type.value, ""),
            )
            for question_type, spec in practice_prompts.TYPE_SPECS.items()
        ],
        difficulties=_DIFFICULTIES,
        max_questions=practice_service.MAX_QUESTIONS,
        min_chars_per_question=practice_service.MIN_CHARS_PER_QUESTION,
    )


@router.post("/generate", response_model=GenerationJobOut, status_code=202)
def generate(
    payload: GenerateIn,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
) -> GenerationJobOut:
    try:
        job = practice_service.start_generation(
            db,
            counts=payload.counts,
            topic_ids=payload.topic_ids,
            document_ids=payload.document_ids,
            days=payload.days,
            difficulty=payload.difficulty,
        )
    except PracticeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    background.add_task(practice_service.run_generation, job["id"])
    return GenerationJobOut(**job)


@router.get("/jobs/{job_id}", response_model=GenerationJobOut)
def job(job_id: str) -> GenerationJobOut:
    try:
        snapshot = practice_service.job_snapshot(job_id)
    except PracticeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return GenerationJobOut(**snapshot)


@router.get("/sessions", response_model=list[SessionBriefOut])
def list_sessions(limit: int = 20, db: Session = Depends(get_db)) -> list[SessionBriefOut]:
    return [_brief_out(session) for session in practice_service.list_sessions(db, limit=limit)]


@router.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: str, db: Session = Depends(get_db)) -> SessionOut:
    try:
        session = practice_service.get_session(db, session_id)
    except PracticeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _session_out(session)


@router.delete("/sessions/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        practice_service.delete_session(db, session_id)
    except PracticeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PracticeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": session_id}


@router.delete("/sessions/{session_id}/questions/{question_id}", response_model=SessionOut)
def remove_question(
    session_id: str, question_id: str, db: Session = Depends(get_db)
) -> SessionOut:
    try:
        session = practice_service.remove_question(db, session_id, question_id)
    except PracticeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PracticeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_out(session)


# ---------- 作答与判分（FR-05） ----------


@router.post("/sessions/{session_id}/start", response_model=SessionOut)
def start_session(session_id: str, db: Session = Depends(get_db)) -> SessionOut:
    """进入作答。幂等：已经开始了再点一次只是拿回当前进度。"""
    try:
        session = practice_service.start_session(db, session_id)
    except PracticeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PracticeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_out(session)


@router.patch("/sessions/{session_id}/answers", response_model=SessionOut)
def save_answer(
    session_id: str, payload: AnswerIn, db: Session = Depends(get_db)
) -> SessionOut:
    """逐题自动保存：断点续答靠它，不靠浏览器的本地缓存。"""
    try:
        session = practice_service.save_answer(
            db,
            session_id,
            payload.question_id,
            response=payload.response,
            duration_seconds=payload.duration_seconds,
            self_grade=payload.self_grade,
        )
    except PracticeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PracticeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_out(session)


@router.post("/sessions/{session_id}/submit", response_model=SessionOut)
def submit(
    session_id: str, payload: SubmitIn, db: Session = Depends(get_db)
) -> SessionOut:
    try:
        session, _score = practice_service.submit_session(
            db, session_id, [item.model_dump() for item in payload.answers]
        )
    except PracticeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PracticeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_out(session)


# ---------- 错题本（FR-06） ----------


def _topic_names(db: Session) -> dict[str, str]:
    return {topic.id: topic.name for topic in Topics(db).list()}


def _wrong_out(item: WrongAnswer, topics: dict[str, str]) -> WrongOut:
    primary = item.question.sources[0] if item.question.sources else None
    return WrongOut(
        id=item.id,
        question=_question_out(item.question),
        wrong_count=item.wrong_count,
        last_response=item.last_response,
        first_wrong_at=item.first_wrong_at,
        last_wrong_at=item.last_wrong_at,
        topic_id=item.topic_id,
        topic_name=topics.get(item.topic_id or UNCATEGORIZED_ID, UNCATEGORIZED_NAME),
        document_name=primary.document_name if primary else "",
    )


@router.get("/wrong-answers/summary", response_model=WrongSummaryOut)
def wrong_summary(days: int | None = None, db: Session = Depends(get_db)) -> WrongSummaryOut:
    return WrongSummaryOut(**practice_service.wrong_summary(db, days=days))


@router.get("/wrong-answers", response_model=list[WrongOut])
def list_wrong_answers(
    topic_id: str | None = None,
    document_id: str | None = None,
    type: str | None = None,
    days: int | None = None,
    min_wrong: int | None = None,
    sort: str = "recent",
    limit: int = 200,
    db: Session = Depends(get_db),
) -> list[WrongOut]:
    items = practice_service.list_wrong_answers(
        db,
        topic_id=topic_id,
        document_id=document_id,
        question_type=type,
        days=days,
        min_wrong=min_wrong,
        sort=sort if sort in {"recent", "count"} else "recent",
        limit=max(1, min(limit, 500)),
    )
    topics = _topic_names(db)
    return [_wrong_out(item, topics) for item in items]


@router.delete("/wrong-answers/{wrong_id}")
def remove_wrong_answer(wrong_id: str, db: Session = Depends(get_db)) -> dict:
    try:
        practice_service.remove_wrong_answer(db, wrong_id)
    except PracticeNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": wrong_id}


@router.post("/retry", response_model=SessionOut, status_code=201)
def retry(payload: RetryIn, db: Session = Depends(get_db)) -> SessionOut:
    """从错题组卷。不复用出题链路——重练的是同一道题，不该再让模型换一种问法。"""
    try:
        session = practice_service.start_retry(
            db, wrong_ids=payload.wrong_ids or None, limit=payload.limit
        )
    except PracticeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return _session_out(session)
