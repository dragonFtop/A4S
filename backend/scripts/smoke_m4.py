"""M4 冒烟测试：出题与草稿（FR-04）。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/smoke_m4.py

使用独立数据库 data/test_m4.db，不污染开发库。
LLM 用假模型（按题型的合法 JSON），切分/入库/向量化走真实链路，
因此能验证「范围 → 取材 → 出题 → 校验来源 → 存草稿 → 删题」整条链路。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TEST_DB = BACKEND / "data" / "test_m4.db"
os.environ["DATABASE_URL"] = "sqlite:///./data/test_m4.db"
os.environ["LLM_API_KEY"] = ""

if TEST_DB.exists():
    TEST_DB.unlink()

from fastapi.testclient import TestClient  # noqa: E402

from shared.config import settings  # noqa: E402
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
    """假模型：从提示词里读题型与数量，返回结构合法的题目。

    mode:
      normal      正常返回
      broken      第一次返回非 JSON（触发一次修复重试）
      hallucinate 额外塞一道来源编号是编造的题
    """

    def __init__(self, mode: str = "normal") -> None:
        self.mode = mode
        self.calls = 0
        self.prompts: list[list[dict]] = []

    async def chat(self, messages: list[dict], **options) -> str:
        self.calls += 1
        self.prompts.append(messages)
        if self.mode == "broken" and self.calls == 1:
            return "好的，我这就出题：\n```json\n（此处本该是题目）\n```"

        text = "\n".join(item["content"] for item in messages)
        # 系统提示里写的是「题型：单选。」，所以只匹配英文键
        question_type = re.search(
            r"题型：(single_choice|multi_choice|true_false|short_answer)", text
        ).group(1)
        count = int(re.search(r"数量：(\d+)\s*道", text).group(1))
        chunk_ids = re.findall(r"\[chunk:([A-Za-z0-9\-]+)\]", text)
        if not chunk_ids:
            return "[]"

        items = [
            _fake_question(question_type, index, chunk_ids[index % len(chunk_ids)])
            for index in range(count)
        ]
        if self.mode == "hallucinate" and items:
            items.append(
                _fake_question(question_type, 99, "00000000-dead-beef-0000-000000000000")
            )
        return json.dumps(items, ensure_ascii=False)

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


def patch_llm(monkeypatch_target, fake: FakeLLM) -> None:
    monkeypatch_target.build_llm = lambda: fake


def main() -> int:
    with TestClient(app) as client:
        login(client)
        import client.services.practice_service as practice_service

        print("\n== 1. 参数与范围校验 ==")
        r = client.post("/api/practice/generate", json={"counts": {"single_choice": 5}})
        check("未选范围被拒", r.status_code == 400, r.text[:120])
        check("提示指向三种入口", "范围" in r.json().get("detail", ""), r.text[:120])

        r = client.post(
            "/api/practice/generate",
            json={"counts": {"single_choice": 0}, "topic_ids": ["x"]},
        )
        check("题型数量全 0 被拒", r.status_code == 400, r.text[:120])

        meta = client.get("/api/practice/meta").json()
        keys = [item["key"] for item in meta["types"]]
        check(
            "meta 覆盖四种题型",
            set(keys) == {"single_choice", "multi_choice", "true_false", "short_answer"},
            str(keys),
        )
        check("简答标记为非自动判分", all(not t["auto_graded"] for t in meta["types"] if t["key"] == "short_answer"))
        check("题量上限为 30", meta["max_questions"] == 30)

        print("\n== 2. 准备资料 ==")
        topic_crypto = client.post("/api/topics", json={"name": "密码学", "kind": "topic"}).json()["id"]
        topic_algo = client.post("/api/topics", json={"name": "算法", "kind": "topic"}).json()["id"]

        crypto_text = (
            "# 对称与非对称加密\n\n"
            "对称加密使用同一把密钥完成加密与解密，优点是速度快，适合加密大量数据；"
            "难点在于密钥分发，通信双方必须先安全地共享密钥。AES 是常见的对称加密算法，"
            "分组长度 128 位，密钥长度可取 128、192 或 256 位。工作模式决定分组之间如何关联："
            "ECB 对每个分组独立加密，相同明文块会得到相同密文块，因此会泄露数据的结构信息，"
            "不应直接用于真实数据；CBC 让每个明文块先与前一个密文块异或，需要初始向量 IV，"
            "同一密钥下 IV 必须随机且不可预测。\n\n"
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
            "按用途与环境隔离，并保留轮换与吊销的通道。密码学协议的失败大多不是数学被攻破，"
            "而是实现细节泄露了侧信道信息，例如根据比较耗时推断口令前缀。\n\n"
            "理解这些算法时要抓住各自的攻击模型：对称加密假设密钥已共享，防的是窃听；"
            "非对称加密假设公钥可被任何人获得，防的是密钥交换被窃听；哈希假设输入可能被篡改，"
            "防的是内容被悄悄替换。模型不同，能提供的保证就不同，混用会得出错误的安全结论。"
        )
        check("密码学资料足够长", len(crypto_text) > 900, str(len(crypto_text)))
        r = client.post(
            "/api/documents/paste",
            json={"content": crypto_text, "title": "密码学讲义", "topic_id": topic_crypto},
        )
        doc_crypto = r.json()
        check("密码学资料就绪", wait_ready(client, doc_crypto["id"]) == "ready")

        algo_text = (
            "# 二分查找与复杂度\n\n"
            "二分查找要求数据有序，每次把搜索区间缩小一半，因此时间复杂度是 O(log n)，"
            "空间复杂度在迭代写法下是 O(1)，递归写法下是 O(log n) 的栈空间。\n\n"
            "中点通常写成 lo + (hi - lo) // 2 而不是 (lo + hi) // 2，原因是后者在 lo 与 hi 都很大时"
            "可能发生整数溢出。循环不变量决定边界条件：如果区间定义为左闭右闭，"
            "循环条件就是 lo <= hi，更新时 mid 两侧都要排除；如果定义为左闭右开，"
            "循环条件就是 lo < hi，右边界更新为 mid 而不是 mid - 1。两种写法本身都正确，"
            "混用才会出错。\n\n"
            "二分查找的常见错误是区间开闭与更新方式不匹配，导致死循环或漏掉边界元素。"
            "另一个前提是数据必须有序：无序数组需要先排序，排序成本 O(n log n)，"
            "如果只查一次，顺序扫描反而更划算。因此二分只在查询次数较多、或数据本身已有序时划算。\n\n"
            "二分的思想还可以扩展到答案判定：当答案具有单调性时，可以二分答案而不是二分下标，"
            "例如求最小的满足条件的容量。这类题的关键是把「是否可行」写成单调的判定函数，"
            "再对答案区间做二分查找。\n\n"
            "分析复杂度时要区分最好、最坏与平均情况。二分查找的最坏与平均都是 O(log n)，"
            "但前提是每次比较都能把区间严格缩小；如果中点计算写错导致区间不变，"
            "循环就会退化成死循环。工程上判断一段二分是否正确，靠的是循环不变量在每轮结束后仍然成立，"
            "而不是靠测试几组样例碰运气。\n\n"
            "把二分写成模板可以减少边界错误：先确定区间语义，再写循环条件，最后写两个分支的收缩方式，"
            "三步必须自洽。写完以后用长度为 0、1、2 的数组各测一次，边界问题通常在这三组用例里暴露。"
        )
        check("算法资料足够长", len(algo_text) > 700, str(len(algo_text)))
        r = client.post(
            "/api/documents/paste",
            json={"content": algo_text, "title": "算法讲义", "topic_id": topic_algo},
        )
        doc_algo = r.json()
        check("算法资料就绪", wait_ready(client, doc_algo["id"]) == "ready")

        small_topic = client.post("/api/topics", json={"name": "薄资料", "kind": "topic"}).json()["id"]
        r = client.post(
            "/api/documents/paste",
            json={"content": "二分查找要求数组有序。", "title": "一句话资料", "topic_id": small_topic},
        )
        doc_small = r.json()
        check("薄资料就绪", wait_ready(client, doc_small["id"]) == "ready")

        print("\n== 3. 资料不足检测 ==")
        r = client.post(
            "/api/practice/generate",
            json={"topic_ids": [small_topic], "counts": {"single_choice": 30}},
        )
        check("50 字资料出 30 题被拒", r.status_code == 400, r.text[:160])
        detail = r.json().get("detail", "")
        check("提示资料不足并给出字数", "资料不足" in detail and "字" in detail, detail)
        check("提示可减少题量", "减少题量" in detail, detail)

        r = client.post(
            "/api/practice/generate",
            json={"topic_ids": ["no-such-topic"], "counts": {"single_choice": 5}},
        )
        check("空范围被拒", r.status_code == 400, r.text[:120])

        print("\n== 4. 按主题出题（假模型，正常输出）==")
        settings.llm_api_key = "test-key"
        fake = FakeLLM()
        patch_llm(practice_service, fake)
        r = client.post(
            "/api/practice/generate",
            json={
                "topic_ids": [topic_crypto],
                "counts": {"single_choice": 6, "multi_choice": 2, "true_false": 2},
                "difficulty": "medium",
            },
        )
        check("任务受理 202", r.status_code == 202, r.text[:160])
        job = r.json()
        check("任务初始 running", job["state"] == "running", job["state"])
        job = client.get(f"/api/practice/jobs/{job['id']}").json()
        check("后台任务完成", job["state"] == "done", job["message"])
        check("生成题量与计划一致", job["produced"] == 10, str(job["produced"]))

        session = client.get(f"/api/practice/sessions/{job['session_id']}").json()
        types = [item["type"] for item in session["questions"]]
        check("单选 6 道", types.count("single_choice") == 6, str(types))
        check("多选 2 道", types.count("multi_choice") == 2, str(types))
        check("判断 2 道", types.count("true_false") == 2, str(types))
        check("状态为草稿", session["status"] == "draft")
        check("每题都有答案与解析", all(q["answer"] and q["explanation"] for q in session["questions"]))
        check("每题都有来源", all(q["sources"] for q in session["questions"]))
        check(
            "来源带文档名与片段",
            all(q["sources"][0]["document_name"] == "密码学讲义" and q["sources"][0]["snippet"] for q in session["questions"]),
        )
        check(
            "题目只来自所选主题",
            all(q["document_id"] == doc_crypto["id"] for q in session["questions"]),
            str({q["document_id"] for q in session["questions"]}),
        )
        check(
            "单选答案是一个字母且在选项范围内",
            all(
                len(q["answer"]) == 1 and ord(q["answer"]) - 65 < len(q["options"])
                for q in session["questions"]
                if q["type"] == "single_choice"
            ),
        )
        check(
            "多选答案为字母组合",
            all(len(q["answer"]) >= 2 for q in session["questions"] if q["type"] == "multi_choice"),
        )
        check(
            "判断题答案为正确/错误",
            all(q["answer"] in ("正确", "错误") for q in session["questions"] if q["type"] == "true_false"),
        )
        check("会话记录组卷参数", session["meta"].get("counts", {}).get("single_choice") == 6, str(session["meta"])[:160])
        check("会话记录范围", session["meta"].get("topic_ids") == [topic_crypto])

        print("\n== 5. 来源校验与去重 ==")
        r = client.post(
            "/api/practice/generate",
            json={"topic_ids": [topic_crypto], "counts": {"single_choice": 3}},
        )
        job2 = client.get(f"/api/practice/jobs/{r.json()['id']}").json()
        check("正常路径生成 3 道", job2["produced"] == 3, str(job2["produced"]))

        fake_bad = FakeLLM(mode="hallucinate")
        patch_llm(practice_service, fake_bad)
        r = client.post(
            "/api/practice/generate",
            json={"topic_ids": [topic_crypto], "counts": {"single_choice": 3}},
        )
        job3 = client.get(f"/api/practice/jobs/{r.json()['id']}").json()
        check("编造来源的题被丢弃", job3["produced"] == 3, str(job3["produced"]))
        session3 = client.get(f"/api/practice/sessions/{job3['session_id']}").json()
        check(
            "没有编造的 chunk_id 进入题目",
            all(
                source["chunk_id"] != "00000000-dead-beef-0000-000000000000"
                for q in session3["questions"]
                for source in q["sources"]
            ),
        )

        print("\n== 6. JSON 解析容错重试 ==")
        fake_retry = FakeLLM(mode="broken")
        patch_llm(practice_service, fake_retry)
        r = client.post(
            "/api/practice/generate",
            json={"topic_ids": [topic_algo], "counts": {"true_false": 2}},
        )
        job4 = client.get(f"/api/practice/jobs/{r.json()['id']}").json()
        check("首次非 JSON 后重试成功", job4["state"] == "done" and job4["produced"] == 2, job4["message"])
        check("确实发生了重试", fake_retry.calls >= 2, str(fake_retry.calls))

        print("\n== 7. 按资料与按近 N 天 ==")
        patch_llm(practice_service, FakeLLM())
        r = client.post(
            "/api/practice/generate",
            json={"document_ids": [doc_algo["id"]], "counts": {"single_choice": 3}},
        )
        job5 = client.get(f"/api/practice/jobs/{r.json()['id']}").json()
        session5 = client.get(f"/api/practice/sessions/{job5['session_id']}").json()
        check(
            "按资料出题只取该资料",
            all(q["document_id"] == doc_algo["id"] for q in session5["questions"]),
            str({q["document_id"] for q in session5["questions"]}),
        )

        from shared.infrastructure.db.session import SessionLocal
        from shared.infrastructure.db.models import DocumentORM

        db = SessionLocal()
        try:
            row = db.get(DocumentORM, doc_crypto["id"])
            row.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)
            db.commit()
        finally:
            db.close()

        r = client.post(
            "/api/practice/generate",
            json={"topic_ids": [topic_crypto], "days": 7, "counts": {"single_choice": 3}},
        )
        check("主题 ∩ 近 7 天为空时被拒", r.status_code == 400, r.text[:140])

        r = client.post(
            "/api/practice/generate",
            json={"days": 7, "counts": {"single_choice": 3}},
        )
        job6 = client.get(f"/api/practice/jobs/{r.json()['id']}").json()
        session6 = client.get(f"/api/practice/sessions/{job6['session_id']}").json()
        check(
            "近 7 天只含近期资料",
            all(q["document_id"] != doc_crypto["id"] for q in session6["questions"]),
            str({q["document_id"] for q in session6["questions"]}),
        )

        db = SessionLocal()
        try:
            row = db.get(DocumentORM, doc_crypto["id"])
            row.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        finally:
            db.close()

        print("\n== 8. 草稿删题与清理 ==")
        before = len(session["questions"])
        victim = session["questions"][0]["id"]
        r = client.delete(f"/api/practice/sessions/{session['id']}/questions/{victim}")
        check("删题成功", r.status_code == 200, r.text[:120])
        check("题目数减一", len(r.json()["questions"]) == before - 1)
        again = client.get(f"/api/practice/sessions/{session['id']}").json()
        check("删题持久化", len(again["questions"]) == before - 1)
        check("删的是指定题目", victim not in [q["id"] for q in again["questions"]])

        r = client.delete(f"/api/practice/sessions/{session['id']}/questions/{victim}")
        check("重复删题返回 404", r.status_code == 404, r.text[:120])

        briefs = client.get("/api/practice/sessions").json()
        check("练习列表非空", len(briefs) >= 4, str(len(briefs)))
        check(
            "列表行带题数与状态",
            all(item["question_count"] >= 0 and item["status"] == "draft" for item in briefs),
        )
        check(
            "列表按时间倒序",
            all(
                briefs[i]["started_at"] >= briefs[i + 1]["started_at"]
                for i in range(len(briefs) - 1)
            ),
        )

        target = briefs[0]["id"]
        r = client.delete(f"/api/practice/sessions/{target}")
        check("删除草稿成功", r.status_code == 200, r.text[:120])
        check("删除后 404", client.get(f"/api/practice/sessions/{target}").status_code == 404)

        print("\n== 9. 解析工具单测 ==")
        from shared.infrastructure.generators.base import GenerationChunk, GenerationContext
        from shared.infrastructure.generators.parsing import (
            normalize_question,
            parse_json_items,
        )
        from shared.domain.models.entities import QuestionType

        context = GenerationContext(
            chunks=[
                GenerationChunk(
                    chunk_id="real-1",
                    text="RSA 的安全性依赖大整数分解困难。",
                    document_id="d1",
                    document_name="密码学讲义",
                    topic_id="t1",
                )
            ],
            material_text="[chunk:real-1] 《密码学讲义》\nRSA 的安全性依赖大整数分解困难。",
        )
        bad = normalize_question(
            {
                "stem": "无来源的题应该被丢掉吗？",
                "options": ["是", "否"],
                "answer": "A",
                "explanation": "x",
                "source_chunk_ids": ["fake-9"],
            },
            QuestionType.SINGLE_CHOICE,
            context,
        )
        check("编造来源 → 题目丢弃", bad is None)

        good = normalize_question(
            {
                "stem": "RSA 的安全性依赖什么困难问题？",
                "options": ["A. 大整数分解", "B. 离散对数"],
                "answer": "A",
                "explanation": "材料明确指出。",
                "source_chunk_ids": ["[chunk:real-1]"],
            },
            QuestionType.SINGLE_CHOICE,
            context,
        )
        check("选项前缀被清理", good is not None and good.options == ["大整数分解", "离散对数"], str(good.options if good else None))
        check("答案归一为字母", good is not None and good.answer == "A")
        check("来源映射到文档", good is not None and good.sources[0].document_id == "d1")
        check(
            "解析去掉重复的「解析：」前缀",
            good is not None and not good.explanation.startswith("解析"),
            good.explanation if good else "",
        )
        check("主题随来源带回", good is not None and good.topic_id == "t1")

        multi = normalize_question(
            {
                "stem": "以下哪些属于对称加密的特点？",
                "options": ["速度快", "密钥分发难", "公钥公开", "适合大数据量"],
                "answer": "A、B、D",
                "explanation": "x",
                "source_chunk_ids": ["real-1"],
            },
            QuestionType.MULTI_CHOICE,
            context,
        )
        check("多选答案整理为升序字母", multi is not None and multi.answer == "ABD", multi.answer if multi else "")

        check("代码块包裹的 JSON 可解析", parse_json_items('```json\n[{"stem":"x"}]\n```') is not None)
        check("前后带解释的 JSON 可解析", parse_json_items('这是题目：[{"stem":"x"}] 希望有帮助') is not None)
        check("纯文本返回 None", parse_json_items("抱歉，我无法完成") is None)
        check("空数组返回 None", parse_json_items("[]") is None)

    print(f"\n通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        for name in FAILED:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
