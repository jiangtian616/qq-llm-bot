"""每日维护：按账号清理超期数据，并每天备份一次数据库（保留最近 7 份）。

启动 1 分钟后跑一次，之后每 24 小时跑一次；也可以通过控制接口手动触发。每次在 system_events 里记一条。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Any

from agent_core.core.bus import BusEvent, EventBus
from agent_core.core.clock import now_ms
from agent_core.infra.db.migrate import backup_database, prune_backups

log = logging.getLogger(__name__)

DAY_MS = 24 * 3600 * 1000
KEEP_BACKUPS = 7


class MaintenanceWorker:
    def __init__(
        self,
        *,
        db,  # noqa: ANN001
        bus: EventBus,
        database_path: Path,
        backups_dir: Path,
        initial_delay_s: float = 60,
        interval_s: float = 24 * 3600,
    ) -> None:
        self._db = db
        self._bus = bus
        self._database_path = database_path
        self._backups_dir = backups_dir
        self._initial_delay = initial_delay_s
        self._interval = interval_s
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="maintenance")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        await asyncio.sleep(self._initial_delay)
        while True:
            try:
                await self.run_once()
            except Exception:
                log.exception("每日维护出错")
            await asyncio.sleep(self._interval)

    async def run_once(self) -> dict[str, Any]:
        async with self._lock:
            report: dict[str, Any] = {"accounts": [], "backup": None}
            now = now_ms()
            for account in await self._db.accounts.list():
                item: dict[str, Any] = {"account_id": account.id}
                messages_days = int(
                    await self._db.settings.resolve("retention.messages_days", account_id=account.id) or 0
                )
                raw_days = int(await self._db.settings.resolve("retention.raw_events_days", account_id=account.id) or 0)
                if messages_days > 0:
                    item.update(await self._db.maintenance.purge_messages(account.id, now - messages_days * DAY_MS))
                if raw_days > 0:
                    item["raw_events"] = await self._db.maintenance.purge_raw_events(account.id, now - raw_days * DAY_MS)
                report["accounts"].append(item)

            deleted = sum(v for a in report["accounts"] for k, v in a.items() if k not in ("account_id", "epoch_bumped"))
            report["backup"] = await self._daily_backup()
            await self._db.events.add(
                "info",
                "cleanup",
                f"每日维护：删除 {deleted} 行超期数据" + ("，已备份数据库" if report["backup"] else ""),
                data=report,
            )
            await self._bus.publish(BusEvent("maintenance.done", {"deleted": deleted}))
            log.info("每日维护完成：%s", report)
            return report

    async def _daily_backup(self) -> str | None:
        """今天还没有备份过才备份；然后只保留最近 7 份。"""
        today = time.strftime("%Y%m%d")
        if any(self._backups_dir.glob(f"agent-core-{today}-*-daily.db")):
            return None
        path = await asyncio.to_thread(backup_database, self._database_path, self._backups_dir, "daily")
        await asyncio.to_thread(prune_backups, self._backups_dir, KEEP_BACKUPS)
        return str(path)
