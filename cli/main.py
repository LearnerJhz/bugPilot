"""CLI 主逻辑：参数解析 + 驱动引擎。

职责边界（和引擎的分工）：
- ``core.orchestrator.run_pipeline`` 是"引擎主循环"：按拓扑序自走跑完整条流水线，
  AI 阶段由引擎亲自起 Agent 进程执行，失败按 retry_on_fail 回滚重试。
- ``core.orchestrator.run_phase`` 是"只推进单个 phase"的原子操作（调试 / 手工续跑）。
- 本模块只做参数解析 + 分发 + 把引擎的实时进度打印给人看。

退出码约定：
- 0  ：整条流水线成功（或单阶段通过 / preview 成功）
- 20 ：闸门/置信度未过且重试耗尽 —— 整体 BLOCKED
- 1  ：执行器未注册 / Agent 起不来等可预期错误
- 2  ：命令用法错误（argparse 负责）
"""

from __future__ import annotations

import argparse
from typing import Optional

from adapters.run_state_store import RunStateStore
from core.manifest import load_manifest
from core.models import PhaseVerdict, RunStatus
from core.orchestrator import (
    PhaseOutcome,
    run_phase,
    run_pipeline,
)

# 退出码常量
EXIT_OK = 0
EXIT_BLOCKED = 20
EXIT_ERROR = 1  # 阶段/执行器未注册等可预期的中止

# 裁决 -> 展示用小图标
_ICON = {
    PhaseVerdict.PASSED: "✅",
    PhaseVerdict.BLOCKED: "⛔",
    PhaseVerdict.PREVIEW: "👀",
}


def build_parser() -> argparse.ArgumentParser:
    """搭出 CLI 的参数树：目前只有一个子命令 ``run``。"""
    parser = argparse.ArgumentParser(
        prog="cli",
        description="BugPilot 引擎命令行：按 manifest 顺序推进各阶段。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="推进某个任务的流水线")
    run_p.add_argument("task_id", help="任务 id（也是 tasks/<task_id>/ 产物目录名）")
    run_p.add_argument(
        "--description",
        default=None,
        help="任务的自然语言描述（如 issue URL 或 bug 描述），首次跑 intake 时用。",
    )
    run_p.add_argument(
        "--repo",
        default=None,
        help="Agent 改代码的工作区路径，默认当前目录。",
    )

    # 只推进指定的单个阶段（调试 / 手工续跑）；不带 --only 时引擎自走跑完整条流水线。
    run_p.add_argument(
        "--only",
        metavar="PHASE",
        help="只推进指定的单个阶段（调试用；不带则整条流水线自走跑完）。",
    )

    run_p.add_argument(
        "--preview",
        action="store_true",
        help="只预览将写入的 prompt，不落盘、不改状态（仅对起始阶段生效）。",
    )

    # 自测：用内置假 Agent 顶替真 Agent，验证引擎编排本身（不接模型、不花钱）。
    run_p.add_argument(
        "--fake-agent",
        action="store_true",
        help="自测用：AI 阶段用内置假 Agent 顶替（不起 claude），只验证引擎编排。",
    )

    # 第二个子命令：status —— 只读地查看某任务的运行进度。
    # 它有自己独立的一套参数（这里只有一个位置参数 task_id），
    # 和 run 的参数互不相干：只有用户敲了 `status` 时才会用到。
    status_p = sub.add_parser("status", help="查看某个任务的运行进度")
    status_p.add_argument("task_id", help="任务 id（tasks/<task_id>/ 目录名）")

    return parser


def _print_outcome(outcome: PhaseOutcome) -> None:
    """把单个阶段的结果打印成一行人类可读的摘要 + 相关路径。"""
    icon = _ICON.get(outcome.status, "•")
    print(f"{icon} [{outcome.phase_id}] {outcome.status.value}: {outcome.message}")
    if outcome.preview_text:
        print("---- prompt preview ----")
        print(outcome.preview_text)
        print("------------------------")
    if outcome.prompt_path:
        print(f"    prompt : {outcome.prompt_path}")
    if outcome.output_path:
        print(f"    output : {outcome.output_path}")
    if outcome.missing_sections:
        print(f"    missing: {outcome.missing_sections}")


def _maybe_fake_agent(args: argparse.Namespace):
    """自测开关：--fake-agent 时返回内置假 Agent，否则 None（引擎按 manifest 装配真 Agent）。"""
    if not getattr(args, "fake_agent", False):
        return None
    from adapters.fake_agent_runner import FakeAgentRunner

    print("🧪 使用假 Agent（--fake-agent）：只验证引擎编排，不接真模型。")
    return FakeAgentRunner()


def _run_single(args: argparse.Namespace) -> int:
    """--only / --preview：只推进单个阶段（调试 / 手工续跑）。"""
    phase_id = args.only or load_manifest().phase_ids()[0]
    try:
        outcome = run_phase(
            args.task_id,
            phase_id,
            description=args.description,
            repo=args.repo,
            preview=args.preview,
            agent_runner=_maybe_fake_agent(args),
            log=print,
        )
    except KeyError as exc:
        print(f"⛔ [{phase_id}] error: {exc.args[0] if exc.args else exc}")
        return EXIT_ERROR
    _print_outcome(outcome)
    if outcome.status == PhaseVerdict.BLOCKED:
        return EXIT_BLOCKED
    return EXIT_OK


def _cmd_run(args: argparse.Namespace) -> int:
    """`run` 子命令：默认引擎自走跑完整条流水线；--only/--preview 走单阶段。"""
    if args.only or args.preview:
        return _run_single(args)

    try:
        result = run_pipeline(
            args.task_id,
            description=args.description,
            repo=args.repo,
            agent_runner=_maybe_fake_agent(args),
            log=print,  # 引擎每走一步就实时打印
        )
    except KeyError as exc:
        print(f"⛔ error: {exc.args[0] if exc.args else exc}")
        return EXIT_ERROR

    if result.status == RunStatus.SUCCEEDED.value:
        return EXIT_OK
    return EXIT_BLOCKED


def _cmd_status(args: argparse.Namespace) -> int:
    """`status` 子命令的处理：只读地打印某任务的运行进度。"""
    RunStateStore.configure("tasks")
    state = RunStateStore.load(args.task_id)
    if state is None:
        print(f"⛔ 未找到任务 '{args.task_id}' 的运行状态（tasks/{args.task_id}/run_state.json 不存在）。")
        return EXIT_ERROR

    print(f"📋 任务 '{state.task_id}' —— 状态: {state.status.value}")
    if state.current_phase:
        print(f"    当前阶段: {state.current_phase}")
    if not state.phase_results:
        print("    （还没有任何阶段结果）")
    for phase_id, result in state.phase_results.items():
        print(f"    - [{phase_id}] {result.status.value}")
    return EXIT_OK


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 入口。返回进程退出码，交给 __main__ / SystemExit。"""

    # 把字符串 → args 对象
    args = build_parser().parse_args(argv)

    # 按用户选中的子命令（args.command）分发到对应处理函数。
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "status":
        return _cmd_status(args)

    # required=True 已保证必须选一个子命令，正常到不了这里。
    raise SystemExit(f"未知子命令: {args.command}")
