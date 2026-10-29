# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

本地部署的 QQ 官方机器人 + 大模型对话 Agent。两个独立进程：Flutter 桌面端（`lib/`，GetX）和 Python 核心服务（`agent-core/`，FastAPI + SQLite）。界面文字、代码注释、提交信息都用中文；提交信息用 `feat:` / `feat(desktop):` / `feat(agent-core):` / `chore:` / `docs:` 前缀。

## 常用命令

```bash
# 核心服务（在 agent-core/ 下；uv 会自动装 Python 3.12）
uv sync
uv run agent-core --data-dir ./.data          # 单独运行；--port 固定控制接口端口，--log-level DEBUG

# 桌面端（在仓库根目录）；开发模式下会找到 agent-core/.venv 自动拉起核心服务
flutter run -d windows                        # macOS / Linux 换成 macos / linux
flutter analyze lib                           # 改完 Dart 代码后跑一遍，要求 No issues found

# 数据库迁移（在 agent-core/ 下；alembic.ini 指向 ./.data/agent-core.db，先用上面的命令建好库）
uv run alembic revision --autogenerate -m "说明"   # 核心服务启动时会自动升级到最新版本

# 打包便携版（每个平台在自己的系统上；Nuitka 首次编译约 15 分钟，大头是 anthropic SDK，之后有缓存）
uv run --project agent-core --group build python packaging/package.py   # 可加 --skip-core / --skip-flutter
uv run --no-project --with pillow python packaging/make_icons.py        # 从 assets/branding/ 的主图重新生成各平台图标
```

仓库里没有测试文件，也不要新增。验证改动的方式是实际运行：用临时数据目录启动核心服务，通过控制接口操作；需要 QQ 和 Claude 时用本地模拟服务，并设置这些环境变量：

- `QQ_LLM_BOT_QQ_API_BASE`：QQ OpenAPI 和 access_token 接口的地址（`plugins/channel_qq/constants.py`）
- `ANTHROPIC_BASE_URL`：Claude 接口地址（anthropic SDK 自己读取）
- `QQ_LLM_BOT_SECRETS=file`：密钥存文件，不写进系统密钥库
- `QQ_LLM_BOT_DATA_DIR`：数据目录（桌面端和核心服务都认）；`QQ_LLM_BOT_CORE`：桌面端要拉起的核心服务可执行文件

控制接口的端口和令牌在数据目录的 `control.json` 里，请求要带 `Authorization: Bearer <token>`；`POST /shutdown` 可以平稳停止核心服务。

## 架构

### 两个进程怎么配合

- 桌面端不直接读写数据库，所有操作都走核心服务在 `127.0.0.1` 上的控制接口（`agent_core/control/`），实时事件走 `WS /events`。客户端是手写的 `lib/core/api/core_api_client.dart`，接口改了要同步改它和 `models.dart`。
- 桌面端负责拉起和停止核心服务（`lib/platform/core_process_manager.dart`）。核心服务是普通子进程（不用分离模式，否则 Windows 上 uv / venv 启动器拉起 python 时会弹控制台窗口），启动时带 `--parent-pid`，桌面端退出（包括被强制结束、停止调试）后它会自己平稳停止；带这个参数时日志只写文件。查找顺序：`QQ_LLM_BOT_CORE` → 打包时放在程序旁边的 `agent-core/`（macOS 在 `.app/Contents/Helpers/agent-core`）→ 源码目录里 `uv run agent-core`。
- 数据目录的规则在两边各实现了一份，改动时要保持一致：`lib/platform/app_paths.dart` 和 `agent_core/infra/paths.py`。顺序是：环境变量或 `--data-dir` → 程序旁边的 `qq-llm-bot-data` 文件夹（便携模式）→ 系统的用户数据目录。

### 核心服务的分层

