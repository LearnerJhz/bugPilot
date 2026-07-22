"""执行器注册表：将 manifest 中的 ``executor`` 字符串映射到对应的类。

这正是引擎满足开闭原则的关键：新增一个阶段（phase）只需编写一个新的执行器，
并用 ``@register("name")`` 装饰它——orchestrator 永远不需要修改。它只按名字查找
执行器，对任何具体阶段一无所知。
"""

from typing import Callable

from core.ports import PhaseExecutor

_REGISTRY: dict[str, type] = {}

# 返回一个装饰器，用法是 @register("intake") 放在某个执行器类上面。
# 返回类型 Callable[[type], type]:表示「接收一个类、返回一个类」的可调用对象,也就是里面的 decorator
def register(name: str) -> Callable[[type], type]:
    """类装饰器：以 ``name`` 为键注册一个执行器。"""

    # 装饰器只是一个语法糖
    # @register("intake")
    #   class IntakeExecutor:
    # 等价于 
    # class IntakeExecutor:
    # IntakeExecutor = register("intake")(IntakeExecutor)
    # 这里的name是闭包捕获
    def decorator(cls: type) -> type:
        if name in _REGISTRY:
            raise ValueError(f"executor '{name}' already registered")
        _REGISTRY[name] = cls
        return cls

    return decorator

# 查找并实例化
def create_executor(name: str) -> PhaseExecutor:
    """实例化以 ``name`` 为键注册的执行器。"""
    if name not in _REGISTRY:
        raise KeyError(
            f"no executor registered as '{name}'. "
            f"available: {sorted(_REGISTRY)}"
        )
    # 加上 ‘()’ 是调用构造函数
    return _REGISTRY[name]()

# 返回当前注册表里所有的名字
def available() -> list[str]:
    return sorted(_REGISTRY)


def clear() -> None:
    """重置注册表（供测试使用）。"""
    _REGISTRY.clear()


# --------------------------------------------------------------------------
# 内联测试（运行方式：python3 -m core.registry）
#
# 注册表只是一张 名字 -> 类 的字典，用普通哑类就能验证，不依赖 core.ports。
# 每个测试开头先 clear() 一下，保持干净。
# --------------------------------------------------------------------------
class _Dummy:
    pass


def test_register_and_create():
    clear()
    register("dummy")(_Dummy)
    assert available() == ["dummy"]
    assert isinstance(create_executor("dummy"), _Dummy)


def test_duplicate_registration_rejected():
    clear()
    register("dup")(_Dummy)
    try:
        register("dup")(_Dummy)
        assert False, "重复注册应抛 ValueError"
    except ValueError:
        pass


def test_unknown_executor_raises():
    clear()
    try:
        create_executor("nope")
        assert False, "未注册的名字应抛 KeyError"
    except KeyError:
        pass


if __name__ == "__main__":
    import sys

    from testkit import run_module_tests

    exit_code = run_module_tests(globals())
    sys.exit(exit_code)
