"""Update candidate application lifecycle and add artifact linkage

Revision ID: p1a2b3c4d5e6
Revises: o1a2b3c4d5e6
Create Date: 2026-09-26 21:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'p1a2b3c4d5e6'
down_revision: Union[str, None] = 'o1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_APPLICATION_STATUSES = (
    "'DRAFT', 'RESUME_PROCESSING', 'READY_TO_SUBMIT', 'SUBMITTED', 'PROCESSING_FAILED', 'WITHDRAWN'"
)
OLD_APPLICATION_STATUSES = "'SUBMITTED', 'WITHDRAWN'"


def upgrade() -> None:
    # 1. Add artifact_id column and foreign key to candidate_applications
    op.add_column(
        'candidate_applications',
        sa.Column('artifact_id', sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        'fk_candidate_applications_artifact_id_artifacts',
        'candidate_applications',
        'resume_artifacts',
        ['artifact_id'],
        ['artifact_id'],
        ondelete='SET NULL',
    )
    op.create_index(
        'ix_candidate_applications_artifact_id',
        'candidate_applications',
        ['artifact_id'],
        unique=False,
    )

    # 2. Make profile_id, profile_version, submitted_at nullable for drafts
    op.alter_column('candidate_applications', 'profile_id', nullable=True)
    op.alter_column('candidate_applications', 'profile_version', nullable=True)
    op.alter_column('candidate_applications', 'submitted_at', nullable=True)

    # 3. Update check constraints
    bind = op.get_bind()
    if bind.engine.name == "postgresql":
        op.drop_constraint('ck_candidate_applications_status', 'candidate_applications', type_='check')
        op.drop_constraint('ck_candidate_applications_profile_version_positive', 'candidate_applications', type_='check')
        op.drop_constraint('ck_candidate_applications_withdrawn_timestamp_consistency', 'candidate_applications', type_='check')

        op.create_check_constraint(
            'ck_candidate_applications_status',
            'candidate_applications',
            f"status IN ({NEW_APPLICATION_STATUSES})",
        )
        op.create_check_constraint(
            'ck_candidate_applications_profile_version_positive',
            'candidate_applications',
            "profile_version IS NULL OR profile_version >= 1",
        )
        op.create_check_constraint(
            'ck_candidate_applications_withdrawn_timestamp_consistency',
            'candidate_applications',
            "(status = 'WITHDRAWN' AND withdrawn_at IS NOT NULL) OR (status != 'WITHDRAWN' AND withdrawn_at IS NULL)",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.engine.name == "postgresql":
        op.drop_constraint('ck_candidate_applications_status', 'candidate_applications', type_='check')
        op.drop_constraint('ck_candidate_applications_profile_version_positive', 'candidate_applications', type_='check')
        op.drop_constraint('ck_candidate_applications_withdrawn_timestamp_consistency', 'candidate_applications', type_='check')

        op.create_check_constraint(
            'ck_candidate_applications_status',
            'candidate_applications',
            f"status IN ({OLD_APPLICATION_STATUSES})",
        )
        op.create_check_constraint(
            'ck_candidate_applications_profile_version_positive',
            'candidate_applications',
            "profile_version >= 1",
        )
        op.create_check_constraint(
            'ck_candidate_applications_withdrawn_timestamp_consistency',
            'candidate_applications',
            "(status = 'WITHDRAWN' AND withdrawn_at IS NOT NULL) OR (status = 'SUBMITTED' AND withdrawn_at IS NULL)",
        )

    op.alter_column('candidate_applications', 'submitted_at', nullable=False)
    op.alter_column('candidate_applications', 'profile_version', nullable=False)
    op.alter_column('candidate_applications', 'profile_id', nullable=False)

    op.drop_index('ix_candidate_applications_artifact_id', table_name='candidate_applications')
    op.drop_constraint('fk_candidate_applications_artifact_id_artifacts', 'candidate_applications', type_='foreignkey')
    op.drop_column('candidate_applications', 'artifact_id')
