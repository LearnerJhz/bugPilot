# Claude CLI 测试命令记录

## 冒烟测试命令

```bash
printf '只回复一个词：PONG' | claude -p --model deepseek-v4-flash --permission-mode bypassPermissions --max-turns 1 2>&1; echo "---exit=$?---"
```

## 这条命令是干什么的

这是一个**冒烟测试（smoke test）**：确认端点/token/模型在 headless 下真能通，发一句「只回复一个词：PONG」给 `claude` CLI，看它能否用 `deepseek-v4-flash` 模型正常返回，并通过退出码判断调用是否成功。

- 如果输出 `PONG` 且 `---exit=0---` → CLI、模型、鉴权配置都正常。
- 如果输出报错 + 非 0 退出码 → 可以据此排查（模型名错误、鉴权失败、网络问题等）。

## 逐部分拆解

```bash
printf '只回复一个词：PONG' | claude -p --model deepseek-v4-flash --permission-mode bypassPermissions --max-turns 1 2>&1; echo "---exit=$?---"
```

### 1. `printf '只回复一个词：PONG'`

输出字符串 `只回复一个词：PONG`（不带换行），作为后面命令的输入。这相当于给 AI 发的提示词（prompt）。

### 2. `|`（管道）

把 `printf` 的输出「喂」给右边的 `claude` 命令，作为它的标准输入。

### 3. `claude -p ...`

调用 `claude` 这个 CLI 工具，各参数含义：

| 参数 | 含义 |
|------|------|
| `-p` | `--print` 模式，非交互式：读取输入 → 输出结果 → 退出（适合脚本调用） |
| `--model deepseek-v4-flash` | 指定使用的模型（这里是 `deepseek-v4-flash`，说明该 CLI 被配置成可调用非 Anthropic 的模型） |
| `--permission-mode bypassPermissions` | 权限模式设为「绕过所有权限确认」，不弹窗询问，直接执行 |
| `--max-turns 1` | 最多只进行 1 轮对话（防止多轮 agent 循环） |

### 4. `2>&1`

把标准错误（stderr）重定向合并到标准输出（stdout），这样报错信息也能一起被看到（不会被吞掉）。

### 5. `; echo "---exit=$?---"`

分号表示前面命令结束后接着执行。`$?` 是上一条命令的退出码，`0` 表示成功，非 `0` 表示失败。所以最后会打印类似 `---exit=0---`。
