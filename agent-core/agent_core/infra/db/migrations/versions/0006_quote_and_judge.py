"""引用消息和发送者标识：入站消息记下自己的发送者标识和引用的消息，发件箱记下机器人的发送者标识。

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-03 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('messages', sa.Column('sender_key', sa.Text(), nullable=True))
    op.add_column('messages', sa.Column('quote_json', sa.Text(), nullable=True))
    op.create_index(
        op.f('ix_messages_conversation_id_sender_key'), 'messages', ['conversation_id', 'sender_key'], unique=False
    )
    op.add_column('outbox', sa.Column('sender_key', sa.Text(), nullable=True))
    op.create_index(
        op.f('ix_outbox_conversation_id_sender_key'), 'outbox', ['conversation_id', 'sender_key'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_outbox_conversation_id_sender_key'), table_name='outbox')
    with op.batch_alter_table('outbox') as batch:
        batch.drop_column('sender_key')
    op.drop_index(op.f('ix_messages_conversation_id_sender_key'), table_name='messages')
    with op.batch_alter_table('messages') as batch:
        batch.drop_column('quote_json')
        batch.drop_column('sender_key')
