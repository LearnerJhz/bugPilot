"""orchestrator：引擎主循环（引擎驱动 Agent 的控制平面）。

这是"路线B"的核心：引擎按 manifest 拓扑序推进各阶段，**对 AI 阶段亲自起一个
Agent 进程**（通过 ``AgentRunner``）让它自走干完，再回收产物、判卷；失败则按
``retry_on_fail`` 把执行指针拨回指定阶段回滚重跑。全程引擎自己不调大模型，
"用哪个模型"由 Agent CLI 的环境变量决定（见 ``adapters.agent_runner``）。

代码结构就两个对象，变量各归其位、不再到处手传：

- ``Engine``：**整条运行**的持有者。manifest / 三个适配器 / agent / run_state /
  重试反馈都挂在它身上，它负责主循环（依赖门、断点续跑、回滚重试、落状态）。
- ``Phase``：**一个阶段一次执行**的持有者。把 spec / executor / ctx / result 束在
  一起，对外只暴露一条固定四步的 ``run()``::

      准备 prompt  →  喂给 Agent  →  收产物  →  校验
      prepare()       _produce()（AI 阶段起进程 / 确定性阶段直接写）  _gate()

  这四步是 ``Phase`` 的方法，全部从 ``self`` 取料，所以彼此几乎不用传参。

模块级 ``run_pipeline`` / ``run_phase`` 只是薄薄的入口：组一个 ``Engine`` 再让它跑。
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from core.manifest import load_manifest
from core.models import (
    PhaseResult,
    PhaseSpec,
    PhaseStatus,
    PhaseVerdict,
    RunState,
    RunStatus,
    utc_now_iso,
)
from core.ports import ExecutionContext, PhaseExecutor, PhasePrep
from core.registry import create_executor

from adapters.agent_runner import build_agent_runner
from adapters.promote_store import PromoteStore
from adapters.run_state_store import RunStateStore
from adapters.local_workspace import LocalWorkspace

# 注册执行器（import 触发 @register）。新增执行器时在这里加一行 import。
import executors.intake  # noqa: F401,E402
import executors.agent_executor  # noqa: F401,E402
import executors.apply  # noqa: F401,E402
import executors.verify  # noqa: F401,E402


@dataclass
class PhaseOutcome:
    """``phase.run()`` 一次执行的返回值：一个 :class:`PhaseVerdict` 裁决 + 相关路径/诊断。

    注意别和 :class:`core.models.PhaseResult` 混淆——``PhaseResult`` 是**落盘**的
    单阶段生命周期记录（状态/时间戳/重试次数），``PhaseOutcome`` 是**这一轮跑完**
    临时返回给主循环的裁决（不落盘）。
    """

    status: PhaseVerdict
    phase_id: str
    message: str = ""
    prompt_path: Optional[str] = None
    output_path: Optional[str] = None
    missing_sections: list[str] = field(default_factory=list)
    confidence: Optional[float] = None
    preview_text: Optional[str] = None


@dataclass
class PipelineResult:
    """整条流水线跑完（或卡住）后的总结。``status`` 用 RunStatus 值。"""

    status: str
    run_state: RunState
    last_outcome: Optional[PhaseOutcome] = None


# 引擎发给外部（CLI）的实时进度回调；默认吞掉不打印。
Logger = Callable[[str], None]


def _noop_logger(_msg: str) -> None:  # pragma: no cover - 默认空实现
    pass


# ==========================================================================
# Phase：一个阶段一次执行 —— 把"要用的东西"束成一个对象，四步都在它身上
# ==========================================================================
@dataclass
class Phase:
    """一个阶段一次执行所需的全部上下文 + 干活方法。

    构造时只需给 ``spec / executor / result`` 和一个 ``engine`` 反向引用；交给
    executor 的 ``ctx`` 由 Phase **自己**在 ``__post_init__`` 里拼（料都在 engine 上），
    Engine 不再手工造 ctx。于是四步方法（``_produce`` / ``_gate`` / ``_preview``）几乎
    不用参数——store / agent / log 全从 ``self`` 够得着。
    """

    engine: "Engine"
    spec: PhaseSpec
    executor: PhaseExecutor
    result: PhaseResult

    # 交给 executor 的只读上下文，由本类在 __post_init__ 里自造（不从外部塞入）。
    ctx: ExecutionContext = field(init=False)
    # 本轮 prompt 落盘后的路径（供 outcome 展示）；无 prompt（确定性阶段）则为 None。
    prompt_path: Optional[str] = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.ctx = self._build_context()

    def _build_context(self) -> ExecutionContext:
        """把交给 executor 的只读上下文拼出来（料全部取自 engine）。"""
        eng = self.engine
        return ExecutionContext(
            task_id=eng.task_id,
            spec=self.spec,
            description=eng.run_state.description,
            workspace=eng.workspace,
            store=eng.store,
            prior_artifacts=eng.gather_prior_artifacts(self.spec.id),
            project=eng.manifest.project,
            retry_feedback=eng.retry_feedback.get(self.spec.id),
        )

    # ---- 便捷访问：常用的几个从 spec / engine 取，方法体里就不再层层传参 ----
    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def output(self) -> str:
        return self.spec.output

    @property
    def store(self) -> type[PromoteStore]:
        return self.engine.store

    @property
    def task_id(self) -> str:
        return self.engine.task_id

    def _log(self, msg: str) -> None:
        self.engine.log(msg)

    def _out_path(self) -> str:
        return self.store.path_for(self.task_id, self.output)

    # ======================================================================
    # 固定四步：准备 prompt → 喂给 Agent → 收产物 → 校验
    # ======================================================================
    def run(self, *, preview: bool = False) -> PhaseOutcome:
        self.result.status = PhaseStatus.RUNNING

        # ① 准备：执行器把这一阶段的 prompt（AI 阶段）或最终产物（确定性阶段）拼好。
        prep = self.executor.prepare(self.ctx)

        # preview：只想看将交给 Agent 的 prompt，不落盘、不起 Agent，直接返回。
        if preview:
            return self._preview(prep)

        # 把 prompt 与执行器要求的附加产物落盘（便于排查 / 复现）。
        self._persist_inputs(prep)

        # ② 喂给 Agent + ③ 收产物（确定性阶段则直接写出产物）。
        #    收产物失败会直接给回一个 BLOCKED 的 outcome；否则拿到产物内容。
        produced = self._produce(prep)
        if isinstance(produced, PhaseOutcome):
            return produced

        # ④ 校验：必需章节齐全 + 置信度达标才 PASSED。
        return self._gate(produced)

    # ---- preview：不落盘、不起 Agent ----
    def _preview(self, prep: PhasePrep) -> PhaseOutcome:
        return PhaseOutcome(
            status=PhaseVerdict.PREVIEW,
            phase_id=self.id,
            message="preview：以下为将交给 Agent 的 prompt（未落盘、未起 Agent）。",
            output_path=self._out_path(),
            preview_text=prep.prompt_text or prep.artifact,
        )

    # ---- 落盘 prompt（AI 阶段）+ 执行器额外要求的附加产物 ----
    def _persist_inputs(self, prep: PhasePrep) -> None:
        if prep.prompt_text:
            self.prompt_path = self.store.write(
                self.task_id, f"_prompt_{self.id}.md", prep.prompt_text
            )
        for name, extra in prep.extra_artifacts.items():
            self.store.write(self.task_id, name, extra)

    # ---- ②+③：产出产物。AI 阶段起 Agent 自走后读回；确定性阶段直接写。 ----
    # 返回产物内容（str）；若收产物失败则返回一个 BLOCKED 的 PhaseOutcome。
    def _produce(self, prep: PhasePrep):
        # 确定性阶段：执行器已算好产物，写盘即最终产物，不劳驾 Agent。
        if not prep.prompt_text:
            self.store.write(self.task_id, self.output, prep.artifact)
            return prep.artifact

        # AI 阶段：先清残留旧产物，确保"跑完后产物在" == 这轮 Agent 真写了。
        self.store.delete(self.task_id, self.output)

        self._log(f"   ↳ 起 Agent 进程自走（阶段 {self.id}）…")
        run_res = self.engine.agent.run(
            prompt=prep.prompt_text, workspace=self.ctx.workspace, phase_id=self.id
        )
        self.result.notes.append(
            f"agent exit={run_res.exit_code} dur={run_res.duration_s:.1f}s timeout={run_res.timed_out}"
        )

        # 两道健壮性检查，任一不满足即 BLOCKED（不进 gate）：
        # 3a) 进程本身要正常退出（非零退出 / 超时都算失败）。
        if not run_res.ok:
            return self._blocked(
                f"Agent 进程异常（exit={run_res.exit_code}）："
                f"{(run_res.stderr or '').strip()[:400]}"
            )
        # 3b) 进程正常，但必须真的把产物写到约定路径（prompt 里已要求）。
        if not self.store.exists(self.task_id, self.output):
            return self._blocked(
                f"Agent 跑完但没写出产物 '{self.output}'（prompt 里已要求写到该路径）。"
            )

        return self.store.read(self.task_id, self.output)

    # ---- ④ 校验：必需章节 +（可选）置信度门，两者都过才 PASSED ----
    def _gate(self, artifact: str) -> PhaseOutcome:
        self.result.output_path = self._out_path()
        gate = self.executor.gate(self.ctx, artifact)

        # 置信度只信 executor.gate() 的单一真源——它已负责从产物里解析 `## Confidence`
        # （解析不到再回退到 1.0/0.0），引擎不再重复解析一遍。
        conf = gate.confidence

        sections_ok = gate.passed
        conf_ok = self.spec.confidence_min is None or (
            conf is not None and conf >= self.spec.confidence_min
        )

        if sections_ok and conf_ok:
            self.result.status = PhaseStatus.SUCCEEDED
            self.result.finished_at = utc_now_iso()
            return PhaseOutcome(
                status=PhaseVerdict.PASSED,
                phase_id=self.id,
                message=f"通过闸门（confidence={conf}）。",
                output_path=self._out_path(),
                confidence=conf,
                prompt_path=self.prompt_path,
            )

        self.result.status = PhaseStatus.BLOCKED
        if not sections_ok:
            msg = f"缺少必需章节 {gate.missing_sections}"
        else:
            msg = f"置信度 {conf} < 阈值 {self.spec.confidence_min}"
        return PhaseOutcome(
            status=PhaseVerdict.BLOCKED,
            phase_id=self.id,
            message=msg,
            output_path=self._out_path(),
            missing_sections=gate.missing_sections,
            confidence=conf,
            prompt_path=self.prompt_path,
        )

    # ---- 收产物阶段的失败：标 BLOCKED 并给一个 BLOCKED 的 outcome ----
    def _blocked(self, message: str) -> PhaseOutcome:
        self.result.status = PhaseStatus.BLOCKED
        return PhaseOutcome(
            status=PhaseVerdict.BLOCKED,
            phase_id=self.id,
            message=message,
            prompt_path=self.prompt_path,
            output_path=self._out_path(),
        )


# ==========================================================================
# Engine：整条运行的持有者 —— manifest / 适配器 / agent / 状态都在这里，主循环也在这里
# ==========================================================================
class Engine:
    """一次运行（一个 task）的控制平面。

    构造时把"整条运行共享"的东西一次性装配好（manifest / 三个适配器 / agent /
    run_state / 分支），之后主循环 ``run_pipeline`` 每推进一个阶段，就现组一个
    ``Phase`` 让它跑那固定四步，自己只管：依赖门、断点续跑、回滚重试、落状态。
    """

    def __init__(
        self,
        task_id: str,
        *,
        description: Optional[str] = None,
        repo: Optional[str] = None,
        agent_runner=None,
        log: Logger = _noop_logger,
    ) -> None:
        self.task_id = task_id
        self.log = log

        # 声明式流程
        self.manifest = load_manifest()
        self.order = self.manifest.topo_order()  # 已满足依赖的合法执行顺序
        self.id_to_idx = {spec.id: i for i, spec in enumerate(self.order)}

        # 适配器（端口的具体实现）：产物读写 / 状态存 / 代码工作区。
        # PromoteStore / RunStateStore 都是全局静态工具，无需实例化——
        # 设定一次基目录后直接用类本身当 store。
        PromoteStore.configure("tasks")
        RunStateStore.configure("tasks")
        
        # 把「类本身」存起来
        self.store: type[PromoteStore] = PromoteStore
        self.state_store: type[RunStateStore] = RunStateStore
        self.workspace = LocalWorkspace(repo or ".")

        # 运行状态（没有就新建）。分支由你自己提前切好，引擎不切/建分支、不提交。
        self.run_state = self.state_store.load(task_id) or RunState(task_id=task_id)
        if description:
            self.run_state.description = description
        self.run_state.status = RunStatus.RUNNING

        # 执行单元：给 prompt 起 Agent 进程自走（测试可注入 fake）
        self.agent = agent_runner or build_agent_runner(self.manifest.project)

        # phase_id -> 给该阶段下一轮 prepare 的重试反馈（回滚时写入，被 target 阶段读取）
        self.retry_feedback: dict[str, str] = {}

    # ---- 主循环：按拓扑序自走跑完整条流水线 ----
    def run_pipeline(self) -> PipelineResult:
        last_outcome: Optional[PhaseOutcome] = None
        idx = 0
        while idx < len(self.order):
            spec = self.order[idx]

            # 断点续跑：已成功且产物还在 → 跳过（回滚会把状态重置，故不会误跳）。
            if self._resume_skip(spec):
                idx += 1
                continue

            # 依赖门：前置阶段必须都成功，否则本阶段 SKIP。
            if not self._deps_satisfied(spec):
                self._mark_skipped(spec)
                idx += 1
                continue

            phase = self._new_phase(spec)
            self.log(f"▶️  [{spec.id}] 开始（executor={spec.executor}）…")
            outcome = phase.run()
            self._commit_phase(phase, outcome)
            last_outcome = outcome

            if outcome.status == PhaseVerdict.PASSED:
                self.log(f"✅ [{spec.id}] 过闸（confidence={outcome.confidence}）。")
                self.retry_feedback.pop(spec.id, None)
                idx += 1
                continue

            # 未过闸：按 retry_on_fail 决定回滚重试还是整体停下。
            next_idx = self._on_failure(phase, outcome, idx)
            if next_idx is None:
                return PipelineResult(
                    status=RunStatus.BLOCKED.value,
                    run_state=self.run_state,
                    last_outcome=outcome,
                )
            idx = next_idx

        self.run_state.status = RunStatus.SUCCEEDED
        self.run_state.current_phase = None
        self.run_state.updated_at = utc_now_iso()
        self._save()
        self.log(f"🎉 任务 '{self.task_id}' 全部阶段完成。")
        return PipelineResult(
            status=RunStatus.SUCCEEDED.value,
            run_state=self.run_state,
            last_outcome=last_outcome,
        )

    # ---- 单阶段：只推进一个 phase（--only / --preview，用于调试 / 手工续跑）----
    def run_one(self, phase_id: str, *, preview: bool = False) -> PhaseOutcome:
        spec = self._resolve_spec(phase_id)
        phase = self._new_phase(spec)
        outcome = phase.run(preview=preview)
        if outcome.status == PhaseVerdict.PREVIEW:
            return outcome
        self._finalize_single(phase_id, phase.result, outcome)
        self._save()
        return outcome

    # ------------------------------------------------------------------
    # 组装一个 Phase：只递 spec/executor/result，ctx 由 Phase 自己拼
    # ------------------------------------------------------------------
    def _new_phase(self, spec: PhaseSpec) -> Phase:
        result = self.run_state.phase_results.get(spec.id) or PhaseResult(phase_id=spec.id)
        result.started_at = result.started_at or utc_now_iso()
        executor = create_executor(spec.executor)
        return Phase(engine=self, spec=spec, executor=executor, result=result)

    def gather_prior_artifacts(self, phase_id: str) -> list[tuple[str, str]]:
        """按拓扑序收集当前阶段之前、且已存在的产物，作为下游 prompt 的上下文。

        由 ``Phase._build_context`` 调用（造 ctx 的一环）；放在 Engine 是因为它要
        用到整条运行的拓扑序 ``self.order``。
        """
        prior: list[tuple[str, str]] = []
        for spec in self.order:
            if spec.id == phase_id:
                break
            if self.store.exists(self.task_id, spec.output):
                prior.append((spec.output, self.store.read(self.task_id, spec.output)))
        return prior

    # ------------------------------------------------------------------
    # 主循环里的一个个小决策（都读写 self.run_state）
    # ------------------------------------------------------------------
    def _resume_skip(self, spec: PhaseSpec) -> bool:
        prev = self.run_state.phase_results.get(spec.id)
        if (
            prev
            and prev.status == PhaseStatus.SUCCEEDED
            and self.store.exists(self.task_id, spec.output)
        ):
            self.log(f"⏭️  [{spec.id}] 已完成，跳过（resume）。")
            return True
        return False

    def _deps_satisfied(self, spec: PhaseSpec) -> bool:
        """依赖门：spec.depends_on 里的阶段是否都已 SUCCEEDED。"""
        for dep in spec.depends_on:
            r = self.run_state.phase_results.get(dep)
            if not r or r.status != PhaseStatus.SUCCEEDED:
                return False
        return True

    def _mark_skipped(self, spec: PhaseSpec) -> None:
        pr = self.run_state.phase_results.get(spec.id) or PhaseResult(phase_id=spec.id)
        pr.status = PhaseStatus.SKIPPED
        self.run_state.phase_results[spec.id] = pr
        self._save()
        self.log(f"⤵️  [{spec.id}] 依赖未满足，SKIP。")

    def _commit_phase(self, phase: Phase, outcome: PhaseOutcome) -> None:
        """一个阶段跑完（无论过没过）后，把结果写回 run_state 并落盘。"""
        self.run_state.phase_results[phase.id] = phase.result
        self.run_state.current_phase = phase.id
        self.run_state.updated_at = utc_now_iso()
        self._save()

    def _on_failure(
        self, phase: Phase, outcome: PhaseOutcome, idx: int
    ) -> Optional[int]:
        """未过闸的处理：能重试就回滚并返回目标 idx；重试耗尽则整体 BLOCKED 返回 None。"""
        spec = phase.spec
        target_id, max_retries = _retry_policy(spec)
        target_idx = self.id_to_idx.get(target_id, idx)

        if phase.result.retry_count < max_retries:
            phase.result.retry_count += 1
            self.retry_feedback[target_id] = _make_feedback(
                spec, outcome, phase.result.retry_count, max_retries
            )
            self._rollback(target_idx, idx)
            self._save()
            self.log(
                f"🔁 [{spec.id}] 未过（{outcome.message}）→ 回滚到 '{target_id}' 重试 "
                f"({phase.result.retry_count}/{max_retries})。"
            )
            return target_idx

        self.run_state.status = RunStatus.BLOCKED
        self.run_state.updated_at = utc_now_iso()
        self._save()
        self.log(f"⛔ [{spec.id}] 未过且重试耗尽：{outcome.message}")
        return None

    def _rollback(self, target_idx: int, current_idx: int) -> None:
        """回滚：清掉 target..current 各阶段的产物与状态，让它们下轮从头重跑。

        注意保留各阶段的 ``retry_count``（重试预算是累计的，不能被回滚清零）。
        """
        for i in range(target_idx, current_idx + 1):
            spec = self.order[i]
            self.store.delete(self.task_id, spec.output)
            self.store.delete(self.task_id, f"_prompt_{spec.id}.md")
            pr = self.run_state.phase_results.get(spec.id)
            if pr:
                pr.status = PhaseStatus.PENDING
                pr.finished_at = None
                pr.output_path = None

    # ------------------------------------------------------------------
    # 单阶段（run_one）收尾
    # ------------------------------------------------------------------
    def _finalize_single(
        self, phase_id: str, result: PhaseResult, outcome: PhaseOutcome
    ) -> None:
        """写回本阶段结果并据此更新整次运行状态。"""
        self.run_state.current_phase = phase_id
        self.run_state.phase_results[phase_id] = result
        if outcome.status == PhaseVerdict.PASSED and self._all_done():
            self.run_state.status = RunStatus.SUCCEEDED
            self.run_state.current_phase = None
        elif outcome.status == PhaseVerdict.BLOCKED:
            self.run_state.status = RunStatus.BLOCKED
        self.run_state.updated_at = utc_now_iso()

    def _all_done(self) -> bool:
        """整条流水线是否全部 SUCCEEDED。"""
        return all(
            self.run_state.phase_results.get(p.id)
            and self.run_state.phase_results[p.id].status == PhaseStatus.SUCCEEDED
            for p in self.manifest.phases
        )

    def _resolve_spec(self, phase_id: str) -> PhaseSpec:
        phase_map = self.manifest.phase_map()
        if phase_id not in phase_map:
            raise KeyError(f"未知 phase '{phase_id}'，可选: {self.manifest.phase_ids()}")
        return phase_map[phase_id]

    def _save(self) -> None:
        self.state_store.save(self.run_state)


# ==========================================================================
# 模块级入口：薄薄一层，组个 Engine 让它跑（保持对 CLI / 测试的调用口不变）
# ==========================================================================
def run_pipeline(
    task_id: str,
    *,
    description: Optional[str] = None,
    repo: Optional[str] = None,
    agent_runner=None,
    log: Logger = _noop_logger,
) -> PipelineResult:
    """引擎自走跑完整条流水线：依赖门 → 执行（AI 阶段起 Agent）→ 判卷 → 回滚重试。"""
    engine = Engine(
        task_id, description=description, repo=repo, agent_runner=agent_runner, log=log
    )
    return engine.run_pipeline()


def run_phase(
    task_id: str,
    phase_id: str,
    *,
    description: Optional[str] = None,
    repo: Optional[str] = None,
    preview: bool = False,
    agent_runner=None,
    log: Logger = _noop_logger,
) -> PhaseOutcome:
    """只推进单个 phase：prepare →（AI 阶段起 Agent）→ gate。无回滚，卡住即返回。"""
    engine = Engine(
        task_id, description=description, repo=repo, agent_runner=agent_runner, log=log
    )
    return engine.run_one(phase_id, preview=preview)


# ==========================================================================
# 纯函数小工具（无状态，Engine / Phase 都用）
# ==========================================================================
def _retry_policy(spec: PhaseSpec) -> tuple[str, int]:
    """从 spec.retry_on_fail 取 (回滚目标阶段, 最大重试次数)；未配置则不重试。"""
    cfg = spec.retry_on_fail or {}
    target = cfg.get("target", spec.id)
    max_retries = int(cfg.get("max_retries", 0))
    return target, max_retries


def _make_feedback(
    spec: PhaseSpec, outcome: PhaseOutcome, attempt: int, max_retries: int
) -> str:
    """把"为什么失败"整理成一段反馈，注入回滚目标阶段的下一轮 prompt（当反例）。"""
    lines = [
        f"上一轮在阶段 '{spec.id}' 未通过（第 {attempt}/{max_retries} 次重试）。原因：",
        f"- {outcome.message}",
    ]
    if outcome.missing_sections:
        lines.append(f"- 产物缺少必需章节：{outcome.missing_sections}")
    if outcome.confidence is not None and spec.confidence_min is not None:
        lines.append(
            f"- 置信度 {outcome.confidence} 低于阈值 {spec.confidence_min}，请换思路重新定位根因。"
        )
    lines.append("请针对以上问题调整，不要重复上一轮的错误假设。")
    return "\n".join(lines)
