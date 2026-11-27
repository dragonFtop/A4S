"""M8 冒烟测试：知识库体检道（巡检员 / 修复员 / 审核员）。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/smoke_m8_health.py

使用独立数据库 data/test_m8.db，不污染开发库。
体检不调模型，所以这里全部是确定性断言。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TEST_DB = BACKEND / "data" / "test_m8.db"
os.environ["DATABASE_URL"] = "sqlite:///./data/test_m8.db"

if TEST_DB.exists():
    TEST_DB.unlink()

from fastapi.testclient import TestClient  # noqa: E402

from shared.infrastructure.db.models import ChunkORM, DocumentORM, TopicORM  # noqa: E402
from shared.infrastructure.db.session import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from scripts._auth import login, sign_in  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

GOOD_TEXT = (
    "分组密码把明文切成固定长度的块，再用密钥逐块变换。CBC 模式把上一块的密文与当前明文异或，"
    "所以第一块需要一个随机初始向量 IV；IV 不必保密，但必须不可预测，否则相同明文会得到相同密文。"
    "CTR 模式把分组密码当成流密码使用，对递增的计数器加密后与明文异或，加解密完全相同，"
    "可以并行处理，但计数器绝不能重复，一旦重用，两次密文的异或就泄露明文异或。"
    "ECB 模式相同的明文块会产生相同的密文块，会暴露数据的结构，因此不适合加密超过一个块的消息。"
    "选择模式时要在并行性、随机访问、错误传播与完整性保护之间权衡。"
    "CBC 的错误会传播到下一块，但不会影响更后面的块；CTR 与 OFB 这类流模式只错对应的一位，"
    "代价是必须自己加消息认证码，否则攻击者翻转密文的一位就能可预测地翻转明文的一位。"
)

SECOND_TEXT = (
    "公钥密码用一对密钥解决密钥分发问题，公开的那把用来加密或验签，私有的那把用来解密或签名。"
    "RSA 的安全性依赖大整数分解，密钥长度通常取 2048 位以上；椭圆曲线把离散对数搬到曲线群上，"
    "同等安全强度下密钥更短，适合移动设备与握手频繁的场景。签名与加密是两件事：签名用私钥生成、"
    "公钥验证，加密反过来；实际协议里还要加填充与随机数，否则相同明文会得到相同密文。"
    "中间人攻击的防线不是加密本身，而是证书链与域名校验；证书过期、自签名证书都会让握手失败。"
    "实践中还要注意侧信道：解密耗时会泄露信息，所以实现里要做常数时间比较与盲化。"
)

UNCAT_TEXT = (
    "线性代数研究向量空间与线性映射。矩阵的秩等于行秩也等于列秩，初等变换不改变秩；"
    "可逆矩阵的行列式不为零，特征值之积等于行列式，相似矩阵有相同的特征多项式。"
    "特征分解把矩阵写成对角形，前提是有足够多的线性无关特征向量；实对称矩阵一定可以正交对角化，"
    "所以二次型总能化成标准形。做题时先判断矩阵形状，再决定用消元还是分解；"
    "求逆之前先看行列式是否为零，解方程组之前先看秩与增广矩阵的秩是否一致。"
)

SHORT_TEXT = "这是一份太短的资料，只有一句话，问不出什么。"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [ok] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def _space_id(client: TestClient) -> str:
    return client.get("/api/auth/me").json()["current_space"]["id"]


def _document(space_id: str, doc_id: str, name: str, *, topic_id: str, text: str | None, status: str = "ready") -> None:
    db = SessionLocal()
    try:
        db.add(
            DocumentORM(
                id=doc_id,
                name=name,
                ext="md",
                size=len(text or ""),
                status=status,
                chunk_count=1 if text else 0,
                error="扫描件没有文字层" if status == "failed" else None,
                topic_id=topic_id,
                space_id=space_id,
            )
        )
        if text:
            db.add(
                ChunkORM(
                    id=f"chunk-{doc_id}",
                    document_id=doc_id,
                    chunk_index=0,
                    text=text,
                    topic_id=topic_id,
                    space_id=space_id,
                )
            )
        db.commit()
    finally:
        db.close()


def seed_healthy(space_id: str) -> None:
    """两份互相独立的资料，凑成一份没有问题的知识库。"""
    db = SessionLocal()
    try:
        db.add(TopicORM(space_id=space_id, id="t-good", name="密码学", kind="topic"))
        db.commit()
    finally:
        db.close()
    _document(space_id, "d-good-1", "分组密码笔记", topic_id="t-good", text=GOOD_TEXT)
    _document(space_id, "d-good-2", "公钥密码讲义", topic_id="t-good", text=SECOND_TEXT)


def seed_broken(space_id: str) -> None:
    """把 PRD 里列出的几类问题各造一份。"""
    db = SessionLocal()
    try:
        db.add(TopicORM(space_id=space_id, id="t-empty", name="操作系统", kind="topic"))
        db.commit()
    finally:
        db.close()
    _document(space_id, "d-fail", "扫描版教材", topic_id="t-good", text=None, status="failed")
    _document(space_id, "d-empty", "纯图片讲义", topic_id="t-good", text=None)
    _document(space_id, "d-short", "一页速记", topic_id="t-good", text=SHORT_TEXT)
    # 与 d-good-1 同名，触发重复检测
    _document(space_id, "d-dup", "分组密码笔记", topic_id="t-good", text=GOOD_TEXT + "另一份拷贝。")
    _document(space_id, "d-uncat", "零散摘录", topic_id="uncategorized", text=UNCAT_TEXT)


def main() -> int:
    with TestClient(app) as client:
        login(client)

        print("\n== 1. 还没跑过体检 ==")
        resp = client.get("/api/kb-health/latest")
        check("无记录返回 404", resp.status_code == 404, str(resp.status_code))

        print("\n== 2. 空库体检 ==")
        resp = client.post("/api/kb-health/run")
        check("空库也能跑", resp.status_code == 200, str(resp.status_code))
        report = resp.json()
        check("空库健康分 0", report.get("score") == 0, str(report.get("score")))
        checks = [item.get("check") for item in report.get("findings") or []]
        check("报出空库", checks == ["empty_library"], str(checks))
        proposals = report.get("proposals") or []
        check("给出导入建议", [item.get("action") for item in proposals] == ["import"], str(proposals))
        roles = {step.get("role") for step in report.get("steps") or []}
        check("步骤覆盖三个角色", roles == {"inspector", "fixer", "verifier"}, str(roles))
        check("空库没有模型调用", all(step.get("tool") != "model" for step in report.get("steps") or []))
        empty_run_id = report.get("run_id")

        print("\n== 3. 健康的知识库 ==")
        space_id = _space_id(client)
        seed_healthy(space_id)
        resp = client.post("/api/kb-health/run")
        report = resp.json()
        check("健康库满分", report.get("score") == 100, str(report.get("score")))
        check("没有问题项", report.get("findings") == [], str(report.get("findings")))
        check("没有修复提案", report.get("proposals") == [], str(report.get("proposals")))
        check("检查了两份资料", report.get("documents") == 2, str(report.get("documents")))
        check("每份资料都有一步", len([s for s in report.get("steps") or [] if s.get("tool") == "inspect_document"]) == 2)
        healthy_run_id = report.get("run_id")
        check("latest 返回最近一次", client.get("/api/kb-health/latest").json().get("run_id") == healthy_run_id)

        print("\n== 4. 有问题的知识库 ==")
        seed_broken(space_id)
        before = client.get("/api/documents").json()
        before_ids = {item["id"] for item in before}
        report = client.post("/api/kb-health/run").json()
        checks = {item.get("check") for item in report.get("findings") or []}
        for expected in ("parse_failed", "empty_chunks", "too_short", "duplicate", "uncategorized", "topic_empty"):
            check(f"报出 {expected}", expected in checks, str(checks))
        severities = [item.get("severity") for item in report.get("findings") or []]
        check("高危排在前面", severities and severities[0] == "high", str(severities))
        check("健康分被扣", 0 < int(report.get("score", 0)) < 100, str(report.get("score")))
        findings = report.get("findings") or []
        proposals = report.get("proposals") or []
        check("每条问题都有修复建议", len(proposals) == len(findings), f"{len(proposals)} vs {len(findings)}")
        finding_ids = {item.get("id") for item in findings}
        check(
            "建议都能对回问题",
            all(item.get("finding_id") in finding_ids for item in proposals),
            str([item.get("finding_id") for item in proposals]),
        )
        dup = next((item for item in findings if item.get("check") == "duplicate"), {})
        check("重复项带两份资料 id", len((dup.get("evidence") or {}).get("document_ids") or []) == 2, str(dup))
        roles = {step.get("role") for step in report.get("steps") or []}
        check("体检步骤仍是三角色", roles == {"inspector", "fixer", "verifier"}, str(roles))
        after_ids = {item["id"] for item in client.get("/api/documents").json()}
        check("体检不改资料", before_ids == after_ids, f"{len(before_ids)} vs {len(after_ids)}")

        print("\n== 5. 运行账本与空间隔离 ==")
        latest = client.get("/api/kb-health/latest").json()
        check("latest 是最新一次", latest.get("run_id") == report.get("run_id"))
        check("状态为 completed", latest.get("status") == "completed", str(latest.get("status")))
        check("没有模型调用", all(step.get("role") != "model" for step in latest.get("steps") or []))
        check("不同运行号不同", empty_run_id != healthy_run_id != latest.get("run_id"))

        token = sign_in(client, "health-other", "health-test-123")
        client.headers["Authorization"] = f"Bearer {token}"
        other = client.get("/api/kb-health/latest")
        check("别人看不到我的体检", other.status_code == 404, str(other.status_code))

    total = len(PASSED) + len(FAILED)
    print(f"\n结果：通过 {len(PASSED)} 项，失败 {len(FAILED)} 项（共 {total} 项）")
    if FAILED:
        for name in FAILED:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())