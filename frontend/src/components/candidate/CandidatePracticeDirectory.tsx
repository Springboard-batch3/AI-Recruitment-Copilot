import React, { useEffect, useState } from "react";
import { getCandidatePracticeDiscovery } from "../../api/candidatePractice";
import { CandidatePracticeJobSummary } from "../../types/ai_question_set";
import { Icon } from "../Icon";
import { CandidateInterviewWorkspace } from "./CandidateInterviewWorkspace";

export function CandidatePracticeDirectory() {
  const [practiceJobs, setPracticeJobs] = useState<CandidatePracticeJobSummary[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [activeRelease, setActiveRelease] = useState<CandidatePracticeJobSummary | null>(null);

  const fetchData = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const assignedRes = await getCandidatePracticeDiscovery().catch(() => ({ items: [] }));
      setPracticeJobs(assignedRes.items || []);
    } catch (err: any) {
      setError(err?.message || "Failed to load assigned interviews.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
  }, []);

  if (activeRelease) {
    return (
      <CandidateInterviewWorkspace
        releaseId={activeRelease.release_id}
        initialMode={activeRelease.interview_mode}
        jobTitle={activeRelease.job_title}
        jobRevision={activeRelease.job_revision}
        conversationCount={activeRelease.conversation_question_count ?? activeRelease.conversation_count ?? 0}
        onExit={() => {
          setActiveRelease(null);
          fetchData();
        }}
      />
    );
  }

  return (
    <div className="candidate-home-container" style={{ maxWidth: "960px", margin: "0 auto", padding: "16px 20px" }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "28px" }}>
        <div>
          <h1 style={{ fontSize: "24px", fontWeight: 700, color: "var(--text-primary, #0f172a)", margin: "0 0 8px 0" }}>
            Assigned Interviews
          </h1>
          <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", margin: 0, lineHeight: 1.5 }}>
            View official interviews released by recruiters for your submitted applications.
          </p>
        </div>

        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={fetchData}
          disabled={isLoading}
          title="Refresh assigned interviews"
          style={{ display: "inline-flex", alignItems: "center", gap: "6px", flexShrink: 0 }}
        >
          <Icon name="refresh" size={14} aria-hidden="true" />
          <span>Refresh</span>
        </button>
      </div>

      {error && (
        <div className="feedback-box error" role="alert" style={{ marginBottom: "20px" }}>
          <Icon name="alert-triangle" size={16} aria-hidden="true" />
          <span>{error}</span>
        </div>
      )}

      {isLoading ? (
        <div style={{ textAlign: "center", padding: "60px 20px" }}>
          <div className="loading-spinner" style={{ margin: "0 auto 16px auto" }} />
          <p style={{ color: "var(--text-secondary, #64748b)", fontSize: "14px" }}>
            Loading assigned practice interviews...
          </p>
        </div>
      ) : practiceJobs.length === 0 ? (
        /* Case 0: No Practice Jobs Released Yet */
        <div className="candidate-welcome-card" style={{ marginTop: "20px" }}>
          <div className="candidate-welcome-icon-box">
            <Icon name="message-square" className="welcome-icon" aria-hidden="true" />
          </div>
          <h2 className="candidate-welcome-title">No assigned practice sessions yet</h2>
          <p className="candidate-welcome-description">
            Your recruiter has not released an official interview for your submitted applications yet. Once a recruiter marks your application interview-ready and releases an interview, it will appear here.
          </p>
          <div style={{ display: "flex", justifyContent: "center", gap: "12px", alignItems: "center", flexWrap: "wrap" }}>
            <span
              className="candidate-status-indicator"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: "8px",
                backgroundColor: "#f0fdf4",
                border: "1px solid #bbf7d0",
                color: "#166534",
                padding: "6px 14px",
                borderRadius: "9999px",
                fontSize: "12px",
                fontWeight: 600,
              }}
            >
              <span style={{ width: "8px", height: "8px", borderRadius: "50%", backgroundColor: "#22c55e" }} />
              Candidate Account Active
            </span>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={fetchData}
              style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
            >
              <Icon name="refresh" size={14} aria-hidden="true" />
              <span>Check Again</span>
            </button>
          </div>
        </div>
      ) : practiceJobs.length === 1 ? (
        /* Case 1: Exactly 1 Released Official Interview */
        (() => {
          const singleJob = practiceJobs[0];
          const isAudio = singleJob.interview_mode === "AUDIO";
          const convCount = singleJob.conversation_question_count ?? singleJob.conversation_count ?? 0;
          const mcqCount = isAudio ? 0 : (singleJob.mcq_count ?? 0);
          const revNum = singleJob.job_revision ?? 1;
          const totalQ = isAudio ? convCount : (singleJob.total_question_count ?? (convCount + mcqCount));
          const estMinutes = isAudio ? (convCount * 4 || 15) : ((convCount * 5) + (mcqCount * 2) || 15);

          return (
            <div
              className="aiq-panel"
              style={{
                backgroundColor: "var(--surface-base, #ffffff)",
                border: "1px solid var(--border-subtle, #e2e8f0)",
                borderRadius: "12px",
                padding: "32px",
                boxShadow: "0 2px 8px rgba(0, 0, 0, 0.04)",
                display: "flex",
                flexDirection: "column",
                gap: "24px",
              }}
            >
              {/* Card Header */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "12px" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px" }}>
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 700,
                        textTransform: "uppercase",
                        color: "#2563eb",
                        letterSpacing: "0.5px",
                        backgroundColor: "#eff6ff",
                        padding: "3px 8px",
                        borderRadius: "4px",
                        border: "1px solid #bfdbfe",
                      }}
                    >
                      Official Interview
                    </span>
                    <span
                      style={{
                        fontSize: "11px",
                        fontWeight: 600,
                        padding: "3px 8px",
                        borderRadius: "4px",
                        backgroundColor: isAudio ? "#f5f3ff" : "#f1f5f9",
                        color: isAudio ? "#6d28d9" : "#475569",
                        border: isAudio ? "1px solid #ddd6fe" : "1px solid #cbd5e1",
                      }}
                    >
                      {isAudio ? "Audio-based" : "Text-based"}
                    </span>
                    <span style={{ fontSize: "12px", color: "var(--text-secondary, #64748b)" }}>
                      Job Revision #{revNum}
                    </span>
                  </div>
                  <h2 style={{ fontSize: "22px", fontWeight: 700, color: "var(--text-primary, #0f172a)", margin: 0 }}>
                    {singleJob.job_title}
                  </h2>
                </div>

                {singleJob.is_submitted || singleJob.session_status === "SUBMITTED" ? (
                  <span
                    style={{
                      backgroundColor: "#f8fafc",
                      color: "#475569",
                      fontSize: "12px",
                      fontWeight: 600,
                      padding: "6px 12px",
                      borderRadius: "16px",
                      border: "1px solid #cbd5e1",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "6px",
                    }}
                  >
                    <Icon name="check-circle" size={14} style={{ color: "#16a34a" }} aria-hidden="true" />
                    Interview Submitted
                  </span>
                ) : (
                  <span
                    style={{
                      backgroundColor: "#f0fdf4",
                      color: "#166534",
                      fontSize: "12px",
                      fontWeight: 600,
                      padding: "6px 12px",
                      borderRadius: "16px",
                      border: "1px solid #bbf7d0",
                      display: "inline-flex",
                      alignItems: "center",
                      gap: "6px",
                    }}
                  >
                    <span style={{ width: "8px", height: "8px", borderRadius: "50%", backgroundColor: "#22c55e" }} />
                    Available
                  </span>
                )}
              </div>

              {/* Assessment Breakdown Blocks */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "16px" }}>
                <div
                  style={{
                    backgroundColor: "var(--surface-muted, #f8fafc)",
                    border: "1px solid var(--border-subtle, #e2e8f0)",
                    borderRadius: "8px",
                    padding: "16px 20px",
                  }}
                >
                  <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)", display: "block", textTransform: "uppercase", fontWeight: 700, letterSpacing: "0.5px", marginBottom: "4px" }}>
                    Total Questions
                  </span>
                  <div style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-primary, #0f172a)" }}>
                    {totalQ} Questions
                  </div>
                  <span style={{ fontSize: "12px", color: "var(--text-secondary, #64748b)", marginTop: "2px", display: "block" }}>
                    {isAudio ? `${convCount} conversational` : `${convCount} conversational · ${mcqCount} MCQ`}
                  </span>
                </div>

                <div
                  style={{
                    backgroundColor: "var(--surface-muted, #f8fafc)",
                    border: "1px solid var(--border-subtle, #e2e8f0)",
                    borderRadius: "8px",
                    padding: "16px 20px",
                  }}
                >
                  <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)", display: "block", textTransform: "uppercase", fontWeight: 700, letterSpacing: "0.5px", marginBottom: "4px" }}>
                    Released Date
                  </span>
                  <div style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-primary, #0f172a)" }}>
                    {new Date(singleJob.released_at).toLocaleDateString()}
                  </div>
                  <span style={{ fontSize: "12px", color: "var(--text-secondary, #64748b)", marginTop: "2px", display: "block" }}>
                    Assigned by hiring team
                  </span>
                </div>

                <div
                  style={{
                    backgroundColor: "var(--surface-muted, #f8fafc)",
                    border: "1px solid var(--border-subtle, #e2e8f0)",
                    borderRadius: "8px",
                    padding: "16px 20px",
                  }}
                >
                  <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)", display: "block", textTransform: "uppercase", fontWeight: 700, letterSpacing: "0.5px", marginBottom: "4px" }}>
                    Estimated Duration
                  </span>
                  <div style={{ fontSize: "20px", fontWeight: 700, color: "var(--text-primary, #0f172a)" }}>
                    ~{estMinutes} Minutes
                  </div>
                  <span style={{ fontSize: "12px", color: "var(--text-secondary, #64748b)", marginTop: "2px", display: "block" }}>
                    Official interview stage
                  </span>
                </div>
              </div>

              {/* Guidance Info Box */}
              <div
                style={{
                  padding: "14px 18px",
                  backgroundColor: singleJob.is_submitted || singleJob.session_status === "SUBMITTED" ? "#f0fdf4" : "#eff6ff",
                  borderRadius: "8px",
                  border: singleJob.is_submitted || singleJob.session_status === "SUBMITTED" ? "1px solid #bbf7d0" : "1px solid #bfdbfe",
                  display: "flex",
                  alignItems: "center",
                  gap: "12px",
                }}
              >
                <Icon
                  name={singleJob.is_submitted || singleJob.session_status === "SUBMITTED" ? "check-circle" : "info"}
                  size={18}
                  style={{ color: singleJob.is_submitted || singleJob.session_status === "SUBMITTED" ? "#16a34a" : "#2563eb", flexShrink: 0 }}
                  aria-hidden="true"
                />
                <p style={{ fontSize: "13px", color: singleJob.is_submitted || singleJob.session_status === "SUBMITTED" ? "#166534" : "#1e40af", margin: 0, lineHeight: 1.5 }}>
                  {singleJob.is_submitted || singleJob.session_status === "SUBMITTED"
                    ? "You have already completed and submitted your official interview for this position. Responses are finalized and under review by the hiring team."
                    : `This official interview was released for your submitted application. The hiring team has approved this ${isAudio ? "Audio-based" : "Text-based"} question set for Revision #${revNum}.`}
                </p>
              </div>

              {/* Action Button */}
              <div style={{ display: "flex", justifyContent: "flex-start", paddingTop: "8px" }}>
                {singleJob.is_submitted || singleJob.session_status === "SUBMITTED" ? (
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => setActiveRelease(singleJob)}
                    aria-label="View Submitted Interview"
                    style={{ display: "inline-flex", alignItems: "center", gap: "8px", padding: "10px 20px", fontSize: "14px" }}
                  >
                    <Icon name="check-circle" size={16} aria-hidden="true" />
                    <span>View Submission</span>
                  </button>
                ) : (
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => setActiveRelease(singleJob)}
                    aria-label={isAudio ? "Prepare Interview" : "Start Practice Interview"}
                    style={{ display: "inline-flex", alignItems: "center", gap: "8px", padding: "10px 20px", fontSize: "14px" }}
                  >
                    <span>{isAudio ? "Prepare Interview" : "Start Practice Interview"}</span>
                    <Icon name="chevron-right" size={16} aria-hidden="true" />
                  </button>
                )}
              </div>
            </div>
          );
        })()
      ) : (
        /* Case >1: Multiple Released Official Interviews */
        <div>
          <div
            style={{
              padding: "12px 16px",
              backgroundColor: "var(--surface-muted, #f8fafc)",
              border: "1px solid var(--border-subtle, #e2e8f0)",
              borderRadius: "8px",
              marginBottom: "20px",
              display: "flex",
              alignItems: "center",
              gap: "8px",
              fontSize: "14px",
              color: "var(--text-secondary, #475569)",
            }}
          >
            <Icon name="info" size={16} style={{ color: "#2563eb" }} aria-hidden="true" />
            <span>
              Your recruiters have released <strong>{practiceJobs.length} official interviews</strong> for your submitted applications.
            </span>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(420px, 1fr))", gap: "20px" }}>
            {practiceJobs.map((job) => {
              const isAudio = job.interview_mode === "AUDIO";
              const convCount = job.conversation_question_count ?? job.conversation_count ?? 0;
              const mcqCount = isAudio ? 0 : (job.mcq_count ?? 0);
              const revNum = job.job_revision ?? 1;
              const totalQ = isAudio ? convCount : (job.total_question_count ?? (convCount + mcqCount));
              const estMinutes = isAudio ? (convCount * 4 || 15) : ((convCount * 5) + (mcqCount * 2) || 15);

              return (
                <div
                  key={job.release_id}
                  className="aiq-panel"
                  style={{
                    backgroundColor: "var(--surface-base, #ffffff)",
                    border: "1px solid var(--border-subtle, #e2e8f0)",
                    borderRadius: "10px",
                    padding: "24px",
                    display: "flex",
                    flexDirection: "column",
                    gap: "16px",
                    boxShadow: "0 1px 3px rgba(0, 0, 0, 0.05)",
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "8px" }}>
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "4px" }}>
                        <span style={{ fontSize: "11px", fontWeight: 700, textTransform: "uppercase", color: "#2563eb", letterSpacing: "0.5px" }}>
                          Official Interview
                        </span>
                        <span
                          style={{
                            fontSize: "11px",
                            fontWeight: 600,
                            padding: "2px 6px",
                            borderRadius: "4px",
                            backgroundColor: isAudio ? "#f5f3ff" : "#f1f5f9",
                            color: isAudio ? "#6d28d9" : "#475569",
                            border: isAudio ? "1px solid #ddd6fe" : "1px solid #cbd5e1",
                          }}
                        >
                          {isAudio ? "Audio-based" : "Text-based"}
                        </span>
                        <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)" }}>
                          • Rev #{revNum}
                        </span>
                      </div>
                      <h2 style={{ fontSize: "18px", fontWeight: 700, color: "var(--text-primary, #0f172a)", margin: 0 }}>
                        {job.job_title}
                      </h2>
                    </div>

                    {job.is_submitted || job.session_status === "SUBMITTED" ? (
                      <span
                        style={{
                          backgroundColor: "#f8fafc",
                          color: "#475569",
                          fontSize: "11px",
                          fontWeight: 600,
                          padding: "3px 8px",
                          borderRadius: "12px",
                          border: "1px solid #cbd5e1",
                          whiteSpace: "nowrap",
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "4px",
                        }}
                      >
                        <Icon name="check-circle" size={12} style={{ color: "#16a34a" }} aria-hidden="true" />
                        Submitted
                      </span>
                    ) : (
                      <span
                        style={{
                          backgroundColor: "#f0fdf4",
                          color: "#166534",
                          fontSize: "11px",
                          fontWeight: 600,
                          padding: "3px 8px",
                          borderRadius: "12px",
                          border: "1px solid #bbf7d0",
                          whiteSpace: "nowrap",
                          display: "inline-flex",
                          alignItems: "center",
                          gap: "4px",
                        }}
                      >
                        <span style={{ width: "6px", height: "6px", borderRadius: "50%", backgroundColor: "#22c55e" }} />
                        Available
                      </span>
                    )}
                  </div>

                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
                    <div style={{ backgroundColor: "var(--surface-muted, #f8fafc)", padding: "10px 12px", borderRadius: "6px" }}>
                      <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)", display: "block" }}>Questions</span>
                      <strong style={{ fontSize: "14px", color: "var(--text-primary, #0f172a)" }}>
                        {totalQ} Questions {isAudio ? "(Conv)" : ""}
                      </strong>
                    </div>
                    <div style={{ backgroundColor: "var(--surface-muted, #f8fafc)", padding: "10px 12px", borderRadius: "6px" }}>
                      <span style={{ fontSize: "11px", color: "var(--text-secondary, #64748b)", display: "block" }}>Released</span>
                      <strong style={{ fontSize: "14px", color: "var(--text-primary, #0f172a)" }}>{new Date(job.released_at).toLocaleDateString()}</strong>
                    </div>
                  </div>

                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: "8px", borderTop: "1px solid var(--border-subtle, #f1f5f9)" }}>
                    <span style={{ fontSize: "12px", color: "var(--text-secondary, #64748b)" }}>
                      ~{estMinutes} Mins
                    </span>
                    {job.is_submitted || job.session_status === "SUBMITTED" ? (
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        onClick={() => setActiveRelease(job)}
                        style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}
                      >
                        <Icon name="check-circle" size={14} aria-hidden="true" />
                        <span>View</span>
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        onClick={() => setActiveRelease(job)}
                        style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}
                      >
                        <span>{isAudio ? "Prepare" : "Start"}</span>
                        <Icon name="chevron-right" size={14} aria-hidden="true" />
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

    </div>
  );
}
