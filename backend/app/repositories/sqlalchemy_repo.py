"""仓储实现的统一出口。

具体实现按实体拆在同目录，各管一张表：

- `topics.py`          主题 / 标签
- `documents.py`       资料元信息、标签关联、删除级联
- `chunks.py`          资料分块
- `notes.py`           电子笔记（含 FTS 同步）
- `practice.py`        练习卷（题目与作答存 JSON 快照）
- `wrong_answers.py`   错题本
- `_converters.py`     题目快照的序列化（练习与错题本共用）

调用方统一从本文件 import，不直接依赖具体文件——
将来换存储实现时只需要改这里的指向。
"""

from __future__ import annotations

from app.repositories._converters import question_from_dict, question_to_dict
from app.repositories.chunks import SqlAlchemyChunkRepository
from app.repositories.documents import SqlAlchemyDocumentRepository
from app.repositories.notes import SqlAlchemyNoteRepository
from app.repositories.practice import SqlAlchemyPracticeRepository
from app.repositories.topics import SqlAlchemyTopicRepository
from app.repositories.wrong_answers import SqlAlchemyWrongAnswerRepository

__all__ = [
    "SqlAlchemyChunkRepository",
    "SqlAlchemyDocumentRepository",
    "SqlAlchemyNoteRepository",
    "SqlAlchemyPracticeRepository",
    "SqlAlchemyTopicRepository",
    "SqlAlchemyWrongAnswerRepository",
    "question_from_dict",
    "question_to_dict",
]
