"""M5 冒烟测试：回顾报告与定时推送（FR-07 / FR-08 / FR-09）。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/smoke_m5.py

独立库 data/test_m5.db，不碰开发库。
重点验证四件事：
  1. 空库能生成「提示性报告」而不是报错；
  2. 报告正文的五个部分齐全，且数字与库里的真实数据一致；
  3. 时间窗真的是 7 天，不是「最近一段随便」；
  4. 邮件发不出去时，报告仍然保存，失败原因能回显。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TEST_DB = BACKEND / "data" / "test_m5.db"
os.environ["DATABASE_URL"] = "sqlite:///./data/test_m5.db"
os.environ["LLM_API_KEY"] = ""
os.environ["SMTP_HOST"] = ""
os.environ["SMTP_USER"] = ""
os.environ["SMTP_PASSWORD"] = ""
os.environ["SMTP_TO"] = ""
os.environ["SCHEDULER_ENABLED"] = "false"

if TEST_DB.exists():
    TEST_DB.unlink()

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app
from scripts._auth import login  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [ok] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


# ---- 假模型：出题用一个，写回顾分析用另一个 ----

ENCRYPTION = (
    "# 对称与非对称加密\n\n"
    "对称加密使用同一把密钥完成加密与解密，优点是速度快，适合加密大量数据；"
    "难点在于密钥分发，通信双方必须先安全地共享密钥。AES 是常见的对称加密算法，"
    "分组长度 128 位，密钥长度可取 128、192 或 256 位。工作模式决定分组之间如何关联："
    "ECB 对每个分组独立加密，相同明文块会得到相同密文块，因此会泄露数据的结构信息；"
    "CBC 让每个明文块先与前一个密文块异或，需要初始向量 IV，同一密钥下 IV 必须随机且不可预测。\n\n"
    "非对称加密使用公钥加密、私钥解密，公钥可以公开，因此解决了密钥分发问题，"
    "但运算速度远慢于对称加密。RSA 的安全性依赖大整数分解困难：把两个大素数相乘很容易，"
    "但由乘积反推两个素数在计算上不可行。实践中常用混合加密：用非对称加密传递会话密钥，"
    "再用对称加密传输正文。这样既避免了密钥分发问题，又保留了对称加密的速度。\n\n"
    "哈希函数把任意长度输入映射为固定长度输出，具有单向性：由摘要无法还原原文。"
    "哈希不提供加密所需的可逆性，因此不能用来传输机密内容，只用于完整性校验与口令存储。"
    "口令存储不能直接保存哈希值，因为相同口令会得到相同摘要，攻击者可以用彩虹表批量比对；"
    "正确做法是加盐并选用慢哈希算法，使每次计算都足够昂贵，从而抬高暴力破解成本。\n\n"
    "数字签名用私钥对消息摘要签名，任何人可用公钥验证。它同时提供身份认证与完整性，"
    "但不提供机密性——签名不隐藏消息内容。签名与加密的方向相反：加密用对方公钥，"
    "签名用自己的私钥，这是初学者最容易混淆的一点。\n\n"
    "密钥管理往往比算法本身更容易出问题。密钥硬编码在代码仓库里、在日志里打印密钥、"
    "长期不轮换，都会让再强的算法形同虚设。工程上的基本要求是：密钥从环境变量或密钥管理服务读取，"
    "按用途与环境隔离，并保留轮换与吊销的通道。"
)

PROCESS = (
    "# 进程与线程\n\n"
    "进程是资源分配的基本单位，拥有独立的地址空间；线程是调度的基本单位，"
    "同一进程内的线程共享地址空间与打开的文件，因此切换开销比进程小得多。"
    "共享内存让线程通信高效，也带来了数据竞争：多个线程同时修改同一变量时结果取决于调度顺序。\n\n"
    "互斥锁把临界区串行化，代价是可能死锁。死锁的四个必要条件同时成立才会发生："
    "互斥、请求并保持、不可剥夺、循环等待。破坏任意一个即可避免，实践中常按固定顺序加锁。\n\n"
    "虚拟内存让每个进程以为自己独占地址空间，由页表完成虚拟地址到物理地址的映射。"
    "缺页中断发生时操作系统从磁盘换入页面；换页过于频繁会导致抖动，此时增加并发反而降低吞吐。"
)


class FakeGenerateLLM:
    """出题用：答案固定，便于精确控制得分。"""

    def __init__(self) -> None:
        self.calls = 0

    async def chat(self, messages: list[dict], **options) -> str:
        self.calls += 1
        text = "\n".join(item["content"] for item in messages)
        question_type = re.search(
            r"题型：(single_choice|multi_choice|true_false|short_answer)", text
        ).group(1)
        count = int(re.search(r"数量：(\d+)\s*道", text).group(1))
        chunk_ids = re.findall(r"\[chunk:([A-Za-z0-9\-]+)\]", text)
        if not chunk_ids:
            return "[]"
        items = []
        for index in range(count):
            base = {
                "explanation": "解析：材料里明确写了这一点。",
                "source_chunk_ids": [chunk_ids[index % len(chunk_ids)]],
                "knowledge_point": "材料要点",
            }
            if question_type == "single_choice":
                items.append({**base, "stem": f"单选第 {index} 题：哪一项正确？",
                              "options": ["甲", "乙", "丙", "丁"], "answer": "A"})
            elif question_type == "true_false":
                items.append({**base, "stem": f"判断第 {index} 题：材料支持该结论。",
                              "options": [], "answer": "正确"})
            else:
                items.append({**base, "stem": f"简答第 {index} 题：简述核心机制。",
                              "options": [], "answer": "参考答案：先甲后乙。"})
        return json.dumps(items, ensure_ascii=False)

    async def stream(self, messages: list[dict]):  # pragma: no cover
        yield await self.chat(messages)


class FakeReviewLLM:
    """写分析用：返回合法 JSON，验证「模型版」路径。"""

    def __init__(self) -> None:
        self.calls = 0
        self.last_prompt = ""

    async def chat(self, messages: list[dict], **options) -> str:
        self.calls += 1
        self.last_prompt = messages[-1]["content"]
        return json.dumps(
            {
                "topic_summaries": [{"topic": "密码学", "summary": "覆盖对称加密、非对称加密与哈希的口令存储。"}],
                "weak_points": ["密码学的 ECB 与 CBC 差异没分清，正确率偏低。"],
                "suggestions": ["重读 CBC 的初始向量要求，再针对该章出 5 道题。"],
            },
            ensure_ascii=False,
        )

    async def stream(self, messages: list[dict]):  # pragma: no cover
        yield await self.chat(messages)


def wait_ready(client: TestClient, document_id: str, timeout: int = 120) -> str:
    deadline = time.time() + timeout
    status = "pending"
    while time.time() < deadline:
        for doc in client.get("/api/documents").json():
            if doc["id"] == document_id:
                status = doc["status"]
                break
        if status in ("ready", "failed"):
            return status
        time.sleep(0.4)
    return status


def build_answers(questions: list[dict], wrong_ids: set[str]) -> list[dict]:
    answers = []
    for question in questions:
        wrong = question["id"] in wrong_ids
        if question["type"] == "short_answer":
            answers.append(
                {
                    "question_id": question["id"],
                    "response": "我的理解是先甲后乙。",
                    "self_grade": "unsure" if wrong else "mastered",
                    "duration_seconds": 25,
                }
            )
        elif question["type"] == "true_false":
            answers.append(
                {
                    "question_id": question["id"],
                    "response": "错误" if wrong else "正确",
                    "duration_seconds": 6,
                }
            )
        else:
            answers.append(
                {"question_id": question["id"], "response": "B" if wrong else "A", "duration_seconds": 8}
            )
    return answers


def new_session(client: TestClient, topic_id: str, counts: dict[str, int]) -> dict:
    r = client.post(
        "/api/practice/generate",
        json={"topic_ids": [topic_id], "counts": counts, "difficulty": "medium"},
    )
    assert r.status_code == 202, r.text
    job = client.get(f"/api/practice/jobs/{r.json()['id']}").json()
    assert job["state"] == "done", job
    return client.get(f"/api/practice/sessions/{job['session_id']}").json()


def main() -> int:
    with TestClient(app) as client:
        login(client)
        print("\n== 1. 空库生成提示性报告 ==")
        empty = client.post("/api/reviews", json={"window_days": 7})
        check("空库不报错", empty.status_code == 201, empty.text[:200])
        empty_report = empty.json() if empty.status_code == 201 else {}
        md = empty_report.get("markdown", "")
        check("空库标记为无行为", empty_report.get("summary", {}).get("has_activity") is False)
        check("空库给出五节标题", all(h in md for h in ("一、本期新增资料", "二、练习完成情况", "三、错题整理", "四、薄弱点分析", "五、复习建议")))
        check("空库说明没有新增资料", "本期没有新增资料" in md)
        check("空库推送跳过而非失败", empty_report.get("push_status") == "skipped", str(empty_report.get("push_status")))

        print("\n== 2. 造数据：两份资料 + 一次练习 ==")
        topic_id = client.post("/api/topics", json={"name": "密码学", "kind": "topic"}).json()["id"]
        other_id = client.post("/api/topics", json={"name": "操作系统", "kind": "topic"}).json()["id"]

        doc_a = client.post(
            "/api/documents/paste",
            json={"content": ENCRYPTION, "title": "密码学讲义", "topic_id": topic_id},
        ).json()
        doc_b = client.post(
            "/api/documents/paste",
            json={"content": PROCESS, "title": "进程与线程", "topic_id": other_id},
        ).json()
        check("资料 A 就绪", wait_ready(client, doc_a["id"]) == "ready")
        check("资料 B 就绪", wait_ready(client, doc_b["id"]) == "ready")

        # 把 B 挪到 20 天前，用来验证时间窗真的在过滤
        from datetime import datetime, timedelta, timezone

        from app.infrastructure.db.session import SessionLocal
        from app.infrastructure.db.models import DocumentORM

        with SessionLocal() as db:
            row = db.get(DocumentORM, doc_b["id"])
            row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=20)
            db.commit()

        settings.llm_api_key = "test-key"
        from app.services import practice_service

        practice_service.build_llm = lambda: FakeGenerateLLM()  # type: ignore[assignment]
        counts = {"single_choice": 3, "true_false": 1, "short_answer": 1}
        session = new_session(client, topic_id, counts)
        check("出题 5 道", len(session["questions"]) == 5, str(len(session["questions"])))

        client.post(f"/api/practice/sessions/{session['id']}/start")
        wrong_ids = {q["id"] for q in session["questions"][:2]}
        submitted = client.post(
            f"/api/practice/sessions/{session['id']}/submit",
            json={"answers": build_answers(session["questions"], wrong_ids)},
        ).json()
        stats = submitted["stats"]
        check("5 题错 2 题 → 60 分", abs(stats["accuracy"] - 60.0) < 0.01, str(stats))
        check("错题入本 2 条", len(client.get("/api/practice/wrong-answers").json()) == 2)

        print("\n== 3. 报告数字与库一致 ==")
        created = client.post("/api/reviews", json={"window_days": 7})
        check("生成成功", created.status_code == 201, created.text[:300])
        report = created.json()
        summary = report["summary"]
        md = report["markdown"]

        check("窗口内资料 1 份（B 在 20 天前）", summary["documents"] == 1, str(summary["documents"]))
        check("窗口内完成 1 套", summary["sessions"] == 1, str(summary["sessions"]))
        check("作答 5 题", summary["answered"] == 5, str(summary["answered"]))
        check("答对 3 题", summary["correct"] == 3, str(summary["correct"]))
        check("正确率 60%", abs(summary["accuracy"] - 60.0) < 0.01, str(summary["accuracy"]))
        check("错题 2 道", summary["wrong"] == 2, str(summary["wrong"]))
        check("五部分齐全", all(h in md for h in ("一、本期新增资料", "二、练习完成情况", "三、错题整理", "四、薄弱点分析", "五、复习建议")))
        check("报告按主题分组", "密码学" in md and "操作系统" not in md, "操作系统不应出现在 7 天报告里")
        check("报告含正确率数字", "60.0%" in md or "60%" in md)
        check("报告含错题题干", session["questions"][0]["stem"][:12] in md)
        check("模型不可用时降级为规则", report["summary"]["analysis_source"] == "rule", str(report["summary"]["analysis_source"]))
        check("规则版在正文标注来源", "规则生成" in md)

        print("\n== 4. 时间窗真的是 7 天 ==")
        wide = client.post("/api/reviews", json={"window_days": 30}).json()
        check("30 天窗口含 2 份资料", wide["summary"]["documents"] == 2, str(wide["summary"]["documents"]))
        check("非法窗口被拒", client.post("/api/reviews", json={"window_days": 5}).status_code == 422)

        print("\n== 5. 模型可用时走 LLM 分析 ==")
        import app.infrastructure.llm.openai_compat as compat

        fake_review = FakeReviewLLM()
        original_build = compat.build_llm
        compat.build_llm = lambda: fake_review  # type: ignore[assignment]
        try:
            llm_report = client.post("/api/reviews", json={"window_days": 7}).json()
        finally:
            compat.build_llm = original_build
        check("调用了一次模型", fake_review.calls == 1, str(fake_review.calls))
        check("来源标记为 llm", llm_report["summary"]["analysis_source"] == "llm", str(llm_report["summary"]["analysis_source"]))
        check("模型给出的薄弱点进了正文", "ECB 与 CBC" in llm_report["markdown"], llm_report["markdown"][:200])
        check("模型给出的建议进了正文", "初始向量" in llm_report["markdown"])
        check("prompt 里带上了真实数字", "60" in fake_review.last_prompt)
        check("无来源时不留规则标注", "规则生成" not in llm_report["markdown"])

        print("\n== 6. 邮件未配置时的失败处理 ==")
        failed = client.post(f"/api/reviews/{llm_report['id']}/push").json()
        check("推送返回失败", failed["ok"] is False)
        check("失败原因可读", "SMTP" in failed["detail"], failed["detail"])
        check("报告仍然存在", client.get(f"/api/reviews/{llm_report['id']}").status_code == 200)
        check("推送状态落库为 failed", failed["report"]["push_status"] == "failed")
        check("push-state 能读到失败", client.get("/api/reviews/push-state").json()["status"] == "failed")

        not_configured = client.post("/api/settings/test-mail", json={}).json()
        check("测试发送提示未配置", not_configured["ok"] is False and "SMTP" in not_configured["message"], str(not_configured))

        print("\n== 7. 定时调度启停 ==")
        from app.infrastructure import scheduler

        check("默认不注册任务", scheduler.state()["enabled"] is False)
        client.put("/api/settings", json={"scheduler_enabled": True, "scheduler_cron_day_of_week": "mon",
                                          "scheduler_cron_hour": 9, "scheduler_cron_minute": 0})
        state = scheduler.state()
        check("开启后注册任务", state["job_registered"] is True, str(state))
        check("下次触发时间可得", bool(state["next_run"]), str(state))
        client.put("/api/settings", json={"scheduler_enabled": False})
        check("关闭后移除任务", scheduler.state()["job_registered"] is False)

        ran = client.post("/api/schedule/run").json()
        check("可手动触发一次", ran["ok"] is True, str(ran))
        scheduled = [r for r in client.get("/api/reviews").json() if r["trigger"] == "schedule"]
        check("定时来源被记录", len(scheduled) >= 1, str(len(scheduled)))
        check("定时报告无 SMTP 时记录失败", scheduled[0]["push_status"] == "failed", scheduled[0]["push_status"])

        print("\n== 8. 列表与删除 ==")
        reports = client.get("/api/reviews").json()
        check("列表按时间倒序", reports[0]["created_at"] >= reports[-1]["created_at"])
        target = reports[-1]["id"]
        check("删除成功", client.delete(f"/api/reviews/{target}").status_code == 200)
        check("删除后 404", client.get(f"/api/reviews/{target}").status_code == 404)
        check("删除不存在的报告 404", client.delete(f"/api/reviews/{target}").status_code == 404)

        print("\n== 9. 概览的完成套数不再为 0（回归）==")
        overview = client.get("/api/overview").json()
        check(
            "概览完成套数统计到 submitted",
            overview["practice"]["finished"] >= 1,
            str(overview["practice"]),
        )
        check(
            "概览平均分非零",
            overview["practice"]["avg_score"] > 0,
            str(overview["practice"]["avg_score"]),
        )

    print(f"\n{'=' * 46}")
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for name in FAILED:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
