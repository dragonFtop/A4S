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
# 片段索引：问答的第二路召回。和笔记分开两张表，字段与排序权重都不同。
CHUNKS_TABLE = "chunks_fts"

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
            # 片段只有正文，没有标题，所以不设字段权重
            conn.execute(
                text(
                    f"""CREATE VIRTUAL TABLE IF NOT EXISTS {CHUNKS_TABLE} USING fts5(
                        chunk_id UNINDEXED,
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


# ---------------------------------------------------------------- 片段索引（问答召回用）

# 单字停用：出现频率极高，作为检索条件只会灌进来一堆无关片段。
# 注意只用于**查询侧**，索引侧仍然全量写入（否则「的」这类字永远搜不到）。
_STOP_CHARS = set("的了吗呢吧啊呀嘛是在和与也就都而及或有着过被把让给向从对为以于")

_NON_CJK = re.compile(r"[A-Za-z0-9]+")


def _chunk_query(keyword: str) -> str:
    """把问题变成 FTS 查询串：中文按二元组、英文按整词，组间取 OR。

    为什么不用笔记那套「整句短语」：那要求字符连续，而提问几乎不可能在原文里
    连续出现（"CBC 模式为什么需要 IV" 不会原样躺在资料里）。二元组 OR 能召回
    "CBC 模式" 和 "初始化向量" 这类片段，噪音交给后面的 RRF 排名压下去。
    """
    text = (keyword or "").strip()
    if not text:
        return ""

    terms: list[str] = []
    for word in _NON_CJK.findall(text):
        terms.append(f'"{word.lower()}"')

    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        chars = [ch for ch in run if ch not in _STOP_CHARS]
        joined = "".join(chars)
        if len(joined) == 1:
            terms.append(f'"{joined}"')
        for i in range(len(joined) - 1):
            # 关键：索引里是「明 文」（逐字一个 token），查询也必须逐字切开。
            # unicode61 不切中文，"明文" 会被当成**一个** token，永远匹配不上。
            terms.append(f'"{char_split(joined[i:i + 2])}"')

    # 去重保序，避免 "模式模式" 这类重复项把查询撑长
    unique = list(dict.fromkeys(terms))
    return " OR ".join(unique)


def upsert_chunks(conn, items: list[tuple[str, str]]) -> None:
    """同事务批量写片段索引。items = [(chunk_id, text)]。"""
    if not items:
        return
    for chunk_id, body in items:
        conn.execute(
            text(f"DELETE FROM {CHUNKS_TABLE} WHERE chunk_id = :c"), {"c": chunk_id}
        )
        conn.execute(
            text(f"INSERT INTO {CHUNKS_TABLE}(chunk_id, body) VALUES (:c, :b)"),
            {"c": chunk_id, "b": char_split(body)},
        )


def remove_chunks(conn, chunk_ids: list[str]) -> None:
    if not chunk_ids:
        return
    for chunk_id in chunk_ids:
        conn.execute(
            text(f"DELETE FROM {CHUNKS_TABLE} WHERE chunk_id = :c"), {"c": chunk_id}
        )


def remove_chunks_by_document(conn, document_ids: list[str]) -> None:
    """删资料时清索引：chunks_fts 不认得 document_id，只能先拿到片段 id 再删。"""
    if not document_ids:
        return
    rows = conn.execute(
        text(
            "SELECT id FROM chunks WHERE document_id IN "
            f"({', '.join('?' * len(document_ids))})"
        ),
        list(document_ids),
    ).fetchall()
    remove_chunks(conn, [row[0] for row in rows])


def search_chunks(conn, keyword: str, limit: int = 50) -> list[tuple[str, float]]:
    """返回 [(chunk_id, bm25)]，bm25 越小越相关（FTS5 返回负值）。"""
    query = _chunk_query(keyword)
    if not query:
        return []
    rows = conn.execute(
        text(
            f"""SELECT chunk_id, bm25({CHUNKS_TABLE}) AS s
                FROM {CHUNKS_TABLE}
                WHERE {CHUNKS_TABLE} MATCH :q
                ORDER BY s
                LIMIT :k"""
        ),
        {"q": query, "k": limit},
    ).fetchall()
    return [(row[0], float(row[1])) for row in rows]


def rebuild_chunk_index(conn) -> int:
    """全量重建片段索引（索引重建/维度变更后调用）。返回写入条数。"""
    conn.execute(text(f"DELETE FROM {CHUNKS_TABLE}"))
    rows = conn.execute(text("SELECT id, text FROM chunks")).fetchall()
    upsert_chunks(conn, [(row[0], row[1] or "") for row in rows])
    return len(rows)
