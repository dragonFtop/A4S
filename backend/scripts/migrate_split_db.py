"""P1 数据分家：把单体库 data/app.db 搬到 data/client.db。

背景：拆分后客户端连 data/client.db、服务端连 data/server.db。本脚本把旧
app.db 的全部内容（含 FTS5 / sqlite-vec 虚表、alembic 版本号）原样拷贝到
client.db，并把 app.db 重命名为 app.db.bak_pre_split 留底——**不删除**，
随时可把留底改回 app.db 完成回滚。server.db 留空，首次启动服务端时建表。

幂等：
  - app.db 不存在        → 无需迁移，退出 0
  - client.db 已含数据    → 跳过拷贝，避免覆盖现有客户端数据
  - 留底文件已存在        → 追加时间戳，绝不覆盖

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/migrate_split_db.py           # 执行
    .venv/Scripts/python.exe scripts/migrate_split_db.py --dry-run # 只校验，不改文件
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
DATA = BACKEND / "data"
SRC = DATA / "app.db"
DST = DATA / "client.db"
BAK_PREFIX = "app.db.bak_pre_split"

# 计入校验的用户表。虚表（chunks_fts / notes_fts / vec_chunks）及其影子表由
# SQLite backup API 整库拷贝，不在此逐表比对——它们在目标库会完整重建。
USER_TABLES = [
    "users",
    "auth_tokens",
    "spaces",
    "space_members",
    "space_shares",
    "topics",
    "documents",
    "document_tags",
    "chunks",
    "notes",
    "note_sources",
    "note_tags",
    "practice_sessions",
    "wrong_answers",
    "study_plans",
    "review_reports",
]


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in USER_TABLES:
        try:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.OperationalError:
            counts[table] = -1  # 表不存在（目标库尚未建表时）
    return counts


def _has_data(conn: sqlite3.Connection) -> bool:
    return any(v > 0 for v in _row_counts(conn).values())


def _backup(src: Path, dst: Path) -> None:
    """用 SQLite backup API 整库拷贝，虚表与影子表一并复制。"""
    s = _connect(src)
    try:
        # 先 checkpoint，确保 WAL 里的改动落回主库文件再备份
        try:
            s.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.OperationalError:
            pass
        d = _connect(dst)
        try:
            s.backup(d)
        finally:
            d.close()
    finally:
        s.close()


def _verify(src: Path, dst: Path) -> bool:
    s = _connect(src)
    d = _connect(dst)
    try:
        cs, cd = _row_counts(s), _row_counts(d)
    finally:
        s.close()
        d.close()
    ok = True
    for table in USER_TABLES:
        if cs[table] != cd[table]:
            print(f"  [FAIL] {table}: src={cs[table]} dst={cd[table]}")
            ok = False
    return ok


def _archive_path() -> Path:
    base = DATA / BAK_PREFIX
    if not base.exists():
        return base
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DATA / f"{BAK_PREFIX}_{ts}"


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    if not SRC.exists():
        print(f"[skip] {SRC.name} 不存在，无需迁移。")
        return 0

    # client.db 已有数据 → 不覆盖（可能已经迁过，或是用户的新数据）
    if DST.exists():
        conn = _connect(DST)
        has = _has_data(conn)
        conn.close()
        if has:
            print(f"[skip] {DST.name} 已存在且含数据，不覆盖。"
                  f"如需重迁，请先删除 {DST.name}。")
            return 0

    print(f"[copy] {SRC.name} -> {DST.name} ...")

    if dry_run:
        # 只校验：拷到临时文件比对，不动 DST / 不重命名 SRC
        tmp = DATA / "_migrate_dryrun.db"
        if tmp.exists():
            tmp.unlink()
        _backup(SRC, tmp)
        ok = _verify(SRC, tmp)
        tmp.unlink(missing_ok=True)
        print(f"[dry-run] 校验{'通过' if ok else '失败'}（未改动任何文件）")
        return 0 if ok else 1

    _backup(SRC, DST)

    print("[verify] 比对行数 ...")
    if not _verify(SRC, DST):
        print("[FAIL] 拷贝校验失败：已生成 DST 但不归档 SRC，请人工检查后重试。")
        return 1
    print("[verify] 全部一致。")

    bak = _archive_path()
    SRC.rename(bak)
    print(f"[archive] {SRC.name} -> {bak.name}（留底；把它改回 app.db 即可回滚）")

    print("[done] 完成。客户端下次启动连 client.db；服务端首次启动建空 server.db。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
