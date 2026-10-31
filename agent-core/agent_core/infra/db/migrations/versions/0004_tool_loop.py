"""tool loop：问答记下搜索次数和工具轮数，原始历史记下写入时的工具指纹。

另外把默认提示词里的 Markdown 发图说明去掉：回复格式的规则改写在回复工具的描述里。
只改和那一版默认提示词完全一样的模型配置，用户改过的不动。

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-01 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PROMPT = (
    "你是一个 QQ 机器人助手。回答简洁、友好，用中文回复。"
    "群聊里每条用户消息前的方括号是发言人标识，用来区分不同的人，回复时不要复述这个标识。"
    "QQ 消息不支持 Markdown，请用纯文本回复，不要使用 Markdown 格式。"
)
_PROMPT_WITH_IMAGES = _PROMPT + (
    "唯一的例外是发图片：需要发图片时，单独一行写 ![图片说明](图片链接)，系统会把它作为图片发出；"
    "只能用你确定存在、可以公开访问的 png、jpg 或 gif 图片链接，不要编造链接。"
)


def upgrade() -> None:
    op.add_column('turns', sa.Column('search_requests', sa.Integer(), server_default='0', nullable=False))
    op.add_column('turns', sa.Column('tool_rounds', sa.Integer(), server_default='0', nullable=False))
    op.add_column('provider_messages', sa.Column('tools_fingerprint', sa.Text(), server_default='', nullable=False))
    op.execute(
        sa.text("UPDATE provider_profiles SET system_prompt = :new WHERE system_prompt = :old").bindparams(
            new=_PROMPT, old=_PROMPT_WITH_IMAGES
        )
    )


def downgrade() -> None:
    with op.batch_alter_table('provider_messages') as batch:
        batch.drop_column('tools_fingerprint')
    with op.batch_alter_table('turns') as batch:
        batch.drop_column('tool_rounds')
        batch.drop_column('search_requests')
