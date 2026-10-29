import React, { useEffect, useState } from "react";
import { confirmATSExport, listActiveATSConnections, previewATSExport } from "../../api/ats";
import { ATSConnection, ATSExportPreview, ATSExportReceipt } from "../../types/ats";
import { Icon } from "../Icon";

interface AtsExportModalProps {
  isOpen: boolean;
  candidateId: string;
  candidateName: string;
  onClose: () => void;
  onExportSuccess?: (receipt: ATSExportReceipt) => void;
}

export function AtsExportModal({
  isOpen,
  candidateId,
  candidateName,
  onClose,
  onExportSuccess,
}: AtsExportModalProps) {
  const [connections, setConnections] = useState<ATSConnection[]>([]);
  const [selectedConnId, setSelectedConnId] = useState<string>("");
  const [isLoadingConns, setIsLoadingConns] = useState<boolean>(true);

  const [destination, setDestination] = useState<string>("");
  const [noteText, setNoteText] = useState<string>("");

  const [preview, setPreview] = useState<ATSExportPreview | null>(null);
  const [receipt, setReceipt] = useState<ATSExportReceipt | null>(null);
  const [isProcessing, setIsProcessing] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      loadConnections();
      setDestination(`ATS Candidate Notes - ${candidateName}`);
      setNoteText("");
      setPreview(null);
      setReceipt(null);
      setError(null);

      const handleKeyDown = (e: KeyboardEvent) => {
        if (e.key === "Escape") {
          onClose();
        }
      };
      window.addEventListener("keydown", handleKeyDown);
      return () => window.removeEventListener("keydown", handleKeyDown);
    }
  }, [isOpen, candidateName, onClose]);

  const loadConnections = async () => {
    setIsLoadingConns(true);
    try {
      const res = await listActiveATSConnections();
      setConnections(res.items || []);
      if (res.items && res.items.length > 0) {
        setSelectedConnId(res.items[0].connection_id);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to load active ATS connections.");
    } finally {
      setIsLoadingConns(false);
    }
  };

  const handleGeneratePreview = async () => {
    if (!selectedConnId || !noteText.trim()) return;

    setIsProcessing(true);
    setError(null);
    try {
      const res = await previewATSExport({
        connection_id: selectedConnId,
        candidate_id: candidateId,
        destination: destination.trim(),
        note_text: noteText.trim(),
      });
      setPreview(res);
    } catch (err: any) {
      setError(err?.message || "Failed to generate export preview.");
    } finally {
      setIsProcessing(false);
    }
  };

  const handleConfirmExport = async () => {
    if (!preview) return;

    setIsProcessing(true);
    setError(null);
    try {
      const res = await confirmATSExport({
        connection_id: preview.connection_id,
        candidate_id: preview.candidate_id,
        destination: preview.destination,
        note_text: preview.note_text,
        preview_hash: preview.preview_hash,
      });
      setReceipt(res);
      if (onExportSuccess) {
        onExportSuccess(res);
      }
    } catch (err: any) {
      setError(err?.message || "Export confirmation failed.");
    } finally {
      setIsProcessing(false);
    }
  };

  if (!isOpen) return null;

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
      aria-labelledby="ats-export-modal-title"
    >
      <div
        className="modal-surface"
        style={{
          backgroundColor: "var(--bg-surface)",
          borderRadius: "var(--radius-lg)",
          maxWidth: "600px",
          width: "100%",
          maxHeight: "90vh",
          overflowY: "auto",
          padding: "24px",
          boxShadow: "var(--shadow-lg)",
        }}
      >
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "16px" }}>
          <div>
            <h2 id="ats-export-modal-title" style={{ fontSize: "18px", fontWeight: 700, margin: "0 0 4px 0", color: "var(--text-primary)" }}>
              Export Recruiter Note to ATS
            </h2>
            <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: 0 }}>
              Export human-reviewed evaluation notes directly to candidate record in your external ATS.
            </p>
          </div>
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onClose}
            aria-label="Close export dialog"
          >
            <Icon name="x" size={14} />
          </button>
        </div>

        {error && (
          <div className="feedback-box error" role="alert" style={{ marginBottom: "16px" }}>
            <Icon name="alert-triangle" size={16} />
            <p className="feedback-text error">{error}</p>
          </div>
        )}

        {isLoadingConns ? (
          <div style={{ textAlign: "center", padding: "30px" }}>
            <div className="loading-spinner" style={{ margin: "0 auto 8px auto" }} />
            <p style={{ fontSize: "13px", color: "var(--text-secondary)" }}>Loading connections...</p>
          </div>
        ) : receipt ? (
          /* Receipt State */
          <div>
            <div
              style={{
                backgroundColor: receipt.status === "SUCCESS" ? "#f0fdf4" : "#fffbeb",
                border: `1px solid ${receipt.status === "SUCCESS" ? "#bbf7d0" : "#fde68a"}`,
                borderRadius: "var(--radius-md)",
                padding: "16px",
                marginBottom: "16px",
              }}
            >
              <h3 style={{ fontSize: "15px", fontWeight: 600, margin: "0 0 8px 0", color: "var(--text-primary)" }}>
                {receipt.status === "SUCCESS" ? "Note Exported Successfully" : "Reconciliation Required"}
              </h3>
              <p style={{ fontSize: "13px", color: "var(--text-secondary)", margin: "0 0 8px 0" }}>
                {receipt.status === "SUCCESS"
                  ? "The recruiter note has been recorded and safely pushed to the external ATS."
                  : "A timeout occurred while communicating with the ATS. The export was recorded as RECONCILIATION_REQUIRED."}
              </p>
              <div style={{ fontSize: "12px", fontFamily: "var(--font-mono)", color: "var(--text-muted)" }}>
                Receipt ID: {receipt.receipt_id}
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end" }}>
              <button type="button" className="btn btn-primary btn-md" onClick={onClose}>
                Close
              </button>
            </div>
          </div>
        ) : preview ? (
          /* Preview Confirmation Step */
          <div>
            <div
              style={{
                backgroundColor: "var(--bg-surface-subtle)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-md)",
                padding: "16px",
                marginBottom: "16px",
              }}
            >
              <div style={{ marginBottom: "10px" }}>
                <span style={{ fontSize: "12px", color: "var(--text-muted)", display: "block" }}>Destination</span>
                <strong style={{ fontSize: "14px", color: "var(--text-primary)" }}>{preview.destination}</strong>
              </div>

              <div style={{ marginBottom: "10px" }}>
                <span style={{ fontSize: "12px", color: "var(--text-muted)", display: "block" }}>Outgoing Note Content</span>
                <div
                  style={{
                    backgroundColor: "var(--bg-surface)",
                    border: "1px solid var(--border-subtle)",
                    borderRadius: "var(--radius-sm)",
                    padding: "10px 12px",
                    fontSize: "13px",
                    color: "var(--text-primary)",
                    whiteSpace: "pre-wrap",
                    marginTop: "4px",
                  }}
                >
                  {preview.note_text}
                </div>
              </div>

              <div>
                <span style={{ fontSize: "11px", color: "var(--text-muted)", display: "block" }}>
                  Server Preview Hash (Verification Integrity)
                </span>
                <code style={{ fontSize: "11px", color: "var(--text-muted)", wordBreak: "break-all" }}>
                  {preview.preview_hash}
                </code>
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button
                type="button"
                className="btn btn-secondary btn-md"
                onClick={() => setPreview(null)}
                disabled={isProcessing}
              >
                Back to Edit
              </button>
              <button
                type="button"
                className="btn btn-primary btn-md"
                onClick={handleConfirmExport}
                disabled={isProcessing}
              >
                {isProcessing ? "Pushing Note..." : "Confirm & Push to ATS"}
              </button>
            </div>
          </div>
        ) : (
          /* Authoring & Preview Form */
          <div>
            <div style={{ marginBottom: "14px" }}>
              <label className="form-label" htmlFor="ats-export-conn" style={{ display: "block", marginBottom: "6px", fontSize: "13px", fontWeight: 600 }}>
                Target ATS Integration
              </label>
              <select
                id="ats-export-conn"
                className="form-input"
                style={{ width: "100%" }}
                value={selectedConnId}
                onChange={(e) => setSelectedConnId(e.target.value)}
              >
                {connections.map((c) => (
                  <option key={c.connection_id} value={c.connection_id}>
                    {c.connection_name} ({c.adapter_type})
                  </option>
                ))}
              </select>
            </div>

            <div style={{ marginBottom: "14px" }}>
              <label className="form-label" htmlFor="ats-export-dest" style={{ display: "block", marginBottom: "6px", fontSize: "13px", fontWeight: 600 }}>
                Destination Reference
              </label>
              <input
                id="ats-export-dest"
                type="text"
                className="form-input"
                style={{ width: "100%" }}
                value={destination}
                onChange={(e) => setDestination(e.target.value)}
                required
              />
            </div>

            <div style={{ marginBottom: "16px" }}>
              <label className="form-label" htmlFor="ats-export-note" style={{ display: "block", marginBottom: "6px", fontSize: "13px", fontWeight: 600 }}>
                Recruiter Evaluation Note *
              </label>
              <textarea
                id="ats-export-note"
                className="form-input"
                style={{ width: "100%", minHeight: "120px", resize: "vertical" }}
                placeholder="Author or paste human-reviewed candidate assessment notes to sync with ATS..."
                value={noteText}
                onChange={(e) => setNoteText(e.target.value)}
                required
              />
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button type="button" className="btn btn-secondary btn-md" onClick={onClose} disabled={isProcessing}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-md"
                onClick={handleGeneratePreview}
                disabled={isProcessing || !noteText.trim() || !selectedConnId}
              >
                {isProcessing ? "Generating..." : "Preview Export"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
