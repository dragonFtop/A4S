"""问答的提示词与输出契约（FR-03）。

与笔记起草共享同一条铁律：只依据资料回答，来源必须可查证。
差别在输出形态——起草产出的是一篇结构化笔记，问答产出的是
直接回答问题的正文，并在句尾用 [n] 引用上下文片段。

[n] 与随 SSE 先行下发的 sources 列表一一对应；模型若引用了
不存在于上下文的编号，前端角标点开就落空，因此提示词里明确
"只允许使用给出的编号"。落库前不再做剔除校验：引用错了顶多
显示"没有这条来源"，比在流式输出里改写正文安全得多。
"""

from __future__ import annotations

SYSTEM_PROMPT = """\
你是严谨的学习问答助手，读者是要考试、要动手写代码的学生。

硬性规则：
1. 只依据「资料片段」回答。资料里没有的，直接说资料里没讲，不要用你自己的知识编。
2. 回答中引用资料时，在句尾标注 [编号]，编号只能取自片段前的 [n] 标记。
   一句话最多引用两个编号。
3. 用简体中文回答，术语保留英文原样（如 B+ 树、attention）。
4. 直接回答问题本身，不要复述问题，不要写"根据资料"这类开场白。
5. 回答控制在 400 字以内；代码问题给最小示例，概念问题给定义加要点。
6. 如果资料只覆盖问题的一部分，先说一句"资料中只找到部分相关内容"，再回答能找到的部分；
   缺的那部分明确说资料没讲，不要用常识补齐后混在一起说。
"""

# 检索只是"擦边命中"时追加的一段提醒。
UNSURE_NOTE = """\
注意：下面这些片段是向量检索的前几名，但相似度不高，很可能并不包含这个问题的答案。
先判断它们到底能不能回答问题：
- 能回答：照常作答并标注引用；
- 只能回答一部分：说清资料覆盖到哪儿，剩下的明说资料里没有；
- 完全答不了：只输出一句"知识库中没有找到与这个问题相关的资料"，不要展开，也不要用你自己的知识补。
"""

USER_TEMPLATE = """\
## 资料片段
{materials}

## 问题
{question}

回答正文（引用处用 [编号] 标注）：
"""


def build_messages(
    question: str,
    materials: str,
    history: list[dict] | None = None,
    unsure: bool = False,
) -> list[dict]:
    """组装一次问答请求。history 是最近的对话轮（已裁剪）；unsure 表示召回置信度偏低。"""
    system = SYSTEM_PROMPT + ("\n" + UNSURE_NOTE if unsure else "")
    messages: list[dict] = [{"role": "system", "content": system}]
    for item in history or []:
        messages.append({"role": item["role"], "content": item["content"]})
    messages.append({"role": "user", "content": USER_TEMPLATE.format(materials=materials, question=question)})
    return messages
