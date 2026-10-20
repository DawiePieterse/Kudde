"""camps: a managed list of the farm's camps

Adds the camp table (see models.Camp) and seeds it from every location
already recorded on a movement or birth event, so a farm that has been
moving cattle between "Bo-kamp" and "Onder-kamp" for a year finds both
waiting in Settings rather than having to type them again.

Seeded ignoring case, keeping the first spelling seen: "Noordkamp" and
"noordkamp" were two camps before this and are one afterwards. The events
themselves are not touched - the apps snap a typed name to this table's
spelling from now on, and the history keeps whatever was typed at the time.

Revision ID: 7f3c2a91d4b5
Revises: c1d84f2a7e13
Created: 2026-10-20

"""
from alembic import op
import sqlalchemy as sa
import sqlmodel  # noqa: F401 - autogenerate renders sqlmodel.sql.sqltypes.AutoString


revision = '7f3c2a91d4b5'
down_revision = 'c1d84f2a7e13'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'camp',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('archived', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('camp', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_camp_name'), ['name'], unique=True)

    # Seed from the history. Plain SQL rather than the models: a migration
    # has to keep working after the models move on.
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT location FROM event "
        "WHERE kind IN ('movement', 'birth') AND location <> '' "
        "ORDER BY event_date, id"
    )).fetchall()
    seen = set()
    names = []
    for (location,) in rows:
        name = " ".join(str(location).split())
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    for order, name in enumerate(sorted(names, key=str.casefold)):
        conn.execute(
            sa.text("INSERT INTO camp (name, sort_order, archived, created_at) "
                    "VALUES (:name, :order, 0, CURRENT_TIMESTAMP)"),
            {"name": name, "order": order},
        )


def downgrade() -> None:
    with op.batch_alter_table('camp', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_camp_name'))
    op.drop_table('camp')
