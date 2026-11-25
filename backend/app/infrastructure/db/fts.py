"""笔记全文检索（SQLite FTS5）。

为什么不用 ORM
--------------
FTS5 是虚表，SQLAlchemy 没有对应的方言支持，只能用 `CREATE VIRTUAL TABLE`。
这一层把裸 SQL 收在基础设施内，业务侧只看到 `index / remove / search`。

中文为什么要「按字切分」
------------------------
FTS5 的 `unicode61` 分词器会把连续汉字当成**一个** token（"向量索引" → 一个词），
于是搜「索引」永远命中不了。做法是入库前把汉字逐字用空格隔开
（"向量索引" → "向 量 索 引"），查询时同样处理，再用短语查询要求字符连续，
这样单字与多字都能命中，且不会把无关的字符组合也算命中。

索引与业务行的写入必须同一个事务
--------------------------------
FTS 行由笔记仓储在 add/update/delete 时同步维护（同一 Session），
避免"笔记删了但索引还在"这类只在搜索时暴露的脏数据。
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

TABLE = "notes_fts"

# 汉字与常用中日韩标点：逐字切开；ASCII/数字保持整词
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_SPACE = re.compile(r"\s+")


def char_split(value: str) -> str:
    """把中文拆成单字序列，英文与数字保持原样。"""
    if not value:
        return ""
    pieces = _CJK.sub(lambda m: f" {m.group(0)} ", value)
    return _SPACE.sub(" ", pieces).strip()


def ensure_schema(engine: Engine) -> bool:
    """建虚表。非 SQLite 或未编译 FTS5 时返回 False，检索自动降级为 LIKE。"""
    if engine.dialect.name != "sqlite":
        return False
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""CREATE VIRTUAL TABLE IF NOT EXISTS {TABLE} USING fts5(
                        note_id UNINDEXED,
                        title,
                        body,
                        tokenize = 'unicode61'
                    )"""
                )
            )
        return True
    except Exception as exc:  # noqa: BLE001 - 缺 FTS5 不应让应用起不来
        logger.warning("FTS5 不可用，笔记搜索降级为 LIKE：%s", exc)
        return False


def upsert(conn, note_id: str, title: str, body: str) -> None:
    """同事务写入索引。调用方传当前 Session 的 Connection。"""
    conn.execute(text(f"DELETE FROM {TABLE} WHERE note_id = :n"), {"n": note_id})
    conn.execute(
        text(f"INSERT INTO {TABLE}(note_id, title, body) VALUES (:n, :t, :b)"),
        {"n": note_id, "t": char_split(title), "b": char_split(body)},
    )


def remove(conn, note_id: str) -> None:
    conn.execute(text(f"DELETE FROM {TABLE} WHERE note_id = :n"), {"n": note_id})


def search(conn, keyword: str, limit: int = 200) -> list[str]:
    """返回命中的 note_id，按相关度排序（bm25 越小越相关）。"""
    query = _phrase(keyword)
    if not query:
        return []
    rows = conn.execute(
        text(
            f"""SELECT note_id FROM {TABLE}
                WHERE {TABLE} MATCH :q
                ORDER BY bm25({TABLE}, 4.0, 1.0)
                LIMIT :k"""
        ),
        {"q": query, "k": limit},
    ).fetchall()
    return [row[0] for row in rows]


def _phrase(keyword: str) -> str:
    """把用户输入变成短语查询，避免单个字符被当成 OR 命中一切。"""
    tokens = char_split(keyword).split()
    if not tokens:
        return ""
    if len(tokens) == 1:
        return f'"{tokens[0]}"'
    return '"' + " ".join(tokens) + '"'
