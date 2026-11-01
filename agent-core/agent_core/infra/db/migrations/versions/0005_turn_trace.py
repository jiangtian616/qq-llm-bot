"""turn trace：记下问答的处理过程（turn_steps）和发件箱每次发送尝试（outbox_attempts），问答记下触发它的收件箱事件。

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-01 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'turn_steps',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('turn_id', sa.Integer(), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('title', sa.Text(), nullable=False),
        sa.Column('detail', sa.Text(), nullable=False),
        sa.Column('data_json', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('started_at', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['turn_id'], ['turns.id'], name=op.f('fk_turn_steps_turn_id_turns'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_turn_steps')),
    )
    op.create_index(op.f('ix_turn_steps_turn_id_seq'), 'turn_steps', ['turn_id', 'seq'], unique=False)
    op.create_table(
        'outbox_attempts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('outbox_id', sa.Integer(), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('result', sa.Text(), nullable=False),
        sa.Column('error_code', sa.Integer(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('started_at', sa.Integer(), nullable=False),
        sa.Column('duration_ms', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ['outbox_id'], ['outbox.id'], name=op.f('fk_outbox_attempts_outbox_id_outbox'), ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_outbox_attempts')),
    )
    op.create_index(op.f('ix_outbox_attempts_outbox_id'), 'outbox_attempts', ['outbox_id'], unique=False)
    op.add_column('turns', sa.Column('inbox_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_turns_inbox_id'), 'turns', ['inbox_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_turns_inbox_id'), table_name='turns')
    with op.batch_alter_table('turns') as batch:
        batch.drop_column('inbox_id')
    op.drop_index(op.f('ix_outbox_attempts_outbox_id'), table_name='outbox_attempts')
    op.drop_table('outbox_attempts')
    op.drop_index(op.f('ix_turn_steps_turn_id_seq'), table_name='turn_steps')
    op.drop_table('turn_steps')
