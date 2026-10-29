import { apiFetch } from "./client";
import {
  ATSConnection,
  ATSConnectionCreatePayload,
  ATSConnectionListResponse,
  ATSExportPreview,
  ATSExportReceipt,
  ATSImportRun,
} from "../types/ats";

// Admin API
export async function listAdminATSConnections(): Promise<ATSConnectionListResponse> {
  return apiFetch<ATSConnectionListResponse>("/api/v1/ats/connections");
}

export async function createATSConnection(
  payload: ATSConnectionCreatePayload
): Promise<ATSConnection> {
  return apiFetch<ATSConnection>("/api/v1/ats/connections", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateATSConnection(
  connectionId: string,
  payload: Partial<ATSConnectionCreatePayload> & {
    is_enabled?: boolean;
    kill_switch_active?: boolean;
  }
): Promise<ATSConnection> {
  return apiFetch<ATSConnection>(`/api/v1/ats/connections/${connectionId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function testATSConnection(connectionId: string): Promise<ATSConnection> {
  return apiFetch<ATSConnection>(`/api/v1/ats/connections/${connectionId}/test`, {
    method: "POST",
  });
}

// Recruiter API
export async function listActiveATSConnections(): Promise<ATSConnectionListResponse> {
  return apiFetch<ATSConnectionListResponse>("/api/v1/ats/active-connections");
}

export async function triggerATSImport(
  connectionId: string,
  payload: {
    is_dry_run: boolean;
    destination_job_id?: string;
    destination_job_version_id?: string;
    preview_token?: string;
    preview_hash?: string;
    file_content_b64?: string;
    filename?: string;
  }
): Promise<ATSImportRun> {
  return apiFetch<ATSImportRun>(`/api/v1/ats/connections/${connectionId}/import`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getATSImportRun(runId: string): Promise<ATSImportRun> {
  return apiFetch<ATSImportRun>(`/api/v1/ats/import-runs/${runId}`);
}

export async function previewATSExport(payload: {
  connection_id: string;
  candidate_id: string;
  destination: string;
  note_text: string;
}): Promise<ATSExportPreview> {
  return apiFetch<ATSExportPreview>("/api/v1/ats/export/preview", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function confirmATSExport(payload: {
  connection_id: string;
  candidate_id: string;
  destination: string;
  note_text: string;
  preview_hash: string;
}): Promise<ATSExportReceipt> {
  return apiFetch<ATSExportReceipt>("/api/v1/ats/export/confirm", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}
