"""撤回群消息：发件箱记下撤回的时间（状态改成 recalled）。

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-03 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('outbox', sa.Column('recalled_at', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('outbox') as batch:
        batch.drop_column('recalled_at')
