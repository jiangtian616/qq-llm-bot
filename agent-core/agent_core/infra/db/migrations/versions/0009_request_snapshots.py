"""问答过程记下每次请求模型的完整请求（请求快照），查看完整上下文用。

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-04 10:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('turn_steps', sa.Column('request_z', sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('turn_steps') as batch:
        batch.drop_column('request_z')
