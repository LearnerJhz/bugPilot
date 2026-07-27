# 阶段任务：fix

你是一个被引擎调度的自走 Agent。请**独立完成本阶段的全部工作**：读代码、
必要时改代码、跑命令定位问题，最后把结论写成产物文件。不要向人提问、不要中途停下。

## 本次任务
test_algo.py 失败：bubble_sort 结果是降序，应为升序。请定位 buggy.py 里的根因并修复，使 test_algo.py 全部用例通过。

## 工作区（在这里读/改代码）
- 代码根目录: /Users/bytedance/person/githubBug/bugpilot-demo-target

## 前置阶段产物（上下文，供你参考）
### 01-intake.md
# Intake — demo_sort

## Summary
test_algo.py 失败：bubble_sort 结果是降序，应为升序。请定位 buggy.py 里的根因并修复，使 test_algo.py 全部用例通过。

## Inputs
- Task ID: demo_sort
- Request: test_algo.py 失败：bubble_sort 结果是降序，应为升序。请定位 buggy.py 里的根因并修复，使 test_algo.py 全部用例通过。
- Workspace: /Users/bytedance/person/githubBug/bugpilot-demo-target
- Intake at: 2026-07-27T02:13:41+00:00

### 02-analyze.md
# Analyze — demo_sort

## Root Cause

`buggy.py:14` 中比较运算符写反：升序冒泡排序应在 **前 > 后** 时交换相邻元素，但代码写成了 `if arr[j] < arr[j + 1]`（前 < 后时交换），导致每轮冒泡把较小元素上浮到末尾而非较大元素，最终输出降序序列。

**关键证据**：
- `buggy.py:14`：`if arr[j] < arr[j + 1]:` —— 条件应为 `>`。
- 运行 `python3 test_algo.py` 的输出：
  ```
  FAIL: bubble_sort([3, 1, 2]) = [3, 2, 1], expected [1, 2, 3]
  FAIL: bubble_sort([5, 4, 3, 2, 1]) = [5, 4, 3, 2, 1], expected [1, 2, 3, 4, 5]
  FAIL: bubble_sort([2, 2, 1]) = [2, 2, 1], expected [1, 2, 2]
  ```
  空列表和单元素用例通过是因为循环体不执行，不受影响。

## Confidence

1.0

原因：根因极其明确——单行比较符号错误，代码注释也直接指出了该行是 bug 所在。测试输出与根因推理完全吻合。

## Plan

1. **改 `buggy.py:14`**：将 `if arr[j] < arr[j + 1]:` 改为 `if arr[j] > arr[j + 1]:`
   - 这是唯一需要改动的位置。
   - 升序冒泡排序的标准语义是：当左边元素大于右边元素时交换，让较大元素逐步"冒泡"到尾部。改后即满足该语义。
2. **验证**：运行 `python3 test_algo.py`，确认全部 5 个用例通过、退出码为 0。

## 阶段提示词
你正处在 **代码修复（fix）** 阶段。请依据上一阶段 `02-analyze.md` 里的根因与修复
方案，在工作区里**真正把代码改好**。

请你自主完成：
1. 按 analyze 的 `## Plan` 修改工作区源码，改动保持最小、聚焦本 bug。
2. 做基本自查：改动是否覆盖根因、是否引入明显语法/逻辑错误、是否遗漏边界情况。
3. 不要顺手做无关重构；不要改与本 bug 无关的文件。

产物必须包含这些二级标题：

- `## Changes`：分点列出你实际改了哪些文件、每处改了什么、为什么。
- `## Self Check`：你的自查结论（覆盖了根因吗？有无明显风险？为何认为修复成立）。

代码改动请直接落在工作区文件里（后续 apply 阶段会用 `git diff` 记账、verify 阶段会跑验证）。

## 硬约束（务必遵守）
1. 把本阶段结论写入产物文件（覆盖写）：`/Users/bytedance/person/githubBug/bugPilot/tasks/demo_sort/03-fix.md`
2. 该产物**必须包含以下二级标题**（原文，勿翻译/改写，引擎按字符串校验）：
   - `## Changes`
   - `## Self Check`
3. 只改与本 bug 相关的代码，改动落在上面的工作区里。
4. 全程自主完成，不要询问、不要等待确认。

完成后请确认产物文件 `/Users/bytedance/person/githubBug/bugPilot/tasks/demo_sort/03-fix.md` 已按要求写好。