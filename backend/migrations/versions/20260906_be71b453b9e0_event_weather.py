"""event weather

Adds the three weather columns an event carries. The farm's position they
are looked up against lives on the farm table, added by a637cf32c23b - this
revision therefore chains onto it rather than creating a farm table of its
own.

Revision ID: be71b453b9e0
Revises: a637cf32c23b
Created: 2026-09-06 20:34:01.365808

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = 'be71b453b9e0'
down_revision = 'a637cf32c23b'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable and additive: every event already on a farm keeps its NULLs,
    # which is also what an event recorded with no farm position set looks
    # like from here on.
    with op.batch_alter_table('event', schema=None) as batch_op:
        batch_op.add_column(sa.Column('weather_temp_max', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('weather_temp_min', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('weather_precipitation', sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('event', schema=None) as batch_op:
        batch_op.drop_column('weather_precipitation')
        batch_op.drop_column('weather_temp_min')
        batch_op.drop_column('weather_temp_max')
