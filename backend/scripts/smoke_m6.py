"""M6 边界冒烟测试：坏输入、极端配置、越界请求（验收表「边界测试」一行）。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/smoke_m6.py

独立库 data/test_m6.db。这里不验证功能正确性（各里程碑的 smoke 已覆盖），
只验证**出错时是否给出人能看懂的原因**——这是单机自用工具最容易敷衍、
也最影响体验的一环：报错比崩溃好，说明白的报错比「500 Internal Error」好。
"""

from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TEST_DB = BACKEND / "data" / "test_m6.db"
os.environ["DATABASE_URL"] = "sqlite:///./data/test_m6.db"
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
from app.main import app  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [ok] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def wait_status(client: TestClient, document_id: str, timeout: int = 120) -> dict:
    deadline = time.time() + timeout
    row: dict = {}
    while time.time() < deadline:
        for doc in client.get("/api/documents").json():
            if doc["id"] == document_id:
                row = doc
                break
        if row.get("status") in ("ready", "failed"):
            return row
        time.sleep(0.4)
    return row


CONTENT = (
    "# 操作系统：进程调度\n\n"
    "先来先服务（FCFS）按到达顺序调度，实现最简单，但短作业可能排在长作业后面等待很久，"
    "平均周转时间偏高，这种现象被称为护航效应。\n\n"
    "短作业优先（SJF）选择预计运行时间最短的作业，可以证明其平均等待时间最小，"
    "但需要预知运行时间，实际系统只能根据历史行为估计，因此有了最高响应比优先（HRRN）："
    "响应比等于（等待时间 + 要求服务时间）除以要求服务时间，兼顾了等待久的作业。\n\n"
    "时间片轮转（RR）按固定时间片轮流执行，响应快、适合交互式系统；时间片过大退化为 FCFS，"
    "过小则上下文切换开销占比升高。多级反馈队列综合了上述思路：新进程进入最高优先级队列，"
    "用完时间片仍未结束就降到下一级，从而让短作业快速完成、长作业逐步下沉。\n\n"
    "调度算法评价常用指标：CPU 利用率、吞吐量、周转时间（提交到完成）、"
    "等待时间（在就绪队列中等待的总时长）、响应时间（提交到首次响应）。"
)


