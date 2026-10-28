"""启动时自动迁移；迁移前先备份数据库。"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from agent_core.infra.db.engine import sync_url

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).with_name("migrations")


def alembic_config(db_path: Path) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", sync_url(db_path))
    return cfg


def current_revision(db_path: Path) -> str | None:
    engine = create_engine(sync_url(db_path))
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()


def head_revision(cfg: Config) -> str | None:
    return ScriptDirectory.from_config(cfg).get_current_head()


def backup_database(db_path: Path, backups_dir: Path, tag: str) -> Path:
    """用 SQLite 在线备份接口，WAL 里没合并的内容也会带上。"""
    target = backups_dir / f"agent-core-{time.strftime('%Y%m%d-%H%M%S')}-{tag}.db"
    with sqlite3.connect(db_path) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return target


def enable_wal(db_path: Path) -> None:
    """WAL 模式写在数据库文件里，设一次就一直生效；事务提交即落盘，断电也不容易损坏。"""
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")


def upgrade_to_head(db_path: Path, backups_dir: Path) -> str | None:
    enable_wal(db_path)
    cfg = alembic_config(db_path)
    head = head_revision(cfg)
    current = current_revision(db_path) if db_path.exists() else None
    if current == head:
        return head
    if db_path.exists() and current is not None:
        backup = backup_database(db_path, backups_dir, f"before-{head}")
        log.info("迁移前已备份数据库到 %s", backup)
    log.info("数据库迁移：%s -> %s", current, head)
    command.upgrade(cfg, "head")
    return head


def prune_backups(backups_dir: Path, keep: int = 7) -> None:
    files = sorted(backups_dir.glob("agent-core-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[keep:]:
        old.unlink()
