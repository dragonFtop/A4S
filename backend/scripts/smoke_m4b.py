"""M4 冒烟测试（第二批）：作答判分与错题本（FR-05 / FR-06）。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/smoke_m4b.py

独立库 data/test_m4b.db。出题走真实链路（假模型保证答案可控），
所以这里验证的是「出题 → 作答 → 判分 → 错题 → 重练」的完整闭环，
而判分口径另有纯函数单测，不依赖数据库。
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

TEST_DB = BACKEND / "data" / "test_m4b.db"
os.environ["DATABASE_URL"] = "sqlite:///./data/test_m4b.db"
os.environ["LLM_API_KEY"] = ""

if TEST_DB.exists():
    TEST_DB.unlink()

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.domain.models.entities import Question, QuestionType  # noqa: E402
from app.main import app  # noqa: E402
from app.services import grading  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [ok] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


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


class FakeLLM:
    """答案固定，判分才可断言：单选 A、多选 AB、判断 正确、简答给参考答案。"""

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
        return json.dumps(
            [
                _fake_question(question_type, index, chunk_ids[index % len(chunk_ids)])
                for index in range(count)
            ],
            ensure_ascii=False,
        )

    async def stream(self, messages: list[dict]):  # pragma: no cover - 出题不用流式
        yield await self.chat(messages)


def _fake_question(question_type: str, index: int, chunk_id: str) -> dict:
    base = {
        "explanation": "解析：材料里明确写了这一点，其他选项与原文不符。",
        "source_chunk_ids": [chunk_id],
        "knowledge_point": "材料要点",
    }
    if question_type == "single_choice":
        return {
            **base,
            "stem": f"单选第 {index} 题：关于材料中的说法，下列哪一项正确？",
            "options": ["说法一", "说法二", "说法三", "说法四"],
            "answer": "A",
        }
    if question_type == "multi_choice":
        return {
            **base,
            "stem": f"多选第 {index} 题：下列哪些说法与材料一致？",
            "options": ["说法一", "说法二", "说法三", "说法四"],
            "answer": "AB",
        }
    if question_type == "true_false":
        return {
            **base,
            "stem": f"判断第 {index} 题：材料支持这一结论。",
            "options": [],
            "answer": "正确",
        }
    return {
        **base,
        "stem": f"简答第 {index} 题：简述材料中的核心机制。",
        "options": [],
        "answer": "参考答案：按材料所述，先……再……，因此得到该结论。",
    }


def _q(question_type: QuestionType, answer: str) -> Question:
    return Question(id="q", type=question_type, stem="题干", answer=answer, explanation="解析")


def build_answers(
    questions: list[dict], *, wrong_ids: set[str] | None = None, skip_ids: set[str] | None = None
) -> list[dict]:
    """按题型给出「正确 / 错误」作答，用于精确控制得分。"""
    wrong_ids = wrong_ids or set()
    skip_ids = skip_ids or set()
    answers: list[dict] = []
    for question in questions:
        if question["id"] in skip_ids:
            continue
        wrong = question["id"] in wrong_ids
        if question["type"] == "short_answer":
            answers.append(
                {
                    "question_id": question["id"],
                    "response": "我的理解是先做 A 再做 B。",
                    "self_grade": "unsure" if wrong else "mastered",
                    "duration_seconds": 20,
                }
            )
        elif question["type"] == "true_false":
            answers.append(
                {
                    "question_id": question["id"],
                    "response": "错误" if wrong else "正确",
                    "duration_seconds": 5,
                }
            )
        elif question["type"] == "multi_choice":
            # 错法是「少选」——多选最容易搞错、也最容易被写成宽松判分的地方
            answers.append(
                {
                    "question_id": question["id"],
                    "response": "A" if wrong else "AB",
                    "duration_seconds": 8,
                }
            )
        else:
            answers.append(
                {
                    "question_id": question["id"],
                    "response": "B" if wrong else "A",
                    "duration_seconds": 6,
                }
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
    print("\n== 1. 判分口径（纯函数）==")
    check(
        "单选：带标点的 A 判对",
        grading.grade(_q(QuestionType.SINGLE_CHOICE, "A"), "A、").is_correct,
    )
    check(
        "单选：B 判错",
        not grading.grade(_q(QuestionType.SINGLE_CHOICE, "A"), "B").is_correct,
    )
    check(
        "多选：完全一致得分",
        grading.grade(_q(QuestionType.MULTI_CHOICE, "AB"), "A、B").is_correct,
    )
    check(
        "多选：顺序无关",
        grading.grade(_q(QuestionType.MULTI_CHOICE, "AB"), "BA").is_correct,
    )
    check(
        "多选：少选不得分",
        not grading.grade(_q(QuestionType.MULTI_CHOICE, "AB"), "A").is_correct,
    )
    check(
        "多选：多选不得分",
        not grading.grade(_q(QuestionType.MULTI_CHOICE, "AB"), "ABC").is_correct,
    )
    check(
        "判断：「对」等同「正确」",
        grading.grade(_q(QuestionType.TRUE_FALSE, "正确"), "对").is_correct,
    )
    check(
        "判断：true 等同「正确」",
        grading.grade(_q(QuestionType.TRUE_FALSE, "正确"), "true").is_correct,
    )
    check(
        "判断：答反判错",
        not grading.grade(_q(QuestionType.TRUE_FALSE, "正确"), "错误").is_correct,
    )
    check(
        "简答：自评掌握算对",
        grading.grade(
            _q(QuestionType.SHORT_ANSWER, "参考答案"), "我的理解", self_grade="mastered"
        ).is_correct,
    )
    check(
        "简答：自评未掌握算错",
        not grading.grade(
            _q(QuestionType.SHORT_ANSWER, "参考答案"), "我的理解", self_grade="unsure"
        ).is_correct,
    )
    check("空作答不算答过", not grading.grade(_q(QuestionType.SINGLE_CHOICE, "A"), "").answered)
    empty = grading.grade(_q(QuestionType.SINGLE_CHOICE, "A"), "")
    check("空作答不得分", not empty.is_correct)
    score = grading.summarize(
        [_q(QuestionType.SINGLE_CHOICE, "A")] * 2,
        [grading.grade(_q(QuestionType.SINGLE_CHOICE, "A"), "A"), empty],
        30.0,
    )
    check("未作答按 0 分计（1 对 1 空 = 50 分）", abs(score.accuracy - 50.0) < 0.01, str(score))
    check("正确数与作答数分开统计", score.correct == 1 and score.answered == 1, str(score))

    with TestClient(app) as client:
        from app.services import practice_service

        print("\n== 2. 准备资料 ==")
        topic_id = client.post("/api/topics", json={"name": "密码学", "kind": "topic"}).json()["id"]
        text = (
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
            "按用途与环境隔离，并保留轮换与吊销的通道。\n\n"
            "理解这些算法时要抓住各自的攻击模型：对称加密假设密钥已共享，防的是窃听；"
            "非对称加密假设公钥可被任何人获得，防的是密钥交换被窃听；哈希假设输入可能被篡改，"
            "防的是内容被悄悄替换。模型不同，能提供的保证就不同，混用会得出错误的安全结论。"
        )
        doc = client.post(
            "/api/documents/paste",
            json={"content": text, "title": "密码学讲义", "topic_id": topic_id},
        ).json()
        check("资料就绪", wait_ready(client, doc["id"]) == "ready")

        settings.llm_api_key = "test-key"
        practice_service.build_llm = lambda: FakeLLM()  # type: ignore[assignment]
        counts = {"single_choice": 2, "multi_choice": 1, "true_false": 1, "short_answer": 1}

        print("\n== 3. 开始作答与断点续答 ==")
        first = new_session(client, topic_id, counts)
        check("出题 5 道", len(first["questions"]) == 5, str(len(first["questions"])))
        check("初始为草稿", first["status"] == "draft")
        check("初始无作答记录", first["answers"] == [])
        check("草稿阶段不能提交空卷以外的东西", first["stats"] is None)

        started = client.post(f"/api/practice/sessions/{first['id']}/start").json()
        check("进入作答", started["status"] == "in_progress", started["status"])
        check("记录开始作答时间", "answer_started_at" in started["meta"], str(started["meta"].keys()))
        again = client.post(f"/api/practice/sessions/{first['id']}/start").json()
        check("重复进入幂等", again["status"] == "in_progress" and again["answers"] == [])

        single_one = next(q for q in first["questions"] if q["type"] == "single_choice")
        r = client.patch(
            f"/api/practice/sessions/{first['id']}/answers",
            json={"question_id": single_one["id"], "response": "A", "duration_seconds": 7},
        )
        check("单题保存成功", r.status_code == 200, r.text[:120])
        check("作答记录 1 条", len(r.json()["answers"]) == 1, str(r.json()["answers"]))
        check("答对即时判定", r.json()["answers"][0]["is_correct"] is True)

        r = client.patch(
            f"/api/practice/sessions/{first['id']}/answers",
            json={"question_id": single_one["id"], "response": "C", "duration_seconds": 9},
        )
        check("同题改答案覆盖不新增", len(r.json()["answers"]) == 1, str(len(r.json()["answers"])))
        check("改后判定跟随", r.json()["answers"][0]["is_correct"] is False)
        check("耗时取最新一次", r.json()["answers"][0]["duration_seconds"] == 9)

        reloaded = client.get(f"/api/practice/sessions/{first['id']}").json()
        check("重新进入能读到进度", len(reloaded["answers"]) == 1, str(reloaded["answers"]))
        check("进度里含用户答案", reloaded["answers"][0]["response"] == "C")

        r = client.patch(
            f"/api/practice/sessions/{first['id']}/answers",
            json={"question_id": single_one["id"], "response": "   "},
        )
        check("清空作答等于未答", r.json()["answers"] == [], str(r.json()["answers"]))

        print("\n== 4. 提交与判分 ==")
        answers = build_answers(first["questions"])
        r = client.post(f"/api/practice/sessions/{first['id']}/submit", json={"answers": answers})
        check("提交成功", r.status_code == 200, r.text[:160])
        submitted = r.json()
        check("状态变为已提交", submitted["status"] == "submitted", submitted["status"])
        check("全对得 100 分", abs(submitted["score"] - 100.0) < 0.01, str(submitted["score"]))
        check("统计块齐全", submitted["stats"] is not None and submitted["stats"]["correct"] == 5, str(submitted["stats"]))
        check("作答数与题量一致", submitted["stats"]["answered"] == 5, str(submitted["stats"]))
        check("有结束时间", submitted["finished_at"] is not None, str(submitted["finished_at"]))
        check("全对不产生错题", client.get("/api/practice/wrong-answers").json() == [])

        r = client.patch(
            f"/api/practice/sessions/{first['id']}/answers",
            json={"question_id": single_one["id"], "response": "B"},
        )
        check("已提交不能再改答案", r.status_code == 400, r.text[:120])
        r = client.post(f"/api/practice/sessions/{first['id']}/submit", json={"answers": answers})
        check("已提交不能重复提交", r.status_code == 400, r.text[:120])

        print("\n== 5. 错题收录 ==")
        second = new_session(client, topic_id, counts)
        wrong_single = next(q for q in second["questions"] if q["type"] == "single_choice")
        skipped_tf = next(q for q in second["questions"] if q["type"] == "true_false")
        payload = build_answers(
            second["questions"],
            wrong_ids={wrong_single["id"]},
            skip_ids={skipped_tf["id"]},
        )
        result = client.post(
            f"/api/practice/sessions/{second['id']}/submit", json={"answers": payload}
        ).json()
        check("错 1 题、空 1 题得 60 分", abs(result["score"] - 60.0) < 0.01, str(result["score"]))
        check("作答数不含未答题", result["stats"]["answered"] == 4, str(result["stats"]))

        wrongs = client.get("/api/practice/wrong-answers").json()
        check("错题本出现 1 条", len(wrongs) == 1, str(len(wrongs)))
        wrong = wrongs[0]
        check("错题对应答错的那道", wrong["question"]["id"] == wrong_single["id"])
        check("记录了你的答案", wrong["last_response"] == "B", wrong["last_response"])
        check("正确答案仍可读", wrong["question"]["answer"] == "A", wrong["question"]["answer"])
        check("解析与来源完整", bool(wrong["question"]["explanation"]) and bool(wrong["question"]["sources"]))
        check("带主题名", wrong["topic_name"] == "密码学", wrong["topic_name"])
        check("带文档名", wrong["document_name"] == "密码学讲义", wrong["document_name"])
        check("错误次数为 1", wrong["wrong_count"] == 1, str(wrong["wrong_count"]))
        check("未答题没有进错题本", all(w["question"]["id"] != skipped_tf["id"] for w in wrongs))

        print("\n== 6. 重练与次数累加 ==")
        r = client.post("/api/practice/retry", json={"wrong_ids": [wrong["id"]]})
        check("重练创建成功", r.status_code == 201, r.text[:160])
        retry = r.json()
        check("重练直接进入作答", retry["status"] == "in_progress", retry["status"])
        check("重练只有错的那一道", len(retry["questions"]) == 1, str(len(retry["questions"])))
        check("重练复用原题快照", retry["questions"][0]["id"] == wrong_single["id"])
        check("重练不带答案泄露以外的改动", retry["questions"][0]["answer"] == "A")

        r = client.post(
            f"/api/practice/sessions/{retry['id']}/submit",
            json={"answers": [{"question_id": wrong_single["id"], "response": "A"}]},
        )
        check("重练答对得 100 分", abs(r.json()["score"] - 100.0) < 0.01, str(r.json()["score"]))
        wrongs = client.get("/api/practice/wrong-answers").json()
        check("答对不新增错题条目", len(wrongs) == 1, str(len(wrongs)))
        check("答对不减少错误次数", wrongs[0]["wrong_count"] == 1, str(wrongs[0]["wrong_count"]))

        retry2 = client.post("/api/practice/retry", json={"wrong_ids": [wrong["id"]]}).json()
        client.post(
            f"/api/practice/sessions/{retry2['id']}/submit",
            json={"answers": [{"question_id": wrong_single["id"], "response": "C"}]},
        )
        wrongs = client.get("/api/practice/wrong-answers").json()
        check("再错一次仍是 1 条", len(wrongs) == 1, str(len(wrongs)))
        check("错误次数累加为 2", wrongs[0]["wrong_count"] == 2, str(wrongs[0]["wrong_count"]))
        check("最近一次答案被更新", wrongs[0]["last_response"] == "C", wrongs[0]["last_response"])

        print("\n== 7. 筛选与聚合 ==")
        by_topic = client.get(f"/api/practice/wrong-answers?topic_id={topic_id}").json()
        check("按主题筛选命中", len(by_topic) == 1, str(len(by_topic)))
        by_other = client.get("/api/practice/wrong-answers?topic_id=no-such").json()
        check("主题不匹配返回空", by_other == [])
        by_doc = client.get(f"/api/practice/wrong-answers?document_id={doc['id']}").json()
        check("按文档筛选命中", len(by_doc) == 1, str(len(by_doc)))
        by_type = client.get("/api/practice/wrong-answers?type=single_choice").json()
        check("按题型筛选命中", len(by_type) == 1, str(len(by_type)))
        by_type_empty = client.get("/api/practice/wrong-answers?type=true_false").json()
        check("题型不匹配返回空", by_type_empty == [])
        by_count = client.get("/api/practice/wrong-answers?min_wrong=2").json()
        check("按错误次数筛选", len(by_count) == 1, str(len(by_count)))
        by_count3 = client.get("/api/practice/wrong-answers?min_wrong=3").json()
        check("次数阈值过高返回空", by_count3 == [])

        summary = client.get("/api/practice/wrong-answers/summary").json()
        check("汇总条目数与明细一致", summary["total"] == len(wrongs) == 1, str(summary["total"]))
        check(
            "汇总累计次数与明细一致",
            summary["total_wrong_count"] == sum(w["wrong_count"] for w in wrongs),
            str(summary["total_wrong_count"]),
        )
        check("重复错题计数", summary["repeat_count"] == 1, str(summary["repeat_count"]))
        crypto_bucket = next(
            (item for item in summary["by_topic"] if item["topic_id"] == topic_id), None
        )
        check("主题聚合含该主题", crypto_bucket is not None, str(summary["by_topic"]))
        if crypto_bucket:
            check(
                "主题聚合错次与明细一致",
                crypto_bucket["wrong_count"] == wrongs[0]["wrong_count"],
                str(crypto_bucket),
            )
            check(
                "主题聚合给出错误率",
                0 <= crypto_bucket["error_rate"] <= 100,
                str(crypto_bucket),
            )
            check(
                "主题聚合作答数来自真实作答",
                crypto_bucket["answered"] >= 6,
                str(crypto_bucket),
            )
        type_bucket = next(
            (item for item in summary["by_type"] if item["type"] == "single_choice"), None
        )
        check("题型聚合含单选", type_bucket is not None and type_bucket["count"] == 1, str(summary["by_type"]))

        print("\n== 8. 移出与边界 ==")
        r = client.delete(f"/api/practice/wrong-answers/{wrong['id']}")
        check("移出错题本成功", r.status_code == 200, r.text[:120])
        check("移出后列表为空", client.get("/api/practice/wrong-answers").json() == [])
        r = client.post("/api/practice/retry", json={"wrong_ids": [wrong["id"]]})
        check("错题清空后不能重练", r.status_code == 400, r.text[:120])
        r = client.delete(f"/api/practice/wrong-answers/{wrong['id']}")
        check("重复移出返回 404", r.status_code == 404, r.text[:120])

        r = client.post("/api/practice/sessions/no-such/start")
        check("不存在的练习 404", r.status_code == 404, r.text[:120])
        r = client.post(
            f"/api/practice/sessions/{second['id']}/submit", json={"answers": []}
        )
        check("已提交的练习不能再交", r.status_code == 400, r.text[:120])

    print(f"\n通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for name in FAILED:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
