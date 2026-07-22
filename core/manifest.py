"""
Manifest 加载器：声明式流程 -> 带类型的 WorkflowManifest。

将"流程是什么"（manifest.yaml 中的数据）与"如何运行"（orchestrator
中的代码）分离。调整流水线顺序或新增阶段时只需改动 YAML。

使用 PyYAML 解析 manifest。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

from core.models import PhaseSpec

# Path(__file__):__file__ 是当前文件路径的字符串,包成 Path 对象后就能用面向对象的方式操作路径。
# .resolve():把相对路径/软链接解析成绝对路径。
# .parents[1]:parents 是"各级父目录"的序列。parents[0] 是文件所在目录(core/),parents[1] 是再上一层(项目根目录)。
# PROJECT_ROOT / "manifest.yaml":Path 重载了 / 运算符,用来拼路径,等价于 os.path.join。比字符串拼接更安全、跨平台。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = PROJECT_ROOT / "manifest.yaml"


# --------------------------------------------------------------------------
# 工作流Manifest
# --------------------------------------------------------------------------

# @dataclass 是一个装饰器,它会自动帮你生成 __init__、__repr__、__eq__ 等样板方法。
# 只需声明字段,不用手写 def __init__(self, version, project, ...)
@dataclass
class WorkflowManifest:
    version: int
    project: dict[str, Any]
    phases: list[PhaseSpec] = field(default_factory=list)

    # 字典推导式:遍历每个 phase,以 p.id 为键、phase 对象本身为值,构造一个"id → phase"的查找表。
    def phase_map(self) -> dict[str, PhaseSpec]:
        return {p.id: p for p in self.phases}

    # 列表推导式:把所有 phase 的 id 收集成列表
    def phase_ids(self) -> list[str]:
        return [p.id for p in self.phases]

    # 拓扑排序。根据每个 phase 的 depends_on(依赖),算出一个合法的执行顺序(被依赖的先跑)
    # 算法流程(Kahn):
    # ready:入度为 0(没有任何未满足依赖)的 phase 就是"现在可以跑的"。这里用了带条件的列表推导式 [... for ... if ...]。
    # while ready: 空列表在 Python 里是"假",所以这行意思是"只要还有可跑的就继续"。
    # ready.pop(0):取出队首(先进先出,保证与 YAML 中出现顺序一致,文件从上往下读更自然)。
    # 每处理完一个 current,把它的下游 indegree 都减 1;某个下游减到 0 就变成"可跑",加入 ready。
    # 最后 if len(ordered) != len(self.phases):如果排出来的数量对不上,说明有 phase 永远入度不为 0 —— 即存在循环依赖(A 依赖 B、B 依赖 A),报错。
    def topo_order(self) -> list[PhaseSpec]:
        """Kahn topological sort honoring ``depends_on``. Ties break by the
        order phases appear in the manifest, so the file reads top-to-bottom."""
        by_id = self.phase_map()
        indegree = {p.id: 0 for p in self.phases}
        dependents: dict[str, list[str]] = {p.id: [] for p in self.phases}
        for phase in self.phases:
            for dep in phase.depends_on:
                if dep not in by_id:
                    raise ValueError(
                        f"phase '{phase.id}' depends on unknown phase '{dep}'"
                    )
                indegree[phase.id] += 1
                dependents[dep].append(phase.id)

        ready = [p.id for p in self.phases if indegree[p.id] == 0]
        ordered: list[str] = []
        while ready:
            current = ready.pop(0)
            ordered.append(current)
            for nxt in dependents[current]:
                indegree[nxt] -= 1
                if indegree[nxt] == 0:
                    ready.append(nxt)

        if len(ordered) != len(self.phases):
            raise ValueError("dependency cycle detected in manifest phases")
        return [by_id[pid] for pid in ordered]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowManifest":
        phases = []
        for raw in data.get("phases", []):
            phases.append(
                PhaseSpec(
                    id=raw["id"],
                    executor=raw["executor"],
                    output=raw["output"],
                    depends_on=list(raw.get("depends_on", []) or []),
                    required_sections=list(raw.get("required_sections", []) or []),
                    prompt=raw.get("prompt"),
                )
            )
        return cls(
            version=int(data.get("version", 1)),
            project=dict(data.get("project", {}) or {}),
            phases=phases,
        )


def load_manifest(path: Optional[Path] = None) -> WorkflowManifest:
    path = Path(path) if path else DEFAULT_MANIFEST
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return WorkflowManifest.from_dict(data)


# --------------------------------------------------------------------------
# 测试方法 (run: python3 -m core.manifest)
# --------------------------------------------------------------------------
def test_load_real_manifest():
    manifest = load_manifest()
    assert manifest.phase_ids() == ["intake", "analyze", "fix", "apply", "verify"]
    assert manifest.project["name"] == "demo"


def test_topo_order_respects_dependencies():
    manifest = load_manifest()
    order = [p.id for p in manifest.topo_order()]
    assert order.index("intake") < order.index("analyze")
    assert order.index("fix") < order.index("apply")
    assert order.index("apply") < order.index("verify")


def test_cycle_is_detected():
    bad = WorkflowManifest(
        version=1,
        project={},
        phases=[
            PhaseSpec(id="a", executor="x", output="a.md", depends_on=["b"]),
            PhaseSpec(id="b", executor="x", output="b.md", depends_on=["a"]),
        ],
    )
    try:
        bad.topo_order()
    except ValueError:
        return
    raise AssertionError("expected a cycle error")


if __name__ == "__main__":
    import sys

    from testkit import run_module_tests

    exit_code = run_module_tests(globals())
    sys.exit(exit_code)
