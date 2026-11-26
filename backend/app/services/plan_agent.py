"""规划 Agent 的循环（M7）。

这是全项目唯一带「动作 → 观察 → 再决定」回路的模块。它只做一件事：
把学习数据翻译成一份 7 天计划。边界写在 docs/AGENT_DECISION.md §3.6，
代码里对应的三条硬约束是：

1. `MAX_STEPS = 6`——没有上限的循环就是没有刹车的车；
2. 工具返回统一截断 2000 字符（在 plan_tools 里）——错题原文会撑爆上下文；
3. 每一步都推 SSE——前端要能看到「查了薄弱点 → 出了 6 道题 → 写了计划」，
   这是它和工作流唯一肉眼可见的区别。

失败只有三个出口：步数用尽、模型不按格式、工具报错。前两个都退化成
「给出已查到的结论 + 一句话说明」，不产出半张计划。
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime

from app.config import settings
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.llm.openai_compat import build_llm
from app.repositories.sqlalchemy_repo import SqlAlchemyStudyPlanRepository
from app.repositories.stats_repo import StatsRepository
from app.services import plan_prompts, plan_tools
from app.services.plan_tools import ToolError

logger = logging.getLogger(__name__)

MAX_STEPS = 6
# 同一个只读工具最多查两次：第三次开始大概率是在原地打转
MAX_SAME_TOOL_CALLS = 2
MAX_RESULT_PREVIEW = 400

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


def _event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _explain(message: str) -> str:
    lowered = message.lower()
    if "401" in message or "unauthorized" in lowered or "api key" in lowered:
        return "模型鉴权失败（401）：请检查设置页的 API Key"
    if "timeout" in lowered or "timed out" in lowered:
        return "模型响应超时：请稍后重试"
    if "429" in message:
        return "模型额度不足或请求过快（429）：请稍后重试"
    return message[:300]


def parse_action(raw: str) -> tuple[str, dict] | None:
    """从模型输出里抠出 (action, args)。

    容忍三种常见走样：包了 ```json 代码块、前后写了半句解释、args 缺失。
    返回值非法一律给 None，由调用方发一条纠正消息再试——不猜、不硬解释。
    """
    text = _FENCE.sub("", (raw or "").strip())
    if not text:
        return None

    candidates = [text]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        action = data.get("action") or data.get("tool")
        if not isinstance(action, str) or not action.strip():
            continue
        args = data.get("args")
        return action.strip(), args if isinstance(args, dict) else {}
    return None


def _brief(tool: str, result: dict, ok: bool) -> str:
    """给时间线看的一句话。不追求完整，只要求「一眼知道这步发生了什么」。"""
    if not ok:
        return f"失败：{result.get('error') or '未知错误'}"
    if tool == "get_progress":
        documents = result.get("documents") or {}
        practice = result.get("practice") or {}
        wrong = result.get("wrong") or {}
        return (
            f"资料 {documents.get('ready', 0)} 份 · 练习 {practice.get('finished', 0)} 套 · "
            f"平均 {practice.get('avg_score', 0)} 分 · 错题 {wrong.get('total', 0)} 道"
        )
    if tool == "list_weak_points":
        items = result.get("items") or []
        if not items:
            return result.get("note") or "没有薄弱点"
        first = items[0]
        return (
            f"{len(items)} 个薄弱主题，最弱的是「{first.get('topic')}」"
            f"（错误率 {first.get('error_rate')}%）"
        )
    if tool == "list_wrong_answers":
        items = result.get("items") or []
        if not items:
            return result.get("note") or "没有错题"
        return f"{len(items)} 道错题，最多错过 {max(item.get('wrong_count', 1) for item in items)} 次"
    if tool == "generate_practice":
        return f"已生成 {result.get('produced', 0)} 道题（草稿）"
    return "完成"


def _preview(result: dict) -> str:
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) <= MAX_RESULT_PREVIEW else text[: MAX_RESULT_PREVIEW - 1] + "…"


async def sse_plan(payload: dict) -> AsyncIterator[str]:
    """规划 SSE 主循环。自己开 Session，响应期间保持可用。"""
    db = SessionLocal()
    started = time.monotonic()
    goal = (payload.get("goal") or "").strip()[:200]

    try:
        if not settings.llm_configured:
            yield _event("error", {"detail": "尚未配置大模型 API Key，请先在设置页填写"})
            return

        # 循环前的检查：两份数据都是 0 时不出计划。
        # 否则模型会开始编「你最近在学什么」——这比不生成更糟。
        space_id = payload["space_id"]
        stats = StatsRepository(db, space_id).summary()
        if stats["documents"]["total"] == 0 and stats["practice"]["sessions"] == 0:
            yield _event(
                "no_data",
                {
                    "detail": "还没有资料和练习记录，先导入一份资料或做一套题，计划才有依据。",
                },
            )
            return

        llm = build_llm()
        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        messages = plan_prompts.build_messages(goal, today)
        steps: list[dict] = []
        calls: dict[str, int] = {}
        plan_id: str | None = None

        yield _event("start", {"steps_max": MAX_STEPS, "goal": goal, "today": today})

        for step_index in range(1, MAX_STEPS + 1):
            if step_index == MAX_STEPS:
                messages.append(
                    {
                        "role": "user",
                        "content": "这是最后一步，信息已经够用。请立刻调用 save_plan，不要再查别的工具。",
                    }
                )

            yield _event("thinking", {"step": step_index, "steps_max": MAX_STEPS})
            try:
                reply = await llm.chat(messages, temperature=0.2)
            except Exception as exc:  # noqa: BLE001 - 供应商报错要原样转给人
                logger.exception("规划生成失败")
                detail = _explain(str(exc) or exc.__class__.__name__)
                yield _event("error", {"detail": detail, "steps": steps})
                return

            action = parse_action(reply)
            if action is None:
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": plan_prompts.correction_message(reply)})
                continue

            tool, args = action

            if tool == "save_plan":
                markdown = str(args.get("markdown") or "")
                meta = {
                    "steps": steps,
                    "stats": {
                        "documents": stats["documents"]["ready"],
                        "sessions": stats["practice"]["finished"],
                        "avg_score": round(float(stats["practice"]["avg_score"]), 1),
                        "wrong": stats["wrong"]["total"],
                    },
                    "model": settings.llm_model,
                    "generated_at": datetime.now().astimezone().isoformat(),
                }
                try:
                    saved = plan_tools.save_plan(
                        db,
                        space_id=space_id,
                        markdown=markdown,
                        goal=goal or str(args.get("goal") or ""),
                        meta=meta,
                    )
                except ToolError as exc:
                    messages.append({"role": "assistant", "content": reply})
                    messages.append(
                        {
                            "role": "user",
                            "content": plan_prompts.feedback_message(
                                "save_plan", {"ok": False, "error": str(exc)}
                            ),
                        }
                    )
                    continue

                plan_id = saved["plan_id"]
                elapsed = round(time.monotonic() - started, 1)
                SqlAlchemyStudyPlanRepository(db).update_meta(
                    plan_id,
                    {"elapsed_seconds": elapsed, "steps_count": len(steps)},
                    space_id,
                )
                yield _event(
                    "plan",
                    {
                        "plan_id": plan_id,
                        "markdown": markdown.strip(),
                        "chars": saved["chars"],
                        "steps": len(steps),
                        "elapsed_seconds": elapsed,
                    },
                )
                yield _event("done", {"plan_id": plan_id, "steps": len(steps)})
                return

            handler = plan_tools.TOOLS.get(tool)
            if handler is None:
                messages.append({"role": "assistant", "content": reply})
                messages.append(
                    {
                        "role": "user",
                        "content": plan_prompts.feedback_message(
                            tool, {"ok": False, "error": f"没有名为 {tool} 的工具"}
                        ),
                    }
                )
                continue

            calls[tool] = calls.get(tool, 0) + 1
            if calls[tool] > MAX_SAME_TOOL_CALLS:
                messages.append({"role": "assistant", "content": reply})
                messages.append(
                    {
                        "role": "user",
                        "content": plan_prompts.feedback_message(
                            tool,
                            {
                                "ok": False,
                                "error": "这个工具已经查过了，结果在上文。请换一个工具或直接 save_plan。",
                            },
                        ),
                    }
                )
                continue

            begin = time.monotonic()
            ok = True
            try:
                result = await handler(db, space_id=space_id, **args)
            except ToolError as exc:
                result, ok = {"ok": False, "error": str(exc)}, False
            except Exception as exc:  # noqa: BLE001 - 工具故障要让模型换路，不是整轮失败
                logger.exception("规划工具失败：%s", tool)
                result, ok = {"ok": False, "error": _explain(str(exc) or exc.__class__.__name__)}, False
            elapsed_ms = int((time.monotonic() - begin) * 1000)
            brief = _brief(tool, result, ok)

            steps.append(plan_prompts.step_meta(tool, args, result, elapsed_ms, ok, brief))
            yield _event(
                "step",
                {
                    "index": len(steps),
                    "tool": tool,
                    "label": plan_tools.TOOL_LABELS.get(tool, tool),
                    "args": args,
                    "ok": ok,
                    "brief": brief,
                    "preview": _preview(result),
                    "elapsed_ms": elapsed_ms,
                    # 前端据此给「去作答」入口：计划里说「做这套题」时，得能跳过去
                    "session_id": result.get("session_id") if isinstance(result, dict) else None,
                },
            )

            messages.append({"role": "assistant", "content": reply})
            messages.append(
                {"role": "user", "content": plan_prompts.feedback_message(tool, result)}
            )

        # 步数用尽：给出已经查到的东西，不留半张计划
        elapsed = round(time.monotonic() - started, 1)
        yield _event(
            "no_plan",
            {
                "detail": f"这次用了 {MAX_STEPS} 步还没写完计划。下面是已经查到的结论，可以再试一次。",
                "steps": steps,
                "elapsed_seconds": elapsed,
            },
        )
    finally:
        db.close()
