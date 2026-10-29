import React, { useEffect, useRef, useState } from "react";
import {
  PracticeMessageDto,
  PracticeSessionDetailResponse,
  abandonPracticeSession,
  getPracticeSessionState,
  retryFinalReview,
  retryQuestionGeneration,
  submitConversationalAnswer,
  submitMcqAnswer,
} from "../../api/candidatePracticeSession";
import { Icon } from "../Icon";
import { CandidatePracticeReport } from "./CandidatePracticeReport";

interface CandidatePracticeRoomProps {
  sessionId: string;
  onExit: () => void;
}

export function CandidatePracticeRoom({ sessionId, onExit }: CandidatePracticeRoomProps) {
  const [session, setSession] = useState<PracticeSessionDetailResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [isRetrying, setIsRetrying] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  // Conversational response state
  const [answerText, setAnswerText] = useState<string>("");
  const [selectedOptionId, setSelectedOptionId] = useState<string>("");

  // Timer countdown
  const [timeRemaining, setTimeRemaining] = useState<number>(0);

  // Auto-scroll ref and textarea ref for focus management
  const chatStreamEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const fetchSessionState = async () => {
    try {
      setIsLoading(true);
      setError(null);
      const data = await getPracticeSessionState(sessionId);
      setSession(data);
      setTimeRemaining(data.time_remaining_seconds || 0);
    } catch (err: any) {
      setError(err?.message || "Failed to load practice session.");
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchSessionState();
  }, [sessionId]);

  // Scroll to bottom when messages or state updates
  useEffect(() => {
    if (chatStreamEndRef.current && typeof chatStreamEndRef.current.scrollIntoView === "function") {
      chatStreamEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [session?.messages, session?.lifecycle_status]);

  // Focus textarea when awaiting answer
  useEffect(() => {
    if (session?.lifecycle_status === "AWAITING_ANSWER" && textareaRef.current) {
      textareaRef.current.focus();
    }
  }, [session?.lifecycle_status, session?.current_conv_index]);

  // Countdown timer effect
  useEffect(() => {
    if (timeRemaining <= 0) return;
    const activeStates = [
      "IN_PROGRESS",
      "QUESTION_GENERATING",
      "AWAITING_ANSWER",
      "ANSWER_SAVING",
      "FINAL_ANSWER_SAVING",
      "FINAL_REVIEW_GENERATING",
    ];
    if (!session || !activeStates.includes(session.lifecycle_status)) return;

    const timer = setInterval(() => {
      setTimeRemaining((prev) => {
        if (prev <= 1) {
          clearInterval(timer);
          fetchSessionState();
          return 0;
        }
        return prev - 1;
      });
    }, 1000);

    return () => clearInterval(timer);
  }, [timeRemaining, session?.lifecycle_status]);

  const formatTimer = (seconds: number) => {
    const hrs = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    const secs = seconds % 60;
    if (hrs > 0) {
      return `${hrs}h ${mins.toString().padStart(2, "0")}m`;
    }
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const handleConversationalSubmit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!session || !session.active_question || isSubmitting) return;

    const trimmed = answerText.trim();
    if (!trimmed) {
      setError("Please enter your answer before submitting.");
      return;
    }

    try {
      setIsSubmitting(true);
      setError(null);
      const updated = await submitConversationalAnswer(
        session.session_id,
        session.active_question.question_id,
        trimmed
      );
      setSession(updated);
      setTimeRemaining(updated.time_remaining_seconds || 0);
      setAnswerText("");
    } catch (err: any) {
      setError(err?.message || "Failed to submit your response.");
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleRetryQuestion = async () => {
    if (!session || isRetrying) return;
    try {
      setIsRetrying(true);
      setError(null);
      const updated = await retryQuestionGeneration(session.session_id);
      setSession(updated);
      setTimeRemaining(updated.time_remaining_seconds || 0);
    } catch (err: any) {
      setError(err?.message || "Retry question generation failed.");
    } finally {
      setIsRetrying(false);
    }
  };

  const handleRetryFinalReview = async () => {
    if (!session || isRetrying) return;
    try {
      setIsRetrying(true);
      setError(null);
      const updated = await retryFinalReview(session.session_id);
      setSession(updated);
      setTimeRemaining(updated.time_remaining_seconds || 0);
    } catch (err: any) {
      setError(err?.message || "Retry final evaluation failed.");
    } finally {
      setIsRetrying(false);
    }
  };

  const handleAbandon = async () => {
    if (!session) return;
    const confirm = window.confirm(
      "Are you sure you want to exit and abandon this practice interview? Incomplete sessions will not be saved."
    );
    if (!confirm) return;

    try {
      setIsLoading(true);
      await abandonPracticeSession(session.session_id);
      onExit();
    } catch (err: any) {
      setError(err?.message || "Failed to abandon session.");
      setIsLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      handleConversationalSubmit();
    }
  };

  if (isLoading && !session) {
    return (
      <div className="candidate-home-container" style={{ padding: "60px 20px" }}>
        <div style={{ textAlign: "center" }}>
          <div className="loading-spinner" style={{ margin: "0 auto 16px auto" }} />
          <p style={{ color: "var(--text-secondary, #64748b)", fontSize: "15px" }}>
            Preparing your practice interview room...
          </p>
        </div>
      </div>
    );
  }

  if (error && !session) {
    return (
      <div className="candidate-home-container" style={{ padding: "40px 20px" }}>
        <div className="feedback-box warning" role="alert" style={{ marginBottom: "20px" }}>
          <Icon name="alert-triangle" size={16} />
          <span>{error}</span>
        </div>
        <button type="button" className="btn btn-secondary" onClick={onExit}>
          Back to Directory
        </button>
      </div>
    );
  }

  if (!session) return null;

  // Calculate Progress Percentage
  const totalQuestions = session.total_conv_count || 1;
  let completedCount = session.current_conv_index;
  if (session.lifecycle_status === "COMPLETED") {
    completedCount = totalQuestions;
  }
  const progressPercent = Math.min(100, Math.round((completedCount / totalQuestions) * 100));

  const isLiveActive =
    session.lifecycle_status === "AWAITING_ANSWER" ||
    session.lifecycle_status === "QUESTION_GENERATING" ||
    session.lifecycle_status === "ANSWER_SAVING" ||
    session.lifecycle_status === "QUESTION_GENERATION_FAILED_RETRYABLE" ||
    session.lifecycle_status === "FINAL_ANSWER_SAVING" ||
    session.lifecycle_status === "FINAL_REVIEW_GENERATING" ||
    session.lifecycle_status === "FINAL_REVIEW_FAILED_RETRYABLE" ||
    session.lifecycle_status === "IN_PROGRESS";

  // When session is completed, render the full dedicated scrollable coaching report
  if (session.lifecycle_status === "COMPLETED") {
    return (
      <div className="practice-report-page-wrapper">
        <CandidatePracticeReport
          sessionId={session.session_id}
          onExit={onExit}
        />
      </div>
    );
  }

  // When session is abandoned or expired, render the standalone status view
  if (session.lifecycle_status === "ABANDONED" || session.lifecycle_status === "EXPIRED") {
    return (
      <div className="practice-room-layout">
        <div className="practice-completed-view">
          <div
            style={{
              width: "56px",
              height: "56px",
              borderRadius: "50%",
              backgroundColor: "#f1f5f9",
              color: "#475569",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
            }}
          >
            <Icon name="alert-circle" size={28} />
          </div>
          <div>
            <h2 style={{ fontSize: "18px", fontWeight: 700, color: "#0f172a", margin: "0 0 8px 0" }}>
              {session.lifecycle_status === "ABANDONED" ? "Session Abandoned" : "Session Expired"}
            </h2>
            <p style={{ fontSize: "14px", color: "#64748b", maxWidth: "440px", margin: "0 auto", lineHeight: 1.5 }}>
              This practice session is closed. You can start a fresh session from your practice directory at any time (up to 3 attempts per 24 hours).
            </p>
          </div>
          <button type="button" className="btn btn-secondary" onClick={onExit}>
            Return to Directory
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="practice-room-layout">
      {/* Live Region for Accessible Announcements */}
      <div className="sr-only" role="status" aria-live="polite">
        {isSubmitting && "Saving your response..."}
        {session.lifecycle_status === "QUESTION_GENERATING" && "Preparing your next question..."}
        {session.lifecycle_status === "FINAL_REVIEW_GENERATING" && "Generating final interview evaluation..."}
        {session.lifecycle_status === "AWAITING_ANSWER" && `Question ${session.current_conv_index + 1} of ${totalQuestions} is ready.`}
      </div>

      {/* Header */}
      <header className="practice-room-header">
        <div className="practice-header-meta">
          <h1 className="practice-header-title">
            <span>Role Practice Interview</span>
            <span
              className="badge"
              style={{
                backgroundColor: "#f0fdf4",
                color: "#166534",
                fontSize: "11px",
                border: "1px solid #bbf7d0",
              }}
            >
              Private Session
            </span>
          </h1>
          <div className="practice-header-subtitle">
            <span>
              Question {Math.min(session.current_conv_index + 1, totalQuestions)} of {totalQuestions}
            </span>
          </div>
        </div>

        <div className="practice-header-actions">
          <div className="practice-timer-badge" title="Session expires in 24 hours">
            <Icon name="clock" size={13} />
            <span>{formatTimer(timeRemaining)}</span>
          </div>

          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={handleAbandon}
            title="Abandon practice session"
          >
            Exit Session
          </button>
        </div>
      </header>

      {/* Progress Strip */}
      <div className="practice-progress-strip">
        <div className="practice-progress-fill" style={{ width: `${progressPercent}%` }} />
      </div>

      {session.safe_fallback_notice && (
        <div
          role="status"
          style={{
            margin: "12px 24px 0 24px",
            padding: "10px 14px",
            backgroundColor: "var(--bg-elevated, #f8fafc)",
            border: "1px solid var(--border-color, #e2e8f0)",
            borderRadius: "6px",
            fontSize: "13px",
            color: "var(--text-secondary, #475569)",
            display: "flex",
            alignItems: "center",
            gap: "8px",
          }}
        >
          <Icon name="info" size={14} />
          <span>{session.safe_fallback_notice}</span>
        </div>
      )}

      {/* Chat Stream Transcript */}
      <div className="practice-chat-stream" role="log" aria-label="Live interview transcript">
        {session.messages.map((msg: PracticeMessageDto) => {
          const isInterviewer = msg.role === "INTERVIEWER";
          const isCandidate = msg.role === "CANDIDATE";

          let kindLabel = "";
          let kindClass = "primary";
          if (msg.message_kind === "GREETING") {
            kindLabel = "Welcome";
            kindClass = "ack";
          } else if (msg.message_kind === "PRIMARY_QUESTION") {
            kindLabel = "AI-generated question";
            kindClass = "primary";
          } else if (msg.message_kind === "ANSWER") {
            kindLabel = "Your Answer";
            kindClass = "answer";
          }

          return (
            <div
              key={msg.message_id}
              className={`practice-message-bubble ${
                isInterviewer ? "interviewer" : isCandidate ? "candidate" : "system"
              }`}
            >
              <div className={`practice-avatar ${isInterviewer ? "interviewer" : "candidate"}`}>
                {isInterviewer ? <Icon name="sparkles" size={16} /> : <Icon name="user" size={16} />}
              </div>

              <div className="practice-bubble-content-card">
                <div className="practice-bubble-header">
                  <span className="practice-bubble-sender">
                    {isInterviewer ? "AI Practice Interviewer" : "You"}
                  </span>
                  {kindLabel && (
                    <span className={`practice-bubble-kind-chip ${kindClass}`}>{kindLabel}</span>
                  )}
                </div>
                <div className="practice-bubble-text">{msg.text}</div>
              </div>
            </div>
          );
        })}

        {/* Live Generation Indicator */}
        {(session.lifecycle_status === "QUESTION_GENERATING" || isSubmitting) && (
          <div className="practice-message-bubble interviewer">
            <div className="practice-avatar interviewer">
              <Icon name="sparkles" size={16} />
            </div>
            <div className="practice-bubble-content-card" style={{ display: "flex", alignItems: "center", gap: "10px", padding: "14px 18px" }}>
              <div className="loading-spinner-sm" />
              <span style={{ fontSize: "14px", color: "var(--text-secondary, #475569)" }}>
                {isSubmitting ? "Saving your response..." : "Preparing your next question…"}
              </span>
            </div>
          </div>
        )}

        {/* Final Review Generating Indicator */}
        {session.lifecycle_status === "FINAL_REVIEW_GENERATING" && (
          <div className="practice-message-bubble interviewer">
            <div className="practice-avatar interviewer">
              <Icon name="sparkles" size={16} />
            </div>
            <div className="practice-bubble-content-card" style={{ display: "flex", alignItems: "center", gap: "10px", padding: "14px 18px" }}>
              <div className="loading-spinner-sm" />
              <span style={{ fontSize: "14px", color: "var(--text-secondary, #475569)" }}>
                Evaluating interview transcript and preparing final coaching report…
              </span>
            </div>
          </div>
        )}

        <div ref={chatStreamEndRef} />
      </div>

      {/* Error banner if submission failed */}
      {error && (
        <div style={{ padding: "0 24px" }}>
          <div className="feedback-box warning" role="alert" style={{ margin: "12px 0 0 0" }}>
            <Icon name="alert-triangle" size={14} />
            <span>{error}</span>
          </div>
        </div>
      )}

      {/* Question Generation Failed Retry Banner */}
      {session.lifecycle_status === "QUESTION_GENERATION_FAILED_RETRYABLE" && (
        <div style={{ padding: "12px 24px" }}>
          <div className="feedback-box warning" role="alert" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Icon name="alert-triangle" size={16} />
              <span>The next question could not be generated.</span>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleRetryQuestion}
              disabled={isRetrying}
            >
              {isRetrying ? "Retrying..." : "Retry Question Generation"}
            </button>
          </div>
        </div>
      )}

      {/* Final Review Failed Retry Banner */}
      {session.lifecycle_status === "FINAL_REVIEW_FAILED_RETRYABLE" && (
        <div style={{ padding: "12px 24px" }}>
          <div className="feedback-box warning" role="alert" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", margin: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Icon name="alert-triangle" size={16} />
              <span>The final evaluation could not be generated.</span>
            </div>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleRetryFinalReview}
              disabled={isRetrying}
            >
              {isRetrying ? "Retrying..." : "Retry Final Review"}
            </button>
          </div>
        </div>
      )}

      {/* Answer Composer Area (Rendered ONLY when Awaiting Answer) */}
      {(session.lifecycle_status === "AWAITING_ANSWER" || session.lifecycle_status === "IN_PROGRESS") && session.active_question && (
        <div className="practice-composer-panel">
          {session.active_question.question_format === "MCQ_SINGLE_ANSWER" ? (
            <div>
              <div style={{ marginBottom: "12px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-secondary, #475569)" }}>
                  Competency: {session.active_question.competency_name}
                </span>
                <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
                  Multiple Choice Question
                </span>
              </div>

              <div style={{ marginBottom: "14px", fontSize: "15px", fontWeight: 500, color: "var(--text-primary, #0f172a)" }}>
                {session.active_question.wording}
              </div>

              <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginBottom: "16px" }}>
                {(session.active_question.options || []).map((opt) => (
                  <label
                    key={opt.option_id}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "10px",
                      padding: "10px 14px",
                      borderRadius: "6px",
                      border: "1px solid var(--border-color, #cbd5e1)",
                      cursor: "pointer",
                      backgroundColor: selectedOptionId === opt.option_id ? "var(--bg-elevated, #f1f5f9)" : "transparent",
                    }}
                  >
                    <input
                      type="radio"
                      name="mcq-option"
                      value={opt.option_id}
                      checked={selectedOptionId === opt.option_id}
                      onChange={() => setSelectedOptionId(opt.option_id)}
                      disabled={isSubmitting}
                    />
                    <span style={{ fontSize: "14px" }}>{opt.text}</span>
                  </label>
                ))}
              </div>

              <div className="practice-composer-footer">
                <span />
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={isSubmitting || !selectedOptionId}
                  onClick={async () => {
                    if (!session || !session.active_question || !selectedOptionId) return;
                    try {
                      setIsSubmitting(true);
                      setError(null);
                      const updated = await submitMcqAnswer(
                        session.session_id,
                        session.active_question.question_id,
                        selectedOptionId
                      );
                      setSession(updated);
                      setSelectedOptionId("");
                    } catch (err: any) {
                      setError(err?.message || "Failed to submit MCQ answer.");
                    } finally {
                      setIsSubmitting(false);
                    }
                  }}
                >
                  {isSubmitting ? (
                    <>
                      <div className="loading-spinner-sm" style={{ marginRight: "6px" }} />
                      <span>Saving...</span>
                    </>
                  ) : (
                    <>
                      <span>Submit Answer & Continue</span>
                      <Icon name="chevron-right" size={14} />
                    </>
                  )}
                </button>
              </div>
            </div>
          ) : (
            <form onSubmit={handleConversationalSubmit}>
              <div style={{ marginBottom: "8px", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: "13px", fontWeight: 600, color: "var(--text-secondary, #475569)" }}>
                  Competency: {session.active_question.competency_name}
                </span>
                <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
                  Question {session.current_conv_index + 1} of {totalQuestions}
                </span>
              </div>

              <textarea
                ref={textareaRef}
                className="practice-composer-textarea"
                placeholder="Type your structured response here... (Press Ctrl+Enter or Cmd+Enter to submit)"
                value={answerText}
                onChange={(e) => setAnswerText(e.target.value.slice(0, 2000))}
                onKeyDown={handleKeyDown}
                disabled={isSubmitting}
                rows={4}
                aria-label="Your response"
              />

              <div className="practice-composer-footer">
                <span>{answerText.length} / 2000 characters</span>

                <button
                  type="submit"
                  className="btn btn-primary"
                  disabled={isSubmitting || answerText.trim().length === 0}
                >
                  {isSubmitting ? (
                    <>
                      <div className="loading-spinner-sm" style={{ marginRight: "6px" }} />
                      <span>Saving response...</span>
                    </>
                  ) : (
                    <>
                      <span>Submit Response</span>
                      <Icon name="chevron-right" size={14} />
                    </>
                  )}
                </button>
              </div>
            </form>
          )}
        </div>
      )}

    </div>
  );
}
