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
- `DEEPSEEK_BASE_URL`：DeepSeek 接口地址（`plugins/provider_deepseek/provider.py`）
- `QQ_LLM_BOT_SECRETS=file`：密钥存文件，不写进系统密钥库
- `QQ_LLM_BOT_ALLOW_PRIVATE_FETCH=1`：读网页、下载图片时允许访问本机和内网地址（默认只访问公网），让它们指向本地模拟服务；只用于开发调试
- `QQ_LLM_BOT_DATA_DIR`：数据目录（桌面端和核心服务都认）；`QQ_LLM_BOT_CORE`：桌面端要拉起的核心服务可执行文件

控制接口的端口和令牌在数据目录的 `control.json` 里，请求要带 `Authorization: Bearer <token>`；`POST /shutdown` 可以平稳停止核心服务。

## 架构

### 两个进程怎么配合

- 桌面端不直接读写数据库，所有操作都走核心服务在 `127.0.0.1` 上的控制接口（`agent_core/control/`），实时事件走 `WS /events`。客户端是手写的 `lib/core/api/core_api_client.dart`，接口改了要同步改它和 `models.dart`。
- 桌面端负责拉起和停止核心服务（`lib/platform/core_process_manager.dart`）。核心服务是普通子进程（不用分离模式，否则 Windows 上 uv / venv 启动器拉起 python 时会弹控制台窗口），启动时带 `--parent-pid`，桌面端退出（包括被强制结束、停止调试）后它会自己平稳停止；带这个参数时日志只写文件。查找顺序：`QQ_LLM_BOT_CORE` → 打包时放在程序旁边的 `agent-core/`（macOS 在 `.app/Contents/Helpers/agent-core`）→ 源码目录里 `uv run agent-core`。
- 数据目录的规则在两边各实现了一份，改动时要保持一致：`lib/platform/app_paths.dart` 和 `agent_core/infra/paths.py`。顺序是：环境变量或 `--data-dir` → 程序旁边的 `qq-llm-bot-data` 文件夹（便携模式，安装包不再自带）→ 设置页选的自定义目录（指针文件是系统用户数据目录里的 `data-location.json`，目录不存在时报错、不退回默认目录）→ 系统的用户数据目录。更换数据目录在桌面端完成（`lib/platform/data_dir_mover.dart`）：停下核心服务、复制数据、写指针，再重新启动桌面端。

### 核心服务的分层

