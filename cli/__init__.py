"""cli 包：BugPilot 引擎的命令行入口。

引擎主循环（core.orchestrator.run_pipeline）会按拓扑序自走跑完整条流水线，AI 阶段
由引擎亲自起 Agent 进程执行、失败按 retry_on_fail 回滚重试。本包在它之上薄薄包一层
"命令行 + 分发 + 实时打印"，不含任何业务智能。

用法示例::

    python3 -m cli run demo2 --description "登录页空密码崩溃"   # 正常任务：引擎自走跑完整条流水线
    python3 -m cli run demo2 --only intake                     # 只推进单个阶段（调试 / 手工续跑）
    python3 -m cli run demo2 --only analyze --preview          # 只预览将交给 Agent 的 prompt，不落盘、不起 Agent
    python3 -m cli status demo2                                # 查看进度（给人看）
"""

from cli.main import main

__all__ = ["main"]
