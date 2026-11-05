"""入站消息加上桌面端展示用的文字（display_content）；模型里早就有这一列，之前漏了迁移。

和 content 一样时为空，读取时退回 content，所以已有的消息不用补。

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-05 22:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('display_content', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('messages') as batch:
        batch.drop_column('display_content')