- `core/`：统一的消息模型、端口接口（`core/ports/`：渠道、传输、模型提供商、工具等）、事件总线，以及所有配置项和默认值（`core/settings.py` 的 `DEFAULTS`）。不依赖其它层。
- `plugins/`：`channel_qq`（QQ 接入，WebSocket 传输；Webhook 只是空壳）、`provider_claude` 和 `provider_deepseek`（Chat Completions 接口，直接用 aiohttp）。提供商插件要实现 `describe()`，返回模型目录（各模型支持的 effort、默认 effort、输出上限、价格、能不能看图），控制接口 `GET /providers` 原样给桌面端画模型配置界面，保存模型配置时也按它校验；Claude 的目录在 `plugins/provider_claude/catalog.py`，以官方文档为准。插件通过 `pyproject.toml` 的 entry points 注册；Nuitka 打包后读不到这些元数据，所以 `app/registry.py` 的 `BUILTIN_PLUGINS` 里还要再登记一份。新增插件两处都要加。
- `pipeline/`：消息流水线。`ConnectionManager` 在同一个事务里写入 `inbox_events` 并更新网关的 `last_seq`（按事件 ID 去重）→ `Dispatcher` 加租约领取，同一会话串行处理 → `AgentEngine` 生成回复 → 回复和要发的消息在同一个事务里写进 `outbox` → `OutboxWorker` 发送，按 `msg_seq` 去重，按错误码分类重试。启动时释放遗留的租约，崩溃重启后接着处理。`MaintenanceWorker` 每天清理超期数据、备份数据库。
- `agent/engine.py`：判断是否触发回复、处理指令（`/清空`、`/帮助`）、限频和每日预算、组装上下文、调用提供商。`agent/media.py` 负责下载图片交给模型。
- 工具（`core/ports/tool.py`）：本地工具（`Tool`，放在 `agent/tools/`，各提供商通用）和服务端工具（`ServerTool`，如 Claude 自带的 `web_search`）。工具循环写在提供商插件的 `generate()` 里：执行本地工具、把结果交回去接着请求，服务端工具返回 `pause_turn` 就原样发回；模型调用 `terminal` 的回复工具（`agent/tools/reply.py`）这一轮就结束，参数作为结构化的回复段（文字、图片、图文卡片）交给引擎，同时补上它的工具结果（每个工具调用都要有结果，压缩请求也拒绝以没结果的工具调用结尾）。回复格式的规则写在回复工具的描述里，不依赖用户的提示词。联网搜索：Claude 用服务端 `web_search`（基础版），DeepSeek 没有服务端搜索，`search_tool()` 给出本地的 `web_search`（Search 接口）；DeepSeek 的工具参数用 strict 模式走 `/beta`，失败就退回普通地址，带 tools 的请求要回传历史里全部的 `reasoning_content`。
- 可信链接：工具循环记下这一轮出现过的链接（用户消息和附件、搜索结果、工具结果）。回复里的图片、卡片只能用这些链接，`validate()` 不过就把原因交回模型重来一次，再不合规由回复编排丢掉那一段。图片链接靠 `page_images`（`agent/tools/page_images.py`）读网页拿到，它也只读出现过的网址。读网页和下载图片都走 `infra/http.py` 的 `fetch_public()`，每一跳都检查目标地址只访问公网（放行代理 TUN 的 fake-ip 网段）。
- `agent/reply.py` 把回复段（或没调用回复工具时的文字，兜底解析 Markdown 图片）排成发件箱的几段：文字里的网址一律去掉（QQ 拒收带网址的消息，40054010）；卡片按 QQ 的要求补全（标题、摘要、https 的网址和封面，没写的从网页的 og 信息取，拿不到封面就退回成文字标题）。QQ 适配器发卡片被拒时改发文字「标题：摘要」，用同一个 `msg_seq`。开关是 `reply.rich_media.enabled`（默认开，提供商支持工具才生效）和 `tools.web_search.*`（默认关）；整轮限时、最多几轮工具调用是 `engine.py` 里的常量。
- 问答过程（`core/trace.py`）：引擎和提供商的工具循环往 `Trace` 里记步骤（组装上下文、每次请求、服务端搜索、本地工具、回复工具、回复编排、历史压缩、出错），这一轮结束（包括失败、超时）时写进 `turn_steps`；发件箱每次发送尝试写进 `outbox_attempts`（卡片被拒改发文字的说明走 `SendResult.note`）。只记本轮新增的内容，`sanitize()` 去掉图片 base64、加密内容和签名。新增提供商的 `generate()` 也要记这些步骤。控制接口 `GET /turns`、`GET /turns/{id}`；桌面端的时间线对话框是 `lib/core/ui/turn_trace_dialog.dart`，收件箱和发件箱详情、会话页的回复气泡、日志页的「问答」都能打开它。
- `infra/`：SQLite（WAL）的仓储类都在 `infra/db/repos.py`，迁移在 `infra/db/migrations/`；密钥存储 `infra/secrets.py`。
- `control/`：FastAPI 控制接口。

### 对话历史

