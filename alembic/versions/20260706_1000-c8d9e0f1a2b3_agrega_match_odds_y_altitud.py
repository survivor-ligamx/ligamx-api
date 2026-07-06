"""agrega tabla match_odds (histórico de momios) y altitude_m a stadiums

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-07-06 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8d9e0f1a2b3'
down_revision: Union[str, None] = 'b7c8d9e0f1a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Altitud del estadio (m s.n.m.), opcional.
    with op.batch_alter_table('stadiums', schema=None) as batch_op:
        batch_op.add_column(sa.Column('altitude_m', sa.Integer(), nullable=True))

    # Histórico de momios (serie temporal por partido).
    op.create_table(
        'match_odds',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('season', sa.String(), nullable=True),
        sa.Column('home_team', sa.String(), nullable=True),
        sa.Column('away_team', sa.String(), nullable=True),
        sa.Column('match_date', sa.DateTime(), nullable=True),
        sa.Column('source', sa.String(), nullable=True),
        sa.Column('odds_local', sa.Float(), nullable=True),
        sa.Column('odds_empate', sa.Float(), nullable=True),
        sa.Column('odds_visita', sa.Float(), nullable=True),
        sa.Column('ou_linea', sa.Float(), nullable=True),
        sa.Column('odds_over', sa.Float(), nullable=True),
        sa.Column('odds_under', sa.Float(), nullable=True),
        sa.Column('extra', sa.JSON(), nullable=True),
        sa.Column('captured_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_match_odds_id', 'match_odds', ['id'])
    op.create_index('ix_match_odds_season', 'match_odds', ['season'])
    op.create_index('ix_match_odds_home_team', 'match_odds', ['home_team'])
    op.create_index('ix_match_odds_away_team', 'match_odds', ['away_team'])
    op.create_index('ix_match_odds_captured_at', 'match_odds', ['captured_at'])


def downgrade() -> None:
    op.drop_index('ix_match_odds_captured_at', table_name='match_odds')
    op.drop_index('ix_match_odds_away_team', table_name='match_odds')
    op.drop_index('ix_match_odds_home_team', table_name='match_odds')
    op.drop_index('ix_match_odds_season', table_name='match_odds')
    op.drop_index('ix_match_odds_id', table_name='match_odds')
    op.drop_table('match_odds')
    with op.batch_alter_table('stadiums', schema=None) as batch_op:
        batch_op.drop_column('altitude_m')
