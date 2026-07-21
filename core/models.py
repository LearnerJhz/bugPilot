"""在各模块之间传递的数据结构：阶段、状态、结果、补丁。

仅使用标准库，无第三方依赖。纯数据定义，不含业务逻辑。
"""
from dataclasses import dataclass, field
from typing import Optional

# --------------------------------------------------------------------------
# Manifest 侧契约（声明式地描述"流程是什么"）
# --------------------------------------------------------------------------
@dataclass
class PhaseSpec:
    """在 manifest 中声明的一个阶段：由哪个执行器驱动、输出什么文件，
    以及（可选）该输出必须包含的标题。引擎负责调度这些阶段；执行器只负责
    填充产物。新增一个阶段 = 一条 manifest 条目加一个已注册的执行器，
    永远不需要改动编排器。"""

    # 唯一的阶段 id，用于依赖引用、调度查找以及展示（例如 "intake"）
    id: str

    # 驱动该阶段的执行器名称；引擎据此解析具体实现
    executor: str

    # 该阶段产生的输出文件；下一个阶段会消费它
    output: str

    # 前置阶段 id；拓扑排序据此确定执行顺序。
    # 可变默认值（list/dict/set）必须使用 default_factory，否则会在多个实例间共享。
    depends_on: list[str] = field(default_factory=list)

    # 输出必须包含的标题；用于校验完整性
    required_sections: list[str] = field(default_factory=list)

    # 交给执行器（通常是 LLM）的可选提示词；未使用时为 None
    prompt: Optional[str] = None
