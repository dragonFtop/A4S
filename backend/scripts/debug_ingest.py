"""单份粘贴入库诊断：打印完整异常链。"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
TEST_DB = BACKEND / "data" / "debug_ingest.db"
os.environ["DATABASE_URL"] = "sqlite:///./data/debug_ingest.db"
if TEST_DB.exists():
    TEST_DB.unlink()

from app.db import SessionLocal, get_vector_store  # noqa: E402
from app.topic_seed import ensure_builtin  # noqa: E402
from app.db import init_db  # noqa: E402
from app.services import ingest_service  # noqa: E402
from app.store import Documents  # noqa: E402

init_db()
db = SessionLocal()
ensure_builtin(db)

text = "栈是后进先出的线性表。队列是先进先出。\n\n" * 40
doc = ingest_service.create_from_text(db, content=text, title="诊断样本")
print("文档:", doc.id, doc.status.value)

store = get_vector_store()
print("向量库启用:", store.is_compatible(), "索引维度:", store.index_dimension(), "配置维度:", store.dimension)

start = time.time()
ingest_service.run_ingest(doc.id)
print(f"处理耗时 {time.time() - start:.1f}s")

db2 = SessionLocal()
fresh = Documents(db2).get(doc.id)
print("状态:", fresh.status.value if fresh else None)
print("片段数:", fresh.chunk_count if fresh else None)
print("错误:", fresh.error if fresh else None)
db2.close()
db.close()
