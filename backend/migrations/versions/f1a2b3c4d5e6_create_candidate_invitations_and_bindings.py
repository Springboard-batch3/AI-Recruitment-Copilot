"""Create candidate_invitations and candidate_account_bindings tables and add CANDIDATE role

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-09-21 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Update check constraint on users.role to include CANDIDATE
    # SQLite / PostgreSQL compatibility handling
    bind = op.get_bind()
    if bind.engine.name == "postgresql":
        op.drop_constraint('ck_users_role', 'users', type_='check')
        op.create_check_constraint(
            'ck_users_role',
            'users',
            "role IN ('ADMIN', 'RECRUITER', 'REVIEWER', 'CANDIDATE')"
        )

    # 2. Create candidate_invitations table
    op.create_table(
        'candidate_invitations',
        sa.Column('invitation_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('candidate_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('application_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('purpose', sa.String(length=50), nullable=False, server_default='CANDIDATE_ACCESS'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('consumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_by_user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('resulting_user_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ['candidate_id'],
            ['candidate_identities.identity_id'],
            name='fk_candidate_invitations_candidate_id_identities',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['created_by_user_id'],
            ['users.user_id'],
            name='fk_candidate_invitations_created_by_users',
            ondelete='RESTRICT'
        ),
        sa.ForeignKeyConstraint(
            ['resulting_user_id'],
            ['users.user_id'],
            name='fk_candidate_invitations_resulting_user_id_users',
            ondelete='SET NULL'
        ),
        sa.UniqueConstraint('token_hash', name='uq_candidate_invitations_token_hash'),
        sa.CheckConstraint('length(token_hash) = 64', name='ck_candidate_invitations_token_hash_length')
    )
    op.create_index('ix_candidate_invitations_token_hash', 'candidate_invitations', ['token_hash'])
    op.create_index('ix_candidate_invitations_candidate_id', 'candidate_invitations', ['candidate_id'])
    op.create_index('ix_candidate_invitations_org_id', 'candidate_invitations', ['organization_id'])
    op.create_index('ix_candidate_invitations_created_by', 'candidate_invitations', ['created_by_user_id'])
    op.create_index('ix_candidate_invitations_resulting_user', 'candidate_invitations', ['resulting_user_id'])

    # 3. Create candidate_account_bindings table
    op.create_table(
        'candidate_account_bindings',
        sa.Column('binding_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('candidate_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('invitation_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False, server_default='ACTIVE'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ['user_id'],
            ['users.user_id'],
            name='fk_candidate_account_bindings_user_id_users',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['candidate_id'],
            ['candidate_identities.identity_id'],
            name='fk_candidate_account_bindings_candidate_id_identities',
            ondelete='CASCADE'
        ),
        sa.ForeignKeyConstraint(
            ['invitation_id'],
            ['candidate_invitations.invitation_id'],
            name='fk_candidate_account_bindings_invitation_id',
            ondelete='RESTRICT'
        ),
        sa.UniqueConstraint('user_id', name='uq_candidate_account_bindings_user_id'),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUSPENDED')", name='ck_candidate_account_bindings_status')
    )
    op.create_index('ix_candidate_account_bindings_user_id', 'candidate_account_bindings', ['user_id'])
    op.create_index('ix_candidate_account_bindings_candidate_id', 'candidate_account_bindings', ['candidate_id'])
    op.create_index('ix_candidate_account_bindings_org_id', 'candidate_account_bindings', ['organization_id'])
    op.create_index('ix_candidate_account_bindings_invitation_id', 'candidate_account_bindings', ['invitation_id'])


def downgrade() -> None:
    # 1. Drop candidate_account_bindings
    op.drop_index('ix_candidate_account_bindings_invitation_id', table_name='candidate_account_bindings')
    op.drop_index('ix_candidate_account_bindings_org_id', table_name='candidate_account_bindings')
    op.drop_index('ix_candidate_account_bindings_candidate_id', table_name='candidate_account_bindings')
    op.drop_index('ix_candidate_account_bindings_user_id', table_name='candidate_account_bindings')
    op.drop_table('candidate_account_bindings')

    # 2. Drop candidate_invitations
    op.drop_index('ix_candidate_invitations_resulting_user', table_name='candidate_invitations')
    op.drop_index('ix_candidate_invitations_created_by', table_name='candidate_invitations')
    op.drop_index('ix_candidate_invitations_org_id', table_name='candidate_invitations')
    op.drop_index('ix_candidate_invitations_candidate_id', table_name='candidate_invitations')
    op.drop_index('ix_candidate_invitations_token_hash', table_name='candidate_invitations')
    op.drop_table('candidate_invitations')

    # 3. Restore role check constraint on users
    bind = op.get_bind()
    if bind.engine.name == "postgresql":
        op.drop_constraint('ck_users_role', 'users', type_='check')
        op.create_check_constraint(
            'ck_users_role',
            'users',
            "role IN ('ADMIN', 'RECRUITER', 'REVIEWER')"
        )
