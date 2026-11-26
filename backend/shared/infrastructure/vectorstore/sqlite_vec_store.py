"""sqlite-vec 向量存储实现。

设计要点：
- **分区键是 `space_id`，不是 `topic_id`。** 分区键唯一的作用是让 KNN 少扫数据，
  所以必须选"每次查询都带的列"。空间隔离是强制的，每个查询都带 space_id；
  主题筛选是可选的，只有用户主动选了才带。选 topic 当分区键，等于大多数查询
  全表扫，而且别的用户的数据会挤掉你的候选（top-K 被稀释）。
- chunk 正文与定位信息存在关系库 `chunks` 表，本表只存 `chunk_id + 向量 + 分区键`。
  主题过滤走 `allowed_chunk_ids`（检索后置），因为 vec0 0.1.9 不允许在 KNN 里
  过滤辅助列，也无法同时有两个分区键。
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

# 分区键列名。改动它会触发一次不重算向量的自愈迁移。
PARTITION_COLUMN = "space_id"

# 自动迁移的行数上限：迁移要把向量读进内存，超过这个量就交给"重建索引"，
# 免得启动时吃掉几百 MB。当前规模（万级以内）远低于此。
_AUTO_MIGRATE_LIMIT = 50_000

_PARTITION_RE = re.compile(r"(\w+)\s+TEXT\s+partition\s+key", re.IGNORECASE)
_DIM_RE = re.compile(r"FLOAT\[(\d+)\]", re.IGNORECASE)


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

    def _create_sql(self) -> str:
        return f"""CREATE VIRTUAL TABLE {TABLE} USING vec0(
            chunk_id TEXT PRIMARY KEY,
            embedding FLOAT[{self.dimension}] distance_metric=cosine,
            {PARTITION_COLUMN} TEXT partition key
        )"""

    def ensure_schema(self) -> None:
        schema = self._existing_schema()
        if schema is None:
            with self.engine.begin() as conn:
                conn.execute(text(self._create_sql()))
            return

        dim = _parse_dimension(schema)
        if dim is not None and dim != self.dimension:
            # 不自动删表——那会静默丢数据。交给上层提示用户重建索引。
            logger.warning(
                "向量索引维度为 %s，当前配置为 %s，检索结果不可用，需要重建索引",
                dim,
                self.dimension,
            )
            return

        partition = _parse_partition(schema)
        if partition != PARTITION_COLUMN:
            self._migrate_partition(partition)

    def _migrate_partition(self, old_column: str | None) -> None:
        """把分区键从旧列迁到 space_id，**不重算向量**。

        vec0 不支持改分区键，也不支持 RENAME（只改主表、影子表不跟着改），
        所以只能读出来重建。整段放在一个事务里：SQLite 的 DDL 是事务性的，
        中途失败会整体回滚，不会出现"表没了、向量也没了"。
        """
        with self.engine.connect() as conn:
            total = conn.exec_driver_sql(f"SELECT count(*) FROM {TABLE}").scalar() or 0
        if total > _AUTO_MIGRATE_LIMIT:
            logger.warning(
                "向量索引分区键为 %s，需要迁移为 %s，但现有 %s 条超过自动迁移上限，"
                "请到设置页重建索引",
                old_column or "?",
                PARTITION_COLUMN,
                total,
            )
            return

        with self.engine.begin() as conn:
            rows = conn.exec_driver_sql(f"SELECT chunk_id, embedding FROM {TABLE}").fetchall()
            # 片段还在不在决定它该挂哪个空间；孤儿向量（chunk 已删）直接丢弃，
            # 它们本来就检索不到（取不到正文）。
            owners = dict(conn.exec_driver_sql("SELECT id, space_id FROM chunks").fetchall())

            conn.exec_driver_sql(f"DROP TABLE {TABLE}")
            conn.exec_driver_sql(self._create_sql())

            payload = []
            orphaned = 0
            for chunk_id, embedding in rows:
                space_id = owners.get(chunk_id)
                if space_id is None:
                    orphaned += 1
                    continue
                payload.append((chunk_id, embedding, space_id))

            if payload:
                conn.exec_driver_sql(
                    f"INSERT INTO {TABLE}(chunk_id, embedding, {PARTITION_COLUMN}) "
                    "VALUES (?, ?, ?)",
                    payload,
                )

        logger.info(
            "向量索引分区键已从 %s 迁移为 %s：搬移 %s 条，丢弃孤儿 %s 条（向量未重算）",
            old_column or "?",
            PARTITION_COLUMN,
            len(payload),
            orphaned,
        )

    def _existing_schema(self) -> str | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                text("SELECT sql FROM sqlite_master WHERE name = :n"), {"n": TABLE}
            ).fetchone()
        if not row or not row[0]:
            return None
        return str(row[0])

    def _existing_dimension(self) -> int | None:
        schema = self._existing_schema()
        return _parse_dimension(schema) if schema else None

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
        """items: [(chunk_id, vector, space_id)]"""
        if not items:
            return
        if not self.is_compatible():
            raise VecDimensionMismatch(
                f"向量维度不一致：索引 {self.index_dimension()}，配置 {self.dimension}，请重建索引"
            )
        import sqlite_vec

        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                f"INSERT OR REPLACE INTO {TABLE}(chunk_id, embedding, {PARTITION_COLUMN}) "
                "VALUES (?, ?, ?)",
                [
                    (chunk_id, sqlite_vec.serialize_float32(vector), space_id)
                    for chunk_id, vector, space_id in items
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

    def repartition(self, chunk_ids: list[str], space_id: str) -> int:
        """把片段改到另一个空间分区（跨空间搬资料时用）。

        vec0 不能原地改分区键，但向量可以原样读回再插入——
        所以搬动资料不需要重新做 embedding。
        """
        if not chunk_ids:
            return 0
        ids = list(chunk_ids)
        select_stmt = text(
            f"SELECT chunk_id, embedding FROM {TABLE} WHERE chunk_id IN :ids"
        ).bindparams(bindparam("ids", expanding=True))
        delete_stmt = text(f"DELETE FROM {TABLE} WHERE chunk_id IN :ids").bindparams(
            bindparam("ids", expanding=True)
        )
        with self.engine.begin() as conn:
            rows = conn.execute(select_stmt, {"ids": ids}).fetchall()
            if not rows:
                return 0
            conn.execute(delete_stmt, {"ids": ids})
            conn.exec_driver_sql(
                f"INSERT INTO {TABLE}(chunk_id, embedding, {PARTITION_COLUMN}) VALUES (?, ?, ?)",
                [(row[0], row[1], space_id) for row in rows],
            )
        return len(rows)

    # ---------- 检索 ----------

    def search(
        self,
        vector: list[float],
        top_k: int = 5,
        *,
        space_id: str,
        allowed_chunk_ids: set[str] | None = None,
    ) -> list[tuple[str, float]]:
        """返回 [(chunk_id, distance)]，按距离升序（越小越相似）。

        `space_id` 是**必填关键字参数**：它同时是分区键和隔离边界，
        漏传就不该有结果，而不是"返回全站"。这里不设默认值，让漏传直接报错。

        allowed_chunk_ids 用于"按主题/标签圈定"这类无法下推的过滤，
        此时先取 top_k * oversample 再截断，避免过滤后不足。
        """
        import sqlite_vec

        if not space_id:
            raise ValueError("检索必须带 space_id：向量表按空间分区，无空间即无结果")

        if not self.is_compatible():
            raise VecDimensionMismatch(
                f"向量维度不一致：索引 {self.index_dimension()}，配置 {self.dimension}，请重建索引"
            )

        oversample = 4 if allowed_chunk_ids else 1
        k = max(top_k * oversample, top_k)

        sql = (
            f"SELECT chunk_id, distance FROM {TABLE} "
            "WHERE embedding MATCH :v AND k = :k AND space_id = :space"
        )
        params: dict = {
            "v": sqlite_vec.serialize_float32(vector),
            "k": k,
            "space": space_id,
        }
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

    def count_by_space(self) -> dict[str, int]:
        """各空间的向量条数。运维排查用（索引健康、孤儿检测）。"""
        if self._existing_dimension() is None:
            return {}
        with self.engine.connect() as conn:
            rows = conn.exec_driver_sql(
                f"SELECT {PARTITION_COLUMN}, count(*) FROM {TABLE} GROUP BY {PARTITION_COLUMN}"
            ).fetchall()
        return {r[0]: int(r[1]) for r in rows}


def _parse_dimension(schema: str) -> int | None:
    match = _DIM_RE.search(schema)
    return int(match.group(1)) if match else None


def _parse_partition(schema: str) -> str | None:
    match = _PARTITION_RE.search(schema)
    return match.group(1) if match else None
