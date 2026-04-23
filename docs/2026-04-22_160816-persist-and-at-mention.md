# Plan: 持久化路由与微信 @ 功能

目标
- 1) 将 HermesClaw 的路由/状态持久化，随 Hermes 启动加载，保证进程重启后路由状态保留。
- 2) 在微信端实现 @ 功能，允许 OpenClaw/Hermes Agent 在收到含 @ 的消息时，将消息路由到指定 agent 或广播给全部 agent。

当前上下文 / 假设
- 代码库位于 /opt/data/wechat-route，当前分支 feature/container-hermes-side。
- hermesclaw.py 已实现基本队列与路由 State 类（router_state.json 存储），当前为文件存储。
- 容器内 Hermes 使用 /opt/data 作为配置挂载点；.env 已写入 OpenClaw/Hermes proxy 配置。

方案概览
- 持久化：
  - 改进 State 类，使其在 Hermes 启动时（main 初始化）注册为 Hermes 的持久化后端，或将状态保存/加载迁移到 Hermes 自带的持久化机制（如果 Hermes 主程序提供 API）。
  - 添加原子写入、文件锁（或使用 SQLite）和定期快照以增强可靠性。
  - 提供命令行或 HTTP 管理端点以导出/导入路由状态（用于备份/回滚）。

- @ 功能：
  - 在消息处理链（proc_msg / extract_text）中检测 @ 标记（例如文本中包含 "@agent:NAME" 或标准微信 @ 格式）。
  - 解析目标：支持单个 agent 指定（@agent:alice），按用户名或 id 匹配；支持 @all 广播。
  - 当匹配到目标 agent：将该消息的 route 强制设置为目标 agent（或在 route_message 中为目标构造特殊 msg），并在必要时保持原路由给其他 agent（支持 both 模式）。
  - 更新 router_state.json 模式以记录 @ 操作历史（审计字段）。

详细步骤
1) 持久化改进（4-6 小时）
  - 阅读 Hermes 主程序接口，确认是否存在插件/扩展点可在 Hermes 启动时注册持久化后端。
  - 如果不存在，改用 SQLite 存储：新增 module hermesclaw/store.py，实现 State 类的 SQLite-backed 版本（同样提供 get, set, save 方法）。
  - 在 State.save 内使用事务与原子替换（写入临时文件再重命名或使用 SQLite 的事务）。
  - 在 main() 初始化 State 时根据环境变量选择存储后端（STATE_BACKEND=file|sqlite），并迁移现有 router_state.json 数据到 SQLite（若存在且后端切换）。
  - 添加 hermesclaw CLI 子命令（scripts/manage_state.py）用于导出/导入/备份/恢复路由状态。
  - 写单元测试覆盖 State 的并发读写（使用 tempfile 与 pytest-xdist 可模拟多线程场景）。

2) @ 功能实现（4-8 小时）
  - 在 extract_text 中返回原始文本与解析后的 tokens（@ 标记提取）。
  - 修改 proc_msg：在识别到 @ 指令时，调用新的函数 handle_at_mention(msg, tokens, state)，实现路由选择与消息改写。
  - 支持配置项（ENV）控制 @ 功能启用/禁用及语法（DEFAULT_AT_SYNTAX="@name"）。
  - 增加单元测试：文本解析边界、@all 广播、未匹配用户的回退策略。
  - 集成测试：在容器内模拟 getupdates + sendmessage 循环，验证 @ 指令能把消息投递给目标 agent 的队列（hermes_q / oc_q）。

可能变更文件
- hermesclaw.py (proc_msg, extract_text, route_message) 
- hermesclaw/store.py (新的持久化实现)
- scripts/manage_state.py (导出/导入工具)
- tests/test_state.py, tests/test_at_mention.py
- .env / README 更新文档

验收标准
- 重启 hermesclaw 后 router_state 保持不变（路由设置持久化）
- 通过集成测试：发送含 @ 的消息，指定 agent 能收到消息（或 @all 被广播）
- 提供导出/导入路由状态的工具与说明文档

风险与权衡
- 使用 SQLite 增加依赖与二进制兼容性（容器需包含 SQLite 支持的 Python）。
- 文件锁与并发写入需谨慎测试，避免在高频更新场景下出现竞争。

开放问题
- Hermes 是否已有插件/持久化 API（需要确认以复用而非重实现）。
- Agent 的标识是否应使用 human-readable 名称还是内部 UID？建议内部 UID 为主，另提供映射表。
