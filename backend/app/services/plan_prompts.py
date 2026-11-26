"""规划 Agent 的提示词与输出协议（M7）。

为什么不用原生 function calling
------------------------------
项目只依赖 OpenAI 兼容的 chat 端点，不同供应商对 tools 字段的支持程度不一致；
而这里的动作只有五个、参数都是扁平结构，用「一行 JSON」的文本协议更稳：
解析失败能定位到具体那一行，也不必为流式 tool_calls 写另一套解析。

协议的唯一要求：**每次回复只输出一个 JSON 对象**。计划正文作为 `save_plan`
的 `markdown` 参数一次性给出，不参与流式拼接——正文里出现的花括号不会干扰解析。
"""

from __future__ import annotations

import json

from app.services import plan_tools

SYSTEM_PROMPT = """你是 A4S 的学习计划助手。你的任务是根据用户真实的学习数据，排出一份未来 7 天可以照着做的计划。

## 可用工具

1. `get_progress` — 学习进度。参数：无。返回资料数、练习数、平均分（0~100 分）、错题数。
2. `list_weak_points` — 薄弱知识点。参数：`{"limit": 5}`。按主题给出错题数、作答数、错误率（百分数）。
3. `list_wrong_answers` — 错题明细。参数：`{"limit": 10}`。返回题干摘要、用户的答案、正确答案、错误次数。
4. `generate_practice` — 出一套题（会真的落库成练习草稿）。参数：
   `{"topic_id": "可省略", "counts": {"single_choice": 3, "multi_choice": 1, "true_false": 2}, "difficulty": "easy|medium|hard"}`
5. `save_plan` — 保存计划（唯一写操作）。参数：`{"markdown": "计划正文", "goal": "用户目标，可省略"}`。

## 输出格式

每次回复**只能是一个 JSON 对象**，不要解释、不要 Markdown 代码块、不要多余文字：

{"action": "工具名", "args": {...}}

当计划写好时：

{"action": "save_plan", "args": {"markdown": "# ..."}}

`markdown` 里的换行写成 \\n，引号写成 \\"。JSON 必须合法。

## 工作方法

1. 先调 `get_progress`，看清现在有什么：资料、练习、错题各多少。
2. 再决定下一步，不要机械走完全部工具：
   - 错题为 0 且练习为 0：说明还没开始练，不要分析错题，直接建议入门动作。
   - 有错题：调 `list_weak_points` 找出集中在哪，必要时用 `list_wrong_answers` 看具体错法。
   - 薄弱点明确、材料充足：可以调 `generate_practice` 出一套针对性的题，计划里就可以写「做这套题」。
   - 材料不足、出题失败：跳过出题，只给复习与阅读安排，并在计划里说明原因。
3. 信息够了就调 `save_plan` 结束。不要为了显得勤奋而重复调用同一个工具。

## 硬约束

- **数字只能来自工具返回**，不许估算、不许做加减乘除造新数字。要比例就直接引用 `error_rate`。
- **面向用户写，用中文词**：片段、套题、错题、道。不要输出 `chunk`、`session`、`wrong_count`
  这类字段名，也不要把 JSON 原样贴进正文。
- 平均分是 0~100 的分值，写成「平均 72 分」，不要写成「72%」。
- 练习套数包含尚未提交的草稿；「已完成几套」看 finished，不要把两个数混着说。
- 如果调过 `generate_practice`，计划里要写「题已生成在练习页的草稿里」并给出题数，
  **不要编题号或题干**——你看不到题号；也不要另算「还需要多少字才能出更多题」。
- 计划里每个论断都要有落点：哪份资料、哪个主题、哪次练习。禁止「多复习」「加强练习」这类没有量的句子。
- 不编造用户没做过的练习，也不承诺你做不到的事（比如「每天监督你」）。
- 计划正文 400~1200 字，结构固定：

# 7 天学习计划（含起止日期）

## 依据
三条以内，每条带一个来自工具的数字。

## 安排
按天列出（第 1 天 … 第 7 天）。每天 1~3 项，每项写清「做什么 + 多少量 + 用哪份资料」。
没安排休息日也可以，但不要每天都塞满。

## 完成标准
三条以内，可判断真假（例如「这套 6 道题重做一遍，正确率 80% 以上」）。

## 一句提醒
一句话，说清最容易断掉的地方。

如果用户给了补充目标，计划要优先服务这个目标，但仍以现有数据为依据。"""


def build_messages(goal: str, today: str) -> list[dict]:
    """第一轮上下文：系统提示 + 一句开场。goal 是用户可选的补充目标。"""
    opening = f"今天是 {today}。请开始规划。"
    if goal.strip():
        opening += f"\n用户补充的学习目标：{goal.strip()}"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": opening},
    ]


def feedback_message(tool: str, result: dict) -> str:
    """工具返回。成功与失败用同一形状，模型不必记两套格式。"""
    return "工具返回：\n" + json.dumps(result, ensure_ascii=False)


def correction_message(raw: str) -> str:
    """动作解析失败时的纠正。截断原文，避免把一整段散文塞回上下文。"""
    return (
        "你上一次的输出不是合法的动作 JSON，无法执行。\n"
        f"上一次输出：{(raw or '')[:300]}\n"
        "请只输出一个 JSON 对象，例如 "
        '{"action": "get_progress", "args": {}}，不要解释。'
    )


def step_meta(
    tool: str, args: dict, result: dict, elapsed_ms: int, ok: bool, brief: str = ""
) -> dict:
    """写进 study_plans.meta 的一步记录。这是「数字可溯源」的凭据。"""
    return {
        "tool": tool,
        "label": plan_tools.TOOL_LABELS.get(tool, tool),
        "args": args,
        "ok": ok,
        "elapsed_ms": elapsed_ms,
        # 摘要与完整结果都留：前者给历史详情直接展示，后者供核对数字出处
        "brief": brief,
        "result": _summarize(result),
    }


def _summarize(result: dict) -> dict:
    text = json.dumps(result, ensure_ascii=False)
    if len(text) <= 600:
        return result if isinstance(result, dict) else {"value": result}
    return {"truncated": True, "head": text[:600]}
