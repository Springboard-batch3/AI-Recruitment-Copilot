export type ATSAdapterType = "MOCK" | "CSV" | "JSON" | "LOCAL_FOLDER_DEMO";
export type ATSHealthStatus = "HEALTHY" | "DEGRADED" | "UNHEALTHY" | "UNKNOWN";
export type ATSImportStatus = "PENDING" | "RUNNING" | "COMPLETED" | "FAILED" | "PARTIAL_SUCCESS";
export type ATSItemResultStatus = "SUCCESS" | "DUPLICATE" | "CHANGED" | "FAILED" | "VALIDATION_ERROR";
export type ATSExportStatus = "SUCCESS" | "FAILED" | "RECONCILIATION_REQUIRED";

export interface ATSConnection {
  connection_id: string;
  organization_id: string;
  connection_name: string;
  adapter_type: ATSAdapterType;
  tenant_label?: string | null;
  scopes: string[];
  is_enabled: boolean;
  health_status: ATSHealthStatus;
  last_sync_at?: string | null;
  secret_ref_masked?: string | null;
  kill_switch_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface ATSConnectionListResponse {
  items: ATSConnection[];
  total_count: number;
}

export interface ATSConnectionCreatePayload {
  connection_name: string;
  adapter_type: ATSAdapterType;
  tenant_label?: string;
  scopes?: string[];
  secret_ref?: string;
  config?: Record<string, any>;
}

export interface ATSImportItemResult {
  result_id: string;
  item_type: "JOB" | "CANDIDATE" | "ATTACHMENT";
  external_id: string;
  status: ATSItemResultStatus;
  safe_error_message?: string | null;
  internal_id?: string | null;
  created_at: string;
}

export interface LocalFolderScanItemMeta {
  external_id: string;
  filename: string;
  format: string;
  file_size_bytes: number;
  status: string;
  safe_error_message?: string | null;
}

export interface ATSImportRun {
  run_id: string;
  connection_id: string;
  run_type: ATSAdapterType;
  is_dry_run: boolean;
  status: ATSImportStatus;
  destination_job_id?: string | null;
  preview_token?: string | null;
  preview_hash?: string | null;
  expires_at?: string | null;
  total_jobs_count: number;
  total_candidates_count: number;
  total_attachments_count: number;
  successful_items_count: number;
  failed_items_count: number;
  duplicate_items_count: number;
  changed_items_count: number;
  summary_json: Record<string, any>;
  error_message?: string | null;
  started_at: string;
  completed_at?: string | null;
  items: ATSImportItemResult[];
}

export interface ATSExportPreview {
  preview_hash: string;
  connection_id: string;
  connection_name: string;
  candidate_id: string;
  candidate_name: string;
  destination: string;
  note_text: string;
  warning?: string | null;
}

export interface ATSExportReceipt {
  receipt_id: string;
  connection_id: string;
  internal_candidate_id: string;
  external_candidate_id: string;
  preview_hash: string;
  destination: string;
  exported_note_text: string;
  status: ATSExportStatus;
  response_receipt: Record<string, any>;
  created_at: string;
}
