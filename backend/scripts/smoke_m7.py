"""M7/M8 冒烟测试：多智能体学习教练（学习计划）。

用法（在 backend/ 下）：
    .venv/Scripts/python.exe scripts/smoke_m7.py           # 假模型跑通全链路
    .venv/Scripts/python.exe scripts/smoke_m7.py --real    # 追加真实模型一次真实规划

使用独立数据库 data/test_m7.db，不污染开发库。
除了「出题成功」一项会真正走进 practice_service（假模型返回合法题目），
其余都在假模型上跑——要测的是角色交接、审核拦截、预算与落库，不是模型水平。
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

TEST_DB = BACKEND / "data" / "test_m7.db"
os.environ["DATABASE_URL"] = "sqlite:///./data/test_m7.db"

if TEST_DB.exists():
    TEST_DB.unlink()

from fastapi.testclient import TestClient  # noqa: E402

from shared.config import settings  # noqa: E402
from shared.domain.models.entities import Question, QuestionType, SourceRef  # noqa: E402
from shared.infrastructure.db.models import (  # noqa: E402
    ChunkORM,
    DocumentORM,
    PracticeSessionORM,
    TopicORM,
    WrongAnswerORM,
)
from shared.infrastructure.db.session import SessionLocal  # noqa: E402
from app.main import app
from scripts._auth import login  # noqa: E402
from shared.repositories._converters import question_to_dict  # noqa: E402
import client.services.plan_agent as plan_agent
import client.services.plan_tools as plan_tools
import client.services.practice_service as practice_service  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

MATERIAL = (
    "分组密码把明文切成固定长度的块，再用密钥逐块变换。CBC 模式把上一块的密文与当前明文异或，"
    "所以第一块需要一个随机初始向量 IV；IV 不必保密，但必须不可预测，否则相同明文会得到相同密文。"
    "CTR 模式把分组密码当成流密码使用，对递增的计数器加密后与明文异或，加解密完全相同，"
    "可以并行处理，但计数器绝不能重复，一旦重用，两次密文的异或就泄露明文异或。"
    "ECB 模式相同的明文块会产生相同的密文块，会暴露数据的结构，因此不适合加密超过一个块的消息。"
    "选择模式时要在并行性、随机访问、错误传播与完整性保护之间权衡。"
    "CBC 的错误会传播到下一块，但不会影响更后面的块；CTR 与 OFB 这类流模式只错对应的一位，"
    "代价是必须自己加消息认证码，否则攻击者翻转密文的一位就能可预测地翻转明文的一位。"
)

PLAN_MD = """# 7 天学习计划（11 月 26 日 — 12 月 2 日）

## 依据
- 已就绪资料 1 份，共 1 个片段，可覆盖分组密码的工作模式。
- 完成练习 1 套，平均 66.7 分；错题 1 道，累计答错 3 次。
- 薄弱主题「密码学」错误率 100.0%，集中在 CBC 与 CTR 的区别。

## 安排
- 第 1 天：重读《分组密码笔记》，把 CBC、CTR、ECB 三种模式的加密流程各画一遍，约 30 分钟。
- 第 2 天：做 3 道单选题（已生成草稿），重点看解析里对 IV 与计数器的说明，约 20 分钟。
- 第 3 天：把第 2 天做错的题重做一遍，写出错因，约 15 分钟。
- 第 4 天：用自己的话写一段 200 字的对比，说明 CBC 与 CTR 各自适合什么场景。
- 第 5 天：回到错题本，只看题干，先口述答案再展开对照。
- 第 6 天：再生成 3 道题，限时 15 分钟完成。
- 第 7 天：复盘这一周，把仍然说不清的点记成一条新笔记。

## 完成标准
- 三种模式能不看资料说出加密流程与一个典型缺陷。
- 错题本里这道题连续两次答对。
- 能解释「为什么 CTR 的计数器不能重复」。

