"""备份客户端数据库（VACUUM INTO 一致性快照）。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/backup_client_db.py

从环境/配置读取：DATABASE_URL（或角色默认 client.db）、BACKUP_DIR、BACKUP_KEEP、BACKUP_OFFSITE_DIR。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("APP_ROLE", "client")

from client.services.backup_service import create_backup  # noqa: E402


def main() -> int:
    result = create_backup()
    print(f"快照: {result['snapshot']}")
    if result["offsite"]:
        print(f"异地: {result['offsite']}")
    for path in result["removed"]:
        print(f"清理: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