- 两份历史：`messages` 是所有提供商共用的中立历史；`provider_messages` 是每个提供商的原始消息，只追加、不改写（Claude 的回复原样保存整个 `response.content`，包括 thinking 块）。换了提供商时，从中立历史重建上下文。同一段里换走又换回来时，原始历史接着用，中间别的提供商答过的轮次从中立历史补在后面（`ConversationRepo.load_context` 的 `missing`，按 `turn_id` 对齐），和本轮一起追加。
- 原始历史每行记下写入时的工具指纹（`Toolset.fingerprint()`，没有工具时为空串）。`load_context` 只取和本轮指纹一致的行，工具清单变了（开关搜索、改了工具定义）就按换了提供商处理；`seq_no` 在同一提供商、同一段里不分指纹往后排。
- Claude 发请求时去掉之前各轮历史里的 thinking 块（本轮工具循环里的原样带回）：图片换成文字标记、改了系统提示词以后，思考块前面的内容和生成时不一样，Haiku 5.5 / Sonnet 5.5 / Opus 5.5 / Fable 5.1 发回这样的思考块会返回 400。DeepSeek 同样去掉 `reasoning_content`（不带 tools 时官方说不用回传），并把连续的同角色消息合并成一条再发。
- 历史按 `history_epoch` 分段，旧记录保留，但不再进上下文。`/清空` 时 epoch 加 1，从零开始。
- 历史压缩（`AgentEngine._maybe_compact`）：每轮结束后估算下一次请求的 token 数，超过 `history.compact_at_tokens` 或当前这一段超过 `history.max_turns` 轮时，把较早的部分交给提供商的 `compact()` 压成摘要，最近 `history.keep_recent_turns` 轮原文保留，开始新的一段（`ConversationRepo.start_epoch`）。Claude 用官方按需压缩（beta `compact-2026-09-04`），返回的 compaction 块放在新一段最前面原样发回，之后带着它的请求都走 beta 接口；摘要文字另存一条 `direction=summary` 的中立消息。保留的最近几轮在中立历史里也挪到新一段（按回复数从后往前数，不算拒答），换了提供商重建时才看得到；摘要写得比它们晚，读取时按「摘要在前」排序（`load_context` 和会话页都是）。模型不支持压缩时只保留最近几轮。压缩的费用记在触发它的那一轮上。
- 平台推哪些群消息（仅 @ / 全部消息）由 QQ 客户端的群设置决定；推过来的里面回复哪些看 `group.reply_mode`：`mention`（默认，只回 @ 或引用机器人的）、`all`、`auto`（没 @ 的每条先用 `provider.judge()` 单独问一次，标准是 `group.judge.prompt`，模型配置是 `group.judge.profile_id`）。判断的费用和处理过程记在这条消息的问答上：要回复就改成排队（`start_judged_turn`，费用累加），不回复就是 `skipped`（`/turns` 默认不列出）。没回复的群消息（没 @、判断不回、被限频或预算挡下）会拼进下一轮的 user 消息，只带最新的 `group.context_max_messages` 条、`group.context_max_chars` 字，更早的换成一行「省略了更早的 N 条群消息」。图片只随当前这一轮发给模型，存进历史时换成「[图片1]」这样的文字标记。
- 引用消息（message_type 103）：被引用消息的内容和附件在 `msg_elements` 里，存进 `messages.quote_json`，组装上下文时写成「[引用 某人 的消息：…]」，图片和本条的一起交给模型。是谁发的靠引用索引里的发送者标识（`sender_key`：REFIDX 解码后去掉第一个 16 字节块，按实测同一个人在同一个群里不变；机器人发消息返回的 ref_idx 和别人引用它时的不同，只有这部分对得上），入站消息和发件箱各存一份，`ConversationRepo.quote_sender` 查。
- 指令结果、限频和预算提醒这类系统回复，`turn_id` 为空，不进模型上下文。

### 分层配置

配置项按「会话 → 账号 → 全局 → `DEFAULTS`」的顺序取值（`SettingsRepo.resolve`）。新增配置项时：先加进 `DEFAULTS`（不在里面的键会被控制接口拒绝），再在配置页（`lib/features/config/config_page.dart`）和单独配置对话框（`lib/core/ui/scoped_settings_dialog.dart` 的 `_specs` 和按键列表）里加界面。例外：`budget.daily_usd` 不继承，账号预算和全局预算各算各的，任何一个用完都暂停回复。

网络代理是 `network.*` 配置项，只有全局级（`GLOBAL_ONLY_PREFIXES`），界面在设置页。核心服务里取代理用 `SettingsRepo.proxy("providers" | "qq")`；用 aiohttp 发请求的地方一律通过 `infra/http.py` 的 `client_session(proxy)` 建会话，不要直接 `aiohttp.ClientSession()`。Claude SDK 的 `DefaultAsyncHttpxClient` 不看 `trust_env`，总会挂上系统代理，直连和自定义代理要用 `mounts` 覆盖（见 `provider_claude/provider.py` 的 `_http_client`）。模型接口换了代理会重建提供商实例；QQ 发消息下一次就换，WebSocket 连接要重新启动才换。

### 桌面端

- 每个页面是 `lib/features/<名字>/` 下的一个功能模块（feature / page / controller / repository），在 `lib/app/feature_registry.dart` 里登记，顺序就是左侧菜单的顺序。repository 通过 `ConnectionStore.api` 调用控制接口。
- Windows 上隐藏系统标题栏，由 `lib/app/app_title_bar.dart` 自绘（`WindowService.customTitleBar`）；macOS / Linux 仍用系统标题栏。关闭按钮默认缩到托盘、核心服务继续运行，设置页可改成直接退出；只跟桌面端有关的偏好存在数据目录的 `desktop.json`（`lib/platform/desktop_prefs.dart`）。

## 约束

- AppSecret 和 API Key 只存系统密钥库（没有密钥库时退回权限 0600 的文件），数据库里只存键名，不能写进数据库、日志和导出文件。
- Claude 默认模型是 `claude-haiku-5-5`，`effort` 设为 `low`。system prompt 保持固定，以便命中提示缓存，动态内容只放在 messages 里。
