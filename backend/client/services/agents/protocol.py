"""角色之间共用的输出协议与事件形状。

这里不碰数据库、不碰模型：只把「一行 JSON」解析成动作，把工具结果翻译成
时间线上的一句话，以及定义角色向协调员上报的事件。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

MAX_RESULT_PREVIEW = 400
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


@dataclass
class AgentEvent:
    """角色执行过程中的一条事件。

    name     —— 推给前端的 SSE 事件名（thinking / step / handoff ...）
    data     —— 推给前端的数据；handoff 不推，只落库
    persist  —— 需要写进 agent_steps 的字段；None 表示纯 UI 事件
    """

    name: str
    data: dict = field(default_factory=dict)
    persist: dict | None = None


def event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def explain(message: str) -> str:
    lowered = message.lower()
    if "401" in message or "unauthorized" in lowered or "api key" in lowered:
        return "模型鉴权失败（401）：请检查设置页的 API Key"
    if "timeout" in message or "timed out" in lowered:
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


def brief(tool: str, result: dict, ok: bool) -> str:
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


def preview(result: dict) -> str:
    text = json.dumps(result, ensure_ascii=False)
    return text if len(text) <= MAX_RESULT_PREVIEW else text[: MAX_RESULT_PREVIEW - 1] + "…"