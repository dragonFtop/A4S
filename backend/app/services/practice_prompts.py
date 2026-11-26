"""出题提示词（FR-04）。

一条底线：题目只能来自给定材料。材料片段带 `[chunk:id]` 编号，模型必须在
`source_chunk_ids` 里回报编号；服务层据此剔除「无来源」的题——宁可少一道，
也不要一道没出处的题混进错题本。

题型分开调用：每种题型一套约束（选项数、答案格式、解析写法都不同），
混在一次请求里容易互相污染，数量配比也不可控。
"""

from __future__ import annotations

from app.models import QuestionType

SYSTEM_PROMPT = """你是一位严谨的命题老师，负责从给定学习材料中出检测题。

硬性规则：
1. 只能依据【材料】出题。不得使用材料之外的常识、不得推测、不得编造数据或结论。
2. 每道题必须真正可判定：读完材料的人能唯一确定答案。
3. `source_chunk_ids` 必须是材料中出现过的编号（形如 abc-123），1~2 个；禁止编造。
4. `explanation` 要说明依据，尽量引用材料里的关键句，不要只写「因为答案是对的」。
5. 只输出一个 JSON 数组，不要 Markdown 代码块，不要任何解释性文字。
6. 题目之间不要重复，不要只改一个词就当成新题。
7. 如果材料不足以支撑要求数量，就少出几道——材料不够时编题是最严重的问题。"""

TYPE_SPECS: dict[QuestionType, dict[str, str]] = {
    QuestionType.SINGLE_CHOICE: {
        "label": "单选题",
        "rule": """题型：单选。
- 4 个选项，互斥且只有一个正确；错误选项要像「真的会有人这么想」，而不是明显荒谬。
- `options` 只写选项内容，不要带 "A." 前缀；`answer` 只写一个字母（A/B/C/D）。""",
    },
    QuestionType.MULTI_CHOICE: {
        "label": "多选题",
        "rule": """题型：多选。
- 4~5 个选项，正确答案 2~3 个；每个正确项都要能在材料里找到依据。
- `options` 只写选项内容，不要带前缀；`answer` 写字母组合，按字母升序，如 "ABD"。""",
    },
    QuestionType.TRUE_FALSE: {
        "label": "判断题",
        "rule": """题型：判断。
- `stem` 是一句可以被判定对错的陈述；不要写成疑问句。
- 错误选项要基于真实材料的常见误解，不要为了凑「错」而写出材料根本没提的内容。
- `options` 留空数组；`answer` 只写 "正确" 或 "错误"。""",
    },
    QuestionType.SHORT_ANSWER: {
        "label": "简答题",
        "rule": """题型：简答。
- 问题要聚焦一个概念或机制，能让人用 2~4 句话答完；不要出「谈谈你的理解」这类无法判定的题。
- `options` 留空数组；`answer` 是参考答案（2~4 句，覆盖得分要点）。""",
    },
}

DIFFICULTY_HINTS = {
    "easy": "难度偏易：考概念定义、直接结论，材料里能直接找到答案。",
    "medium": "难度中等：考理解与辨析，需要把材料里的两三句话联系起来。",
    "hard": "难度偏难：考应用、对比与边界条件，但仍须严格由材料支撑。",
}

JSON_TEMPLATE = """[
  {
    "stem": "题干",
    "options": ["选项内容1", "选项内容2"],
    "answer": "A",
    "explanation": "解析：依据材料……",
    "source_chunk_ids": ["材料中的编号"],
    "knowledge_point": "这道题考的知识点（6~12 字）"
  }
]"""


def build_messages(
    question_type: QuestionType,
    material: str,
    count: int,
    difficulty: str = "medium",
) -> list[dict]:
    spec = TYPE_SPECS[question_type]
    system = (
        f"{SYSTEM_PROMPT}\n\n{spec['rule']}\n\n"
        f"{DIFFICULTY_HINTS.get(difficulty, DIFFICULTY_HINTS['medium'])}\n\n"
        f"输出格式（JSON 数组，每题一个对象）：\n{JSON_TEMPLATE}"
    )
    user = (
        f"题型：{question_type.value}\n"
        f"数量：{count} 道（材料不足时少出）\n\n"
        f"【材料】\n{material}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


REPAIR_MESSAGE = (
    "上一次输出无法被解析为 JSON。请只输出 JSON 数组本身："
    "不要代码块标记、不要前后说明、不要注释，字符串内不要出现未转义的换行。"
)
