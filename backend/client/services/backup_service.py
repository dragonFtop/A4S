"""客户端数据库一致性备份（P4 / FR-4.2 / AC-18）。

用 SQLite 的 `VACUUM INTO` 生成**一致性快照**：它在一个读事务里把整库写出，
不会像直接拷贝文件那样在 WAL/写入中途拿到半截数据。

保留策略：按时间戳命名的快照，保留最近 N 份，超出清理最旧；
可选配置异地目录，每次额外复制一份过去。
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from shared.config import DATA_DIR, settings

PREFIX = "client-"


def _source_path() -> Path:
    url = settings.resolved_database_url
    if not url.startswith("sqlite:///"):
        raise RuntimeError("备份仅支持 SQLite 数据库")
    return Path(url.removeprefix("sqlite:///"))


def _snapshot(src: Path, dest: Path) -> None:
    # VACUUM INTO 的目标在部分 SQLite 版本不接受参数绑定，这里用字面量并转义单引号
    literal = str(dest).replace("'", "''")
    con = sqlite3.connect(str(src))
    try:
        con.execute(f"VACUUM INTO '{literal}'")
    finally:
        con.close()


def prune(backup_dir: Path, keep: int) -> list[Path]:
    """删除超出 keep 的最旧快照，返回被删列表。"""
    files = sorted(
        backup_dir.glob(f"{PREFIX}*.db"),
        key=lambda p: p.stat().st_mtime,
    )
    if keep < 0:
        keep = 0
    surplus = files[:-keep] if keep else files
    removed: list[Path] = []
    for path in surplus:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            pass
    return removed


def create_backup(
    *,
    source: Path | None = None,
    backup_dir: Path | None = None,
    keep: int | None = None,
    offsite_dir: Path | None = None,
) -> dict:
    """生成一份快照并按保留策略清理，返回 {snapshot, removed, offsite}。"""
    src = source or _source_path()
    if not src.exists():
        raise FileNotFoundError(f"源数据库不存在：{src}")

    out_dir = backup_dir or (Path(settings.backup_dir) if settings.backup_dir else DATA_DIR / "backups")
    out_dir.mkdir(parents=True, exist_ok=True)
    keep_n = settings.backup_keep if keep is None else keep
    offsite = offsite_dir or (Path(settings.backup_offsite_dir) if settings.backup_offsite_dir else None)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = out_dir / f"{PREFIX}{stamp}.db"
    _snapshot(src, dest)

    removed = prune(out_dir, keep_n)

    offsite_copy: Path | None = None
    if offsite is not None:
        offsite.mkdir(parents=True, exist_ok=True)
        offsite_copy = offsite / dest.name
        shutil.copy2(dest, offsite_copy)
        if keep_n is not None:
            prune(offsite, keep_n)

    return {"snapshot": dest, "removed": removed, "offsite": offsite_copy}
