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

import uvicorn

from agent_core import API_VERSION, __version__
from agent_core.app.config import AppConfig, load_config
from agent_core.app.registry import PluginNotFoundError, PluginRegistry
from agent_core.control.app import create_app
from agent_core.control.auth import generate_token
from agent_core.control.state import ControlState
from agent_core.core.bus import EventBus
from agent_core.core.lifecycle import ServiceLifecycle
from agent_core.infra.db.migrate import prune_backups, upgrade_to_head
from agent_core.infra.fileperm import write_private_file
from agent_core.infra.logging_setup import setup_logging
from agent_core.infra.paths import DataPaths
from agent_core.infra.secrets import SecretStore
from agent_core.infra.single_instance import AlreadyRunningError, SingleInstanceLock

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
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__} (api {API_VERSION})")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    paths = DataPaths.resolve(args.data_dir)
    setup_logging(paths.logs_dir, getattr(logging, args.log_level))

    lock = SingleInstanceLock(paths.lock_file)
    try:
        lock.acquire()
    except AlreadyRunningError as e:
        log.error("%s", e)
        return EXIT_ALREADY_RUNNING

    try:
        log.info("agent-core %s 启动，数据目录：%s", __version__, paths.root)
        try:
            config = load_config(paths.config_file)
            if args.port is not None:
                config.control.port = args.port
            assembly = PluginRegistry().assemble(config.plugins)
        except (PluginNotFoundError, TypeError, ValueError) as e:
            log.error("配置错误：%s", e)
            return EXIT_CONFIG

        revision = upgrade_to_head(paths.database, paths.backups_dir)
        prune_backups(paths.backups_dir)
        secrets = SecretStore(paths.secrets_file)
        log.info("密钥存储：%s（%s）", secrets.kind, secrets.backend_name)

        asyncio.run(_serve(paths, config, secrets, assembly.describe(), revision))
        return EXIT_OK
    finally:
        lock.release()
        log.info("agent-core 已退出")


async def _serve(
    paths: DataPaths,
    config: AppConfig,
    secrets: SecretStore,
    plugins: dict[str, str],
    db_revision: str | None,
) -> None:
    bus = EventBus()
    lifecycle = ServiceLifecycle()
    state = ControlState(
        token=generate_token(),
        bus=bus,
        lifecycle=lifecycle,
        data_dir=paths.root,
        secret_backend=secrets.kind,
        plugins=plugins,
        db_revision=db_revision,
    )
    # TODO(下一阶段)：按 channel_accounts 装配 QQ 连接，并把各自的 stop() 注册为停止钩子。

    # 自己绑定套接字，才能在端口为 0 时拿到实际端口。只监听 127.0.0.1。
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", config.control.port))
    port = sock.getsockname()[1]

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(state),
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
    try:
        await server.serve(sockets=[sock])
    finally:
        watcher.cancel()
        # Ctrl+C / SIGTERM 走这里；/shutdown 已经执行过的话不会重复执行。
        await lifecycle.run_stop_hooks()
        with contextlib.suppress(FileNotFoundError):
            paths.control_file.unlink()


if __name__ == "__main__":
    sys.exit(main())
