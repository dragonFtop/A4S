"""检索消融：同一批问题，分别用「仅向量 / 仅全文 / 融合」跑一遍。

存在的理由：混合检索很容易做成"看起来更高级但没更好"。这里用一组带标准答案的
问题，把三种策略的 Recall@5 与 MRR 摆在一起——如果融合没有超过单路，
那就说明这两路其实是冗余的，该砍。

问题刻意分成两类：
- **语义型**：问法和原文措辞不同，靠意思对上（向量强）
- **字面型**：带编号、符号、英文缩写，靠字面对上（全文强）

两类都过，才说明两路是互补而不是重复。

运行：
    cd backend
    .venv/Scripts/python.exe scripts/eval_retrieval.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_tmp_dir = tempfile.mkdtemp(prefix="a4s_eval_")
os.environ["DATABASE_URL"] = f"sqlite:///{Path(_tmp_dir) / 'eval.db'}"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

TOP_K = 5

DOCS: dict[str, str] = {
    "分组密码": """# 分组密码的三种工作模式

CBC-128 模式需要随机初始化向量。工程约定里把初始化向量的样本值记作 IV-0x1F，
每次加密都必须重新生成；如果复用同一个 IV-0x1F，相同明文会得到相同密文。

ECB 模式不使用初始化向量。它把明文切成固定长度的块分别加密，相同明文块
总是产出相同的密文块，因此会泄露明文的重复结构——一张图片加密后仍能看出轮廓。

GCM 是带认证的加密模式，它在加密的同时计算认证标签，可以检出密文是否被篡改。
""",
    "拥塞控制": """# TCP 拥塞控制

慢启动阶段，拥塞窗口每收到一个确认就加一，呈指数增长。
到达慢启动阈值 ssthresh 后转入拥塞避免，改为每个往返加一，呈线性增长。
发生超时重传时，ssthresh 减半，拥塞窗口回到一个报文段，重新进入慢启动。

快速重传不等到超时：收到三个重复确认就立刻重传丢失的报文段。
""",
    "进程调度": """# 进程调度

时间片轮转把 CPU 时间切成固定长度的时间片，轮流分给就绪队列里的进程，
适合交互式场景。多级反馈队列按优先级排队，短作业会更快拿到 CPU。
优先级反转发生在低优先级进程持有锁、高优先级进程等待该锁的时候。
""",
    "HTTP 缓存": """# HTTP 缓存

强缓存由 Cache-Control 的 max-age 控制，期间浏览器不再发请求。
协商缓存用 ETag 与 If-None-Match 比对，未变更时服务器返回 304 节省带宽。
私有缓存只属于单个用户，公共缓存可被代理服务器共享。
""",
    "Redis 持久化": """# Redis 持久化

RDB 是某一时刻的全量快照，恢复快但可能丢最后一次快照之后的数据。
AOF 追加写命令，可以配置每秒同步或每次写入同步，后者最安全也最慢。
混合持久化把 RDB 与 AOF 结合，重写时以前者为基底、后者记录增量。
""",
    "Git 分支": """# Git 分支模型

分支本质上只是指向某个提交的可变指针，所以创建和切换都很快。
变基会把当前分支的提交逐个重放到目标分支之上，历史更线性，但会改写提交哈希。
合并产生一个双亲提交，保留真实的分叉历史，适合已经推送过的公共分支。
""",
    "数据库索引": """# 索引与并发控制

B+ 树索引在插入时如果目标页已满，会触发页分裂：把一半记录挪到新页，并更新父节点。
聚簇索引的叶子节点直接存放整行数据，因此一张表只能有一个。

