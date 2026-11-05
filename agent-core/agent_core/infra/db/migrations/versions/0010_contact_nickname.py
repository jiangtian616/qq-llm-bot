"""发言人记下平台给的昵称（桌面端展示用）；已有的发言人从收件箱里他最近一条消息补上。

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-04 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('contacts', sa.Column('nickname', sa.Text(), nullable=True))
    op.execute(
        """
        UPDATE contacts SET nickname = (
            SELECT json_extract(e.raw_json, '$.author.username') FROM inbox_events e
            WHERE e.account_id = contacts.account_id
              AND json_valid(e.raw_json)
              AND contacts.openid = CASE contacts.kind
                  WHEN 'member' THEN json_extract(e.raw_json, '$.author.member_openid')
                  ELSE json_extract(e.raw_json, '$.author.user_openid') END
              AND coalesce(json_extract(e.raw_json, '$.author.username'), '') != ''
            ORDER BY e.id DESC LIMIT 1
        )
        """
    )


def downgrade() -> None:
    with op.batch_alter_table('contacts') as batch:
        batch.drop_column('nickname')
