# Intake — T-1

## Summary
一句话:登录页在空密码提交时崩溃

## Inputs
- Issue URL: https://github.com/foo/bar/issues/42
- Repo / 模块: bar / auth
- 复现步骤: 打开登录页 → 密码留空 → 点提交 → 白屏
- 期望 vs 实际: 期望提示"密码必填";实际抛 NPE 崩溃
- 环境: v2.3.1, commit abc123
- 报错/栈: NullPointerException at LoginController.java:88
- 验收标准: 空密码时给出校验提示,不崩溃