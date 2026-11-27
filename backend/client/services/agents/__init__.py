"""学习教练的多智能体实现（M8）。

- `observer` 观察员：只读数据，产出 Finding
- `planner`  规划员：基于事实提出 Proposal
- `verifier` 审核员：确定性核对，给出 Verdict
- `writer`   记录员：唯一写入通道
- `coordinator` 协调员：串起四个角色，管预算、落账本、转 SSE
"""

from .coordinator import run_coach

__all__ = ["run_coach"]