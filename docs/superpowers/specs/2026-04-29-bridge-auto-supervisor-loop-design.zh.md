# Bridge Auto Supervisor Loop 设计

- 日期：2026-04-29
- 状态：用户已确认方向，进入实现
- 范围：在现有 supervised Claude bridge 上增加确定性的后台监督循环

## 目标

supervised bridge 现在已经具备状态、验证、挑战、接受和 needs-human 原语，但仍需要 Codex 手动逐条调用。新能力要把这些原语组合成一个后台自动循环，让用户可以用一条命令启动或接管 bridge，并自动跑到 `accepted` 或 `needs_human`。

## 非目标

- 不在后台 CLI 里直接调用当前 Codex App 的交互式思考能力。
- 不引入新的 LLM provider。
- 不做多 worker crew、自动合并或 UI 改造。
- 不绕过现有 `PolicyGate`、`VerificationRunner` 和 bridge session 记录。

## 命令

新增两个子命令：

```bash
orchestrator claude bridge supervise \
  --repo <repo> \
  --bridge-id <bridge-id> \
  --verification-command "pytest -q" \
  --max-rounds 3
```

`supervise` 接管已存在的 supervised bridge。

```bash
orchestrator claude bridge run \
  --repo <repo> \
  --goal "<goal>" \
  --workspace-mode shared \
  --visual log \
  --verification-command "pytest -q" \
  --max-rounds 3
```

`run` 先创建 supervised bridge，然后自动进入 `supervise`。

## 循环语义

循环由确定性 supervisor policy 驱动：

1. 读取 bridge status。
2. 如果没有 `latest_turn` 且 bridge/session 还在运行，sleep 后继续轮询。
3. 如果 bridge/session 已终结，返回最终状态。
4. 对新的 Claude turn 运行所有 verification commands。
5. 全部验证通过后执行 `accept`。
6. 任一验证失败或 blocked 时执行 `challenge --send`，让 Claude 修复。
7. 达到 `max_rounds` 后执行 `needs-human`，保留最后的失败验证和 challenge 证据。

## 记录和可观测性

loop 返回 JSON，包含：

- `bridge_id`
- `status`
- `rounds_used`
- `accepted`
- `needs_human`
- `events`

每个 event 记录动作：`wait`、`verify`、`challenge`、`accept`、`needs_human` 或 `terminal`。验证和 challenge 仍写入 V2 session。

## 安全

verification command 继续走现有 `VerificationRunner` 和 `PolicyGate`。loop 不直接执行 shell 字符串，也不新增命令执行通道。
