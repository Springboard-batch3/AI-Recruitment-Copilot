"""Add cascade cleanup trigger for candidate data on user delete

Revision ID: t1a2b3c4d5e6
Revises: s1a2b3c4d5e6
Create Date: 2026-09-27 21:52:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 't1a2b3c4d5e6'
down_revision: Union[str, None] = 's1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION cleanup_orphan_candidate_data_on_user_delete() RETURNS trigger AS $$
    DECLARE
        cand_rec RECORD;
    BEGIN
        -- Identify all candidate identities associated with this user
        FOR cand_rec IN (
            SELECT DISTINCT ci.identity_id, ci.artifact_id
            FROM candidate_identities ci
            WHERE ci.identity_id IN (
                SELECT candidate_id FROM candidate_account_bindings WHERE user_id = OLD.user_id
                UNION
                SELECT candidate_id FROM candidate_applications WHERE user_id = OLD.user_id AND candidate_id IS NOT NULL
            )
            OR (OLD.email IS NOT NULL AND LOWER(ci.primary_email) = LOWER(OLD.email))
        ) LOOP
            -- Delete candidate applications for this identity
            DELETE FROM candidate_applications WHERE candidate_id = cand_rec.identity_id;

            -- Delete candidate interview sessions and practice sessions/releases
            DELETE FROM candidate_interview_sessions WHERE candidate_id = cand_rec.identity_id;
            DELETE FROM candidate_practice_reports WHERE candidate_id = cand_rec.identity_id;
            DELETE FROM candidate_practice_sessions WHERE candidate_id = cand_rec.identity_id;
            DELETE FROM candidate_practice_releases WHERE candidate_id = cand_rec.identity_id;
            DELETE FROM candidate_practice_requests WHERE candidate_id = cand_rec.identity_id;
            DELETE FROM candidate_invitations WHERE candidate_id = cand_rec.identity_id;
            DELETE FROM candidate_account_bindings WHERE candidate_id = cand_rec.identity_id;

            -- Downstream matching, plans, and gap reports
            DELETE FROM supplemental_question_provenances WHERE plan_id IN (SELECT plan_id FROM candidate_interview_plans WHERE profile_id IN (SELECT profile_id FROM candidate_profiles WHERE identity_id = cand_rec.identity_id));
            DELETE FROM simulation_attempts WHERE plan_id IN (SELECT plan_id FROM candidate_interview_plans WHERE profile_id IN (SELECT profile_id FROM candidate_profiles WHERE identity_id = cand_rec.identity_id));
            DELETE FROM candidate_interview_plans WHERE profile_id IN (SELECT profile_id FROM candidate_profiles WHERE identity_id = cand_rec.identity_id);

            DELETE FROM gap_report_items WHERE report_id IN (SELECT report_id FROM gap_reports WHERE profile_id IN (SELECT profile_id FROM candidate_profiles WHERE identity_id = cand_rec.identity_id));
            DELETE FROM gap_audit_events WHERE report_id IN (SELECT report_id FROM gap_reports WHERE profile_id IN (SELECT profile_id FROM candidate_profiles WHERE identity_id = cand_rec.identity_id));
            DELETE FROM supplemental_question_provenances WHERE source_gap_report_id IN (SELECT report_id FROM gap_reports WHERE profile_id IN (SELECT profile_id FROM candidate_profiles WHERE identity_id = cand_rec.identity_id));
            DELETE FROM gap_reports WHERE profile_id IN (SELECT profile_id FROM candidate_profiles WHERE identity_id = cand_rec.identity_id);

            DELETE FROM supplemental_question_provenances WHERE source_match_result_id IN (SELECT result_id FROM match_results WHERE profile_id IN (SELECT profile_id FROM candidate_profiles WHERE identity_id = cand_rec.identity_id));
            DELETE FROM match_results WHERE profile_id IN (SELECT profile_id FROM candidate_profiles WHERE identity_id = cand_rec.identity_id);

            -- Delete the resume_artifact (cascades to profile_corrections, candidate_profiles, extractions, processing_jobs, candidate_identities)
            IF cand_rec.artifact_id IS NOT NULL THEN
                DELETE FROM resume_artifacts WHERE artifact_id = cand_rec.artifact_id;
            ELSE
                DELETE FROM candidate_identities WHERE identity_id = cand_rec.identity_id;
            END IF;
        END LOOP;

        -- Clean up any lingering candidate applications directly tied to this user_id
        DELETE FROM candidate_applications WHERE user_id = OLD.user_id;

        RETURN OLD;
    END;
    $$ LANGUAGE plpgsql;

    DROP TRIGGER IF EXISTS trg_cleanup_candidate_data_on_user_delete ON users;
    CREATE TRIGGER trg_cleanup_candidate_data_on_user_delete
    BEFORE DELETE ON users
    FOR EACH ROW
    EXECUTE FUNCTION cleanup_orphan_candidate_data_on_user_delete();
    """)


def downgrade() -> None:
    op.execute("""
    DROP TRIGGER IF EXISTS trg_cleanup_candidate_data_on_user_delete ON users;
    DROP FUNCTION IF EXISTS cleanup_orphan_candidate_data_on_user_delete();
    """)
