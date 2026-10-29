import React, { useEffect, useRef, useState } from "react";
import { Icon } from "../Icon";
import { AiQuestionSetDetail } from "../../types/ai_question_set";
import {
  fetchJobApplicants,
  releaseApplicantInterview,
  RecruiterApplicationSummary,
} from "../../api/recruiterApplications";
import "./ai-interviews.css";

interface ReleaseInterviewSetModalProps {
  isOpen: boolean;
  detail: AiQuestionSetDetail;
  onClose: () => void;
  onSuccessRelease?: (applicationId: string) => void;
  onOpenApplicants?: (jobId: string, jobTitle: string) => void;
}

export function ReleaseInterviewSetModal({
  isOpen,
  detail,
  onClose,
  onSuccessRelease,
  onOpenApplicants,
}: ReleaseInterviewSetModalProps) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const [applicants, setApplicants] = useState<RecruiterApplicationSummary[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [confirmingApp, setConfirmingApp] = useState<RecruiterApplicationSummary | null>(null);
  const [isConfirmingBulkRelease, setIsConfirmingBulkRelease] = useState<boolean>(false);
  const [isReleasing, setIsReleasing] = useState<boolean>(false);
  const [isBulkReleasing, setIsBulkReleasing] = useState<boolean>(false);
  const [releaseActionError, setReleaseActionError] = useState<string | null>(null);
  const [releaseSuccessMsg, setReleaseSuccessMsg] = useState<string | null>(null);

  const isAudioMode = detail.interview_mode === "AUDIO";
  const conversationalCount = (detail.slots || []).filter(
    (s) => s.question_format === "CONVERSATION" || !s.question_format
  ).length;
  const mcqCount = (detail.slots || []).filter(
    (s) => s.question_format === "MCQ_SINGLE_ANSWER"
  ).length;
  const totalCount = isAudioMode ? conversationalCount : conversationalCount + mcqCount;

  // Load applicants for this job filtered by mode
  const loadApplicants = async () => {
    if (!detail.job_id) return;
    try {
      setIsLoading(true);
      setLoadError(null);
      const res = await fetchJobApplicants(detail.job_id, {
        status: "SUBMITTED",
        mode: detail.interview_mode || "TEXT",
        revision: detail.job_revision,
        limit: 100,
      });
      setApplicants(res.items || []);
    } catch (err: any) {
      setLoadError(err?.message || "Failed to load eligible applicants for this job.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    if (isOpen && detail.job_id) {
      setConfirmingApp(null);
      setIsConfirmingBulkRelease(false);
      setReleaseActionError(null);
      setReleaseSuccessMsg(null);
      loadApplicants();
    }
  }, [isOpen, detail.job_id]);

  // Keyboard accessibility
  useEffect(() => {
    if (!isOpen) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        if (confirmingApp) {
          setConfirmingApp(null);
        } else if (isConfirmingBulkRelease) {
          setIsConfirmingBulkRelease(false);
        } else {
          onClose();
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, confirmingApp, isConfirmingBulkRelease, onClose]);

  if (!isOpen) return null;

  const handleConfirmRelease = async () => {
    if (!confirmingApp || !detail.job_id) return;
    const targetApp = confirmingApp;
    try {
      setIsReleasing(true);
      setReleaseActionError(null);
      setReleaseSuccessMsg(null);

      const res = await releaseApplicantInterview(
        detail.job_id,
        targetApp.applicationId,
        detail.ai_set_id
      );

      // Update row in state to Released
      setApplicants((prev) =>
        prev.map((a) =>
          a.applicationId === targetApp.applicationId
            ? { ...a, interviewStatus: "RELEASED", releaseId: res.releaseId }
            : a
        )
      );

      setReleaseSuccessMsg(`Official interview set released successfully to ${targetApp.candidateName}.`);
      setConfirmingApp(null);
      if (onSuccessRelease) {
        onSuccessRelease(targetApp.applicationId);
      }
    } catch (err: any) {
      const errMsg = err?.message || "";
      // Handle idempotent already-released response as success
      if (errMsg.includes("already released") || err?.code === "ALREADY_RELEASED") {
        setApplicants((prev) =>
          prev.map((a) =>
            a.applicationId === targetApp.applicationId
              ? { ...a, interviewStatus: "RELEASED" }
              : a
          )
        );
        setReleaseSuccessMsg(`Interview was already released to ${targetApp.candidateName}.`);
        setConfirmingApp(null);
        if (onSuccessRelease) {
          onSuccessRelease(targetApp.applicationId);
        }
      } else {
        setReleaseActionError(errMsg || "Failed to release interview to candidate.");
      }
    } finally {
      setIsReleasing(false);
    }
  };

  const handleConfirmBulkRelease = async () => {
    if (!detail.job_id || eligibleApplicants.length === 0) return;
    try {
      setIsBulkReleasing(true);
      setReleaseActionError(null);
      setReleaseSuccessMsg(null);

      const toRelease = [...eligibleApplicants];
      let successCount = 0;
      let failCount = 0;
      const successfulAppIds: string[] = [];

      for (const app of toRelease) {
        try {
          const res = await releaseApplicantInterview(
            detail.job_id,
            app.applicationId,
            detail.ai_set_id
          );
          successCount++;
          successfulAppIds.push(app.applicationId);
          setApplicants((prev) =>
            prev.map((a) =>
              a.applicationId === app.applicationId
                ? { ...a, interviewStatus: "RELEASED", releaseId: res.releaseId }
                : a
            )
          );
        } catch (err: any) {
          const errMsg = err?.message || "";
          if (errMsg.includes("already released") || err?.code === "ALREADY_RELEASED") {
            successCount++;
            successfulAppIds.push(app.applicationId);
            setApplicants((prev) =>
              prev.map((a) =>
                a.applicationId === app.applicationId
                  ? { ...a, interviewStatus: "RELEASED" }
                  : a
              )
            );
          } else {
            failCount++;
          }
        }
      }

      setIsConfirmingBulkRelease(false);
      if (successCount > 0) {
        setReleaseSuccessMsg(
          `Official interview set released to ${successCount} candidate${successCount === 1 ? "" : "s"}${
            failCount > 0 ? ` (${failCount} failed)` : ""
          }.`
        );
        if (onSuccessRelease && successfulAppIds.length > 0) {
          onSuccessRelease(successfulAppIds[0]);
        }
      } else if (failCount > 0) {
        setReleaseActionError(`Failed to release interviews to ${failCount} candidates.`);
      }
    } finally {
      setIsBulkReleasing(false);
    }
  };

  const eligibleApplicants = applicants.filter(
    (a) =>
      a.status === "SUBMITTED" &&
      a.jobRevision === detail.job_revision &&
      a.interviewStatus !== "RELEASED" &&
      !a.releaseId
  );

  return (
    <div
      className="aiq-modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-labelledby="release-modal-title"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="aiq-modal-dialog"
        ref={dialogRef}
        style={{ maxWidth: "800px", maxHeight: "90vh", display: "flex", flexDirection: "column" }}
      >
        {/* Modal Header */}
        <div className="aiq-modal-header">
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
              <span
                className="badge badge-success"
                style={{
                  fontSize: "11px",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "4px",
                  backgroundColor: "#ecfdf5",
                  color: "#065f46",
                  border: "1px solid #a7f3d0",
                }}
              >
                <Icon name="lock" size={11} />
                <span>Sealed Question Set</span>
              </span>
              <span
                style={{
                  fontSize: "11px",
                  fontWeight: 600,
                  padding: "2px 8px",
                  borderRadius: "4px",
                  backgroundColor: isAudioMode ? "#f5f3ff" : "#f1f5f9",
                  color: isAudioMode ? "#6d28d9" : "#475569",
                  border: isAudioMode ? "1px solid #ddd6fe" : "1px solid #cbd5e1",
                }}
              >
                {isAudioMode ? "Audio-based interview" : "Text-based interview"}
              </span>
              <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
                Controlled Interview Release
              </span>
            </div>
            <h2 id="release-modal-title" className="aiq-modal-title">
              Release {isAudioMode ? "Audio" : "Text"} Interview Set
            </h2>
          </div>
          <button
            type="button"
            className="aiq-modal-close-btn"
            onClick={onClose}
            aria-label="Close release dialog"
          >
            <Icon name="x" size={18} />
          </button>
        </div>

        {/* Set Metadata Strip */}
        <div
          style={{
            padding: "12px 24px",
            backgroundColor: "var(--surface-subtle, #f8fafc)",
            borderBottom: "1px solid var(--border-subtle, #e2e8f0)",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
            gap: "12px",
            fontSize: "13px",
          }}
        >
          <div>
            <span style={{ color: "var(--text-muted, #64748b)", display: "block", fontSize: "11px", textTransform: "uppercase" }}>Job Title</span>
            <strong style={{ color: "var(--text-primary, #0f172a)" }}>{detail.job_title}</strong>
          </div>
          <div>
            <span style={{ color: "var(--text-muted, #64748b)", display: "block", fontSize: "11px", textTransform: "uppercase" }}>Interview Type</span>
            <strong style={{ color: "var(--text-primary, #0f172a)" }}>{isAudioMode ? "Audio-based" : "Text-based"}</strong>
          </div>
          <div>
            <span style={{ color: "var(--text-muted, #64748b)", display: "block", fontSize: "11px", textTransform: "uppercase" }}>Job Revision</span>
            <strong style={{ color: "var(--text-primary, #0f172a)" }}>Rev #{detail.job_revision}</strong>
          </div>
          <div>
            <span style={{ color: "var(--text-muted, #64748b)", display: "block", fontSize: "11px", textTransform: "uppercase" }}>Question Set</span>
            <strong style={{ color: "var(--text-primary, #0f172a)" }}>Version {detail.set_version}</strong>
          </div>
          <div>
            <span style={{ color: "var(--text-muted, #64748b)", display: "block", fontSize: "11px", textTransform: "uppercase" }}>Total Questions</span>
            <strong style={{ color: "var(--text-primary, #0f172a)" }}>
              {isAudioMode ? `${conversationalCount} Conversational` : `${totalCount} (${conversationalCount} Conv · ${mcqCount} MCQ)`}
            </strong>
          </div>
        </div>

        {/* Feedback / Notifications */}
        {releaseSuccessMsg && (
          <div
            className="feedback-box success"
            style={{ margin: "16px 24px 0", display: "flex", alignItems: "center", gap: "8px", padding: "10px 14px" }}
          >
            <Icon name="check-circle" size={16} />
            <span style={{ fontSize: "13px" }}>{releaseSuccessMsg}</span>
          </div>
        )}

        {releaseActionError && (
          <div
            className="feedback-box error"
            style={{ margin: "16px 24px 0", display: "flex", alignItems: "center", gap: "8px", padding: "10px 14px" }}
          >
            <Icon name="alert-triangle" size={16} />
            <span style={{ fontSize: "13px" }}>{releaseActionError}</span>
          </div>
        )}

        {/* Modal Body */}
        <div className="aiq-modal-body" style={{ padding: "20px 24px", overflowY: "auto", flex: 1 }}>
          {isLoading ? (
            <div style={{ textAlign: "center", padding: "40px 0" }}>
              <div className="loading-spinner" style={{ margin: "0 auto 12px" }} />
              <p style={{ color: "var(--text-secondary, #64748b)", fontSize: "14px", margin: 0 }}>
                Loading eligible applicants for this job revision...
              </p>
            </div>
          ) : loadError ? (
            <div className="feedback-box error" style={{ padding: "16px" }}>
              <Icon name="alert-triangle" size={18} />
              <div>
                <p style={{ margin: 0, fontWeight: 600 }}>{loadError}</p>
                <button
                  type="button"
                  className="btn btn-secondary btn-xs"
                  onClick={loadApplicants}
                  style={{ marginTop: "8px" }}
                >
                  <Icon name="refresh" size={12} />
                  <span>Retry</span>
                </button>
              </div>
            </div>
          ) : applicants.length === 0 ? (
            <div style={{ textAlign: "center", padding: "36px 16px", backgroundColor: "#f8fafc", borderRadius: "8px", border: "1px dashed #cbd5e1" }}>
              <div
                style={{
                  width: "44px",
                  height: "44px",
                  borderRadius: "50%",
                  backgroundColor: "#e2e8f0",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  margin: "0 auto 12px",
                  color: "#64748b",
                }}
              >
                <Icon name="users" size={20} />
              </div>
              <h3 style={{ fontSize: "16px", fontWeight: 700, margin: "0 0 6px 0", color: "#0f172a" }}>
                No Applicants Available
              </h3>
              <p style={{ fontSize: "13px", color: "#64748b", maxWidth: "460px", margin: "0 auto 16px auto", lineHeight: 1.5 }}>
                No applicants have submitted applications for this job requisition yet. Once candidates submit applications, you can release this sealed interview set directly to them.
              </p>
              {onOpenApplicants && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => {
                    onClose();
                    onOpenApplicants(detail.job_id, detail.job_title);
                  }}
                >
                  <Icon name="users" size={14} />
                  <span>View Applicants</span>
                </button>
              )}
            </div>
          ) : (
            <div>
              <div style={{ overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "13px" }}>
                  <thead>
                    <tr style={{ borderBottom: "2px solid #e2e8f0", textAlign: "left", color: "#64748b" }}>
                      <th style={{ padding: "10px 12px", fontWeight: 600 }}>Candidate</th>
                      <th style={{ padding: "10px 12px", fontWeight: 600 }}>Submitted</th>
                      <th style={{ padding: "10px 12px", fontWeight: 600 }}>Review State</th>
                      <th style={{ padding: "10px 12px", fontWeight: 600 }}>Interview State</th>
                      <th style={{ padding: "10px 12px", fontWeight: 600, textAlign: "right" }}>Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {applicants.map((app) => {
                      const isSameRevision = app.jobRevision === detail.job_revision;
                      const isAlreadyReleased = app.interviewStatus === "RELEASED" || Boolean(app.releaseId);
                      const isSubmitted = app.status === "SUBMITTED";
                      const canReleaseApp = isSameRevision && !isAlreadyReleased && isSubmitted;

                      return (
                        <tr
                          key={app.applicationId}
                          style={{
                            borderBottom: "1px solid #f1f5f9",
                            backgroundColor: confirmingApp?.applicationId === app.applicationId ? "#f0f4ff" : "transparent",
                          }}
                        >
                          <td style={{ padding: "12px" }}>
                            <div style={{ fontWeight: 600, color: "#0f172a" }}>{app.candidateName}</div>
                            {app.candidateEmail && (
                              <div style={{ fontSize: "12px", color: "#64748b" }}>{app.candidateEmail}</div>
                            )}
                          </td>
                          <td style={{ padding: "12px", color: "#475569" }}>
                            {app.submittedAt ? new Date(app.submittedAt).toLocaleDateString() : "—"}
                          </td>
                          <td style={{ padding: "12px" }}>
                            <span
                              className={`badge ${
                                app.recruiterReviewState === "INTERVIEW_READY"
                                  ? "badge-success"
                                  : app.recruiterReviewState === "IN_REVIEW"
                                  ? "badge-info"
                                  : "badge-secondary"
                              }`}
                              style={{
                                fontSize: "11px",
                                padding: "2px 8px",
                                borderRadius: "4px",
                                fontWeight: 500,
                              }}
                            >
                              {app.recruiterReviewState === "INTERVIEW_READY"
                                ? "Interview Ready"
                                : app.recruiterReviewState === "IN_REVIEW"
                                ? "In Review"
                                : app.recruiterReviewState || "Received"}
                            </span>
                          </td>
                          <td style={{ padding: "12px" }}>
                            {!isSameRevision ? (
                              <div>
                                <span
                                  className="badge"
                                  style={{
                                    fontSize: "11px",
                                    backgroundColor: "#fef3c7",
                                    color: "#92400e",
                                    border: "1px solid #fde68a",
                                    display: "inline-flex",
                                    alignItems: "center",
                                    gap: "4px",
                                  }}
                                  title={`Application is bound to Job Revision #${app.jobRevision}, but this interview set is for Revision #${detail.job_revision}.`}
                                >
                                  <Icon name="alert-triangle" size={11} />
                                  <span>Different Job revision (Rev #{app.jobRevision})</span>
                                </span>
                              </div>
                            ) : isAlreadyReleased ? (
                              <span
                                className="badge"
                                style={{
                                  fontSize: "11px",
                                  backgroundColor: "#ecfdf5",
                                  color: "#065f46",
                                  border: "1px solid #a7f3d0",
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: "4px",
                                }}
                              >
                                <Icon name="check-circle" size={11} />
                                <span>Released</span>
                              </span>
                            ) : (
                              <span style={{ fontSize: "12px", color: "#64748b" }}>Not released</span>
                            )}
                          </td>
                          <td style={{ padding: "12px", textAlign: "right" }}>
                            {isAlreadyReleased ? (
                              <span style={{ fontSize: "12px", color: "#059669", fontWeight: 500 }}>Active</span>
                            ) : canReleaseApp ? (
                              <button
                                type="button"
                                className="btn btn-primary btn-xs"
                                onClick={() => setConfirmingApp(app)}
                                disabled={isReleasing}
                                title={`Release interview set to ${app.candidateName}`}
                              >
                                <Icon name="user-check" size={12} />
                                <span>Release</span>
                              </button>
                            ) : !isSameRevision ? (
                              <button
                                type="button"
                                className="btn btn-secondary btn-xs"
                                disabled
                                title={`Application was submitted for Job Revision #${app.jobRevision}. This interview set is for Revision #${detail.job_revision}. Generate or seal an interview set for Revision #${app.jobRevision} to release.`}
                              >
                                <span>Release</span>
                              </button>
                            ) : (
                              <button
                                type="button"
                                className="btn btn-secondary btn-xs"
                                disabled
                                title="Application is not in submitted status"
                              >
                                <span>Release</span>
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        {/* Confirmation Modal Sub-dialog */}
        {confirmingApp && (
          <div
            style={{
              position: "fixed",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              backgroundColor: "rgba(15, 23, 42, 0.4)",
              backdropFilter: "blur(2px)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 1100,
              padding: "16px",
            }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-release-title"
          >
            <div
              style={{
                backgroundColor: "var(--surface-base, #ffffff)",
                borderRadius: "10px",
                maxWidth: "480px",
                width: "100%",
                boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.2)",
                padding: "24px",
                border: "1px solid var(--border-subtle, #e2e8f0)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
                <div
                  style={{
                    width: "36px",
                    height: "36px",
                    borderRadius: "50%",
                    backgroundColor: "var(--accent-subtle)",
                    color: "var(--accent-primary)",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  <Icon name="user-check" size={18} />
                </div>
                <h3 id="confirm-release-title" style={{ fontSize: "16px", fontWeight: 700, margin: 0, color: "#0f172a" }}>
                  Confirm Interview Release
                </h3>
              </div>

              <p style={{ fontSize: "14px", color: "#475569", margin: "0 0 16px 0", lineHeight: 1.5 }}>
                You are about to release this official sealed interview set to{" "}
                <strong style={{ color: "#0f172a" }}>{confirmingApp.candidateName}</strong>. The candidate will be able to start and complete the official interview in their candidate portal.
              </p>

              <div
                style={{
                  backgroundColor: "#f8fafc",
                  border: "1px solid #e2e8f0",
                  borderRadius: "6px",
                  padding: "12px 14px",
                  fontSize: "13px",
                  marginBottom: "20px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "6px",
                }}
              >
                <div>
                  <span style={{ color: "#64748b" }}>Candidate:</span>{" "}
                  <strong>{confirmingApp.candidateName}</strong>
                </div>
                <div>
                  <span style={{ color: "#64748b" }}>Job:</span>{" "}
                  <strong>{detail.job_title} (Rev #{detail.job_revision})</strong>
                </div>
                <div>
                  <span style={{ color: "#64748b" }}>Question Set:</span>{" "}
                  <strong>Version {detail.set_version} ({totalCount} questions)</strong>
                </div>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setConfirmingApp(null)}
                  disabled={isReleasing}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={handleConfirmRelease}
                  disabled={isReleasing}
                >
                  <Icon name="user-check" size={14} className={isReleasing ? "animate-spin" : ""} />
                  <span>{isReleasing ? "Releasing..." : "Confirm Release"}</span>
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Bulk Release Confirmation Modal Sub-dialog */}
        {isConfirmingBulkRelease && (
          <div
            style={{
              position: "fixed",
              top: 0,
              left: 0,
              right: 0,
              bottom: 0,
              backgroundColor: "rgba(15, 23, 42, 0.4)",
              backdropFilter: "blur(2px)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              zIndex: 1100,
              padding: "16px",
            }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-bulk-release-title"
          >
            <div
              style={{
                backgroundColor: "var(--surface-base, #ffffff)",
                borderRadius: "10px",
                maxWidth: "500px",
                width: "100%",
                boxShadow: "0 20px 25px -5px rgba(0, 0, 0, 0.2)",
                padding: "24px",
                border: "1px solid var(--border-subtle, #e2e8f0)",
              }}
            >
              <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
                <div
                  style={{
                    width: "36px",
                    height: "36px",
                    borderRadius: "50%",
                    backgroundColor: "#ecfdf5",
                    color: "#065f46",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    flexShrink: 0,
                  }}
                >
                  <Icon name="users" size={18} />
                </div>
                <h3 id="confirm-bulk-release-title" style={{ fontSize: "16px", fontWeight: 700, margin: 0, color: "#0f172a" }}>
                  Release Interview to All Applied Candidates
                </h3>
              </div>

              <p style={{ fontSize: "14px", color: "#475569", margin: "0 0 16px 0", lineHeight: 1.5 }}>
                You are about to release this official sealed {isAudioMode ? "audio" : "text"} interview set to{" "}
                <strong style={{ color: "#0f172a" }}>all {eligibleApplicants.length} eligible applied candidate{eligibleApplicants.length === 1 ? "" : "s"}</strong>.
                Each candidate will receive access in their portal immediately.
              </p>

              <div
                style={{
                  backgroundColor: "#f8fafc",
                  border: "1px solid #e2e8f0",
                  borderRadius: "6px",
                  padding: "12px 14px",
                  fontSize: "13px",
                  marginBottom: "20px",
                  display: "flex",
                  flexDirection: "column",
                  gap: "6px",
                }}
              >
                <div>
                  <span style={{ color: "#64748b" }}>Target Candidates:</span>{" "}
                  <strong>{eligibleApplicants.length} eligible applicant{eligibleApplicants.length === 1 ? "" : "s"}</strong>
                </div>
                <div>
                  <span style={{ color: "#64748b" }}>Job:</span>{" "}
                  <strong>{detail.job_title} (Rev #{detail.job_revision})</strong>
                </div>
                <div>
                  <span style={{ color: "#64748b" }}>Interview Mode:</span>{" "}
                  <strong>{isAudioMode ? "Audio-based interview" : "Text-based interview"}</strong>
                </div>
                <div>
                  <span style={{ color: "#64748b" }}>Question Set:</span>{" "}
                  <strong>Version {detail.set_version} ({totalCount} questions)</strong>
                </div>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setIsConfirmingBulkRelease(false)}
                  disabled={isBulkReleasing}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={handleConfirmBulkRelease}
                  disabled={isBulkReleasing}
                >
                  <Icon name="users" size={14} className={isBulkReleasing ? "animate-spin" : ""} />
                  <span>{isBulkReleasing ? "Releasing to all..." : `Release to all (${eligibleApplicants.length})`}</span>
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Modal Footer */}
        <div
          style={{
            padding: "16px 24px",
            borderTop: "1px solid var(--border-subtle, #e2e8f0)",
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            backgroundColor: "var(--surface-subtle, #f8fafc)",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
              {eligibleApplicants.length} eligible applicant{eligibleApplicants.length === 1 ? "" : "s"} ready for release
            </span>
            {eligibleApplicants.length > 0 && (
              <button
                type="button"
                className="btn btn-primary btn-xs"
                onClick={() => setIsConfirmingBulkRelease(true)}
                disabled={isBulkReleasing || isReleasing}
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "6px",
                  fontWeight: 600,
                }}
              >
                <Icon name="users" size={13} />
                <span>Release to all applied ({eligibleApplicants.length})</span>
              </button>
            )}
          </div>
          <button type="button" className="btn btn-secondary btn-sm" onClick={onClose} disabled={isBulkReleasing || isReleasing}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
