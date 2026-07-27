# 阶段任务：fix

你是一个被引擎调度的自走 Agent。请**独立完成本阶段的全部工作**：读代码、
必要时改代码、跑命令定位问题，最后把结论写成产物文件。不要向人提问、不要中途停下。

## 本次任务
登录页空密码崩溃

## 工作区（在这里读/改代码）
- 代码根目录: /private/tmp/anyrepo
- 工作分支: bugpilot/demo

## 前置阶段产物（上下文，供你参考）
### 01-intake.md
# Intake — demo

## Summary
登录页空密码崩溃

## Inputs
- Task ID: demo
- Branch: bugpilot/demo
- Request: 登录页空密码崩溃
- Workspace: /private/tmp/anyrepo
- Intake at: 2026-07-24T03:02:54+00:00

### 02-analyze.md
# analyze — fake-agent stub（自测桩，非真实结论）

## Root Cause
(fake stub for ## Root Cause)

## Confidence
0.9

## Plan
(fake stub for ## Plan)

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
1. 把本阶段结论写入产物文件（覆盖写）：`/Users/bytedance/person/githubBug/bugPilot/tasks/demo/03-fix.md`
2. 该产物**必须包含以下二级标题**（原文，勿翻译/改写，引擎按字符串校验）：
   - `## Changes`
   - `## Self Check`
3. 只改与本 bug 相关的代码，改动落在上面的工作区里。
4. 全程自主完成，不要询问、不要等待确认。

完成后请确认产物文件 `/Users/bytedance/person/githubBug/bugPilot/tasks/demo/03-fix.md` 已按要求写好。