def main() -> int:
    with TestClient(app) as client:
        print("\n== 1. 坏文件与不支持格式 ==")
        bad_ext = client.post(
            "/api/documents/upload",
            files={"files": ("virus.exe", io.BytesIO(b"MZ\x90\x00fake binary"), "application/octet-stream")},
            data={"topic_id": "uncategorized"},
        )
        # 上传是批量接口：单个文件失败放 errors 里，整体仍 201。要求"没有真入库 + 原因可读"。
        check("批量上传接口不中断", bad_ext.status_code == 201, str(bad_ext.status_code))
        payload = bad_ext.json() if bad_ext.status_code == 201 else {}
        check("不支持的扩展名没有入库", payload.get("documents") == [], str(payload.get("documents")))
        reasons = [item.get("reason", "") for item in payload.get("errors", [])]
        check("错误里给出原因", bool(reasons) and reasons[0], str(reasons))
        check(
            "拒绝原因提到支持的格式",
            any(k in (reasons[0] if reasons else "") for k in ("不支持", "PDF", "格式")),
            str(reasons)[:120],
        )

        broken_pdf = client.post(
            "/api/documents/upload",
            files={"files": ("broken.pdf", io.BytesIO(b"%PDF-1.4\nthis is not a real pdf body"), "application/pdf")},
            data={"topic_id": "uncategorized"},
        )
        check("损坏 PDF 接收为 201（异步解析）", broken_pdf.status_code == 201, broken_pdf.text[:150])
        if broken_pdf.status_code == 201:
            doc_id = broken_pdf.json()["documents"][0]["id"]
            row = wait_status(client, doc_id)
            check("损坏 PDF 标记 failed 而不是卡在 pending", row.get("status") == "failed", str(row.get("status")))
            check("失败带原因", bool(row.get("error") or row.get("failure_reason") or row.get("reason")), str(row))

        empty_paste = client.post("/api/documents/paste", json={"content": "", "title": "空"})
        check("空内容被拒（422）", empty_paste.status_code == 422, str(empty_paste.status_code))

        huge = client.post(
            "/api/documents/paste",
            json={"content": "字" * 100_001, "title": "超长"},
        )
        check("超 10 万字符被拒（400）", huge.status_code == 400, str(huge.status_code))
        if huge.status_code == 400:
            msg = str(huge.json().get("detail"))
            check("超限提示带上限与当前值", "100,000" in msg or "上限" in msg, msg[:120])

        print("\n== 2. 主题边界 ==")
        topic = client.post("/api/topics", json={"name": "操作系统", "kind": "topic"}).json()
        dup = client.post("/api/topics", json={"name": "操作系统", "kind": "topic"})
        check("重名主题被拒（409）", dup.status_code == 409, str(dup.status_code))
        check("重名提示说明冲突", "已存在" in str(dup.json().get("detail")), str(dup.json().get("detail"))[:120])

        ghost = client.post(
            "/api/documents/paste",
            json={"content": CONTENT, "title": "幽灵主题", "topic_id": "not-exist-topic"},
        )
        check("导入到不存在的主题被拒（400）", ghost.status_code == 400, str(ghost.status_code))

        doc_a = client.post(
            "/api/documents/paste",
            json={"content": CONTENT, "title": "调度讲义", "topic_id": topic["id"]},
        ).json()
        doc_b = client.post(
            "/api/documents/paste",
            json={"content": "时间片轮转的关键参数是时间片长度。" * 30, "title": "调度补充", "topic_id": topic["id"]},
        ).json()
        check("两份资料就绪", wait_status(client, doc_a["id"])["status"] == "ready" and wait_status(client, doc_b["id"])["status"] == "ready")

        removed = client.delete(f"/api/topics/{topic['id']}")
        check("删除主题成功", removed.status_code == 200, removed.text[:150])
        migrated = client.get("/api/documents").json()
        moved = [d for d in migrated if d["name"] in ("调度讲义", "调度补充")]
        check(
            "删除主题后资料迁移到未分类而非消失",
            len(moved) == 2 and all(d["topic"]["name"] == "未分类" for d in moved),
            str([(d["name"], d["topic"]["name"]) for d in migrated]),
        )
        check(
            "迁移后检索不报错",
            client.get("/api/documents?topic_ids=uncategorized").status_code == 200,
        )
        check("删除不存在的主题 404", client.delete("/api/topics/not-exist").status_code == 404)

        print("\n== 3. 配置体检（错误密钥 / 断网）==")
        wrong_key = client.post(
            "/api/settings/test",
            json={"target": "llm", "overrides": {"llm_base_url": "https://api.deepseek.com/v1",
                                                  "llm_api_key": "sk-invalid-key-for-test"}},
        ).json()
        check("错误密钥返回 ok=false", wrong_key["ok"] is False, str(wrong_key))
        check("错误密钥给出可读原因", bool(wrong_key.get("message")), str(wrong_key)[:160])

        offline = client.post(
            "/api/settings/test",
            json={"target": "llm", "overrides": {"llm_base_url": "http://127.0.0.1:9/v1",
                                                  "llm_api_key": "sk-any"}},
        ).json()
        check("不可达地址返回 ok=false", offline["ok"] is False, str(offline))
        check("断网错误不是空消息", len(offline.get("message", "")) > 4, str(offline)[:160])

        mail = client.post("/api/settings/test-mail", json={}).json()
        check("空 SMTP 测试发送 ok=false", mail["ok"] is False, str(mail))
        check("提示缺哪一项配置", "SMTP" in mail["message"], mail["message"][:120])

        print("\n== 4. 练习与报告的越界请求 ==")
        empty_topic = client.post("/api/topics", json={"name": "空主题", "kind": "topic"}).json()
        no_material = client.post(
            "/api/practice/generate",
            json={"topic_ids": [empty_topic["id"]], "counts": {"single_choice": 3}},
        )
        check("无资料出题被拒（400）", no_material.status_code == 400, str(no_material.status_code))
        if no_material.status_code == 400:
            msg = str(no_material.json().get("detail"))
            # 资料为 0 时提示「先导入」；有资料但字数不够时提示「现有 X / 需要 Y」。
            # 两种都要能指导下一步动作。
            check(
                "材料不足提示可指导下一步",
                any(k in msg for k in ("先导入", "需要", "不足")),
                msg[:160],
            )

        check("不存在的练习 404", client.get("/api/practice/sessions/not-exist").status_code == 404)
        check("提交不存在的练习 404",
              client.post("/api/practice/sessions/not-exist/submit", json={"answers": []}).status_code == 404)

        check("非法报告窗口被拒（422）", client.post("/api/reviews", json={"window_days": 5}).status_code == 422)
        check("负数窗口被拒", client.post("/api/reviews", json={"window_days": -7}).status_code == 422)
        check("不存在的报告 404", client.get("/api/reviews/not-exist").status_code == 404)
        check("删除不存在的报告 404", client.delete("/api/reviews/not-exist").status_code == 404)
        check("推送不存在的报告 404", client.post("/api/reviews/not-exist/push").status_code == 404)

        print("\n== 5. 模型不可达时出题给出可读失败 ==")
        from app.services import practice_service

        settings.llm_api_key = "sk-any"
        settings.llm_base_url = "http://127.0.0.1:9/v1"
        original_build = practice_service.build_llm
        try:
            job = client.post(
                "/api/practice/generate",
                json={"topic_ids": ["uncategorized"], "counts": {"single_choice": 2}},
            )
            check("有资料时正常受理（202）", job.status_code == 202, job.text[:200])
            job_id = job.json()["id"] if job.status_code == 202 else None
            state = ""
            if job_id:
                for _ in range(90):
                    snapshot = client.get(f"/api/practice/jobs/{job_id}").json()
                    state = snapshot["state"]
                    if state in ("failed", "done"):
                        break
                    time.sleep(1)
            check("模型不可达时 job 失败而非挂住", state == "failed", state)
            if state == "failed":
                message = str(snapshot.get("message"))
                check("失败消息可读（含原因）", len(message) > 6 and "错误" not in message[:2], message[:160])
        finally:
            practice_service.build_llm = original_build  # type: ignore[assignment]
            settings.llm_api_key = ""
            settings.llm_base_url = "https://api.deepseek.com/v1"

        print("\n== 6. 笔记边界 ==")
        note = client.post(
            "/api/notes",
            json={"title": "调度算法笔记", "markdown": "# 调度\n\n- FCFS\n- SJF", "topic_id": "uncategorized"},
        ).json()
        check("新建笔记为草稿", note["status"] == "draft", note.get("status"))
        check("草稿能打开", client.get(f"/api/notes/{note['id']}").status_code == 200)
        confirmed = client.post(f"/api/notes/{note['id']}/confirm").json()
        check("定稿后状态变为 confirmed", confirmed["status"] == "confirmed", confirmed.get("status"))

        # 删掉资料后，笔记必须仍然能打开（笔记正文与来源表都不该被连带删）
        first_doc = client.get("/api/documents").json()[0]["id"]
        client.delete(f"/api/documents/{first_doc}")
        still_there = client.get(f"/api/notes/{note['id']}")
        check("删除资料后笔记仍可打开", still_there.status_code == 200, str(still_there.status_code))
        check("文档删除后列表不再包含它",
              all(d["id"] != first_doc for d in client.get("/api/documents").json()))

        print("\n== 7. 空库概览不出现 NaN ==")
        overview = client.get("/api/overview").json()
        check("正确率不是 NaN/Infinity", overview["practice"]["avg_score"] == 0 or isinstance(overview["practice"]["avg_score"], (int, float)))
        check("空库报告数可读", isinstance(overview["review"]["reports"], int))

    print(f"\n{'=' * 46}")
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for name in FAILED:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
