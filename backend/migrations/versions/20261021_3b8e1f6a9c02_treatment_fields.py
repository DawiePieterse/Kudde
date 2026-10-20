"""treatment fields: product, dose and withdrawal days on an event

Additive. Every event already on a farm keeps empty text and a NULL
withdrawal, which reads as "nothing recorded" - which is true.

Revision ID: 3b8e1f6a9c02
Revises: 7f3c2a91d4b5
Created: 2026-10-21

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '3b8e1f6a9c02'
down_revision = '7f3c2a91d4b5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('event', schema=None) as batch_op:
        batch_op.add_column(sa.Column('product', sqlmodel.sql.sqltypes.AutoString(),
                                      nullable=False, server_default=''))
        batch_op.add_column(sa.Column('dose', sqlmodel.sql.sqltypes.AutoString(),
                                      nullable=False, server_default=''))
        batch_op.add_column(sa.Column('withdrawal_days', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('event', schema=None) as batch_op:
        batch_op.drop_column('withdrawal_days')
        batch_op.drop_column('dose')
        batch_op.drop_column('product')
