"""drop group trigger settings：删掉 group.trigger 和 group.keep_untriggered 的配置值。

群里推送哪些消息由 QQ 客户端的群设置决定，推过来的都回复，这两个配置项已经去掉。

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-30 12:00:00.000000
"""

from collections.abc import Sequence

from alembic import op


revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM settings WHERE key IN ('group.trigger', 'group.keep_untriggered')")


def downgrade() -> None:
    # 删掉的只是各层的覆盖值，降级后按默认值取，不需要恢复。
    pass
