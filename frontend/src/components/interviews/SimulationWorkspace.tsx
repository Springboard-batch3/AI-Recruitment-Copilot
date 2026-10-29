import React, { useEffect, useState, useCallback, useRef } from "react";
import { apiFetch } from "../../api/client";
import { Icon } from "../Icon";
import {
  SimulationAttemptResponse,
  SimulationAnswerItem,
  SimulationAnswerSaveResponse,
  SimulationSubmitResponse,
} from "../../types/interview";
import { formatCompetencyName } from "./InterviewWorkspace";
import { InterviewReviewWorkspace } from "./InterviewReviewWorkspace";

interface SimulationWorkspaceProps {
  planId: string;
  jobId: string;
  onNavigateBack: () => void;
}

type AutosaveState = "SAVED" | "SAVING" | "ERROR" | "IDLE";

export function SimulationWorkspace({
  planId,
  jobId,
  onNavigateBack,
}: SimulationWorkspaceProps) {
  const [attempt, setAttempt] = useState<SimulationAttemptResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Active question navigation
  const [activeQuestionIndex, setActiveQuestionIndex] = useState<number>(0);

  // Local draft answers map: question_id -> text
  const [answersMap, setAnswersMap] = useState<Record<string, string>>({});
  // Versions map: question_id -> answer_version
  const [versionsMap, setVersionsMap] = useState<Record<string, number>>({});

  // Autosave status per question
  const [autosaveStatus, setAutosaveStatus] = useState<AutosaveState>("IDLE");
  const [autosaveMessage, setAutosaveMessage] = useState<string>("All changes saved");
  const [lastSavedTimestamp, setLastSavedTimestamp] = useState<string | null>(null);

  // Submission confirmation modal
  const [showSubmitModal, setShowSubmitModal] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Paused session state
  const [isPaused, setIsPaused] = useState<boolean>(false);

  // Phase 3D.2: Provisional AI Assessment State
  const [isAssessing, setIsAssessing] = useState<boolean>(false);
  const [assessmentError, setAssessmentError] = useState<string | null>(null);
  const [assessmentSuccess, setAssessmentSuccess] = useState<string | null>(null);

  // Phase 3D.3: Human Review Workspace mode
  const [activeMode, setActiveMode] = useState<"SIMULATION" | "REVIEW">("SIMULATION");

  // Debounce ref for autosave
  const autosaveTimerRef = useRef<NodeJS.Timeout | null>(null);
  const dirtyQuestionIdRef = useRef<string | null>(null);

  // Initial load / resume simulation
  useEffect(() => {
    let isMounted = true;
    const startOrResume = async () => {
      try {
        setIsLoading(true);
        setError(null);
        const res = await apiFetch<SimulationAttemptResponse>(
          "/api/v1/interviews/simulations/start",
          {
            method: "POST",
            body: JSON.stringify({ plan_id: planId }),
          }
        );
        if (isMounted) {
          setAttempt(res);
          // Populate answers and versions
          const aMap: Record<string, string> = {};
          const vMap: Record<string, number> = {};
          Object.values(res.answers || {}).forEach((ans: SimulationAnswerItem) => {
            aMap[ans.question_id] = ans.answer_text;
            vMap[ans.question_id] = ans.answer_version;
          });
          setAnswersMap(aMap);
          setVersionsMap(vMap);
          if (
            res.lifecycle_status === "PROVISIONALLY_ASSESSED" ||
            res.lifecycle_status === "FINALIZED" ||
            (res as any).lifecycle_status === "HUMAN_REVIEWED"
          ) {
            setActiveMode("REVIEW");
          }
        }
      } catch (err: any) {
        if (isMounted) {
          setError(
            err?.message ||
              "Failed to initialize simulation. Verify that the interview plan is approved and active."
          );
        }
      } finally {
        if (isMounted) setIsLoading(false);
      }
    };

    startOrResume();

    return () => {
      isMounted = false;
      if (autosaveTimerRef.current) clearTimeout(autosaveTimerRef.current);
    };
  }, [planId]);

  // Combine questions in authoritative display order
  const allQuestions = React.useMemo(() => {
    if (!attempt) return [];
    if (attempt.questions && attempt.questions.length > 0) {
      return attempt.questions;
    }
    const anyAttempt = attempt as any;
    return [...(anyAttempt.common_core_questions || []), ...(anyAttempt.supplemental_questions || [])];
  }, [attempt]);

  const activeQuestion = allQuestions[activeQuestionIndex] || null;
  const isSubmitted = attempt?.lifecycle_status === "SUBMITTED";

  // Calculate answered progress
  const answeredCount = React.useMemo(() => {
    if (!allQuestions.length) return 0;
    return allQuestions.filter((q) => {
      const text = answersMap[q.question_id];
      return text && text.trim().length > 0;
    }).length;
  }, [allQuestions, answersMap]);

  // Perform optimistic concurrency save
  const performSave = React.useCallback(
    async (questionId: string, answerText: string) => {
      if (!attempt || isSubmitted || isPaused) return;

      const expectedVersion = versionsMap[questionId] || 1;

      try {
        setAutosaveStatus("SAVING");
        setAutosaveMessage("Saving draft…");

        const res = await apiFetch<SimulationAnswerSaveResponse>(
          `/api/v1/interviews/simulations/${attempt.attempt_id}/answers/${questionId}`,
          {
            method: "POST",
            body: JSON.stringify({
              answer_text: answerText,
              expected_answer_version: expectedVersion,
            }),
          }
        );

        const nextVersion = res?.answer?.answer_version ?? (res as any)?.answer_version ?? (expectedVersion + 1);
        setVersionsMap((prev) => ({
          ...prev,
          [questionId]: nextVersion,
        }));
        setAutosaveStatus("SAVED");
        const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        setLastSavedTimestamp(now);
        setAutosaveMessage(`Draft saved at ${now}`);
      } catch (err: any) {
        setAutosaveStatus("ERROR");
        setAutosaveMessage("Autosave failed. Retrying on next edit.");
      }
    },
    [attempt, isSubmitted, isPaused, versionsMap]
  );

  // Handle answer textarea input with debounced autosave
  const handleAnswerChange = (questionId: string, newText: string) => {
    if (isSubmitted || isPaused) return;

    setAnswersMap((prev) => ({
      ...prev,
      [questionId]: newText,
    }));

    dirtyQuestionIdRef.current = questionId;

    if (autosaveTimerRef.current) {
      clearTimeout(autosaveTimerRef.current);
    }

    setAutosaveStatus("SAVING");
    setAutosaveMessage("Unsaved changes…");

    autosaveTimerRef.current = setTimeout(() => {
      performSave(questionId, newText);
    }, 1000);
  };

  // Immediate save when changing question or pausing
  const flushPendingSave = () => {
    if (autosaveTimerRef.current && dirtyQuestionIdRef.current) {
      clearTimeout(autosaveTimerRef.current);
      const qId = dirtyQuestionIdRef.current;
      performSave(qId, answersMap[qId] || "");
      dirtyQuestionIdRef.current = null;
    }
  };

  const handleSelectQuestion = (index: number) => {
    flushPendingSave();
    setActiveQuestionIndex(index);
  };

  const handleNextQuestion = () => {
    if (activeQuestionIndex < allQuestions.length - 1) {
      handleSelectQuestion(activeQuestionIndex + 1);
    }
  };

  const handlePrevQuestion = () => {
    if (activeQuestionIndex > 0) {
      handleSelectQuestion(activeQuestionIndex - 1);
    }
  };

  const handleTogglePause = () => {
    flushPendingSave();
    setIsPaused((prev) => !prev);
  };

  // Submit attempt
  const handleConfirmSubmit = async () => {
    if (!attempt || isSubmitted) return;

    try {
      setIsSubmitting(true);
      setSubmitError(null);
      flushPendingSave();

      const res = await apiFetch<SimulationSubmitResponse>(
        `/api/v1/interviews/simulations/${attempt.attempt_id}/submit`,
        {
          method: "POST",
          body: JSON.stringify({
            expected_record_version: attempt.record_version,
          }),
        }
      );

      setAttempt((prev) =>
        prev
          ? {
              ...prev,
              lifecycle_status: res.lifecycle_status as any,
              submitted_at: res.submitted_at,
            }
          : null
      );
      setShowSubmitModal(false);
    } catch (err: any) {
      setSubmitError(err?.message || "Failed to submit interview simulation.");
    } finally {
      setIsSubmitting(false);
    }
  };

  // Phase 3D.2: Run Bounded Provisional Assessment
  const handleRunProvisionalAssessment = async () => {
    if (!attempt || !isSubmitted) return;

    try {
      setIsAssessing(true);
      setAssessmentError(null);
      setAssessmentSuccess(null);

      const endpoint = attempt.template_id
        ? `/api/v1/jobs/${jobId}/interview-templates/${attempt.template_id}/candidate-plans/${planId}/simulation/${attempt.attempt_id}/assess`
        : `/api/v1/interviews/simulations/${attempt.attempt_id}/assess`;

      const res = await apiFetch<any>(endpoint, {
        method: "POST",
      });

      // Update attempt with new assessments and lifecycle status
      setAttempt((prev) => {
        if (!prev) return null;
        const newAssessments: Record<string, any> = { ...(prev.assessments || {}) };
        if (res.items && Array.isArray(res.items)) {
          res.items.forEach((item: any) => {
            newAssessments[item.question_id] = item;
          });
        }
        return {
          ...prev,
          lifecycle_status: (res.lifecycle_status || "PROVISIONALLY_ASSESSED") as any,
          assessments: newAssessments,
        };
      });

      setAssessmentSuccess(
        `Provisional AI assessment complete. ${res.successful_count || 0} evaluated, ${res.insufficient_evidence_count || 0} insufficient evidence, ${res.validation_failure_count || 0} manual review required.`
      );
      setActiveMode("REVIEW");
    } catch (err: any) {
      setAssessmentError(err?.message || "Failed to complete provisional AI assessment.");
    } finally {
      setIsAssessing(false);
    }
  };

  if (isLoading) {
    return (
      <div className="simulation-loading p-12 text-center" role="status" aria-live="polite">
        <div className="loading-spinner mb-4" />
        <p className="text-sm text-slate-600 font-medium">
          Loading authorized recruiter simulation workspace…
        </p>
      </div>
    );
  }

  if (error || !attempt) {
    return (
      <div className="simulation-error p-8 max-w-2xl mx-auto my-12 bg-white border border-slate-200 rounded-lg shadow-sm">
        <div className="flex items-center gap-3 text-slate-800 mb-3">
          <Icon name="alert-circle" size={24} />
          <h2 className="text-lg font-semibold m-0">Unable to Open Simulation</h2>
        </div>
        <p className="text-sm text-slate-600 mb-6">{error}</p>
        <button
          type="button"
          className="btn btn-secondary text-sm"
          onClick={onNavigateBack}
        >
          <Icon name="arrow-left" size={16} />
          <span>Return to Interview Plan</span>
        </button>
      </div>
    );
  }

  const currentAnswer = activeQuestion ? answersMap[activeQuestion.question_id] || "" : "";
  const wordCount = currentAnswer.trim() ? currentAnswer.trim().split(/\s+/).length : 0;
  const isCurrentSupplemental = activeQuestion?.layer === "SUPPLEMENTAL_EVIDENCE";

  return (
    <div className="simulation-workspace max-w-5xl mx-auto px-4 py-6 space-y-6">
      {/* Top Header Bar */}
      <div className="simulation-top-bar flex flex-col md:flex-row md:items-center md:justify-between gap-4 border-b border-slate-200 pb-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <button
              type="button"
              className="btn btn-ghost btn-sm text-slate-600 hover:text-slate-900"
              onClick={() => {
                flushPendingSave();
                onNavigateBack();
              }}
              title="Return to Interview Plan"
              aria-label="Return to Interview Plan"
            >
              <Icon name="arrow-left" size={16} />
              <span>Back to Plan</span>
            </button>
            <span className="text-slate-300">|</span>
            <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">
              Recruiter Text Simulation
            </span>
          </div>
          <h1 className="text-xl font-bold text-slate-900 m-0">
            {attempt.candidate_name} &bull; {attempt.job_title}
          </h1>
          <p className="text-xs text-slate-500 mt-0.5">
            Plan v{attempt.plan_version} &bull; Template Hash:{" "}
            <code>{attempt.authoritative_content_hash ? attempt.authoritative_content_hash.substring(0, 8) : "N/A"}</code>
          </p>
        </div>

        {/* Action and Lifecycle Status */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Phase 3D.3: Mode Switcher when simulation has assessments or is submitted */}
          {(isSubmitted ||
            attempt.lifecycle_status === "PROVISIONALLY_ASSESSED" ||
            attempt.lifecycle_status === "FINALIZED" ||
            (attempt as any).lifecycle_status === "HUMAN_REVIEWED" ||
            (attempt.assessments && Object.keys(attempt.assessments).length > 0)) && (
            <div className="flex items-center gap-1 bg-slate-100 p-0.5 rounded border border-slate-200 text-xs">
              <button
                type="button"
                className={`px-3 py-1.5 rounded font-medium transition-colors ${
                  activeMode === "SIMULATION"
                    ? "bg-white text-slate-900 shadow-xs border border-slate-200"
                    : "text-slate-600 hover:text-slate-900"
                }`}
                onClick={() => setActiveMode("SIMULATION")}
              >
                Simulation
              </button>
              <button
                type="button"
                className={`px-3 py-1.5 rounded font-medium transition-colors ${
                  activeMode === "REVIEW"
                    ? "bg-white text-slate-900 shadow-xs border border-slate-200"
                    : "text-slate-600 hover:text-slate-900"
                }`}
                onClick={() => setActiveMode("REVIEW")}
              >
                Human Review Workspace
              </button>
            </div>
          )}

          {/* Pause / Resume button */}
          {!isSubmitted && (
            <button
              type="button"
              className={`btn btn-sm ${isPaused ? "btn-primary" : "btn-secondary"}`}
              onClick={handleTogglePause}
              title={isPaused ? "Resume simulation session" : "Pause simulation session"}
            >
              <Icon name={isPaused ? "play" : "pause"} size={14} />
              <span>{isPaused ? "Resume Simulation" : "Pause Session"}</span>
            </button>
          )}

          {/* Submit / Assess Action */}
          {!isSubmitted ? (
            <button
              type="button"
              className="btn btn-primary btn-sm flex items-center gap-1.5"
              onClick={() => {
                flushPendingSave();
                setShowSubmitModal(true);
              }}
            >
              <Icon name="check" size={14} />
              <span>Submit &amp; Lock Simulation</span>
            </button>
          ) : (
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1.5 px-3 py-1.5 bg-slate-100 text-slate-800 rounded text-xs font-semibold border border-slate-300">
                <Icon name="lock" size={14} />
                <span>Simulation Submitted &amp; Locked</span>
              </div>
              <button
                type="button"
                className="btn btn-primary btn-sm flex items-center gap-1.5"
                onClick={handleRunProvisionalAssessment}
                disabled={isAssessing}
              >
                <Icon name="sparkles" size={14} />
                <span>{isAssessing ? "Evaluating with AI…" : "Assess submitted answers"}</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Assessment Feedback Alerts */}
      {assessmentError && (
        <div className="feedback-box error p-3 bg-slate-100 border border-slate-300 rounded text-xs text-slate-800 flex items-center gap-2" role="alert">
          <Icon name="alert-triangle" size={16} />
          <span>{assessmentError}</span>
        </div>
      )}
      {assessmentSuccess && (
        <div className="feedback-box success p-3 bg-slate-50 border border-slate-300 rounded text-xs text-slate-800 flex items-center gap-2" role="status">
          <Icon name="check-circle" size={16} />
          <span>{assessmentSuccess}</span>
        </div>
      )}

      {/* Phase 3D.3: Either render Review Workspace or Simulation Workspace */}
      {activeMode === "REVIEW" ? (
        <InterviewReviewWorkspace
          attemptId={attempt.attempt_id}
          onNavigateBack={onNavigateBack}
          onFinalized={() => {
            setAttempt((prev) => (prev ? { ...prev, lifecycle_status: "FINALIZED" as any } : null));
          }}
        />
      ) : (
        <>
          {/* Paused Banner */}
      {isPaused && (
        <div
          className="p-4 bg-amber-50 border border-amber-200 rounded-lg flex items-center justify-between text-amber-900 text-sm"
          role="status"
          aria-live="polite"
        >
          <div className="flex items-center gap-2">
            <Icon name="info" size={18} />
            <span>
              <strong>Simulation Paused:</strong> Draft answers are preserved safely. Click Resume when ready to continue.
            </span>
          </div>
          <button
            type="button"
            className="btn btn-primary btn-xs"
            onClick={handleTogglePause}
          >
            Resume Now
          </button>
        </div>
      )}

      {/* Submitted Read-Only Banner */}
      {isSubmitted && (
        <div
          className="p-4 bg-slate-50 border border-slate-300 rounded-lg flex items-center gap-3 text-slate-800 text-sm"
          role="status"
        >
          <Icon name="check-circle" size={20} />
          <div>
            <strong>Submitted on {attempt.submitted_at ? new Date(attempt.submitted_at).toLocaleString() : "Record"}:</strong>
            <p className="text-xs text-slate-600 m-0">
              Answers are permanently recorded and read-only. Assessment review will be conducted on this locked version.
            </p>
          </div>
        </div>
      )}

      {/* Progress & Question Index Navigator */}
      <div className="question-navigator-card bg-white border border-slate-200 rounded-lg p-4 space-y-3">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs">
          <div className="font-semibold text-slate-800">
            Question Progress: {answeredCount} of {allQuestions.length} answered
          </div>
          {/* Polite ARIA Live Region for Autosave */}
          <div
            className="text-xs text-slate-500 flex items-center gap-1.5"
            role="status"
            aria-live="polite"
          >
            {autosaveStatus === "SAVING" && (
              <span className="inline-block w-2 h-2 rounded-full bg-amber-500 animate-pulse" />
            )}
            {autosaveStatus === "SAVED" && (
              <span className="inline-block w-2 h-2 rounded-full bg-slate-400" />
            )}
            <span>{autosaveMessage}</span>
          </div>
        </div>

        {/* Progress Bar */}
        <div className="w-full bg-slate-100 rounded-full h-1.5 overflow-hidden">
          <div
            className="bg-slate-700 h-1.5 rounded-full transition-all duration-300"
            style={{
              width: `${allQuestions.length ? (answeredCount / allQuestions.length) * 100 : 0}%`,
            }}
          />
        </div>

        {/* Question Selector Tabs */}
        <div className="flex flex-wrap gap-2 pt-1">
          {allQuestions.map((q, idx) => {
            const hasAns = answersMap[q.question_id]?.trim()?.length > 0;
            const isCur = idx === activeQuestionIndex;
            const isSupp = q.layer === "SUPPLEMENTAL_EVIDENCE";

            return (
              <button
                key={q.question_id}
                type="button"
                className={`px-3 py-1.5 text-xs font-medium rounded border transition-colors flex items-center gap-1.5 ${
                  isCur
                    ? "bg-slate-900 text-white border-slate-900 shadow-sm"
                    : hasAns
                    ? "bg-slate-50 text-slate-800 border-slate-300 hover:bg-slate-100"
                    : "bg-white text-slate-500 border-slate-200 hover:bg-slate-50"
                }`}
                onClick={() => handleSelectQuestion(idx)}
                aria-current={isCur ? "true" : undefined}
                aria-label={`Go to Question ${idx + 1}: ${formatCompetencyName(q.competency_name)}`}
              >
                <span>Q{idx + 1}</span>
                {isSupp && <span className="text-[10px] opacity-75">(Supp)</span>}
                {hasAns && <Icon name="check" size={10} />}
              </button>
            );
          })}
        </div>
      </div>

      {/* Active Question Panel */}
      {activeQuestion ? (
        <div className="question-panel bg-white border border-slate-200 rounded-lg p-6 space-y-6 shadow-sm">
          {/* Question Header */}
          <div className="border-b border-slate-100 pb-4 space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <span className="badge badge-primary text-xs font-bold px-2 py-0.5">
                  Question {activeQuestionIndex + 1} of {allQuestions.length}
                </span>
                <span className="badge badge-outline text-xs font-semibold">
                  {formatCompetencyName(activeQuestion.competency_name || (activeQuestion as any).competency_id)}
                </span>
                <span className="badge badge-outline text-xs">{activeQuestion.question_type}</span>
                {isCurrentSupplemental && (
                  <span className="badge badge-secondary text-xs">
                    Supplemental Evidence Clarification
                  </span>
                )}
              </div>
              <div className="text-xs text-slate-500">
                Duration: <strong>{activeQuestion.estimated_time_minutes || 5} min</strong>
              </div>
            </div>

            {/* Prompt Text */}
            <h2 className="text-base font-semibold text-slate-900 pt-1 leading-relaxed">
              {activeQuestion.question_text}
            </h2>

            {/* Permitted Probes */}
            {activeQuestion.permitted_probes && activeQuestion.permitted_probes.length > 0 && (
              <div className="mt-3 p-3 bg-slate-50 border border-slate-200 rounded text-xs space-y-1">
                <span className="font-semibold text-slate-700 block">Permitted Follow-Up Probes:</span>
                <ul className="list-disc list-inside space-y-0.5 text-slate-600">
                  {activeQuestion.permitted_probes.map((probe: string, pIdx: number) => (
                    <li key={pIdx}>{probe}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          {/* Answer Input Area */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <label
                htmlFor={`answer-${activeQuestion.question_id}`}
                className="text-xs font-semibold uppercase tracking-wider text-slate-700"
              >
                Candidate Response / Recruiter Notes
              </label>
              <span className="text-xs text-slate-500">
                {wordCount} {wordCount === 1 ? "word" : "words"} &bull; {currentAnswer.length} chars
              </span>
            </div>

            <textarea
              id={`answer-${activeQuestion.question_id}`}
              className="w-full min-h-[220px] p-3 text-sm border border-slate-300 rounded focus:ring-1 focus:ring-slate-800 focus:border-slate-800 transition bg-white text-slate-900 disabled:bg-slate-50 disabled:text-slate-600"
              placeholder={
                isSubmitted
                  ? "No answer recorded."
                  : isPaused
                  ? "Simulation is paused. Click Resume to edit."
                  : "Type candidate response, transcript notes, or simulated answer details…"
              }
              value={currentAnswer}
              onChange={(e) => handleAnswerChange(activeQuestion.question_id, e.target.value)}
              disabled={isSubmitted || isPaused}
              rows={8}
            />
          </div>

          {/* Phase 3D.2: Provisional AI Assessment Block */}
          {isSubmitted && (
            <div className="provisional-assessment-container border border-slate-200 rounded-lg p-4 bg-slate-50 space-y-3">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Icon name="sparkles" size={16} className="text-slate-700" />
                  <h3 className="text-xs font-bold uppercase tracking-wider text-slate-800 m-0">
                    Provisional AI Assessment
                  </h3>
                </div>
                {attempt.assessments && attempt.assessments[activeQuestion.question_id] ? (
                  <span
                    className={`badge text-xs font-semibold ${
                      attempt.assessments[activeQuestion.question_id].assessment_status === "PROVISIONAL"
                        ? "badge-success"
                        : attempt.assessments[activeQuestion.question_id].assessment_status === "INSUFFICIENT_EVIDENCE"
                        ? "badge-warning"
                        : "badge-outline text-slate-600"
                    }`}
                  >
                    {attempt.assessments[activeQuestion.question_id].assessment_status === "PROVISIONAL"
                      ? `Level: ${attempt.assessments[activeQuestion.question_id].provisional_level}`
                      : attempt.assessments[activeQuestion.question_id].assessment_status === "INSUFFICIENT_EVIDENCE"
                      ? "Insufficient Evidence"
                      : "Manual Review Required"}
                  </span>
                ) : (
                  <span className="text-xs text-slate-500">Not assessed yet</span>
                )}
              </div>

              {attempt.assessments && attempt.assessments[activeQuestion.question_id] ? (
                (() => {
                  const qAssess = attempt.assessments[activeQuestion.question_id];
                  return (
                    <div className="space-y-3 pt-2 text-xs text-slate-700">
                      {qAssess.assessment_status === "PROVISIONAL" && (
                        <>
                          {/* Criterion observations */}
                          {qAssess.criterion_observations && qAssess.criterion_observations.length > 0 && (
                            <div className="space-y-2">
                              <span className="font-semibold text-slate-800 block">Criterion Observations:</span>
                              <div className="space-y-1.5">
                                {qAssess.criterion_observations.map((obs: any, oIdx: number) => (
                                  <div key={oIdx} className="p-2.5 bg-white border border-slate-200 rounded text-slate-800">
                                    <div className="flex items-center justify-between mb-1">
                                      <span className="font-semibold text-slate-900">
                                        {formatCompetencyName(obs.criterion_id)}
                                      </span>
                                      {obs.cited_segment_ids && obs.cited_segment_ids.length > 0 && (
                                        <span className="text-[10px] text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded font-mono">
                                          Citations: {obs.cited_segment_ids.length}
                                        </span>
                                      )}
                                    </div>
                                    <p className="m-0 leading-relaxed text-slate-700">{obs.observation_text}</p>
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}

                          {/* Uncertainty codes */}
                          {qAssess.uncertainty_codes && qAssess.uncertainty_codes.length > 0 && (
                            <div className="flex items-center gap-1.5 text-slate-500">
                              <span className="font-semibold">Uncertainty Codes:</span>
                              <span className="font-mono">{qAssess.uncertainty_codes.join(", ")}</span>
                            </div>
                          )}
                        </>
                      )}

                      {qAssess.assessment_status === "INSUFFICIENT_EVIDENCE" && (
                        <div className="p-3 bg-amber-50 border border-amber-200 rounded text-amber-900 leading-relaxed">
                          <strong>Insufficient Evidence:</strong> {qAssess.failure_category || "Empty or unusable answer provided. Score not assigned."}
                        </div>
                      )}

                      {qAssess.assessment_status === "VALIDATION_REJECTED" && (
                        <div className="p-3 bg-slate-100 border border-slate-300 rounded text-slate-800 leading-relaxed">
                          <strong>Manual Review Preserved:</strong> Model response did not satisfy safety bounds ({qAssess.failure_category || "Validation check failed"}).
                        </div>
                      )}
                    </div>
                  );
                })()
              ) : (
                <div className="text-xs text-slate-600 leading-relaxed pt-1">
                  Click &ldquo;Assess submitted answers&rdquo; in the top bar to run bounded provisional assessment on submitted answers.
                </div>
              )}
            </div>
          )}

          {/* Bottom Navigation Controls */}
          <div className="flex items-center justify-between pt-4 border-t border-slate-100">
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handlePrevQuestion}
              disabled={activeQuestionIndex === 0}
            >
              <Icon name="chevron-left" size={16} />
              <span>Previous Question</span>
            </button>

            <div className="text-xs text-slate-500">
              Question {activeQuestionIndex + 1} of {allQuestions.length}
            </div>

            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleNextQuestion}
              disabled={activeQuestionIndex === allQuestions.length - 1}
            >
              <span>Next Question</span>
              <Icon name="chevron-right" size={16} />
            </button>
          </div>
        </div>
      ) : null}
        </>
      )}

      {/* Submit Confirmation Modal */}
      {showSubmitModal && (
        <div
          className="fixed inset-0 bg-black/50 z-50 flex items-center justify-center p-4 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="submit-modal-title"
        >
          <div className="bg-white rounded-lg max-w-md w-full p-6 space-y-4 shadow-xl border border-slate-200">
            <h3 id="submit-modal-title" className="text-base font-bold text-slate-900 m-0">
              Confirm Simulation Submission
            </h3>
            <p className="text-sm text-slate-600">
              You have answered <strong>{answeredCount} of {allQuestions.length}</strong> questions.
              Once submitted, all answers are permanently locked and cannot be amended without an audited correction.
            </p>

            {answeredCount < allQuestions.length && (
              <div className="p-3 bg-amber-50 border border-amber-200 rounded text-xs text-amber-900">
                Note: {allQuestions.length - answeredCount} question(s) remain unanswered. Unanswered slots will be submitted as empty.
              </div>
            )}

            {submitError && (
              <div className="p-3 bg-slate-100 border border-slate-300 rounded text-xs text-slate-800">
                {submitError}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2 border-t border-slate-100">
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => setShowSubmitModal(false)}
                disabled={isSubmitting}
              >
                Continue Editing
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={handleConfirmSubmit}
                disabled={isSubmitting}
              >
                {isSubmitting ? "Submitting…" : "Confirm & Lock Submission"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
