"""题目快照的序列化与反序列化。

练习记录和错题本都把题目存成 JSON —— 资料日后被删，
历史作答与错题仍然可读、可重练。"""

from __future__ import annotations

from shared.domain.models.entities import Question, QuestionType, SourceRef


def question_to_dict(question: Question) -> dict:
    """题目快照。练习记录必须自洽：资料日后被删，历史作答仍能回看。"""
    return {
        "id": question.id,
        "type": question.type.value,
        "stem": question.stem,
        "options": list(question.options),
        "answer": question.answer,
        "explanation": question.explanation,
        "document_id": question.document_id,
        "knowledge_point": question.knowledge_point,
        "topic_id": question.topic_id,
        "sources": [
            {
                "document_id": source.document_id,
                "document_name": source.document_name,
                "chunk_id": source.chunk_id,
                "snippet": source.snippet,
                "page": source.page,
            }
            for source in question.sources
        ],
    }


def question_from_dict(data: dict) -> Question:
    sources = [
        SourceRef(
            document_id=item.get("document_id") or "",
            document_name=item.get("document_name") or "",
            chunk_id=item.get("chunk_id"),
            snippet=item.get("snippet") or "",
            page=item.get("page"),
        )
        for item in (data.get("sources") or [])
        if isinstance(item, dict)
    ]
    try:
        question_type = QuestionType(data.get("type"))
    except ValueError:
        question_type = QuestionType.SHORT_ANSWER
    return Question(
        id=data.get("id") or "",
        type=question_type,
        stem=data.get("stem") or "",
        answer=data.get("answer") or "",
        explanation=data.get("explanation") or "",
        options=list(data.get("options") or []),
        sources=sources,
        document_id=data.get("document_id"),
        knowledge_point=data.get("knowledge_point"),
        topic_id=data.get("topic_id"),
    )
