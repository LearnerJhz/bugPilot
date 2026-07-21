"""极简的、仅依赖标准库的测试运行器。

每个模块都把自己的 ``test_*`` 函数和被测代码放在同一个文件里，
并在文件末尾这样收尾::

    if __name__ == "__main__":
        import sys
        from testkit import run_module_tests
        exit_code = run_module_tests(globals())
        sys.exit(exit_code)

这样就能用 ``python3 -m core.models`` 之类的命令对任意模块做自测。
sys.exit(n) 的作用是：让整个 Python 进程退出，并把 n 作为"退出码"交给操作系统/shell。

python3 -m core.models && echo "部署下一步"
&& 的意思是"前面成功（退出码 0）才执行后面"。如果你不写 sys.exit(exit_code)：
Python 正常跑完默认退出码永远是 0
"""

import traceback
from typing import Any


def run_module_tests(namespace: dict[str, Any]) -> int:
    name = namespace.get("__name__", "?")
    # 从调用方的 globals() 里挑出所有名为 ``test_*`` 的可调用对象。
    tests = sorted(
        (k, v)
        for k, v in namespace.items()
        if k.startswith("test_") and callable(v)
    )
    failures = 0
    for k, fn in tests:
        try:
            fn()
            print(f"  ok    {k}")
        except Exception:
            # 抛出异常即表示该测试失败；继续跑后面的测试。
            failures += 1
            print(f"  FAIL  {k}")
            traceback.print_exc()
    passed = len(tests) - failures
    print(f"[{name}] {passed}/{len(tests)} passed")
    # 退出码：全部通过返回 0，否则返回 1（便于 CI 检测失败）。
    return 1 if failures else 0
