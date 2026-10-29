"""Create dashboard_tickets table for Phase 4E.2

Revision ID: w1a2b3c4d5e6
Revises: v2a3b4c5d6e7
Create Date: 2026-09-29 20:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'w1a2b3c4d5e6'
down_revision: Union[str, None] = 'v2a3b4c5d6e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'dashboard_tickets',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('ticket_hash', sa.String(length=64), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('organization_id', sa.Uuid(), nullable=False),
        sa.Column('role', sa.String(length=50), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], name='fk_dashboard_tickets_user_id_users', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('ticket_hash', name='uq_dashboard_tickets_ticket_hash'),
        sa.CheckConstraint('length(ticket_hash) = 64', name='ck_dashboard_tickets_ticket_hash_length'),
    )
    op.create_index('ix_dashboard_tickets_ticket_hash', 'dashboard_tickets', ['ticket_hash'], unique=False)
    op.create_index('ix_dashboard_tickets_user_id', 'dashboard_tickets', ['user_id'], unique=False)
    op.create_index('ix_dashboard_tickets_org_id', 'dashboard_tickets', ['organization_id'], unique=False)
    op.create_index('ix_dashboard_tickets_expires_at', 'dashboard_tickets', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_dashboard_tickets_expires_at', table_name='dashboard_tickets')
    op.drop_index('ix_dashboard_tickets_org_id', table_name='dashboard_tickets')
    op.drop_index('ix_dashboard_tickets_user_id', table_name='dashboard_tickets')
    op.drop_index('ix_dashboard_tickets_ticket_hash', table_name='dashboard_tickets')
    op.drop_table('dashboard_tickets')
