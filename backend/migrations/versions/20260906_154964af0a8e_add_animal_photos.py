"""add animal photos

Revision ID: 154964af0a8e
Revises: be71b453b9e0
Created: 2026-09-06

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '154964af0a8e'
down_revision = 'be71b453b9e0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('animalphoto',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('animal_id', sa.Integer(), nullable=False),
    sa.Column('filename', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('content_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('caption', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['animal_id'], ['animal.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('animalphoto', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_animalphoto_animal_id'), ['animal_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('animalphoto', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_animalphoto_animal_id'))

    op.drop_table('animalphoto')
