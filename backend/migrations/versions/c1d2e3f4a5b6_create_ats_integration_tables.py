"""Create ATS integration tables for Phase 3E.3

Revision ID: c1d2e3f4a5b6
Revises: b2c3d4e5f6a7
Create Date: 2026-09-18 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c1d2e3f4a5b6'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. ats_connections
    op.create_table(
        'ats_connections',
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connection_name', sa.String(length=100), nullable=False),
        sa.Column('adapter_type', sa.String(length=50), nullable=False),
        sa.Column('tenant_label', sa.String(length=100), nullable=True),
        sa.Column('scopes', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'[]'")),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('health_status', sa.String(length=50), nullable=False, server_default=sa.text("'UNKNOWN'")),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('secret_ref', sa.String(length=255), nullable=True),
        sa.Column('encrypted_config', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('kill_switch_active', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("adapter_type IN ('MOCK', 'CSV', 'JSON')", name='ck_ats_connections_adapter_type'),
        sa.CheckConstraint("health_status IN ('HEALTHY', 'DEGRADED', 'UNHEALTHY', 'UNKNOWN')", name='ck_ats_connections_health_status'),
    )
    op.create_index('ix_ats_connections_org_id', 'ats_connections', ['organization_id'], unique=False)
    op.create_index('ix_ats_connections_adapter_type', 'ats_connections', ['adapter_type'], unique=False)

    # 2. ats_external_jobs
    op.create_table(
        'ats_external_jobs',
        sa.Column('external_job_pk', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ats_connections.connection_id', ondelete='CASCADE'), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('department', sa.String(length=255), nullable=True),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=True),
        sa.Column('raw_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('organization_id', 'connection_id', 'external_id', name='uq_ats_external_jobs_conn_ext_id'),
    )
    op.create_index('ix_ats_external_jobs_org_conn', 'ats_external_jobs', ['organization_id', 'connection_id'], unique=False)

    # 3. ats_external_candidates
    op.create_table(
        'ats_external_candidates',
        sa.Column('external_candidate_pk', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ats_connections.connection_id', ondelete='CASCADE'), nullable=False),
        sa.Column('external_candidate_id', sa.String(length=255), nullable=False),
        sa.Column('external_application_id', sa.String(length=255), nullable=True),
        sa.Column('external_job_id', sa.String(length=255), nullable=True),
        sa.Column('full_name', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=True),
        sa.Column('phone', sa.String(length=100), nullable=True),
        sa.Column('stage', sa.String(length=100), nullable=True),
        sa.Column('raw_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('organization_id', 'connection_id', 'external_candidate_id', name='uq_ats_external_candidates_conn_ext_id'),
    )
    op.create_index('ix_ats_external_candidates_org_conn', 'ats_external_candidates', ['organization_id', 'connection_id'], unique=False)

    # 4. ats_external_attachments
    op.create_table(
        'ats_external_attachments',
        sa.Column('external_attachment_pk', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ats_connections.connection_id', ondelete='CASCADE'), nullable=False),
        sa.Column('external_candidate_id', sa.String(length=255), nullable=False),
        sa.Column('external_attachment_id', sa.String(length=255), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=100), nullable=False),
        sa.Column('file_size_bytes', sa.Integer(), nullable=False),
        sa.Column('download_url', sa.String(length=1000), nullable=True),
        sa.Column('raw_payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('organization_id', 'connection_id', 'external_attachment_id', name='uq_ats_external_attachments_conn_ext_id'),
    )
    op.create_index('ix_ats_external_attachments_org_conn', 'ats_external_attachments', ['organization_id', 'connection_id'], unique=False)

    # 5. ats_object_mappings
    op.create_table(
        'ats_object_mappings',
        sa.Column('mapping_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ats_connections.connection_id', ondelete='CASCADE'), nullable=False),
        sa.Column('object_type', sa.String(length=50), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=False),
        sa.Column('internal_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('canonical_content_hash', sa.String(length=64), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('organization_id', 'connection_id', 'object_type', 'external_id', name='uq_ats_object_mappings_unique'),
    )
    op.create_index('ix_ats_object_mappings_internal_id', 'ats_object_mappings', ['internal_id'], unique=False)
    op.create_index('ix_ats_object_mappings_org_conn', 'ats_object_mappings', ['organization_id', 'connection_id'], unique=False)

    # 6. ats_import_runs
    op.create_table(
        'ats_import_runs',
        sa.Column('run_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ats_connections.connection_id', ondelete='CASCADE'), nullable=False),
        sa.Column('run_type', sa.String(length=50), nullable=False),
        sa.Column('is_dry_run', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('status', sa.String(length=50), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column('total_jobs_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_candidates_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('total_attachments_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('successful_items_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('failed_items_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('duplicate_items_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('changed_items_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('summary_json', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_ats_import_runs_org_conn', 'ats_import_runs', ['organization_id', 'connection_id'], unique=False)
    op.create_index('ix_ats_import_runs_status', 'ats_import_runs', ['status'], unique=False)

    # 7. ats_import_item_results
    op.create_table(
        'ats_import_item_results',
        sa.Column('result_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('run_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ats_import_runs.run_id', ondelete='CASCADE'), nullable=False),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ats_connections.connection_id', ondelete='CASCADE'), nullable=False),
        sa.Column('item_type', sa.String(length=50), nullable=False),
        sa.Column('external_id', sa.String(length=255), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('safe_error_message', sa.String(length=1000), nullable=True),
        sa.Column('internal_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_ats_import_item_results_run_id', 'ats_import_item_results', ['run_id'], unique=False)
    op.create_index('ix_ats_import_item_results_org_id', 'ats_import_item_results', ['organization_id'], unique=False)

    # 8. ats_sync_checkpoints
    op.create_table(
        'ats_sync_checkpoints',
        sa.Column('checkpoint_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ats_connections.connection_id', ondelete='CASCADE'), nullable=False),
        sa.Column('entity_type', sa.String(length=50), nullable=False),
        sa.Column('cursor_token', sa.String(length=500), nullable=True),
        sa.Column('page_number', sa.Integer(), nullable=False, server_default=sa.text('1')),
        sa.Column('last_synced_timestamp', sa.DateTime(timezone=True), nullable=True),
        sa.Column('status', sa.String(length=50), nullable=False, server_default=sa.text("'HEALTHY'")),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('organization_id', 'connection_id', 'entity_type', name='uq_ats_sync_checkpoints_conn_entity'),
    )

    # 9. ats_export_receipts
    op.create_table(
        'ats_export_receipts',
        sa.Column('receipt_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ats_connections.connection_id', ondelete='CASCADE'), nullable=False),
        sa.Column('internal_candidate_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('external_candidate_id', sa.String(length=255), nullable=False),
        sa.Column('preview_hash', sa.String(length=64), nullable=False),
        sa.Column('destination', sa.String(length=255), nullable=False),
        sa.Column('exported_note_text', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False),
        sa.Column('response_receipt', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'")),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index('ix_ats_export_receipts_org_conn', 'ats_export_receipts', ['organization_id', 'connection_id'], unique=False)
    op.create_index('ix_ats_export_receipts_candidate', 'ats_export_receipts', ['internal_candidate_id'], unique=False)

    # 10. ats_reconciliation_records
    op.create_table(
        'ats_reconciliation_records',
        sa.Column('reconciliation_id', postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column('organization_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('connection_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ats_connections.connection_id', ondelete='CASCADE'), nullable=False),
        sa.Column('export_receipt_id', postgresql.UUID(as_uuid=True), sa.ForeignKey('ats_export_receipts.receipt_id', ondelete='CASCADE'), nullable=False),
        sa.Column('status', sa.String(length=50), nullable=False, server_default=sa.text("'PENDING'")),
        sa.Column('reason', sa.String(length=500), nullable=False),
        sa.Column('resolution_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index('ix_ats_reconciliation_records_org_conn', 'ats_reconciliation_records', ['organization_id', 'connection_id'], unique=False)


def downgrade() -> None:
    op.drop_table('ats_reconciliation_records')
    op.drop_table('ats_export_receipts')
    op.drop_table('ats_sync_checkpoints')
    op.drop_table('ats_import_item_results')
    op.drop_table('ats_import_runs')
    op.drop_table('ats_object_mappings')
    op.drop_table('ats_external_attachments')
    op.drop_table('ats_external_candidates')
    op.drop_table('ats_external_jobs')
    op.drop_table('ats_connections')
