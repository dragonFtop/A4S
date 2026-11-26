"""回顾报告的提示词（FR-07）。

设计取舍：**数字交给程序，文字交给模型。**

新增了几份资料、练习几套、正确率多少、哪几道错得最多——这些一律由服务层
从库里算好写进 Markdown。模型只负责三件需要"读懂"的事：按主题概括知识点、
判断薄弱点、给出复习建议。

这样做的好处是验收条件里的「报告中的错题与错题本数据一致」天然成立：
模型没有机会改动任何数字。代价是报告不是完全由模型自由发挥，
但一份数字对不上的漂亮报告没有价值。
"""

from __future__ import annotations

import json

# 题型中文名只在这里定义一份：prompt 里给中文，模型才不会把 single_choice 抄进正文
TYPE_LABELS: dict[str, str] = {
    "single_choice": "单选",
    "multi_choice": "多选",
    "true_false": "判断",
    "short_answer": "简答",
}

SYSTEM_PROMPT = """你是一位学习教练，负责根据学生的真实学习数据写回顾分析。

硬性规则：
1. 只能依据给定的【数据】判断。数据里没有的主题、资料、知识点，一个字都不能提。
2. 不许编造数字、比例、日期。需要引用数字时，只能原样使用数据里已有的数字。
3. `topic_summaries` 只能覆盖数据里出现过的主题，一个主题一条，不要合并也不要拆分。
4. `weak_points` 给 2~3 条，每条必须点名具体主题，写成一句判断，例如
   「操作系统正确率 45%，低于你的整体水平，虚拟内存这块明显没吃透」。
   不许写「需要加强复习」这类放在任何报告里都成立的空话。
5. `suggestions` 给 3~4 条，要具体可执行：学哪份资料、从哪个知识点补、先做哪类题。
   「多练习」「勤复习」这种不算建议。
6. 数据为空或某个主题没有练习记录时，如实说明，不要虚构表现。
7. 只输出一个 JSON 对象，不要 Markdown 代码块，不要任何额外文字。"""

OUTPUT_SCHEMA = """输出 JSON 结构：
{
  "topic_summaries": [{"topic": "主题名", "summary": "该主题本期资料与知识点的概括，60 字以内"}],
  "weak_points": ["判断一", "判断二"],
  "suggestions": ["建议一", "建议二", "建议三"]
}"""


def build_user_prompt(metrics: dict, wrong: dict, top_wrong: list[dict]) -> str:
    """把窗口指标压成模型可读的紧凑文本。

    刻意不喂完整 JSON：模型容易被嵌套结构带偏，也会把大量无关字段复述进正文。
    """
    lines: list[str] = []
    window = metrics.get("window_days", 7)
    lines.append(f"统计窗口：最近 {window} 天")

    documents = metrics.get("documents", {})
    lines.append(f"\n【本期新增资料】共 {documents.get('total', 0)} 份，{documents.get('chunks', 0)} 个知识片段")
    for group in documents.get("by_topic", []):
        names = "、".join(doc["name"] for doc in group.get("documents", [])[:5])
        lines.append(f"- {group['name']}：{group['count']} 份（{names}）")

    practice = metrics.get("practice", {})
    lines.append(
        f"\n【本期练习】{practice.get('sessions', 0)} 套，题量 {practice.get('questions', 0)}，"
        f"实际作答 {practice.get('answered', 0)} 题，答对 {practice.get('correct', 0)} 题，"
        f"正确率 {practice.get('accuracy', 0)}%"
    )
    for bucket in practice.get("by_topic", []):
        lines.append(
            f"- {bucket['name']}：作答 {bucket['answered']} 题，正确率 {bucket['accuracy']}%"
        )
    for bucket in practice.get("by_type", []):
        label = TYPE_LABELS.get(bucket["type"], bucket["type"])
        lines.append(f"- 题型 {label}：作答 {bucket['answered']} 题，正确率 {bucket['accuracy']}%")

    lines.append(
        f"\n【错题】错题条数 {wrong.get('total', 0)}，累计答错 {wrong.get('total_wrong_count', 0)} 次，"
        f"其中重复错 {wrong.get('repeat_count', 0)} 条"
    )
    for bucket in wrong.get("by_topic", [])[:6]:
        lines.append(
            f"- {bucket['name']}：{bucket['question_count']} 道题，累计错 {bucket['wrong_count']} 次，"
            f"该主题错误率 {bucket.get('error_rate', 0)}%"
        )

    if top_wrong:
        lines.append("\n【错得最多的题】")
        for index, item in enumerate(top_wrong[:5], start=1):
            lines.append(
                f"{index}. [错 {item['wrong_count']} 次][{item.get('topic_name', '未分类')}] {item['stem'][:90]}"
            )

    if not documents.get("total") and not practice.get("sessions") and not wrong.get("total"):
        lines.append("\n【注意】本期没有任何学习行为，请如实说明，并给出「从哪开始」的建议。")

    lines.append("\n" + OUTPUT_SCHEMA)
    return "\n".join(lines)


def empty_analysis() -> dict:
    """模型不可用时的占位，服务层会用规则版建议替换。"""
    return {"topic_summaries": [], "weak_points": [], "suggestions": []}


def parse_analysis(raw: str) -> dict:
    """从模型输出里抠出分析结果。

    只做"尽力而为"：字段缺失时返回空列表，由服务层逐项降级，
    不因为模型少写一个字段就让整份报告生成失败。
    """
    text = (raw or "").strip()
    if not text:
        return empty_analysis()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return empty_analysis()

    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return empty_analysis()
    if not isinstance(payload, dict):
        return empty_analysis()

    def _list_of_dict(key: str, fields: tuple[str, ...]) -> list[dict]:
        items = payload.get(key)
        if not isinstance(items, list):
            return []
        result: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            cleaned = {name: str(item.get(name, "")).strip() for name in fields}
            if all(cleaned.values()):
                result.append(cleaned)
        return result

    def _list_of_str(key: str) -> list[str]:
        items = payload.get(key)
        if not isinstance(items, list):
            return []
        return [str(item).strip() for item in items if str(item).strip()]

    return {
        "topic_summaries": _list_of_dict("topic_summaries", ("topic", "summary")),
        "weak_points": _list_of_str("weak_points"),
        "suggestions": _list_of_str("suggestions"),
    }
