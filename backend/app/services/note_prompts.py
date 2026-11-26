"""笔记起草的提示词与输出契约（FR-13 规则 3、4）。

为什么模板要写死在代码里
------------------------
模板不是"随便选个风格"，而是给笔记一个**固定骨架**：概念卡永远是
定义 / 为什么重要 / 易错点 / 相关概念 四项。只有结构稳定，两份笔记才能对比，
重新生成也才不会让用户每次看到不同形状的东西。

输出契约（模型必须遵守，落库前会被校验）
----------------------------------------
1. 只输出 Markdown 正文，第一行是 `# 标题`；
2. 每个要点/条目结尾必须带来源角标 `[[chunk:<id>]]`，id 只能来自给定资料；
3. 资料里确实没有、但为了让笔记成立必须补的一句话，单独成段并以 `<!--ai-->` 结尾；
4. 不允许编造 `[[chunk:...]]` 里不存在的 id——落库前会被剔除，等于白写。
"""

from __future__ import annotations

from app.models import NoteTemplate

AI_MARK = "<!--ai-->"

TEMPLATE_LABELS: dict[NoteTemplate, str] = {
    NoteTemplate.OUTLINE: "章节摘要",
    NoteTemplate.CONCEPT: "概念卡",
    NoteTemplate.CODE: "代码笔记",
    NoteTemplate.FORMULA: "公式清单",
}

TEMPLATE_HINTS: dict[NoteTemplate, str] = {
    NoteTemplate.OUTLINE: "按原文小节平铺，每节 3~5 条要点",
    NoteTemplate.CONCEPT: "每个概念含 定义 / 为什么重要 / 易错点 / 相关概念",
    NoteTemplate.CODE: "每个知识点含 原理 / 最小示例 / 常见坑 / 相关 API",
    NoteTemplate.FORMULA: "每条含 公式 / 符号含义 / 适用条件 / 推导要点",
}

_BODY: dict[NoteTemplate, str] = {
    NoteTemplate.OUTLINE: """\
## 结构要求
- 用 `##` 作为小节标题，小节顺序与资料原有顺序一致，不要重新编排章节名。
- 每个小节下用无序列表写 3~5 条要点，一条只说一个结论，不要写成长段落。
- 每个小节末尾可加一行 `> 原文：<一句话概括这一节在讲什么>`。""",
    NoteTemplate.CONCEPT: """\
## 结构要求
- 一个概念一个 `###` 小节，小节标题就是概念名。
- 每个概念固定四项，用无序列表，前缀分别是「定义：」「为什么重要：」「易错点：」「相关概念：」。
- 「相关概念」里如果提到资料中的其他概念，请互相呼应，不要凭空发明。""",
    NoteTemplate.CODE: """\
## 结构要求
- 一个知识点一个 `###` 小节。
- 每个知识点固定四项：「原理：」「最小示例：」「常见坑：」「相关 API：」。
- 「最小示例」必须是可直接运行的最小代码块（```python 或对应语言），
  代码里不要出现伪代码或 `...` 省略，示例尽量 15 行以内。
- 如果资料没有给出代码，只能根据资料里的 API 说明补一个最小用法，并标 `<!--ai-->`。""",
    NoteTemplate.FORMULA: """\
## 结构要求
- 一个公式一个 `###` 小节，小节标题用公式的常用名。
- 每个公式固定四项：
  1. 公式本体：块级公式 `$$ ... $$`，变量用 LaTeX 语法；
  2. 符号含义：Markdown 表格，两列「符号 / 含义」；
  3. 适用条件：无序列表；
  4. 推导要点：有序列表，最多 4 步。""",
}

SYSTEM_PROMPT = """\
你是严谨的学习笔记整理助手。你的读者是要考试、要动手写代码的学生。

硬性规则：
1. 只使用我提供的资料内容。资料没写的，不要用你自己的知识补；
   如果为了让笔记成立必须补一句话，就单独成段，并在该段末尾加上 {ai}。
2. 每条要点结尾必须带来源角标，格式为 [[chunk:片段id]]，id 只能取自我给出的
   「可用片段」列表。不要自己编 id。一条要点最多带两个角标。
3. 全部用简体中文，术语保留英文原样（如 B+ 树、attention）。
4. 直接输出 Markdown 正文，不要写"以下是笔记"这类开场白，也不要代码块包裹全文。
5. 第一行是 `# 标题`，标题要具体（例如「第 3 章 存储系统 · 章节摘要」），
   不要用"笔记""总结"这种空标题。"""

USER_TEMPLATE = """\
模板：{label}
骨架要求：
{body}

## 可用片段
{materials}

## 输出
按上面的骨架，把片段整理成一份可直接读的笔记。合并重复内容，保留关键结论与数字。
"""


def build_messages(
    template: NoteTemplate,
    materials: str,
    *,
    topic_name: str = "",
    extra: str = "",
) -> list[dict]:
    """组装一次起草请求。materials 已按「[chunk:id] 出处」格式拼好。"""
    system = SYSTEM_PROMPT.format(ai=AI_MARK)
    user = USER_TEMPLATE.format(
        label=TEMPLATE_LABELS[template], body=_BODY[template], materials=materials
    )
    if topic_name:
        user = f"主题：{topic_name}\n\n" + user
    if extra:
        user += f"\n## 额外要求\n{extra}\n"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
