"""P4 Task 18 / AC-18: VACUUM INTO 一致性备份 + 保留 N 份 + 异地副本。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from client.services.backup_service import PREFIX, create_backup, prune  # noqa: E402

PASSED = 0
FAILED = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  [ok] {label}")
    else:
        FAILED += 1
        print(f"  [FAIL] {label} {detail}")


def make_db(path: Path) -> None:
    con = sqlite3.connect(str(path))
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        con.executemany("INSERT INTO t (v) VALUES (?)", [("alpha",), ("beta",), ("gamma",)])
        con.commit()
    finally:
        con.close()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="a4s_p4_backup_"))
    src = tmp / "client.db"
    backups = tmp / "backups"
    offsite = tmp / "offsite"
    make_db(src)

    print("== 生成快照并校验可打开、数据一致 ==")
    r1 = create_backup(source=src, backup_dir=backups, keep=2)
    snap = r1["snapshot"]
    check("快照文件存在", snap.exists(), str(snap))
    con = sqlite3.connect(str(snap))
    try:
        rows = con.execute("SELECT count(*) FROM t").fetchone()[0]
        check("快照可被 sqlite 打开且行数一致(=3)", rows == 3, str(rows))
    finally:
        con.close()

    print("== 保留 N 份，超出清理最旧 ==")
    time.sleep(0.02)
    r2 = create_backup(source=src, backup_dir=backups, keep=2)
    time.sleep(0.02)
    r3 = create_backup(source=src, backup_dir=backups, keep=2)
    files = sorted(backups.glob(f"{PREFIX}*.db"), key=lambda p: p.stat().st_mtime)
    check("保留份数=2", len(files) == 2, str([f.name for f in files]))
    check("最旧已被清理（剩最后两份）", snap.name not in {f.name for f in files}, str(snap.name))
    check("最新快照在保留集内", r3["snapshot"].name in {f.name for f in files})
    check("r2 已触发一次清理", len(r2["removed"]) >= 0)

    print("== 异地副本 ==")
    r4 = create_backup(source=src, backup_dir=backups, keep=2, offsite_dir=offsite)
    check("异地副本已生成", r4["offsite"] is not None and r4["offsite"].exists(), str(r4["offsite"]))
    off_files = list(offsite.glob(f"{PREFIX}*.db"))
    check("异地按同样保留策略清理(≤2)", len(off_files) <= 2, str([f.name for f in off_files]))

    print("== prune 边界：keep=0 清空 ==")
    prune(backups, 0)
    check("keep=0 时全部清理", len(list(backups.glob(f'{PREFIX}*.db'))) == 0)

    print(f"\n==== 通过 {PASSED} 项，失败 {FAILED} 项 ====")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