## 一句提醒
最容易断掉的是第 4 天：写对比比做题费劲，但只有写出来才知道自己是不是真懂了。
"""

SHORT_PLAN = "# 计划\n\n先复习错题，再做两套题，最后总结。就这样，坚持一周。"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  [ok] {name}")
    else:
        FAILED.append(name)
        print(f"  [FAIL] {name} {detail}")


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for block in text.split("\n\n"):
        name: str | None = None
        data: dict = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            events.append((name, data))
    return events


def _question_json() -> str:
    """假模型给出的合法题目：来源必须命中材料里的 chunk 编号，否则会被整题丢弃。"""
    items = [
        {
            "stem": f"关于分组密码模式的第 {index} 个判断，哪一项符合材料？",
            "options": ["选项甲", "选项乙", "选项丙", "选项丁"],
            "answer": "A",
            "explanation": "依据材料：模式选择要在并行性与错误传播之间权衡。",
            "source_chunk_ids": ["chunk-plan-1"],
            "knowledge_point": "分组密码模式",
        }
        for index in range(1, 5)
    ]
    return json.dumps(items, ensure_ascii=False)


class ScriptedLLM:
    """按脚本回话。出题请求走另一条固定返回，不占用脚本队列。"""

    name = "scripted"

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict]] = []

    async def chat(self, messages: list[dict], **_options) -> str:
        self.calls.append(messages)
        if "命题老师" in messages[0].get("content", ""):
            return _question_json()
        if not self.replies:
            return json.dumps({"action": "save_plan", "args": {"markdown": PLAN_MD}}, ensure_ascii=False)
        return self.replies.pop(0)

    async def stream(self, messages: list[dict], **_options):  # noqa: ANN201
        yield "（脚本模型不支持流式）"


def run_plan(client: TestClient, replies: list[str], goal: str = "") -> tuple[list[tuple[str, dict]], ScriptedLLM]:
    fake = ScriptedLLM(replies)
    original_agent, original_practice = plan_agent.build_llm, practice_service.build_llm
    plan_agent.build_llm = lambda: fake
    practice_service.build_llm = lambda: fake
    try:
        response = client.post("/api/plan", json={"goal": goal})
        return parse_sse(response.text), fake
    finally:
        plan_agent.build_llm = original_agent
        practice_service.build_llm = original_practice


def seed(space_id: str) -> None:
    """造一份资料、一套已提交练习、一道错题：够 Agent 走完整条路径。

    所有行都要带 space_id：数据现在是空间私有的，漏了就会落到占位空间，
    当前用户看不到，Agent 会以为"库里什么都没有"。
    """
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        db.add(
            TopicORM(
                space_id=space_id, id="t-plan", name="密码学", kind="topic", color="#534AB7"
            )
        )
        db.add(
            DocumentORM(
                id="d-plan",
                name="分组密码笔记",
                ext="md",
                size=len(MATERIAL),
                status="ready",
                chunk_count=1,
                topic_id="t-plan",
                space_id=space_id,
            )
        )
        db.add(
            ChunkORM(
                id="chunk-plan-1",
                document_id="d-plan",
                chunk_index=0,
                text=MATERIAL,
                topic_id="t-plan",
                space_id=space_id,
            )
        )

        question = Question(
            id="q-plan-1",
            type=QuestionType.SINGLE_CHOICE,
            stem="CBC 模式为什么需要随机初始向量？",
            answer="A",
            explanation="相同明文在相同 IV 下会得到相同密文。",
            options=["避免相同明文产生相同密文", "为了压缩数据", "为了加快解密", "为了省密钥"],
            sources=[
                SourceRef(document_id="d-plan", document_name="分组密码笔记", chunk_id="chunk-plan-1")
            ],
            document_id="d-plan",
            knowledge_point="CBC 与 IV",
            topic_id="t-plan",
        )
        db.add(
            PracticeSessionORM(
                id="s-plan-1",
                status="submitted",
                score=66.7,
                questions=[question_to_dict(question)],
                answers=[{"question_id": "q-plan-1", "response": "B", "is_correct": False}],
                meta={"result": {"total": 1, "correct": 0}},
                space_id=space_id,
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1),
            )
        )
        db.add(
            WrongAnswerORM(
                id="q-plan-1",
                question_id="q-plan-1",
                document_id="d-plan",
                topic_id="t-plan",
                question=question_to_dict(question),
                wrong_count=3,
                last_response="B",
                space_id=space_id,
                first_wrong_at=now - timedelta(days=3),
                last_wrong_at=now - timedelta(hours=1),
            )
        )
        db.commit()
    finally:
        db.close()


def main() -> int:
    want_real = "--real" in sys.argv
    settings.llm_api_key = settings.llm_api_key or "test-key"

    with TestClient(app) as client:

        login(client)
        print("\n== 1. 空数据不出计划 ==")
        events, fake = run_plan(client, [])
        names = [name for name, _ in events]
        check("空库返回 no_data", names == ["no_data"], str(names))
        check("空库不调用模型", not fake.calls)

        space_id = client.get("/api/auth/me").json()["current_space"]["id"]
        seed(space_id)

        print("\n== 2. 未配置 Key ==")
        saved_key = settings.llm_api_key
        settings.llm_api_key = ""
        events, _ = run_plan(client, [])
        check("缺 Key 报 error", [n for n, _ in events] == ["error"], str(events))
        settings.llm_api_key = saved_key

        print("\n== 3. 观察两查，规划一次提交 ==")
        events, fake = run_plan(
            client,
            [
                json.dumps({"action": "get_progress", "args": {}}),
                json.dumps({"action": "list_weak_points", "args": {"limit": 5}}),
                json.dumps({"action": "save_plan", "args": {"markdown": PLAN_MD}}, ensure_ascii=False),
            ],
            goal="这周想把分组密码搞明白",
        )
        names = [name for name, _ in events]
        check("事件以 start 开头", names[0] == "start", str(names))
        start_event = events[0][1]
        check("start 带运行号与角色", bool(start_event.get("run_id")) and len(start_event.get("roles") or []) == 4, str(start_event))
        check("两步各推一次 step", names.count("step") == 2, str(names))
        check("事件以 done 收尾", names[-1] == "done", str(names))
        step_events = [data for name, data in events if name == "step"]
        check("步骤标注观察员", [step.get("role") for step in step_events] == ["observer", "observer"], str([s.get("role") for s in step_events]))
        plan_event = next((data for name, data in events if name == "plan"), {})
        check("拿到计划正文", plan_event.get("markdown", "").startswith("# 7 天学习计划"))
        check("计划长度达标", len(plan_event.get("markdown", "")) > 300)
        check("plan 事件带运行号", plan_event.get("run_id") == start_event.get("run_id"))
        plan_id = plan_event.get("plan_id")
        run_id = plan_event.get("run_id")

        print("\n== 4. 计划落库、过程可溯源、运行可回放 ==")
        detail = client.get(f"/api/plan/{plan_id}").json()
        check("详情可取", detail.get("id") == plan_id)
        check("goal 落库", detail.get("goal") == "这周想把分组密码搞明白", detail.get("goal", ""))
        meta = detail.get("meta") or {}
        steps = meta.get("steps") or []
        check("meta 记录两步", [step["tool"] for step in steps] == ["get_progress", "list_weak_points"])
        check("每步都带角色", [step.get("role") for step in steps] == ["observer", "observer"], str(steps))
        check("每步都有入参与结果", all(step.get("args") is not None and step.get("result") for step in steps))
        check(
            "统计快照与库一致（平均 66.7）",
            abs(float((meta.get("stats") or {}).get("avg_score", 0)) - 66.7) < 0.05,
            str(meta.get("stats")),
        )
        check("耗时已补写", isinstance(meta.get("elapsed_seconds"), (int, float)))
        agents = meta.get("agents") or {}
        check("meta 记录四个角色", len(agents.get("roles") or []) == 4, str(agents))
        check("审核结论已落库", (agents.get("verdict") or {}).get("passed") is True, str(agents.get("verdict")))
        check("三次模型调用（两查 + 一次直接提交）", agents.get("llm_calls") == 3, str(agents.get("llm_calls")))

        run = client.get(f"/api/plan/runs/{run_id}").json()
        check("运行状态完成", run.get("status") == "completed", str(run.get("status")))
        run_steps = run.get("steps") or []
        check("回放包含四个角色", {step.get("role") for step in run_steps} == {"observer", "planner", "verifier", "writer"}, str({s.get("role") for s in run_steps}))
        check("回放含审核与写入", [s.get("action") for s in run_steps][-2:] == ["verdict", "tool"], str([s.get("action") for s in run_steps]))
        check("交接一次", run.get("handoffs") == 1, str(run.get("handoffs")))
        check("无未知运行 404", client.get("/api/plan/runs/not-exist").status_code == 404)

        print("\n== 5. 动作格式错误会被纠正 ==")
        events, fake = run_plan(
            client,
            [
                "我觉得应该先看看你的进度。",
                json.dumps({"action": "get_progress", "args": {}}),
                json.dumps({"action": "save_plan", "args": {"markdown": PLAN_MD}}, ensure_ascii=False),
            ],
        )
        names = [name for name, _ in events]
        check("纠正后仍能出计划", "plan" in names, str(names))
        check(
            "纠正消息进了上下文",
            any(
                "不是合法的动作 JSON" in (message.get("content") or "")
                for call in fake.calls
                for message in call
            ),
        )

        print("\n== 6. 出题失败后换路 ==")
        events, _ = run_plan(
            client,
            [
                json.dumps(
                    {"action": "generate_practice", "args": {"topic_id": "不存在", "counts": {"single_choice": 2}}}
                ),
                json.dumps({"action": "save_plan", "args": {"markdown": PLAN_MD}}, ensure_ascii=False),
            ],
        )
        step = next((data for name, data in events if name == "step"), {})
        check("失败的那步 ok=false", step.get("ok") is False, str(step))
        check("失败原因写成人话", (step.get("brief") or "").startswith("失败："), step.get("brief", ""))
        check("换路后照样出计划", "plan" in [name for name, _ in events])

        print("\n== 7. 出题工具真的落库 ==")
        before = len(client.get("/api/practice/sessions?limit=50").json())
        events, _ = run_plan(
            client,
            [
                json.dumps(
                    {
                        "action": "generate_practice",
                        "args": {"counts": {"single_choice": 3}, "difficulty": "easy"},
                    }
                ),
                json.dumps({"action": "save_plan", "args": {"markdown": PLAN_MD}}, ensure_ascii=False),
            ],
        )
        step = next((data for name, data in events if name == "step"), {})
        check("出题成功", step.get("ok") is True, step.get("brief", ""))
        after = len(client.get("/api/practice/sessions?limit=50").json())
        check("练习草稿多了一套", after == before + 1, f"{before} -> {after}")
        check("摘要写明题数", "已生成 3 道题" in (step.get("brief") or ""), step.get("brief", ""))

        print("\n== 8. 同一工具不会无限重复 ==")
        events, _ = run_plan(
            client,
            [
                json.dumps({"action": "get_progress", "args": {}}),
                json.dumps({"action": "get_progress", "args": {}}),
                json.dumps({"action": "get_progress", "args": {}}),
                json.dumps({"action": "save_plan", "args": {"markdown": PLAN_MD}}, ensure_ascii=False),
            ],
        )
        steps = [data for name, data in events if name == "step"]
        check("第三次重复被拦下", len(steps) == 2, str(len(steps)))
        check("仍能收口", "plan" in [name for name, _ in events])

        print("\n== 9. 规划员写不出计划时不留半张 ==")
        events, _ = run_plan(
            client,
            [json.dumps({"action": "finish_observation", "args": {"summary": "数据够了"}})]
            + ["我还是再想想。"] * 4,
        )
        names = [name for name, _ in events]
        check("返回 no_plan", names[-1] == "no_plan", str(names))
        check("没有 plan 事件", "plan" not in names)
        no_plan = next(data for name, data in events if name == "no_plan")
        check("说明没写出来的原因", any("预算" in reason for reason in no_plan.get("reasons") or []), str(no_plan.get("reasons")))
        check("no_plan 带运行号", bool(no_plan.get("run_id")))

        print("\n== 10. 计划太短会被审核打回 ==")
        events, fake = run_plan(
            client,
            [
                json.dumps({"action": "save_plan", "args": {"markdown": SHORT_PLAN}}, ensure_ascii=False),
                json.dumps({"action": "save_plan", "args": {"markdown": PLAN_MD}}, ensure_ascii=False),
            ],
        )
        check("短计划被拒后重写成功", "plan" in [name for name, _ in events])
        check(
            "拒绝理由写清字数",
            any("至少写出一周内每天做什么" in (m.get("content") or "") for m in fake.calls[-1]),
        )

        print("\n== 11. 审核两次不过就不写入 ==")
        events, _ = run_plan(
            client,
            [
                json.dumps({"action": "save_plan", "args": {"markdown": SHORT_PLAN}}, ensure_ascii=False),
                json.dumps({"action": "save_plan", "args": {"markdown": SHORT_PLAN}}, ensure_ascii=False),
            ],
        )
        names = [name for name, _ in events]
        check("两次打回后 no_plan", names[-1] == "no_plan", str(names))
        check("没有写入计划", "plan" not in names)
        rejected = next(data for name, data in events if name == "no_plan")
        check("带回打回理由", any("至少写出一周内每天做什么" in reason for reason in rejected.get("reasons") or []), str(rejected.get("reasons")))
        aborted = client.get(f"/api/plan/runs/{rejected.get('run_id')}").json()
        check("运行标记为中止", aborted.get("status") == "aborted", str(aborted.get("status")))
        check("审核步骤为失败", any(step.get("action") == "verdict" and step.get("ok") is False for step in aborted.get("steps") or []))

        print("\n== 12. 历史与删除 ==")
        items = client.get("/api/plan").json()
        check("列表有记录", len(items) >= 1, str(len(items)))
        check("列表带摘要", bool(items[0].get("excerpt")))
        check("列表不算正文", "markdown" not in items[0])
        meta_info = client.get("/api/plan/meta").json()
        check("meta 给出步数上限", meta_info.get("steps_max") == plan_agent.MAX_STEPS)
        check("meta 列出五个工具", len(meta_info.get("tools") or []) == 5, str(meta_info.get("tools")))
        check("meta 声明边界", any("不改笔记" in line for line in meta_info.get("limits") or []))
        check("删除成功", client.delete(f"/api/plan/{plan_id}").status_code == 200)
        check("删除后详情 404", client.get(f"/api/plan/{plan_id}").status_code == 404)

        print("\n== 13. 解析与截断单测 ==")
        check("解析代码块包着的动作", plan_agent.parse_action('```json\n{"action":"get_progress"}\n```')[0] == "get_progress")
        check("解析带前后解释的动作", plan_agent.parse_action('先查进度：{"action":"get_progress","args":{}} 就这样')[0] == "get_progress")
        check("散文返回 None", plan_agent.parse_action("我先看看你的进度") is None)
        check("未知字段返回 None", plan_agent.parse_action('{"foo":"bar"}') is None)
        big = {"items": [{"stem": "很长的题干" * 40} for _ in range(40)]}
        fitted = plan_tools._fit(big)
        check("超长返回被截断", len(json.dumps(fitted, ensure_ascii=False)) <= plan_tools.MAX_RESULT_CHARS)
        check("截断后结构还在", "items" in fitted)

        if want_real:
            print("\n== 14. 真实模型 ==")
            if not settings.llm_configured:
                print("  [skip] 未配置 LLM_API_KEY")
            else:
                response = client.post("/api/plan", json={"goal": "这周想把分组密码搞明白"})
                events = parse_sse(response.text)
                names = [name for name, _ in events]
                plan_event = next((data for name, data in events if name == "plan"), {})
                check("真实规划出计划", "plan" in names, str(names))
                check("真实计划有结构", "##" in plan_event.get("markdown", ""), plan_event.get("markdown", "")[:80])
                check(
                    "真实计划含数字依据",
                    any(char.isdigit() for char in (plan_event.get("markdown") or "")[:600]),
                )
                print(f"  步骤：{[d.get('label') for n, d in events if n == 'step']}")

    print(f"\n==== 通过 {len(PASSED)} 项，失败 {len(FAILED)} 项 ====")
    for name in FAILED:
        print("  FAIL:", name)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())