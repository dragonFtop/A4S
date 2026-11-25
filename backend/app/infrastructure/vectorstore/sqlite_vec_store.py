"""sqlite-vec 向量存储实现。

设计要点（PRD FR-02 规则 7）：
- `topic_id` 是 vec0 的 **partition key**，按主题检索时在分区内取 Top-K，
  而不是"全库取 K 再过滤"——后者在分区内容少时会严重召回不足。
- chunk 正文与定位信息存在关系库 `chunks` 表，本表只存 `chunk_id + 向量 + 分区键`。
- 向量维度由配置决定；模型切换后维度不符时拒绝写入并提示重建索引。
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

TABLE = "vec_chunks"
VEC_LOADER_ATTR = "_a4s_vec_loaded"


def attach_sqlite_vec(engine: Engine) -> bool:
    """给 SQLAlchemy engine 的每条新连接加载 sqlite-vec 扩展。

    返回 False 表示当前不是 SQLite 或扩展不可用——调用方应降级而非崩溃。
    """
    if engine.dialect.name != "sqlite":
        logger.info("非 SQLite 后端，跳过 sqlite-vec（未来走 pgvector）")
        return False
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        logger.warning("未安装 sqlite-vec，向量检索不可用（pip install sqlite-vec）")
        return False

    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):  # pragma: no cover - 由驱动触发
        try:
            dbapi_conn.enable_load_extension(True)
            sqlite_vec.load(dbapi_conn)
            dbapi_conn.enable_load_extension(False)
        except Exception as exc:
            logger.error("加载 sqlite-vec 失败：%s", exc)

    setattr(engine, VEC_LOADER_ATTR, True)
    return True


class VecDimensionMismatch(RuntimeError):
    """向量维度与已有索引不一致，必须重建索引。"""


class SqliteVecStore:
    """向量库端口的具体实现。所有方法都自己开连接，短事务，不持有状态。"""

    def __init__(self, engine: Engine, dimension: int) -> None:
        self.engine = engine
        self.dimension = dimension

    # ---------- 结构 ----------

    def ensure_schema(self) -> None:
        existing = self._existing_dimension()
        if existing is not None and existing != self.dimension:
            # 不自动删表——那会静默丢数据。交给上层提示用户重建索引。
            logger.warning(
                "向量索引维度为 %s，当前配置为 %s，检索结果不可用，需要重建索引",
                existing,
                self.dimension,
            )
            return
        if existing is None:
            with self.engine.begin() as conn:
                conn.execute(
                    text(
                        f"""CREATE VIRTUAL TABLE {TABLE} USING vec0(
                            chunk_id TEXT PRIMARY KEY,
                            embedding FLOAT[{self.dimension}] distance_metric=cosine,
                            topic_id TEXT partition key
                        )"""
                    )
                )

    def _existing_dimension(self) -> int | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": TABLE}
            ).fetchone()
        if not row or not row[0]:
            return None
        match = re.search(r"FLOAT\[(\d+)\]", row[0], re.IGNORECASE)
        return int(match.group(1)) if match else None

    def index_dimension(self) -> int | None:
        return self._existing_dimension()

    def is_compatible(self) -> bool:
        dim = self._existing_dimension()
        return dim is None or dim == self.dimension

    def reset(self) -> None:
        """删表重建。只在用户明确同意重建索引时调用。"""
        with self.engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
        self.ensure_schema()

    # ---------- 写入 ----------

    def add(self, items: list[tuple[str, list[float], str]]) -> None:
        """items: [(chunk_id, vector, topic_id)]"""
        if not items:
            return
        if not self.is_compatible():
            raise VecDimensionMismatch(
                f"向量维度不一致：索引 {self.index_dimension()}，配置 {self.dimension}，请重建索引"
            )
        import sqlite_vec

        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                f"INSERT OR REPLACE INTO {TABLE}(chunk_id, embedding, topic_id) VALUES (?, ?, ?)",
                [
                    (chunk_id, sqlite_vec.serialize_float32(vector), topic_id)
                    for chunk_id, vector, topic_id in items
                ],
            )

    def delete_chunks(self, chunk_ids: list[str]) -> None:
        if not chunk_ids:
            return
        stmt = text(f"DELETE FROM {TABLE} WHERE chunk_id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        with self.engine.begin() as conn:
            conn.execute(stmt, {"ids": list(chunk_ids)})

    def repartition(self, chunk_ids: list[str], topic_id: str) -> int:
        """把片段改到另一个主题分区。

        vec0 不支持原地更新 partition key（0.1.9 会报 "UPDATE on partition key
        columns are not supported"），但向量可以原样读回再插入——
        因此改主题不需要重新做 embedding，只做一次搬移。
        """
        if not chunk_ids:
            return 0
        ids = list(chunk_ids)
        select_stmt = text(f"SELECT chunk_id, embedding FROM {TABLE} WHERE chunk_id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        delete_stmt = text(f"DELETE FROM {TABLE} WHERE chunk_id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        with self.engine.begin() as conn:
            rows = conn.execute(select_stmt, {"ids": ids}).fetchall()
            if not rows:
                return 0
            conn.execute(delete_stmt, {"ids": ids})
            conn.exec_driver_sql(
                f"INSERT INTO {TABLE}(chunk_id, embedding, topic_id) VALUES (?, ?, ?)",
                [(row[0], row[1], topic_id) for row in rows],
            )
        return len(rows)

    # ---------- 检索 ----------

    def search(
        self,
        vector: list[float],
        top_k: int = 5,
        topic_id: str | None = None,
        allowed_chunk_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """返回 [(chunk_id, distance)]，按距离升序（越小越相似）。

        topic_id 走 partition key，在分区内取 Top-K；
        allowed_chunk_ids 用于"按标签圈定"等无法下推到分区的过滤，
        此时先取 top_k * oversample 再截断，避免过滤后不足。
        """
        import sqlite_vec

        if not self.is_compatible():
            raise VecDimensionMismatch(
                f"向量维度不一致：索引 {self.index_dimension()}，配置 {self.dimension}，请重建索引"
            )

        oversample = 4 if allowed_chunk_ids else 1
        k = max(top_k * oversample, top_k)

        sql = f"SELECT chunk_id, distance FROM {TABLE} WHERE embedding MATCH :v AND k = :k"
        params: dict = {"v": sqlite_vec.serialize_float32(vector), "k": k}
        if topic_id:
            sql += " AND topic_id = :topic"
            params["topic"] = topic_id
        sql += " ORDER BY distance"

        with self.engine.connect() as conn:
            rows = conn.exec_driver_sql(sql, params).fetchall()

        results = [(r[0], float(r[1])) for r in rows]
        if allowed_chunk_ids is not None:
            results = [r for r in results if r[0] in allowed_chunk_ids]
        return results[:top_k]

    # ---------- 统计 ----------

    def count(self) -> int:
        if self._existing_dimension() is None:
            return 0
        with self.engine.connect() as conn:
            row = conn.exec_driver_sql(f"SELECT count(*) FROM {TABLE}").fetchone()
        return int(row[0]) if row else 0

    def count_by_topic(self) -> dict[str, int]:
        if self._existing_dimension() is None:
            return {}
        with self.engine.connect() as conn:
            rows = conn.exec_driver_sql(
                f"SELECT topic_id, count(*) FROM {TABLE} GROUP BY topic_id"
            ).fetchall()
        return {r[0]: int(r[1]) for r in rows}
