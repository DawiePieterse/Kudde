"""castrated flag on an animal

A castrated male is a steer, not a bull. Additive: every male already on a
farm starts as a bull, which is right until someone says otherwise.

Revision ID: 9d4a7c2e5f10
Revises: 3b8e1f6a9c02
Created: 2026-10-22

"""
from alembic import op
import sqlalchemy as sa


revision = '9d4a7c2e5f10'
down_revision = '3b8e1f6a9c02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('animal', schema=None) as batch_op:
        batch_op.add_column(sa.Column('castrated', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table('animal', schema=None) as batch_op:
        batch_op.drop_column('castrated')
