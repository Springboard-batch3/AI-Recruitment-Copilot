import React, { useEffect, useState } from "react";
import { listActiveATSConnections, triggerATSImport } from "../../api/ats";
import { fetchJobs, JobSummary } from "../../api/jobs";
import { ATSConnection, ATSImportRun, LocalFolderScanItemMeta } from "../../types/ats";
import { Icon } from "../Icon";
import { useOptionalAuth } from "../../context/AuthContext";
import { getCrossPortalUrl } from "../../utils/portal";

interface AtsImportModalProps {
  isOpen: boolean;
  onClose: () => void;
  onImportComplete?: () => void;
  onOpenApplicants?: (jobId: string) => void;
}

export function AtsImportModal({ isOpen, onClose, onImportComplete, onOpenApplicants }: AtsImportModalProps) {
  const auth = useOptionalAuth();
  const user = auth?.user || null;
  const [connections, setConnections] = useState<ATSConnection[]>([]);
  const [selectedConnId, setSelectedConnId] = useState<string>("");
  const [isLoadingConns, setIsLoadingConns] = useState<boolean>(true);
  const [connsLoadError, setConnsLoadError] = useState<string | null>(null);

  // Available jobs for destination assignment
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string>("");
  const [isLoadingJobs, setIsLoadingJobs] = useState<boolean>(false);

  // File upload state for CSV/JSON
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileBase64, setFileBase64] = useState<string>("");

  // Run execution state
  const [isProcessing, setIsProcessing] = useState<boolean>(false);
  const [processingType, setProcessingType] = useState<"scan" | "import" | null>(null);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [dryRunResult, setDryRunResult] = useState<ATSImportRun | null>(null);
  const [liveResult, setLiveResult] = useState<ATSImportRun | null>(null);
  const [previewExpired, setPreviewExpired] = useState<boolean>(false);

  useEffect(() => {
    if (isOpen) {
      loadInitialData();
      setDryRunResult(null);
      setLiveResult(null);
      setExecutionError(null);
      setConnsLoadError(null);
      setSelectedFile(null);
      setFileBase64("");
      setPreviewExpired(false);

      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === "Escape") {
          onClose();
        }
      };
      window.addEventListener("keydown", handleKeyDown);
      return () => window.removeEventListener("keydown", handleKeyDown);
    }
  }, [isOpen, onClose]);

  const loadInitialData = async () => {
    setIsLoadingConns(true);
    setIsLoadingJobs(true);
    setConnsLoadError(null);

    // 1. Load active connections
    try {
      const res = await listActiveATSConnections();
      const items = res.items || [];
      setConnections(items);
      if (items.length > 0) {
        setSelectedConnId(items[0].connection_id);
      }
    } catch (err: any) {
      setConnsLoadError(err?.message || "Failed to load active ATS connections.");
    } finally {
      setIsLoadingConns(false);
    }

    // 2. Load jobs & filter for ready/approved/released jobs
    try {
      const jobList = await fetchJobs();
      // Filter out archived jobs or jobs where currentVersion is explicitly DRAFT
      const eligible = (jobList || []).filter((j) => {
        if (j.status === "ARCHIVED") return false;
        const vStatus = j.currentVersion?.versionStatus;
        if (!vStatus) return true;
        return vStatus === "READY_FOR_MATCHING" || vStatus === "APPROVED" || j.publicationStatus === "RELEASED";
      });
      setJobs(eligible);
      if (eligible.length > 0) {
        setSelectedJobId(eligible[0].jobId);
      }
    } catch (err: any) {
      console.warn("Failed to load jobs for ATS import modal:", err);
    } finally {
      setIsLoadingJobs(false);
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setSelectedFile(file);
    const reader = new FileReader();
    reader.onload = () => {
      const base64 = (reader.result as string).split(",")[1] || "";
      setFileBase64(base64);
    };
    reader.readAsDataURL(file);
  };

  const selectedConn = connections.find((c) => c.connection_id === selectedConnId);
  const isLocalFolder = selectedConn?.adapter_type === "LOCAL_FOLDER_DEMO";
  const isFileAdapter = selectedConn?.adapter_type === "CSV" || selectedConn?.adapter_type === "JSON";
  const selectedJob = jobs.find((j) => j.jobId === selectedJobId);
  const selectedJobVersionId = selectedJob?.currentVersion?.jobVersionId;

  // Check preview expiry periodically if preview is active
  useEffect(() => {
    if (!dryRunResult?.expires_at) return;
    const interval = setInterval(() => {
      const exp = new Date(dryRunResult.expires_at!).getTime();
      if (Date.now() > exp) {
        setPreviewExpired(true);
      }
    }, 10000);
    return () => clearInterval(interval);
  }, [dryRunResult]);

  const handleScanFolder = async () => {
    if (!selectedConnId) return;
    if (isLocalFolder && !selectedJobId) {
      setExecutionError("Please select a destination job before scanning the folder.");
      return;
    }

    setIsProcessing(true);
    setProcessingType("scan");
    setExecutionError(null);
    setPreviewExpired(false);

    try {
      const res = await triggerATSImport(selectedConnId, {
        is_dry_run: true,
        destination_job_id: isLocalFolder ? selectedJobId : undefined,
        destination_job_version_id: isLocalFolder ? selectedJobVersionId : undefined,
        file_content_b64: isFileAdapter ? fileBase64 : undefined,
        filename: selectedFile?.name,
      });

      setDryRunResult(res);
    } catch (err: any) {
      setExecutionError(err?.message || "Failed to scan ATS source.");
    } finally {
      setIsProcessing(false);
      setProcessingType(null);
    }
  };

  const handleConfirmImport = async () => {
    if (!selectedConnId) return;
    if (isLocalFolder && !selectedJobId) {
      setExecutionError("Please select a destination job before confirming import.");
      return;
    }

    setIsProcessing(true);
    setProcessingType("import");
    setExecutionError(null);

    try {
      let previewToken = dryRunResult?.preview_token;
      let previewHash = dryRunResult?.preview_hash;

      // If dry-run hasn't run or expired, do the dry-run preview step automatically first
      if (isLocalFolder && (!previewToken || !previewHash || previewExpired)) {
        const scanRes = await triggerATSImport(selectedConnId, {
          is_dry_run: true,
          destination_job_id: selectedJobId,
          destination_job_version_id: selectedJobVersionId,
        });
        setDryRunResult(scanRes);
        previewToken = scanRes.preview_token;
        previewHash = scanRes.preview_hash;

        if (scanRes.successful_items_count === 0) {
          setExecutionError("No ready candidates found to import in the selected folder.");
          setIsProcessing(false);
          setProcessingType(null);
          return;
        }
      }

      const res = await triggerATSImport(selectedConnId, {
        is_dry_run: false,
        destination_job_id: isLocalFolder ? selectedJobId : undefined,
        destination_job_version_id: isLocalFolder ? selectedJobVersionId : undefined,
        preview_token: isLocalFolder ? (previewToken || undefined) : undefined,
        preview_hash: isLocalFolder ? (previewHash || undefined) : undefined,
        file_content_b64: isFileAdapter ? fileBase64 : undefined,
        filename: selectedFile?.name,
      });

      setLiveResult(res);
      if (onImportComplete) {
        onImportComplete();
      }
    } catch (err: any) {
      setExecutionError(err?.message || "Import execution failed.");
    } finally {
      setIsProcessing(false);
      setProcessingType(null);
    }
  };

  const getStatusBadge = (status: string, safeMsg?: string | null) => {
    const s = (status || "").toUpperCase();
    if (s === "READY" || s === "SUCCESS") {
      return (
        <span className="badge" style={{ backgroundColor: "#dcfce7", color: "#166534", display: "inline-flex", alignItems: "center", gap: "4px" }}>
          <Icon name="check-circle" size={12} />
          <span>Ready to Import</span>
        </span>
      );
    }
    if (s === "DUPLICATE" || s === "DUPLICATE_CONTENT" || s === "ALREADY_IMPORTED") {
      return (
        <span className="badge" style={{ backgroundColor: "#fef9c3", color: "#854d0e", display: "inline-flex", alignItems: "center", gap: "4px" }} title={safeMsg || "Duplicate record"}>
          <Icon name="copy" size={12} />
          <span>Duplicate (Skip)</span>
        </span>
      );
    }
    return (
      <span
        className="badge"
        style={{
          backgroundColor: "var(--status-danger-bg)",
          color: "var(--status-danger)",
          border: "1px solid var(--status-danger-border)",
          display: "inline-flex",
          alignItems: "center",
          gap: "4px",
        }}
        title={safeMsg || "Validation error"}
      >
        <Icon name="alert-triangle" size={12} />
        <span>{s.replace(/_/g, " ")}</span>
      </span>
    );
  };

  const formatFileSize = (bytes: number): string => {
    if (!bytes || bytes <= 0) return "0 B";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  if (!isOpen) return null;

  const previewScanItems: LocalFolderScanItemMeta[] = dryRunResult?.summary_json?.scan_items || [];
  const readyCount = dryRunResult?.successful_items_count ?? 0;
  const duplicateCount = dryRunResult?.duplicate_items_count ?? 0;
  const failedCount = dryRunResult?.failed_items_count ?? 0;

  return (
    <div
      className="modal-backdrop"
      style={{
        position: "fixed",
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: "rgba(15, 23, 42, 0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 1000,
        padding: "16px",
      }}
      role="dialog"
      aria-modal="true"
      aria-labelledby="ats-import-modal-title"
    >
      <div
        className="modal-surface"
        style={{
          backgroundColor: "var(--bg-surface)",
          borderRadius: "var(--radius-lg)",
          maxWidth: "680px",
          width: "100%",
          maxHeight: "90vh",
          overflowY: "auto",
          padding: "24px",
          boxShadow: "var(--shadow-lg)",
        }}
      >
        {/* Modal Header */}
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "16px" }}>
          <div>
            <h2 id="ats-import-modal-title" style={{ fontSize: "18px", fontWeight: 700, margin: "0 0 4px 0", color: "var(--text-primary)" }}>
              Import from ATS / File
            </h2>
            <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0 }}>
              Scan authorized resumes, preview dry-run validations, and associate candidates with a destination job.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onClose}
            aria-label="Close import dialog"
          >
            <Icon name="x" size={14} />
          </button>
        </div>

        {/* Execution Error Banner (Non-fatal / retryable) */}
        {executionError && (
          <div className="feedback-box error" role="alert" style={{ marginBottom: "16px" }}>
            <Icon name="alert-triangle" size={16} />
            <p className="feedback-text error">{executionError}</p>
          </div>
        )}

        {/* Loading State */}
        {isLoadingConns ? (
          <div style={{ textAlign: "center", padding: "36px 16px" }}>
            <div className="loading-spinner" style={{ margin: "0 auto 12px auto" }} />
            <p style={{ fontSize: "14px", color: "var(--text-secondary)" }}>Loading available ATS connections...</p>
          </div>
        ) : connsLoadError ? (
          /* Connection Loading Error State with Retry */
          <div style={{ textAlign: "center", padding: "24px 8px" }}>
            <div className="feedback-box error" role="alert" style={{ marginBottom: "16px", textAlign: "left" }}>
              <Icon name="alert-triangle" size={16} />
              <p className="feedback-text error">{connsLoadError}</p>
            </div>
            <h3 style={{ fontSize: "15px", fontWeight: 600, color: "var(--text-primary)", marginBottom: "8px" }}>
              We couldn’t load ATS connections.
            </h3>
            <p style={{ fontSize: "13px", color: "var(--text-secondary)", marginBottom: "20px" }}>
              Please check your connection and retry.
            </p>
            <div style={{ display: "flex", justifyContent: "center", gap: "10px" }}>
              <button type="button" className="btn btn-secondary btn-md" onClick={onClose}>
                Close
              </button>
              <button type="button" className="btn btn-primary btn-md" onClick={loadInitialData}>
                Retry
              </button>
            </div>
          </div>
        ) : connections.length === 0 ? (
          /* No Enabled Connections Available */
          <div style={{ textAlign: "center", padding: "28px 8px" }}>
            <h3 style={{ fontSize: "15px", fontWeight: 600, color: "var(--text-primary)", marginBottom: "8px" }}>
              No ATS connections are available for your workspace.
            </h3>
            <p style={{ fontSize: "13px", color: "var(--text-secondary)", marginBottom: "20px" }}>
              Please contact an Administrator to configure an integration.
            </p>
            {user?.role === "ADMIN" ? (
              <a
                href={getCrossPortalUrl("admin", "?tab=ats_integrations")}
                target="_blank"
                rel="noopener noreferrer"
                className="btn btn-primary btn-md"
                style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
              >
                <Icon name="external-link" size={14} />
                <span>Open ATS Connections Admin</span>
              </a>
            ) : (
              <button type="button" className="btn btn-secondary btn-md" onClick={onClose}>
                Close
              </button>
            )}
          </div>
        ) : liveResult ? (
          /* Import Receipt View */
          <div>
            <div
              style={{
                backgroundColor: liveResult.status === "COMPLETED" ? "#f0fdf4" : "#fffbeb",
                border: `1px solid ${liveResult.status === "COMPLETED" ? "#bbf7d0" : "#fde68a"}`,
                borderRadius: "var(--radius-md)",
                padding: "16px 20px",
                marginBottom: "20px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
                <Icon name="check-circle" size={18} />
                <h3 style={{ fontSize: "15px", fontWeight: 600, margin: 0, color: "var(--text-primary)" }}>
                  Import Completed ({liveResult.status})
                </h3>
              </div>
              <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: "0 0 12px 0" }}>
                Source: <strong>{liveResult.summary_json?.source_provenance || selectedConn?.connection_name}</strong> · Job: <strong>{liveResult.summary_json?.destination_job_title || "Assigned"}</strong>
              </p>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "8px", fontSize: "13px" }}>
                <span>Imported Candidates: <strong>{liveResult.successful_items_count}</strong></span>
                <span>Duplicates Skipped: <strong>{liveResult.duplicate_items_count}</strong></span>
                <span>Failed / Rejected: <strong>{liveResult.failed_items_count}</strong></span>
                <span>Total Examined: <strong>{liveResult.total_candidates_count || liveResult.total_attachments_count}</strong></span>
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              {liveResult.summary_json?.destination_job_id && onOpenApplicants && (
                <button
                  type="button"
                  className="btn btn-secondary btn-md"
                  onClick={() => {
                    const destJobId = liveResult.summary_json?.destination_job_id;
                    onClose();
                    if (onImportComplete) onImportComplete();
                    if (destJobId) onOpenApplicants(destJobId);
                  }}
                  style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                >
                  <Icon name="users" size={14} />
                  <span>View Applicants</span>
                </button>
              )}
              <button
                type="button"
                className="btn btn-primary btn-md"
                onClick={() => {
                  onClose();
                  if (onImportComplete) onImportComplete();
                }}
              >
                Go to Candidates
              </button>
            </div>
          </div>
        ) : (
          /* Active Import Form */
          <div>
            {/* Source Connector Selector */}
            <div style={{ marginBottom: "16px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                <label className="form-label" htmlFor="select-ats-conn" style={{ margin: 0, fontSize: "13px", fontWeight: 600 }}>
                  ATS Connector
                </label>
                {user?.role === "ADMIN" && (
                  <a
                    href={getCrossPortalUrl("admin", "?tab=ats_integrations")}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="ats-manage-connections-link"
                    style={{ fontSize: "12px", display: "inline-flex", alignItems: "center", gap: "4px", color: "var(--brand-primary)", textDecoration: "none" }}
                    aria-label="Open ATS connections in admin portal"
                  >
                    <Icon name="external-link" size={12} />
                    <span>Open ATS connections</span>
                  </a>
                )}
              </div>
              <select
                id="select-ats-conn"
                className="form-input"
                style={{ width: "100%" }}
                value={selectedConnId}
                onChange={(e) => {
                  setSelectedConnId(e.target.value);
                  setDryRunResult(null);
                  setExecutionError(null);
                  setPreviewExpired(false);
                }}
                disabled={isProcessing}
              >
                {connections.map((c) => (
                  <option key={c.connection_id} value={c.connection_id}>
                    {c.connection_name} ({c.adapter_type === "LOCAL_FOLDER_DEMO" ? "Local Folder Demo" : c.adapter_type})
                  </option>
                ))}
              </select>
            </div>

            {/* Destination Job Selector */}
            <div style={{ marginBottom: "16px" }}>
              <label className="form-label" htmlFor="select-destination-job" style={{ display: "block", marginBottom: "6px", fontSize: "13px", fontWeight: 600 }}>
                Destination Job Requisition & Revision *
              </label>
              {isLoadingJobs ? (
                <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>Loading active jobs...</p>
              ) : jobs.length === 0 ? (
                <div style={{ fontSize: "13px", color: "var(--text-secondary)", padding: "8px 12px", backgroundColor: "var(--bg-surface-subtle)", borderRadius: "var(--radius-md)" }}>
                  No eligible jobs found. Please create or approve a job requisition first.
                </div>
              ) : (
                <select
                  id="select-destination-job"
                  className="form-input"
                  style={{ width: "100%" }}
                  value={selectedJobId}
                  onChange={(e) => {
                    setSelectedJobId(e.target.value);
                    setDryRunResult(null);
                    setExecutionError(null);
                    setPreviewExpired(false);
                  }}
                  disabled={isProcessing}
                  required
                >
                  {jobs.map((j) => (
                    <option key={j.jobId} value={j.jobId}>
                      {j.currentVersion?.title || "Untitled Job"} {j.currentVersion?.department ? `(${j.currentVersion.department})` : ""} · Rev #{j.currentVersion?.revision || 1} ({j.publicationStatus === "RELEASED" ? "Released" : j.currentVersion?.versionStatus || "Ready"})
                    </option>
                  ))}
                </select>
              )}
              <span style={{ fontSize: "11px", color: "var(--text-muted)", marginTop: "4px", display: "block" }}>
                Imported candidates will be linked to this requisition with explicit provenance.
              </span>
            </div>

            {/* CSV / JSON File Input if applicable */}
            {isFileAdapter && (
              <div style={{ marginBottom: "16px" }}>
                <label className="form-label" htmlFor="ats-file-input" style={{ display: "block", marginBottom: "6px", fontSize: "13px", fontWeight: 600 }}>
                  Select File ({selectedConn?.adapter_type})
                </label>
                <input
                  id="ats-file-input"
                  type="file"
                  accept={selectedConn?.adapter_type === "CSV" ? ".csv" : ".json"}
                  onChange={handleFileChange}
                  disabled={isProcessing}
                  style={{ fontSize: "13px" }}
                />
              </div>
            )}

            {/* Preview Expired Warning */}
            {previewExpired && (
              <div className="feedback-box error" role="alert" style={{ marginBottom: "16px" }}>
                <Icon name="alert-triangle" size={16} />
                <p className="feedback-text error">
                  This dry-run preview has expired. Please click "Scan Folder" to refresh the preview.
                </p>
              </div>
            )}

            {/* Dry Run Preview Summary & Item List */}
            {dryRunResult && (
              <div
                style={{
                  backgroundColor: "var(--bg-surface-subtle)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: "var(--radius-md)",
                  padding: "16px",
                  marginBottom: "16px",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <Icon name="check-circle" size={16} />
                    <strong style={{ fontSize: "14px", color: "var(--text-primary)" }}>
                      {isLocalFolder ? "Dry-Run Scan Preview" : "Dry Run Preview Results"}
                    </strong>
                  </div>
                  <span style={{ fontSize: "13px", fontWeight: 500, color: "var(--text-secondary)" }}>
                    {readyCount} ready · {duplicateCount} already imported · {failedCount} rejected
                  </span>
                </div>

                {/* Items List Table: Filename, Result, Explanation */}
                {previewScanItems.length > 0 ? (
                  <div style={{ maxHeight: "240px", overflowY: "auto", border: "1px solid var(--border-subtle)", borderRadius: "var(--radius-sm)", backgroundColor: "var(--bg-surface)" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
                      <thead>
                        <tr style={{ borderBottom: "1px solid var(--border-subtle)", backgroundColor: "var(--bg-surface-subtle)" }}>
                          <th style={{ textAlign: "left", padding: "8px 12px", fontWeight: 600 }}>Filename</th>
                          <th style={{ textAlign: "left", padding: "8px 12px", fontWeight: 600 }}>Result</th>
                          <th style={{ textAlign: "left", padding: "8px 12px", fontWeight: 600 }}>Explanation</th>
                        </tr>
                      </thead>
                      <tbody>
                        {previewScanItems.map((item, idx) => (
                          <tr key={idx} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                            <td style={{ padding: "8px 12px", fontWeight: 500, color: "var(--text-primary)" }} title={item.filename}>
                              {item.filename}
                            </td>
                            <td style={{ padding: "8px 12px" }}>
                              {getStatusBadge(item.status, item.safe_error_message)}
                            </td>
                            <td style={{ padding: "8px 12px", color: "var(--text-secondary)" }}>
                              {item.safe_error_message || (item.status === "READY" ? `Valid ${item.format} document` : item.status)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : dryRunResult.items && dryRunResult.items.length > 0 ? (
                  <div style={{ maxHeight: "240px", overflowY: "auto", border: "1px solid var(--border-subtle)", borderRadius: "var(--radius-sm)", backgroundColor: "var(--bg-surface)" }}>
                    <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
                      <thead>
                        <tr style={{ borderBottom: "1px solid var(--border-subtle)", backgroundColor: "var(--bg-surface-subtle)" }}>
                          <th style={{ textAlign: "left", padding: "8px 12px", fontWeight: 600 }}>Item</th>
                          <th style={{ textAlign: "left", padding: "8px 12px", fontWeight: 600 }}>Result</th>
                          <th style={{ textAlign: "left", padding: "8px 12px", fontWeight: 600 }}>Explanation</th>
                        </tr>
                      </thead>
                      <tbody>
                        {dryRunResult.items.map((item, idx) => (
                          <tr key={idx} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                            <td style={{ padding: "8px 12px", fontWeight: 500, color: "var(--text-primary)" }}>
                              {item.external_id}
                            </td>
                            <td style={{ padding: "8px 12px" }}>
                              {getStatusBadge(item.status, item.safe_error_message)}
                            </td>
                            <td style={{ padding: "8px 12px", color: "var(--text-secondary)" }}>
                              {item.safe_error_message || item.status}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : isLocalFolder ? (
                  <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: "8px 0 0 0" }}>
                    No supported documents found in the inbox folder.
                  </p>
                ) : null}
              </div>
            )}

            {/* Action Bar */}
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "20px" }}>
              <div>
                {dryRunResult && readyCount === 0 && (
                  <span style={{ fontSize: "13px", color: "var(--text-secondary)", fontStyle: "italic" }}>
                    Nothing new to import.
                  </span>
                )}
              </div>
              <div style={{ display: "flex", gap: "10px" }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-md"
                  onClick={onClose}
                  disabled={isProcessing}
                >
                  Cancel
                </button>

                <button
                  type="button"
                  className="btn btn-secondary btn-md"
                  onClick={handleScanFolder}
                  disabled={isProcessing || (isFileAdapter && !selectedFile) || (isLocalFolder && !selectedJobId)}
                  aria-label={dryRunResult ? "Scan folder again" : isLocalFolder ? "Scan Folder" : "Dry Run Preview"}
                >
                  {isProcessing && processingType === "scan"
                    ? "Scanning..."
                    : dryRunResult
                    ? "Scan again"
                    : isLocalFolder
                    ? "Scan Folder"
                    : "Dry Run Preview"}
                </button>

                <button
                  type="button"
                  className="btn btn-primary btn-md"
                  onClick={handleConfirmImport}
                  disabled={
                    isProcessing ||
                    previewExpired ||
                    (dryRunResult !== null && readyCount === 0) ||
                    (isFileAdapter && !selectedFile) ||
                    (isLocalFolder && !selectedJobId)
                  }
                  aria-label={
                    readyCount > 0
                      ? `Import ${readyCount} candidate${readyCount > 1 ? "s" : ""}`
                      : "Execute Import"
                  }
                >
                  {isProcessing && processingType === "import"
                    ? "Importing..."
                    : readyCount > 0
                    ? `Import ${readyCount} candidate${readyCount > 1 ? "s" : ""}`
                    : "Execute Import"}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
