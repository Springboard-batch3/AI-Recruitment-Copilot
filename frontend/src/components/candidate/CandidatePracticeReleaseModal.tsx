import React, { useState, useEffect, useCallback, useRef } from "react";
import {
  listEligibleJobsForPracticeRelease,
  listCandidatePracticeReleases,
  listCandidatePracticeRequestsForRecruiter,
  releasePracticeToCandidate,
  releasePracticeFromRequest,
  declineCandidatePracticeRequest,
  revokeCandidatePracticeRelease,
  preparePracticeSetForRevision,
  PracticeEligibleItem,
  PracticeIneligibleItem,
  CandidatePracticeRequestDto,
} from "../../api/candidatePractice";
import { RecruiterPracticeReleaseSummary } from "../../types/ai_question_set";
import { Icon } from "../Icon";

interface CandidatePracticeReleaseModalProps {
  candidateId: string;
  candidateName: string;
  isOpen: boolean;
  onClose: () => void;
  onNavigateToJob?: (jobId: string) => void;
}

export function CandidatePracticeReleaseModal({
  candidateId,
  candidateName,
  isOpen,
  onClose,
  onNavigateToJob,
}: CandidatePracticeReleaseModalProps) {
  const [eligibleItems, setEligibleItems] = useState<PracticeEligibleItem[]>([]);
  const [ineligibleItems, setIneligibleItems] = useState<PracticeIneligibleItem[]>([]);
  const [requests, setRequests] = useState<CandidatePracticeRequestDto[]>([]);
  const [releases, setReleases] = useState<RecruiterPracticeReleaseSummary[]>([]);
  const [selectedJobVersionId, setSelectedJobVersionId] = useState<string>("");
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isProcessing, setIsProcessing] = useState<boolean>(false);
  const [preparingVersionId, setPreparingVersionId] = useState<string | null>(null);
  const [decliningRequestId, setDecliningRequestId] = useState<string | null>(null);
  const [declineReason, setDeclineReason] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [successMessage, setSuccessMessage] = useState<string | null>(null);

  const modalRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [copiedLink, setCopiedLink] = useState<boolean>(false);

  const fetchData = useCallback(async () => {
    if (!candidateId) return;
    setIsLoading(true);
    setError(null);
    try {
      const [diagRes, releasesRes, reqsRes] = await Promise.all([
        listEligibleJobsForPracticeRelease(candidateId),
        listCandidatePracticeReleases(candidateId),
        listCandidatePracticeRequestsForRecruiter(candidateId).catch(() => []),
      ]);

      const elig = diagRes.eligible || [];
      const inelig = diagRes.ineligible || [];
      setEligibleItems(elig);
      setIneligibleItems(inelig);
      setReleases(releasesRes || []);
      setRequests(reqsRes || []);

      // Auto-select pending requested job first, or first eligible item
      const requestedItem = elig.find((i) => i.has_candidate_request);
      if (requestedItem) {
        setSelectedJobVersionId(requestedItem.job_revision_id);
      } else if (elig.length > 0) {
        setSelectedJobVersionId(elig[0].job_revision_id);
      } else {
        setSelectedJobVersionId("");
      }
    } catch (err: any) {
      setError(err?.message || "Failed to load practice release data.");
    } finally {
      setIsLoading(false);
    }
  }, [candidateId]);

  useEffect(() => {
    if (isOpen) {
      fetchData();
      setSuccessMessage(null);
      setError(null);
      setDecliningRequestId(null);
      setDeclineReason("");
      setCopiedLink(false);
    }
  }, [isOpen, fetchData]);

  // Handle ESC key to close
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isOpen) {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const handleReleaseSelected = async () => {
    if (!selectedJobVersionId) return;
    const selected = eligibleItems.find((i) => i.job_revision_id === selectedJobVersionId);
    if (!selected) return;

    setIsProcessing(true);
    setError(null);
    setSuccessMessage(null);
    try {
      if (selected.candidate_request_id) {
        await releasePracticeFromRequest(selected.candidate_request_id);
      } else {
        await releasePracticeToCandidate(candidateId, selected.job_id, selected.job_revision_id);
      }
      setSuccessMessage(`Practice interview for "${selected.job_title}" (${selected.job_version}) successfully released to ${candidateName}.`);
      await fetchData();
      bodyRef.current?.scrollTo?.({ top: 0, behavior: "smooth" });
    } catch (err: any) {
      setError(err?.message || "Failed to release practice interview.");
      bodyRef.current?.scrollTo?.({ top: 0, behavior: "smooth" });
    } finally {
      setIsProcessing(false);
    }
  };

  const handleDeclineRequest = async (requestId: string) => {
    setIsProcessing(true);
    setError(null);
    try {
      await declineCandidatePracticeRequest(requestId, declineReason);
      setSuccessMessage("Candidate practice request declined.");
      setDecliningRequestId(null);
      setDeclineReason("");
      await fetchData();
      bodyRef.current?.scrollTo?.({ top: 0, behavior: "smooth" });
    } catch (err: any) {
      setError(err?.message || "Failed to decline practice request.");
      bodyRef.current?.scrollTo?.({ top: 0, behavior: "smooth" });
    } finally {
      setIsProcessing(false);
    }
  };

  const handlePrepareDraft = async (jobId: string, targetVersionId: string) => {
    setPreparingVersionId(targetVersionId);
    setError(null);
    setSuccessMessage(null);
    try {
      await preparePracticeSetForRevision(jobId, targetVersionId);
      setSuccessMessage("Draft practice question set prepared from older approved set! Open the question set workspace to review and approve.");
      await fetchData();
      bodyRef.current?.scrollTo?.({ top: 0, behavior: "smooth" });
    } catch (err: any) {
      setError(err?.message || "Failed to prepare practice set.");
      bodyRef.current?.scrollTo?.({ top: 0, behavior: "smooth" });
    } finally {
      setPreparingVersionId(null);
    }
  };

  const handleRevoke = async (releaseId: string) => {
    setError(null);
    setSuccessMessage(null);
    try {
      await revokeCandidatePracticeRelease(releaseId);
      setSuccessMessage("Practice interview release revoked.");
      await fetchData();
      bodyRef.current?.scrollTo?.({ top: 0, behavior: "smooth" });
    } catch (err: any) {
      setError(err?.message || "Failed to revoke practice release.");
      bodyRef.current?.scrollTo?.({ top: 0, behavior: "smooth" });
    }
  };

  const candidatePortalUrl = typeof window !== "undefined" ? `${window.location.origin}/portal/login` : "/portal/login";

  const handleCopyLink = () => {
    navigator.clipboard?.writeText(candidatePortalUrl);
    setCopiedLink(true);
    setTimeout(() => setCopiedLink(false), 2500);
  };

  const pendingRequests = requests.filter((r) => r.status === "REQUESTED");
  const selectedItem = eligibleItems.find((i) => i.job_revision_id === selectedJobVersionId);

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="practice-release-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="modal-container"
        ref={modalRef}
        style={{
          maxWidth: "760px",
          width: "92%",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
          borderRadius: "8px",
          backgroundColor: "#ffffff",
          boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04)",
        }}
      >
        <header className="modal-header" style={{ padding: "16px 20px", borderBottom: "1px solid #e2e8f0" }}>
          <div className="modal-title-group" style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <Icon name="briefcase" className="modal-title-icon" aria-hidden="true" />
            <h2 id="practice-release-modal-title" className="modal-title" style={{ fontSize: "18px", margin: 0, fontWeight: 600 }}>
              Practice Release Management: {candidateName}
            </h2>
          </div>
          <button
            type="button"
            className="btn-icon-close"
            onClick={onClose}
            aria-label="Close modal"
          >
            <Icon name="x" aria-hidden="true" />
          </button>
        </header>

        <div className="modal-body" ref={bodyRef} style={{ padding: "20px", overflowY: "auto", flex: 1 }}>
          <p className="modal-description" style={{ color: "#475569", fontSize: "14px", marginTop: 0, marginBottom: "16px" }}>
            Release an approved, sealed interview practice set to <strong>{candidateName}</strong>. Correct answers, rubrics, and internal notes remain strictly confidential and are never sent to candidate clients.
          </p>

          {error && (
            <div
              className="feedback-box"
              role="alert"
              style={{
                marginBottom: "16px",
                display: "flex",
                alignItems: "flex-start",
                gap: "10px",
                backgroundColor: "#fffbeb",
                border: "1px solid #fde68a",
                color: "#92400e",
                padding: "12px 16px",
                borderRadius: "8px",
              }}
            >
              <Icon name="alert-triangle" aria-hidden="true" style={{ marginTop: "2px", flexShrink: 0 }} />
              <div>
                <strong>Action Error:</strong> {error}
              </div>
            </div>
          )}

          {successMessage && (
            <div
              className="feedback-box success"
              role="status"
              style={{
                marginBottom: "16px",
                backgroundColor: "#ecfdf5",
                border: "1px solid #a7f3d0",
                color: "#065f46",
                padding: "14px 16px",
                borderRadius: "8px",
                display: "flex",
                flexDirection: "column",
                gap: "8px",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 600 }}>
                <Icon name="check" aria-hidden="true" />
                <span>{successMessage}</span>
              </div>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  backgroundColor: "#ffffff",
                  padding: "8px 12px",
                  borderRadius: "6px",
                  border: "1px solid #bbf7d0",
                  fontSize: "12px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <Icon name="link" aria-hidden="true" style={{ color: "#059669" }} />
                  <span style={{ color: "#334155" }}>Candidate Portal Access:</span>
                  <a
                    href="/portal/login"
                    target="_blank"
                    rel="noreferrer"
                    style={{ color: "#059669", fontWeight: 600, textDecoration: "underline" }}
                  >
                    {candidatePortalUrl}
                  </a>
                </div>
                <button
                  type="button"
                  onClick={handleCopyLink}
                  style={{
                    backgroundColor: "#f0fdf4",
                    border: "1px solid #86efac",
                    color: "#166534",
                    padding: "3px 10px",
                    borderRadius: "4px",
                    cursor: "pointer",
                    fontSize: "11px",
                    fontWeight: 600,
                  }}
                >
                  {copiedLink ? "✓ Copied!" : "Copy Link"}
                </button>
              </div>
            </div>
          )}

          {/* Section A: Candidate Requested Opportunities */}
          {pendingRequests.length > 0 && (
            <section
              style={{
                marginBottom: "20px",
                backgroundColor: "#eff6ff",
                border: "1px solid #bfdbfe",
                borderRadius: "8px",
                padding: "14px 16px",
              }}
              aria-label="Pending Candidate Requests"
            >
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
                <span style={{ backgroundColor: "#2563eb", color: "#fff", padding: "2px 8px", borderRadius: "12px", fontSize: "11px", fontWeight: 700 }}>
                  ACTION REQUESTED
                </span>
                <strong style={{ color: "#1e3a8a", fontSize: "14px" }}>
                  Candidate Requested Practice Access ({pendingRequests.length})
                </strong>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                {pendingRequests.map((req) => (
                  <div
                    key={req.request_id}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      backgroundColor: "#ffffff",
                      padding: "10px 14px",
                      borderRadius: "6px",
                      border: "1px solid #dbeafe",
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 600, color: "#1e293b", fontSize: "14px" }}>
                        {req.job_title} <span style={{ color: "#64748b", fontWeight: 500 }}>({req.job_version})</span>
                      </div>
                      <div style={{ fontSize: "12px", color: "#64748b" }}>
                        Requested {new Date(req.requested_at).toLocaleDateString()}
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                      {decliningRequestId === req.request_id ? (
                        <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                          <input
                            type="text"
                            placeholder="Decline reason (optional)"
                            value={declineReason}
                            onChange={(e) => setDeclineReason(e.target.value)}
                            style={{ fontSize: "12px", padding: "4px 8px", borderRadius: "4px", border: "1px solid #cbd5e1" }}
                          />
                          <button
                            type="button"
                            className="btn btn-secondary btn-xs"
                            onClick={() => handleDeclineRequest(req.request_id)}
                            disabled={isProcessing}
                          >
                            Confirm
                          </button>
                          <button
                            type="button"
                            className="btn btn-ghost btn-xs"
                            onClick={() => setDecliningRequestId(null)}
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <>
                          <button
                            type="button"
                            className="btn btn-secondary btn-xs"
                            onClick={() => setDecliningRequestId(req.request_id)}
                            disabled={isProcessing}
                          >
                            Decline
                          </button>
                          <button
                            type="button"
                            className="btn btn-primary btn-xs"
                            onClick={() => {
                              setSelectedJobVersionId(req.job_version_id);
                              handleReleaseSelected();
                            }}
                            disabled={isProcessing}
                          >
                            Release Now
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Section B: Eligible Practice Sets */}
          <section style={{ marginBottom: "24px" }} aria-label="Eligible Practice Opportunities">
            <h3 style={{ fontSize: "15px", fontWeight: 600, color: "#1e293b", marginBottom: "10px" }}>
              Available Practice Sets (Approved & Ready)
            </h3>

            {isLoading ? (
              <div className="loading-state-inline" role="status" style={{ padding: "16px", textAlign: "center" }}>
                <div className="spinner-inline" />
                <span>Scanning requisitions and question sets...</span>
              </div>
            ) : eligibleItems.length === 0 ? (
              <div
                style={{
                  padding: "16px",
                  borderRadius: "6px",
                  backgroundColor: "#f8fafc",
                  border: "1px dashed #cbd5e1",
                  color: "#64748b",
                  fontSize: "13px",
                  textAlign: "center",
                }}
              >
                No approved question sets are currently eligible for release. See diagnostics below for steps to prepare or approve sets.
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                {eligibleItems.map((item) => {
                  const isSelected = selectedJobVersionId === item.job_revision_id;
                  return (
                    <div
                      key={item.job_revision_id}
                      onClick={() => setSelectedJobVersionId(item.job_revision_id)}
                      style={{
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center",
                        padding: "12px 16px",
                        borderRadius: "8px",
                        border: isSelected ? "2px solid #2563eb" : "1px solid #e2e8f0",
                        backgroundColor: isSelected ? "#f0f7ff" : "#ffffff",
                        cursor: "pointer",
                        transition: "all 0.15s ease",
                      }}
                    >
                      <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                        <input
                          type="radio"
                          name="selected_practice_set"
                          checked={isSelected}
                          onChange={() => setSelectedJobVersionId(item.job_revision_id)}
                          style={{ cursor: "pointer" }}
                        />
                        <div>
                          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                            <strong style={{ fontSize: "14px", color: "#0f172a" }}>{item.job_title}</strong>
                            <span style={{ backgroundColor: "#e2e8f0", color: "#334155", padding: "1px 6px", borderRadius: "4px", fontSize: "11px", fontWeight: 600 }}>
                              {item.job_version}
                            </span>
                            {item.has_candidate_request && (
                              <span style={{ backgroundColor: "#dbeafe", color: "#1d4ed8", padding: "1px 6px", borderRadius: "4px", fontSize: "11px", fontWeight: 600 }}>
                                Candidate Requested
                              </span>
                            )}
                          </div>
                          <div style={{ fontSize: "12px", color: "#64748b", marginTop: "2px" }}>
                            {item.conversational_question_count} Conversational Questions · {item.mcq_question_count} MCQs · Approved
                          </div>
                        </div>
                      </div>
                      <div>
                        <span
                          style={{
                            backgroundColor: "#ecfdf5",
                            color: "#065f46",
                            padding: "4px 8px",
                            borderRadius: "6px",
                            fontSize: "12px",
                            fontWeight: 600,
                          }}
                        >
                          Ready to Release
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          {/* Section C: Ineligible Jobs & Diagnostics */}
          {ineligibleItems.length > 0 && (
            <section style={{ marginBottom: "24px" }} aria-label="Ineligible Roles and Action Diagnostics">
              <h3 style={{ fontSize: "15px", fontWeight: 600, color: "#475569", marginBottom: "10px" }}>
                Ineligible Roles & Diagnostic Actions ({ineligibleItems.length})
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {ineligibleItems.map((item, idx) => (
                  <div
                    key={`${item.job_id}-${item.job_version}-${idx}`}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "10px 14px",
                      borderRadius: "6px",
                      backgroundColor: "#f8fafc",
                      border: "1px solid #e2e8f0",
                    }}
                  >
                    <div>
                      <div style={{ fontWeight: 600, fontSize: "13px", color: "#334155" }}>
                        {item.job_title} <span style={{ color: "#64748b", fontWeight: 400 }}>({item.job_version})</span>
                      </div>
                      <div style={{ fontSize: "12px", color: "#64748b", marginTop: "2px" }}>
                        {item.message}
                      </div>
                    </div>

                    <div>
                      {item.action === "PREPARE_PRACTICE_SET" && item.job_revision_id ? (
                        <button
                          type="button"
                          className="btn btn-secondary btn-xs"
                          onClick={() => handlePrepareDraft(item.job_id, item.job_revision_id!)}
                          disabled={preparingVersionId === item.job_revision_id}
                        >
                          <Icon name="refresh" aria-hidden="true" />
                          <span>{preparingVersionId === item.job_revision_id ? "Preparing..." : `Prepare ${item.job_version} Set`}</span>
                        </button>
                      ) : item.action === "OPEN_QUESTION_SET" || item.action === "CREATE_PRACTICE_SET" ? (
                        onNavigateToJob ? (
                          <button
                            type="button"
                            className="btn btn-secondary btn-xs"
                            onClick={() => {
                              onClose();
                              onNavigateToJob(item.job_id);
                            }}
                          >
                            <Icon name="external-link" aria-hidden="true" />
                            <span>Open Requisition</span>
                          </button>
                        ) : null
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* Section D: Practice Release History Table */}
          <section className="invitation-history-section" aria-label="Practice Release History">
            <h3 style={{ fontSize: "15px", fontWeight: 600, color: "#1e293b", marginBottom: "10px" }}>
              Release History
            </h3>

            {releases.length === 0 ? (
              <div className="empty-history-text" style={{ fontSize: "13px", color: "#94a3b8" }}>
                No practice sessions have been released to this candidate yet.
              </div>
            ) : (
              <div className="invitation-table-wrapper">
                <table className="invitation-table" style={{ width: "100%", fontSize: "13px" }}>
                  <thead>
                    <tr>
                      <th scope="col" style={{ textAlign: "left", padding: "8px" }}>Job Title</th>
                      <th scope="col" style={{ textAlign: "left", padding: "8px" }}>Questions</th>
                      <th scope="col" style={{ textAlign: "left", padding: "8px" }}>Released At</th>
                      <th scope="col" style={{ textAlign: "left", padding: "8px" }}>Status</th>
                      <th scope="col" style={{ textAlign: "right", padding: "8px" }}>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {releases.map((rel) => {
                      const isRevocable = rel.status === "RELEASED";
                      return (
                        <tr key={rel.release_id} style={{ borderTop: "1px solid #f1f5f9" }}>
                          <td style={{ fontWeight: 600, padding: "8px" }}>{rel.job_title}</td>
                          <td style={{ padding: "8px" }}>
                            {rel.conversation_count} Conv, {rel.mcq_count} MCQ
                          </td>
                          <td style={{ padding: "8px" }}>{new Date(rel.released_at).toLocaleDateString()}</td>
                          <td style={{ padding: "8px" }}>
                            <span
                              className="badge"
                              style={{
                                backgroundColor: isRevocable ? "#ecfdf5" : "#f1f5f9",
                                color: isRevocable ? "#065f46" : "#64748b",
                                textTransform: "uppercase",
                                fontSize: "11px",
                              }}
                            >
                              {rel.status}
                            </span>
                          </td>
                          <td style={{ textAlign: "right", padding: "8px" }}>
                            {isRevocable ? (
                              <button
                                type="button"
                                className="btn btn-secondary btn-xs"
                                onClick={() => handleRevoke(rel.release_id)}
                                aria-label={`Revoke release for ${rel.job_title}`}
                              >
                                <Icon name="slash" aria-hidden="true" />
                                <span>Revoke</span>
                              </button>
                            ) : (
                              <span className="text-muted">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        </div>

        <footer
          className="modal-footer"
          style={{
            padding: "16px 20px",
            borderTop: "1px solid #e2e8f0",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            backgroundColor: "#f8fafc",
            borderBottomLeftRadius: "8px",
            borderBottomRightRadius: "8px",
          }}
        >
          <div style={{ fontSize: "13px", color: "#64748b", display: "flex", alignItems: "center", gap: "8px" }}>
            {isProcessing ? (
              <span style={{ color: "#2563eb", fontWeight: 600, display: "flex", alignItems: "center", gap: "6px" }}>
                <span className="spinner-inline" /> Processing release...
              </span>
            ) : !selectedItem && eligibleItems.length === 0 ? (
              <span>No eligible set selected</span>
            ) : selectedItem ? (
              <span>Selected: <strong style={{ color: "#0f172a" }}>{selectedItem.job_title} ({selectedItem.job_version})</strong></span>
            ) : null}
          </div>
          <div style={{ display: "flex", gap: "10px" }}>
            <button type="button" className="btn btn-secondary" onClick={onClose} disabled={isProcessing}>
              Close
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleReleaseSelected}
              disabled={isProcessing || !selectedJobVersionId || eligibleItems.length === 0}
            >
              <Icon name="check-circle" aria-hidden="true" />
              <span>{isProcessing ? "Releasing..." : "Release Practice"}</span>
            </button>
          </div>
        </footer>
      </div>
    </div>
  );
}