MVCC 通过保存多版本让读操作不阻塞写操作：普通查询走快照读，只看到事务开始时的版本；
加锁查询走当前读，会读到最新提交的数据。
""",
}

# (问题, 期望命中的资料名, 用于判定命中的短语, 类型)
CASES: list[tuple[str, str, str, str]] = [
    # 语义型：问法与原文措辞不同
    ("为什么同样的明文重复出现会暴露规律", "分组密码", "泄露明文的重复结构", "语义"),
    ("有什么办法能发现密文被人改过", "分组密码", "认证标签", "语义"),
    ("连接刚开始传输时窗口增长有多快", "拥塞控制", "呈指数增长", "语义"),
    ("超时重传之后发送方会怎么调整", "拥塞控制", "减半", "语义"),
    ("索引插入时页放不下会发生什么", "数据库索引", "页分裂", "语义"),
    ("怎么让读操作不阻塞写操作", "数据库索引", "不阻塞写操作", "语义"),
    # 纯改写型：换了整套说法，字面几乎对不上，只能靠意思
    ("发送端怎么察觉线路开始变得拥挤", "拥塞控制", "减半", "改写"),
    ("两个操作一个读一个写会不会互相等待", "数据库索引", "不阻塞写操作", "改写"),
    ("怎么证明文件在传输途中没被动过手脚", "分组密码", "认证标签", "改写"),
    ("进程的优先级高的反而等了很久是什么情况", "进程调度", "优先级反转", "改写"),
    # 字面型：带编号、符号、英文缩写
    ("IV-0x1F 是什么", "分组密码", "IV-0x1F", "字面"),
    ("CBC-128 需要什么", "分组密码", "CBC-128", "字面"),
    ("ssthresh 在什么时候减半", "拥塞控制", "ssthresh 减半", "字面"),
    ("快速重传靠几个重复确认触发", "拥塞控制", "三个重复确认", "字面"),
    ("B+ 树 页分裂", "数据库索引", "页分裂", "字面"),
    ("MVCC 快照读看到哪个版本", "数据库索引", "快照读", "字面"),
]


def hit_rank(docs: list[tuple[str, str]], expected: str, phrase: str) -> int | None:
    """返回首个命中短语的名次（从 1 开始），没命中返回 None。

    docs = [(chunk_id, text)]，顺序即检索排序。
    """
    for rank, (_, text) in enumerate(docs, start=1):
        if phrase in (text or ""):
            return rank
    return None


def evaluate(name: str, results: list[int | None]) -> dict:
    total = len(results)
    hits = [r for r in results if r is not None]
    recall = len(hits) / total if total else 0.0
    mrr = sum(1.0 / r for r in hits) / total if total else 0.0
    return {"name": name, "recall": recall, "mrr": mrr, "hits": len(hits), "total": total}


def main() -> int:
    with TestClient(app) as client:
        reg = client.post(
            "/api/auth/register",
            json={"username": "eval", "password": "secret123"},
        )
        assert reg.status_code == 201, reg.text
        h = {"Authorization": f"Bearer {reg.json()['token']}"}
        space_id = reg.json()["space"]["id"]

        print("正在导入评测资料…")
        topic = client.post("/api/topics", json={"name": "评测", "kind": "topic"}, headers=h).json()
        doc_ids = []
        for title, body in DOCS.items():
            r = client.post(
                "/api/documents/paste",
                json={"content": body, "title": title, "topic_id": topic["id"]},
                headers=h,
            )
            doc_ids.append(r.json()["id"])

        # 等索引完成
        deadline = time.time() + 180
        while time.time() < deadline:
            docs = client.get("/api/documents", headers=h).json()
            if all(d["status"] in ("ready", "failed") for d in docs) and len(docs) == len(DOCS):
                break
            time.sleep(0.5)
        print(f"资料就绪：{[(d['name'], d['status'], d['chunk_count']) for d in docs]}\n")

        from shared.infrastructure.db import fts
        from shared.infrastructure.db.session import SessionLocal, get_vector_store
        import client.services.chat_service as chat_service

        store = get_vector_store()
        rows = {
            "vector": [],
            "keyword": [],
            "hybrid": [],
        }
        detail: list[tuple[str, str, int | None, int | None, int | None]] = []

        with SessionLocal() as db:
            for question, expected, phrase, kind in CASES:
                # 仅向量。**必须和融合走同一个阈值**：融合会丢弃低于 MIN_SIMILARITY
                # 的纯向量结果，单路若不过滤，比的就是两套标准，融合必输。
                vector = chat_service._embed_query(question)
                vec_hits = store.search(vector, top_k=TOP_K, space_id=space_id)
                vec_docs = []
                for chunk_id, distance in vec_hits:
                    if 1.0 - distance < chat_service.MIN_SIMILARITY:
                        continue
                    chunk = chat_service.SqlAlchemyChunkRepository(db).get(chunk_id, space_id)
                    if chunk is not None:
                        vec_docs.append((chunk.id, chunk.text))

                # 仅全文
                kw_ids = [cid for cid, _ in fts.search_chunks(db.connection(), question, limit=TOP_K)]
                kw_docs = []
                for chunk_id in kw_ids:
                    chunk = chat_service.SqlAlchemyChunkRepository(db).get(chunk_id, space_id)
                    if chunk is not None:
                        kw_docs.append((chunk.id, chunk.text))

                # 融合（走真实检索链路）
                # 注意用完整片段文本判定命中：retrieve 返回的 snippet 只截前 240 字，
                # 拿它判断会把长文档后半段的内容一律判成未命中。
                citations, _, _ = chat_service.retrieve(db, question, space_id=space_id)
                hy_docs = []
                for c in citations:
                    chunk = chat_service.SqlAlchemyChunkRepository(db).get(c["chunk_id"], space_id)
                    if chunk is not None:
                        hy_docs.append((chunk.id, chunk.text))

                r_vec = hit_rank(vec_docs, expected, phrase)
                r_kw = hit_rank(kw_docs, expected, phrase)
                r_hy = hit_rank(hy_docs, expected, phrase)
                rows["vector"].append(r_vec)
                rows["keyword"].append(r_kw)
                rows["hybrid"].append(r_hy)
                detail.append((kind, question, r_vec, r_kw, r_hy))

        print(f"{'类型':<6}{'问题':<28}{'仅向量':<8}{'仅全文':<8}{'融合':<8}")
        print("-" * 60)
        for kind, question, rv, rk, rh in detail:
            fmt = lambda x: f"#{x}" if x else "✗"
            print(f"{kind:<6}{question[:26]:<28}{fmt(rv):<8}{fmt(rk):<8}{fmt(rh):<8}")

        print("\n" + "=" * 52)
        print(f"{'策略':<12}{'Recall@5':<14}{'MRR':<10}{'命中/总数':<12}")
        print("-" * 52)
        for key, label in (("vector", "仅向量"), ("keyword", "仅全文"), ("hybrid", "融合")):
            m = evaluate(label, rows[key])
            print(
                f"{m['name']:<12}{m['recall'] * 100:>5.1f}%        "
                f"{m['mrr']:>6.3f}     {m['hits']}/{m['total']}"
            )
        print("=" * 52)

        # 按类型拆开看：互补性体现在"两类各自不弱、融合都不差"
        for kind in ("语义", "改写", "字面"):
            idx = [i for i, d in enumerate(detail) if d[0] == kind]
            print(f"\n{kind}型的 Recall@5：")
            for key, label in (("vector", "仅向量"), ("keyword", "仅全文"), ("hybrid", "融合")):
                subset = [rows[key][i] for i in idx]
                m = evaluate(label, subset)
                print(f"  {label:<8}{m['recall'] * 100:>5.1f}%  ({m['hits']}/{m['total']})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
