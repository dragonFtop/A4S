"""多智能体教练的预算。

上限不是拍脑袋：它对应 docs/AGENT_OPPORTUNITIES.md 4.4 的验收标准——
「循环在 12 步内终止」。工具步数仍对外承诺 6 步（前端与 /api/plan/meta 都这么说），
角色交接与纠正只消耗 LLM 调用额度，不再额外加「步」。
"""

from __future__ import annotations

# 对用户承诺的工具步数上限（前端文案与 meta 接口使用同一个数）
MAX_TOOL_STEPS = 6
# 一次运行里模型最多被调用多少次（观察 + 规划 + 纠正 + 打回重写）
MAX_LLM_CALLS = 12
# 角色交接次数上限：观察员 → 规划员 → 审核员 → 记录员
MAX_HANDOFFS = 3
# 审核打回后最多重写一次
MAX_VERIFIER_RETRIES = 1
# 同一个只读工具最多查两次：第三次开始大概率是在原地打转
MAX_SAME_TOOL_CALLS = 2

# 观察员自己的小预算：查得太久不如早点交棒
MAX_OBSERVER_STEPS = 3
MAX_OBSERVER_CALLS = 4
# 规划员：出一次题 + 提交（含一次纠正 / 一次打回重写）
MAX_PLANNER_CALLS = 4
MAX_PRACTICE_CALLS = 1