- `core/`：统一的消息模型、端口接口（`core/ports/`：渠道、传输、模型提供商、工具等）、事件总线，以及所有配置项和默认值（`core/settings.py` 的 `DEFAULTS`）。不依赖其它层。
- `plugins/`：`channel_qq`（QQ 接入，WebSocket 传输；Webhook 只是空壳）和 `provider_claude`。提供商插件要实现 `describe()`，返回模型目录（各模型支持的 effort、默认 effort、输出上限、价格），控制接口 `GET /providers` 原样给桌面端画模型配置界面，保存模型配置时也按它校验；Claude 的目录在 `plugins/provider_claude/catalog.py`，以官方文档为准。插件通过 `pyproject.toml` 的 entry points 注册；Nuitka 打包后读不到这些元数据，所以 `app/registry.py` 的 `BUILTIN_PLUGINS` 里还要再登记一份。新增插件两处都要加。
- `pipeline/`：消息流水线。`ConnectionManager` 在同一个事务里写入 `inbox_events` 并更新网关的 `last_seq`（按事件 ID 去重）→ `Dispatcher` 加租约领取，同一会话串行处理 → `AgentEngine` 生成回复 → 回复和要发的消息在同一个事务里写进 `outbox` → `OutboxWorker` 发送，按 `msg_seq` 去重，按错误码分类重试。启动时释放遗留的租约，崩溃重启后接着处理。`MaintenanceWorker` 每天清理超期数据、备份数据库。
- `agent/engine.py`：判断是否触发回复、处理指令（`/清空`、`/帮助`）、限频和每日预算、组装上下文、调用提供商。`agent/media.py` 负责下载图片交给模型。
- `infra/`：SQLite（WAL）的仓储类都在 `infra/db/repos.py`，迁移在 `infra/db/migrations/`；密钥存储 `infra/secrets.py`。
- `control/`：FastAPI 控制接口。

### 对话历史

- 两份历史：`messages` 是所有提供商共用的中立历史；`provider_messages` 是每个提供商的原始消息，只追加、不改写（Claude 的回复原样保存整个 `response.content`，包括 thinking 块）。换了提供商时，从中立历史重建上下文。
- Claude 发请求时去掉历史里的 thinking 块：图片换成文字标记、改了系统提示词以后，思考块前面的内容和生成时不一样，Haiku 5.5 / Sonnet 5.5 / Opus 5.5 / Fable 5.1 发回这样的思考块会返回 400。
- 历史按 `history_epoch` 分段：`/清空` 或超过 `history.max_turns` 时 epoch 加 1，旧记录保留，但不再进上下文。
- 上一轮之后没触发回复的群消息（比如先发图、再 @ 机器人）会拼进本轮的 user 消息。图片只随当前这一轮发给模型，存进历史时换成「[图片1]」这样的文字标记。
- 指令结果、限频和预算提醒这类系统回复，`turn_id` 为空，不进模型上下文。

### 分层配置

配置项按「会话 → 账号 → 全局 → `DEFAULTS`」的顺序取值（`SettingsRepo.resolve`）。新增配置项时：先加进 `DEFAULTS`（不在里面的键会被控制接口拒绝），再在配置页（`lib/features/config/config_page.dart`）和单独配置对话框（`lib/core/ui/scoped_settings_dialog.dart` 的 `_specs` 和按键列表）里加界面。例外：`budget.daily_usd` 不继承，账号预算和全局预算各算各的，任何一个用完都暂停回复。

### 桌面端

- 每个页面是 `lib/features/<名字>/` 下的一个功能模块（feature / page / controller / repository），在 `lib/app/feature_registry.dart` 里登记，顺序就是左侧菜单的顺序。repository 通过 `ConnectionStore.api` 调用控制接口。
- Windows 上隐藏系统标题栏，由 `lib/app/app_title_bar.dart` 自绘（`WindowService.customTitleBar`）；macOS / Linux 仍用系统标题栏。关闭按钮默认缩到托盘、核心服务继续运行，设置页可改成直接退出；只跟桌面端有关的偏好存在数据目录的 `desktop.json`（`lib/platform/desktop_prefs.dart`）。

## 约束

- AppSecret 和 API Key 只存系统密钥库（没有密钥库时退回权限 0600 的文件），数据库里只存键名，不能写进数据库、日志和导出文件。
- Claude 默认模型是 `claude-haiku-5-5`，`effort` 设为 `low`。system prompt 保持固定，以便命中提示缓存，动态内容只放在 messages 里。
