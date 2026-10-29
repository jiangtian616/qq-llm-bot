"""入口：单实例锁 → 迁移数据库 → 装配插件 → 启动本机控制接口（uvicorn）。

退出码：0 正常退出；2 参数或配置错误；3 已有实例在运行。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import os
import socket
import sys
from typing import Any

import uvicorn

from agent_core import API_VERSION, __version__
from agent_core.app.config import AppConfig, load_config
from agent_core.agent.engine import AgentEngine
from agent_core.agent.prompt import DEFAULT_SYSTEM_PROMPT
from agent_core.agent.providers import ProviderPool
from agent_core.app.registry import Assembly, PluginNotFoundError, PluginRegistry
from agent_core.control.app import create_app
from agent_core.control.auth import generate_token
from agent_core.control.state import ControlState
from agent_core.core.bus import EventBus
from agent_core.core.lifecycle import ServiceLifecycle
from agent_core.core.ports import LLMProvider, ProviderContext
from agent_core.infra.db.database import Database
from agent_core.infra.db.migrate import prune_backups, upgrade_to_head
from agent_core.infra.fileperm import write_private_file
from agent_core.infra.logging_setup import setup_logging
from agent_core.infra.parent_watch import watch_parent
from agent_core.infra.paths import DataDirError, DataPaths
from agent_core.infra.secrets import SecretStore
from agent_core.infra.single_instance import AlreadyRunningError, SingleInstanceLock
from agent_core.pipeline.connections import ConnectionManager
from agent_core.pipeline.dispatcher import Dispatcher
from agent_core.pipeline.maintenance import MaintenanceWorker
from agent_core.pipeline.outbox import OutboxWorker

log = logging.getLogger("agent_core")

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_ALREADY_RUNNING = 3

GRACEFUL_TIMEOUT_S = 5


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="agent-core", description="qq-llm-bot 核心服务")
    p.add_argument("--data-dir", help="数据目录，默认用系统的用户数据目录")
    p.add_argument("--port", type=int, help="控制接口端口，默认随机（写在 control.json 里）")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    p.add_argument("--parent-pid", type=int, help="桌面端的进程号：它退出后核心服务跟着停止（由桌面端传入）")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__} (api {API_VERSION})")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        paths = DataPaths.resolve(args.data_dir)
    except DataDirError as e:
        # 日志还没地方写，只能输出到控制台。
        print(f"agent-core: {e}", file=sys.stderr)
        return EXIT_CONFIG
    # 桌面端拉起时 stderr 是管道，日志只写文件：没人读的时候（比如桌面端热重启后）管道写满会卡住进程。
    setup_logging(paths.logs_dir, getattr(logging, args.log_level), console=args.parent_pid is None)

    lock = SingleInstanceLock(paths.lock_file)
    try:
        lock.acquire()
    except AlreadyRunningError as e:
        log.error("%s", e)
        return EXIT_ALREADY_RUNNING

    try:
        log.info("agent-core %s 启动，数据目录：%s（%s）", __version__, paths.root, paths.source)
        try:
            config = load_config(paths.config_file)
            if args.port is not None:
                config.control.port = args.port
            registry = PluginRegistry()
            assembly = registry.assemble(config.plugins)
        except (PluginNotFoundError, TypeError, ValueError) as e:
            log.error("配置错误：%s", e)
            return EXIT_CONFIG

        revision = upgrade_to_head(paths.database, paths.backups_dir)
        prune_backups(paths.backups_dir)
        secrets = SecretStore(paths.secrets_file)
        log.info("密钥存储：%s（%s）", secrets.kind, secrets.backend_name)

        asyncio.run(_serve(paths, config, secrets, registry, assembly, revision, args.parent_pid))
        return EXIT_OK
    finally:
        lock.release()
        log.info("agent-core 已退出")


async def _serve(
    paths: DataPaths,
    config: AppConfig,
    secrets: SecretStore,
    registry: PluginRegistry,
    assembly: Assembly,
    db_revision: str | None,
    parent_pid: int | None,
) -> None:
    bus = EventBus()
    lifecycle = ServiceLifecycle()
    db = Database(paths.database)
    await _ensure_default_profile(db, config.plugins.provider)
    released = await db.inbox.release_leases() + await db.outbox.release_leases()
    if released:
        log.info("接着处理上次没做完的 %s 条", released)

    def make_provider(profile: Any) -> LLMProvider:
        cls = registry.load("provider", profile.provider)
        return cls(
            ProviderContext(
                secrets=secrets,
                secret_ref=profile.secret_ref,
                model=profile.model,
                system_prompt=profile.system_prompt,
                params=json.loads(profile.params_json or "{}"),
            )
        )

    providers = ProviderPool(make_provider)
    dispatcher: Dispatcher | None = None
    connections = ConnectionManager(
        db=db,
        bus=bus,
        secrets=secrets,
        channel_cls=assembly.channel,
        transport_cls=assembly.transport,
        # 收件箱有新活时唤醒调度器；调度器在下面创建，这里调用时才取。
        on_inbox=lambda: dispatcher.wake() if dispatcher else None,
    )
    outbox = OutboxWorker(db=db, connections=connections, bus=bus)
    engine = AgentEngine(db=db, providers=providers, bus=bus, on_outbox=outbox.wake)
    dispatcher = Dispatcher(db=db, connections=connections, engine=engine, bus=bus)
    maintenance = MaintenanceWorker(
        db=db, bus=bus, database_path=paths.database, backups_dir=paths.backups_dir
    )

    # 停止钩子按注册的反序执行：先断开 QQ 连接（不再接新事件、保存 seq），再等在途回复入库、发完。
    lifecycle.on_stop("database", db.close)
    lifecycle.on_stop("providers", providers.close_all)
    lifecycle.on_stop("maintenance", maintenance.stop)
    lifecycle.on_stop("outbox", outbox.stop)
    lifecycle.on_stop("dispatcher", lambda: dispatcher.stop(timeout=8))
    lifecycle.on_stop("connections", connections.stop_all)
    outbox.start()
    dispatcher.start()
    maintenance.start()

    state = ControlState(
        token=generate_token(),
        bus=bus,
        lifecycle=lifecycle,
        data_dir=paths.root,
        secret_backend=secrets.kind,
        plugins=assembly.describe(),
        db_revision=db_revision,
        db=db,
        secrets=secrets,
        connections=connections,
        providers=providers,
        wake_inbox=dispatcher.wake,
        wake_outbox=outbox.wake,
        maintenance=maintenance,
        platform=config.plugins.channel,
        transport=config.plugins.transport,
    )

    # 自己绑定套接字，才能在端口为 0 时拿到实际端口。只监听 127.0.0.1。
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", config.control.port))
    port = sock.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(
                state,
                providers={n: registry.load("provider", n).describe() for n in registry.available("provider")},
            ),
            log_config=None,
            access_log=False,
            timeout_graceful_shutdown=GRACEFUL_TIMEOUT_S,
        )
    )

    async def stop_on_request() -> None:
        await lifecycle.shutdown_requested.wait()
        log.info("收到停止请求，正在平稳停止")
        await lifecycle.run_stop_hooks()
        server.should_exit = True

    watcher = asyncio.create_task(stop_on_request())
    if parent_pid is not None:
        loop = asyncio.get_running_loop()

        def on_parent_exit() -> None:
            log.info("桌面端（pid %s）已退出", parent_pid)
            loop.call_soon_threadsafe(lifecycle.request_shutdown)

        watch_parent(parent_pid, on_parent_exit)
    write_private_file(
        paths.control_file,
        json.dumps(
            {
                "pid": os.getpid(),
                "port": port,
                "token": state.token,
                "version": __version__,
                "api_version": API_VERSION,
            }
        ),
    )
    log.info("控制接口：http://127.0.0.1:%s", port)
    auto_connect = asyncio.create_task(connections.start_auto_connect())
    try:
        await server.serve(sockets=[sock])
    finally:
        watcher.cancel()
        auto_connect.cancel()
        # Ctrl+C / SIGTERM 走这里；/shutdown 已经执行过的话不会重复执行。
        await lifecycle.run_stop_hooks()
        with contextlib.suppress(FileNotFoundError):
            paths.control_file.unlink()


async def _ensure_default_profile(db: Database, provider: str) -> None:
    """第一次启动时建一个默认模型配置；API Key 由用户在桌面端填写。"""
    if await db.profiles.list():
        return
    await db.profiles.create(
        name="默认",
        provider=provider,
        model="claude-haiku-5-5",
        system_prompt=DEFAULT_SYSTEM_PROMPT,
        params={"effort": "low", "max_tokens": 1500},
    )


if __name__ == "__main__":
    sys.exit(main())
