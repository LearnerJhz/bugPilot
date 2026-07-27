"""让 ``python3 -m cli`` 可直接执行。

把真正的逻辑留在 ``cli.main.main``（便于被 import 和测试），这里只负责把它的
返回值当作进程退出码抛给操作系统/shell —— 这样 ``python3 -m cli run ... && echo 下一步``
之类的链式命令才能按退出码正确判断成败。
"""

import sys

from cli.main import main

if __name__ == "__main__":
    sys.exit(main())
