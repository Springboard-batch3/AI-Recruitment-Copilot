"""Add recruiter_review_state to candidate_applications for Phase 4B.5

Revision ID: m1a2b3c4d5e6
Revises: l1a2b3c4d5e6
Create Date: 2026-09-26 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'm1a2b3c4d5e6'
down_revision: Union[str, None] = 'l1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'candidate_applications',
        sa.Column(
            'recruiter_review_state',
            sa.String(length=30),
            server_default=sa.text("'RECEIVED'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        'ck_candidate_applications_recruiter_review_state',
        'candidate_applications',
        "recruiter_review_state IN ('RECEIVED', 'IN_REVIEW', 'INTERVIEW_READY')",
    )
    op.create_index(
        'ix_candidate_applications_recruiter_review_state',
        'candidate_applications',
        ['recruiter_review_state'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('ix_candidate_applications_recruiter_review_state', table_name='candidate_applications')
    op.drop_constraint('ck_candidate_applications_recruiter_review_state', 'candidate_applications', type_='check')
    op.drop_column('candidate_applications', 'recruiter_review_state')
