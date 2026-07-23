"""agrega identificador estable y unico de ESPN a partidos

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-07-23 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEX_NAME = "ix_matches_espn_event_id"


def upgrade() -> None:
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("espn_event_id", sa.String(), nullable=True))

    # external_event_id no guardaba el proveedor (ESPN/365Scores). No se copia
    # automaticamente para evitar etiquetar IDs ambiguos como ESPN. El siguiente
    # sync ESPN reclama de forma segura cada fila al emparejar ID + fixture.
    op.create_index(
        _INDEX_NAME,
        "matches",
        ["espn_event_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="matches")
    with op.batch_alter_table("matches", schema=None) as batch_op:
        batch_op.drop_column("espn_event_id")
