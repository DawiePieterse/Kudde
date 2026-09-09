"""a photo carries the id the device gave it

Adds animalphoto.client_uuid so uploading a photo is idempotent - the same
contract event.client_uuid got in 94ab18b9e9c1, and the same reason: the
field app gives up on a request after 8 seconds and puts it back in the
outbox, so an upload that was slow but actually landed gets replayed.

A photo is several MB over farm wifi, so that is the ordinary case here
rather than the rare one.

Nullable and additive: every photo already on a farm keeps its NULL, and a
unique index over a nullable column lets SQLite hold as many of those as
there are.

Revision ID: c1d84f2a7e13
Revises: 154964af0a8e
Created: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = 'c1d84f2a7e13'
down_revision = '154964af0a8e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('animalphoto', schema=None) as batch_op:
        batch_op.add_column(sa.Column('client_uuid', sqlmodel.sql.sqltypes.AutoString(), nullable=True))
        batch_op.create_index(batch_op.f('ix_animalphoto_client_uuid'), ['client_uuid'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('animalphoto', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_animalphoto_client_uuid'))
        batch_op.drop_column('client_uuid')
