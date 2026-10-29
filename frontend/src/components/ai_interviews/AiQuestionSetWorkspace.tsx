import React, { useEffect, useRef, useState } from "react";
import { apiFetch } from "../../api/client";
import { Icon } from "../Icon";
import {
  AiQuestionSetDetail,
  AiQuestionSlotItem,
  AiQuestionSetValidateResult,
  AiQuestionSetApproveResult,
  SafeRubricDto,
} from "../../types/ai_question_set";
import { SafeRubricModal } from "./SafeRubricModal";
import { McqAuthoringModal, McqFormData } from "./McqAuthoringModal";
import { CandidateSafePreviewModal } from "./CandidateSafePreviewModal";
import { ReleaseInterviewSetModal } from "./ReleaseInterviewSetModal";
import { fetchJobApplicants } from "../../api/recruiterApplications";
import "./ai-interviews.css";

interface AiQuestionSetWorkspaceProps {
  templateId: string;
  onNavigateBack: () => void;
  onOpenApplicants?: (jobId: string, jobTitle: string) => void;
}

type WorkspaceTab = "CONVERSATIONAL" | "MCQ";

export function AiQuestionSetWorkspace({
  templateId,
  onNavigateBack,
  onOpenApplicants,
}: AiQuestionSetWorkspaceProps) {
  const [detail, setDetail] = useState<AiQuestionSetDetail | null>(null);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("CONVERSATIONAL");
  const [activeSlotIndex, setActiveSlotIndex] = useState<number>(0);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [isRegenerating, setIsRegenerating] = useState<boolean>(false);
  const [isUpdatingSlot, setIsUpdatingSlot] = useState<boolean>(false);
  const [isApproving, setIsApproving] = useState<boolean>(false);
  const [isValidating, setIsValidating] = useState<boolean>(false);
  const [isGeneratingMcqs, setIsGeneratingMcqs] = useState<boolean>(false);
  const [validationResult, setValidationResult] = useState<AiQuestionSetValidateResult | null>(null);
  const [activeRubric, setActiveRubric] = useState<{ rubric: SafeRubricDto; competencyName: string } | null>(null);

  // Release Set Modal State & Stats
  const [isReleaseModalOpen, setIsReleaseModalOpen] = useState<boolean>(false);
  const [releaseStats, setReleaseStats] = useState<{ eligibleCount: number; releasedCount: number } | null>(null);

  // Edit wording state for conversational questions
  const [isEditingWording, setIsEditingWording] = useState<boolean>(false);
  const [editedWordingText, setEditedWordingText] = useState<string>("");
  const [actionFeedback, setActionFeedback] = useState<string | null>(null);

  // MCQ Modal State
  const [isMcqModalOpen, setIsMcqModalOpen] = useState<boolean>(false);
  const [editingMcqData, setEditingMcqData] = useState<McqFormData | null>(null);

  // Candidate Preview Modal State
  const [isPreviewModalOpen, setIsPreviewModalOpen] = useState<boolean>(false);

  // Generate MCQs target count dialog
  const [showGenerateMcqDialog, setShowGenerateMcqDialog] = useState<boolean>(false);
  const [mcqGenerateCount, setMcqGenerateCount] = useState<number>(5);

  const [showProvenanceDrawer, setShowProvenanceDrawer] = useState<boolean>(false);
  const [isRegeneratingLegacy, setIsRegeneratingLegacy] = useState<boolean>(false);
  const [isRegeneratingStale, setIsRegeneratingStale] = useState<boolean>(false);

  // Ref to return focus to rubric button after closing modal
  const viewRubricBtnRef = useRef<HTMLButtonElement>(null);

  // Provenance Label Helper
  const getProvenanceBadge = (origin?: string, modelDisplayName?: string | null) => {
    const norm = (origin || "").toUpperCase();
    if (norm === "LOCAL_LLM_GENERATED" || norm === "AI_GENERATED") {
      return {
        label: "AI-generated question set",
        className: "prov-ai",
        icon: "sparkles",
        bg: "#eef1fc",
        color: "#3730a3",
        border: "var(--accent-border)",
      };
    }
    if (norm === "LOCAL_LLM_GENERATED_RECRUITER_EDITED" || norm === "RECRUITER_EDITED") {
      return {
        label: "Generated locally, recruiter edited",
        className: "prov-edited",
        icon: "edit",
        bg: "#f1f5f9",
        color: "#334155",
        border: "#cbd5e1",
      };
    }
    if (norm === "LEGACY_MANUAL" || norm === "RECRUITER_AUTHORED") {
      return {
        label: "Legacy manual",
        className: "prov-manual",
        icon: "user",
        bg: "#fef3c7",
        color: "#92400e",
        border: "#fde68a",
      };
    }
    return {
      label: "Legacy deterministic",
      className: "prov-legacy",
      icon: "file-text",
      bg: "#f3f4f6",
      color: "#4b5563",
      border: "#d1d5db",
    };
  };

  // Humanize helpers
  const humanizeCompetency = (raw: string) => {
    return raw
      .replace(/_/g, " ")
      .toLowerCase()
      .replace(/\b\w/g, (c) => c.toUpperCase());
  };

  const humanizeReviewState = (state: string) => {
    switch (state) {
      case "KEPT":
        return "Reviewed";
      case "EDITED":
        return "Edited";
      case "REVISED":
        return "Regenerated";
      case "RECRUITER_AUTHORED":
        return "Written by recruiter";
      case "UNREVIEWED":
      default:
        return "To review";
    }
  };

  const humanizeType = (type: string) => {
    return type
      .toLowerCase()
      .replace(/\b\w/g, (c) => c.toUpperCase());
  };

  const loadReleaseStats = async (jobId: string, jobRev: number) => {
    try {
      const res = await fetchJobApplicants(jobId, { limit: 100 });
      const items = res.items || [];
      const eligible = items.filter(
        (a) =>
          a.status === "SUBMITTED" &&
          a.recruiterReviewState === "INTERVIEW_READY" &&
          a.jobRevision === jobRev &&
          a.interviewStatus !== "RELEASED" &&
          !a.releaseId
      ).length;
      const released = items.filter(
        (a) => a.jobRevision === jobRev && (a.interviewStatus === "RELEASED" || Boolean(a.releaseId))
      ).length;
      setReleaseStats({ eligibleCount: eligible, releasedCount: released });
    } catch {
      // Ignore background stats fetch errors
    }
  };

  const loadQuestionSet = async (isInitial = false) => {
    try {
      if (isInitial) setIsLoading(true);
      setError(null);
      const res = await apiFetch<AiQuestionSetDetail>(`/api/v1/ai-interviews/${templateId}`);
      setDetail(res);
      const convSlots = res.slots.filter(
        (s) => s.question_format === "CONVERSATION" || !s.question_format
      );
      if (convSlots.length > 0) {
        setEditedWordingText(convSlots[activeSlotIndex]?.ai_wording || "");
      }
      if ((res.lifecycle_status === "APPROVED" || res.is_immutable) && res.job_id) {
        loadReleaseStats(res.job_id, res.job_revision);
      }
    } catch (err: any) {
      setError(err?.message || "Failed to load AI question set.");
    } finally {
      if (isInitial) setIsLoading(false);
    }
  };

  useEffect(() => {
    loadQuestionSet(true);
  }, [templateId]);

  const conversationalSlots: AiQuestionSlotItem[] = (detail?.slots || []).filter(
    (s) => s.question_format === "CONVERSATION" || !s.question_format
  );
  const mcqSlots: AiQuestionSlotItem[] = (detail?.slots || []).filter(
    (s) => s.question_format === "MCQ_SINGLE_ANSWER"
  );

  const activeSlot: AiQuestionSlotItem | undefined = conversationalSlots[activeSlotIndex];

  useEffect(() => {
    if (activeSlot) {
      setEditedWordingText(activeSlot.ai_wording || "");
      setIsEditingWording(false);
    }
  }, [activeSlotIndex]);

  const handleDecision = async (
    decision: "KEEP" | "EDIT" | "RECRUITER_AUTHORED" | "REVISE",
    wording?: string
  ) => {
    if (!activeSlot || detail?.is_immutable) return;

    try {
      setIsUpdatingSlot(true);
      setActionFeedback(null);
      const updatedDetail = await apiFetch<AiQuestionSetDetail>(
        `/api/v1/ai-interviews/${templateId}/items/${activeSlot.slot_id}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            decision,
            wording: wording !== undefined ? wording : activeSlot.ai_wording,
          }),
        }
      );
      setDetail(updatedDetail);
      setIsEditingWording(false);

      const feedbackMsg =
        decision === "KEEP"
          ? "Question wording marked as reviewed."
          : decision === "EDIT"
          ? "Custom question wording saved."
          : "Question slot updated.";
      setActionFeedback(feedbackMsg);

      if (decision === "KEEP") {
        const nextUnreviewedIdx = conversationalSlots.findIndex(
          (s, idx) => idx > activeSlotIndex && s.review_state === "UNREVIEWED"
        );
        if (nextUnreviewedIdx !== -1) {
          setActiveSlotIndex(nextUnreviewedIdx);
        }
      }
    } catch (err: any) {
      setError(err?.message || "Failed to update slot decision.");
    } finally {
      setIsUpdatingSlot(false);
    }
  };

  const handleRegenerateSlot = async () => {
    if (!activeSlot || detail?.is_immutable) return;

    try {
      setIsRegenerating(true);
      setError(null);
      setActionFeedback(null);
      const updatedDetail = await apiFetch<AiQuestionSetDetail>(
        `/api/v1/ai-interviews/${templateId}/regenerate-slot/${activeSlot.slot_id}`,
        { method: "POST" }
      );
      setDetail(updatedDetail);
      setActionFeedback("Slot question wording regenerated via local model.");
    } catch (err: any) {
      setError(err?.message || "Slot regeneration failed. Ensure local AI provider is active.");
    } finally {
      setIsRegenerating(false);
    }
  };

  const handleDeleteSlot = async (slotId: string) => {
    if (detail?.is_immutable) return;
    if (!window.confirm("Are you sure you want to remove this question from the question set?")) return;

    try {
      setIsUpdatingSlot(true);
      setError(null);
      setActionFeedback(null);
      const updated = await apiFetch<AiQuestionSetDetail>(
        `/api/v1/ai-interviews/${templateId}/items/${slotId}`,
        { method: "DELETE" }
      );
      setDetail(updated);
      setValidationResult(null);
      setActionFeedback("Question removed from question set.");

      // Adjust active index if needed
      const remainingConv = (updated.slots || []).filter(
        (s) => s.question_format === "CONVERSATION" || !s.question_format
      );
      if (activeSlotIndex >= remainingConv.length) {
        setActiveSlotIndex(Math.max(0, remainingConv.length - 1));
      }
    } catch (err: any) {
      setError(err?.message || "Failed to remove question.");
    } finally {
      setIsUpdatingSlot(false);
    }
  };

  const handleRegenerateLegacySet = async () => {
    if (!detail) return;
    try {
      setIsRegeneratingLegacy(true);
      setError(null);
      setActionFeedback(null);
      const res = await apiFetch<AiQuestionSetDetail>(
        `/api/v1/ai-interviews/generate`,
        {
          method: "POST",
          body: JSON.stringify({ job_id: detail.job_id, force_regenerate: true }),
        }
      );
      setDetail(res);
      setActionFeedback("Legacy set successfully regenerated as a fresh local-AI draft.");
    } catch (err: any) {
      setError(err?.message || "Failed to regenerate set with local model.");
    } finally {
      setIsRegeneratingLegacy(false);
    }
  };

  const handleRegenerateForLatestJobVersion = async () => {
    if (!detail?.job_id) return;
    try {
      setIsRegeneratingStale(true);
      setError(null);
      setActionFeedback(null);
      const res = await apiFetch<AiQuestionSetDetail>(
        `/api/v1/ai-interviews/generate`,
        {
          method: "POST",
          body: JSON.stringify({ job_id: detail.job_id, force_regenerate: true }),
        }
      );
      setDetail(res);
      setActionFeedback("Question set successfully regenerated for latest job version & requirements.");
    } catch (err: any) {
      setError(err?.message || "Failed to regenerate questions for latest job version.");
    } finally {
      setIsRegeneratingStale(false);
    }
  };

  const handleValidateSet = async () => {
    try {
      setIsValidating(true);
      setError(null);
      const res = await apiFetch<AiQuestionSetValidateResult>(
        `/api/v1/ai-interviews/${templateId}/validate`,
        { method: "POST" }
      );
      setValidationResult(res);
    } catch (err: any) {
      setError(err?.message || "Validation failed.");
    } finally {
      setIsValidating(false);
    }
  };

  const handleApproveSet = async () => {
    if (detail?.is_immutable) return;

    try {
      setIsApproving(true);
      setError(null);
      await apiFetch<AiQuestionSetApproveResult>(
        `/api/v1/ai-interviews/${templateId}/approve`,
        { method: "POST" }
      );
      await loadQuestionSet();
      setActionFeedback(`AI question set approved and sealed.`);
    } catch (err: any) {
      setError(err?.message || "Approval failed. Please resolve review items and retry.");
    } finally {
      setIsApproving(false);
    }
  };

  const handleGenerateMcqs = async () => {
    try {
      setIsGeneratingMcqs(true);
      setError(null);
      setShowGenerateMcqDialog(false);
      const updated = await apiFetch<AiQuestionSetDetail>(
        `/api/v1/ai-interviews/${templateId}/mcqs/generate`,
        {
          method: "POST",
          body: JSON.stringify({ count: mcqGenerateCount, difficulty: "FOUNDATIONAL" }),
        }
      );
      setDetail(updated);
      setActionFeedback(`Successfully generated ${mcqGenerateCount} multiple-choice questions.`);
    } catch (err: any) {
      setError(err?.message || "MCQ generation failed. Ensure local AI provider is reachable.");
    } finally {
      setIsGeneratingMcqs(false);
    }
  };

  const handleSaveMcq = async (formData: McqFormData) => {
    try {
      setError(null);
      if (formData.slot_id) {
        // Edit existing MCQ
        const updated = await apiFetch<AiQuestionSetDetail>(
          `/api/v1/ai-interviews/${templateId}/mcqs/${formData.slot_id}`,
          {
            method: "PATCH",
            body: JSON.stringify({
              competency_name: formData.competency_name,
              question_text: formData.question_text,
              difficulty: formData.difficulty,
              options: formData.options,
              correct_option_id: formData.correct_option_id,
              answer_explanation: formData.answer_explanation,
            }),
          }
        );
        setDetail(updated);
        setActionFeedback("Multiple choice question updated.");
      } else {
        // Add manual MCQ
        const updated = await apiFetch<AiQuestionSetDetail>(
          `/api/v1/ai-interviews/${templateId}/mcqs`,
          {
            method: "POST",
            body: JSON.stringify({
              competency_name: formData.competency_name,
              question_text: formData.question_text,
              difficulty: formData.difficulty,
              options: formData.options,
              correct_option_id: formData.correct_option_id,
              answer_explanation: formData.answer_explanation,
            }),
          }
        );
        setDetail(updated);
        setActionFeedback("Multiple choice question added.");
      }
      setEditingMcqData(null);
      setIsMcqModalOpen(false);
    } catch (err: any) {
      setError(err?.message || "Failed to save multiple choice question.");
    }
  };

  const handleDeleteMcq = async (slotId: string) => {
    if (!window.confirm("Are you sure you want to remove this multiple-choice question?")) return;
    try {
      setError(null);
      const updated = await apiFetch<AiQuestionSetDetail>(
        `/api/v1/ai-interviews/${templateId}/mcqs/${slotId}`,
        { method: "DELETE" }
      );
      setDetail(updated);
      setActionFeedback("Multiple choice question removed.");
    } catch (err: any) {
      setError(err?.message || "Failed to delete multiple choice question.");
    }
  };

  const handleMoveMcq = async (index: number, direction: "UP" | "DOWN") => {
    if (
      index < 0 ||
      (direction === "UP" && index === 0) ||
      (direction === "DOWN" && index === mcqSlots.length - 1)
    ) {
      return;
    }

    const reordered = [...mcqSlots];
    const targetIndex = direction === "UP" ? index - 1 : index + 1;
    const [moved] = reordered.splice(index, 1);
    reordered.splice(targetIndex, 0, moved);

    const newSlotIds = reordered.map((s) => s.slot_id);

    try {
      setError(null);
      const updated = await apiFetch<AiQuestionSetDetail>(
        `/api/v1/ai-interviews/${templateId}/mcqs/reorder`,
        {
          method: "POST",
          body: JSON.stringify({ slot_ids: newSlotIds }),
        }
      );
      setDetail(updated);
    } catch (err: any) {
      setError(err?.message || "Failed to reorder MCQs.");
    }
  };

  const handleOpenAddMcq = () => {
    setEditingMcqData(null);
    setIsMcqModalOpen(true);
  };

  const handleOpenEditMcq = (slot: AiQuestionSlotItem) => {
    setEditingMcqData({
      slot_id: slot.slot_id,
      competency_name: slot.competency_name,
      question_text: slot.ai_wording || "",
      difficulty: slot.difficulty || "INTERMEDIATE",
      estimated_time_minutes: slot.estimated_time_minutes,
      options: slot.options || [],
      correct_option_id: slot.correct_option_id || "A",
      answer_explanation: slot.answer_explanation || "",
    });
    setIsMcqModalOpen(true);
  };

  const handleCloseRubric = () => {
    setActiveRubric(null);
    setTimeout(() => {
      viewRubricBtnRef.current?.focus();
    }, 50);
  };

  if (isLoading) {
    return (
      <div className="aiq-workspace-page">
        <div className="aiq-panel" style={{ textAlign: "center", padding: "48px 24px" }}>
          <div className="loading-spinner" style={{ margin: "0 auto 12px auto" }} />
          <p className="aiq-panel-desc">Loading AI question set...</p>
        </div>
      </div>
    );
  }

  if (error && !detail) {
    return (
      <div className="aiq-workspace-page">
        <div className="feedback-box error" role="alert" style={{ marginBottom: "16px" }}>
          <Icon name="alert-triangle" size={16} />
          <span>{error}</span>
        </div>
        <button type="button" className="btn btn-secondary btn-sm" onClick={onNavigateBack}>
          <Icon name="arrow-left" size={14} />
          <span>Back to AI Questions</span>
        </button>
      </div>
    );
  }

  if (!detail) return null;

  const isApproved = detail.lifecycle_status === "APPROVED" || detail.is_immutable;
  const isStale = detail.is_source_stale || detail.lifecycle_status === "STALE";
  const isLegacy = detail.is_legacy || false;
  const reviewedCount = detail.reviewed_slots_count || 0;
  const totalCount = detail.total_slots_count || detail.slots.length;
  const progressPercent = totalCount > 0 ? Math.round((reviewedCount / totalCount) * 100) : 0;
  const allReviewed = totalCount > 0 && reviewedCount === totalCount;
  const hasIneligibleQuestions = detail.slots.some(
    (s) => !["LOCAL_LLM_GENERATED", "LOCAL_LLM_GENERATED_RECRUITER_EDITED", "AI_GENERATED", "RECRUITER_EDITED"].includes(s.authoring_origin)
  );
  const canApprove = !isApproved && !isStale && !isLegacy && !hasIneligibleQuestions && (validationResult?.is_valid || allReviewed);

  return (
    <div className="aiq-workspace-page">
      {/* Live Region for Screen Readers */}
      <div className="sr-only" aria-live="polite">
        {actionFeedback || (isRegenerating ? "Regenerating question wording..." : "")}
      </div>

      {/* Back Button */}
      <button
        type="button"
        className="aiq-back-btn"
        onClick={onNavigateBack}
        aria-label="Back to AI Interviews"
      >
        <Icon name="arrow-left" size={16} />
        <span>Back to AI Interviews</span>
      </button>

      {/* Workspace Header */}
      <header className="aiq-workspace-header">
        <div className="aiq-header-left">
          <div className="aiq-workspace-title-row">
            <h1 className="aiq-workspace-title">{detail.job_title}</h1>
            <span
              className={`aiq-mode-badge ${detail.interview_mode === "AUDIO" ? "audio-mode" : "text-mode"}`}
            >
              {detail.interview_mode === "AUDIO" ? "Audio-based" : "Text-based"}
            </span>
            <span
              className={`aiq-status-badge ${
                isLegacy
                  ? "legacy"
                  : isApproved
                  ? "approved"
                  : isStale
                  ? "stale"
                  : detail.lifecycle_status === "VALIDATED"
                  ? "validated"
                  : "draft"
              }`}
            >
              {isApproved && !isLegacy && <Icon name="lock" size={13} />}
              {isLegacy
                ? "Legacy set (Ineligible)"
                : isApproved
                ? "Approved & Sealed"
                : isStale
                ? "Blueprint Stale"
                : detail.lifecycle_status === "VALIDATED"
                ? "Ready for Approval"
                : "Draft"}
            </span>

            {/* Compact Provenance Indicator */}
            <span
              className="badge"
              style={{
                fontSize: "13px",
                display: "inline-flex",
                alignItems: "center",
                gap: "6px",
                backgroundColor: "#eef1fc",
                color: "#3730a3",
                padding: "4px 10px",
                borderRadius: "6px",
              }}
            >
              <Icon name="sparkles" size={14} />
              <span>AI-generated question set</span>
            </span>

            {/* Compact Release Summary Badge */}
            {isApproved && !isLegacy && releaseStats && (
              <span
                className="badge"
                style={{
                  fontSize: "13px",
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "6px",
                  backgroundColor: "#ecfdf5",
                  color: "#065f46",
                  border: "1px solid #a7f3d0",
                  padding: "4px 10px",
                  borderRadius: "6px",
                }}
              >
                <Icon name="users" size={14} />
                <span>
                  {releaseStats.eligibleCount} eligible applicant{releaseStats.eligibleCount === 1 ? "" : "s"} · {releaseStats.releasedCount} interview{releaseStats.releasedCount === 1 ? "" : "s"} released
                </span>
              </span>
            )}
          </div>
        </div>

        {/* Header Actions */}
        <div className="aiq-header-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setShowProvenanceDrawer(!showProvenanceDrawer)}
            title="View safe model provenance and revision details"
          >
            <Icon name="info" size={16} />
            <span>Provenance Details</span>
          </button>

          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={() => setIsPreviewModalOpen(true)}
            title="Preview candidate-safe view of this interview set"
          >
            <Icon name="eye" size={16} />
            <span>Candidate Preview</span>
          </button>

          {isStale && !isLegacy && Boolean(detail.job_id) && (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleRegenerateForLatestJobVersion}
              disabled={isRegeneratingStale}
              title="Regenerate interview questions based on the latest approved job revision & requirements"
            >
              <Icon name="sparkles" size={16} className={isRegeneratingStale ? "animate-spin" : ""} />
              <span>{isRegeneratingStale ? "Regenerating..." : "Regenerate for latest job version"}</span>
            </button>
          )}

          {isApproved && !isLegacy && !isStale && Boolean(detail.job_id) && (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => setIsReleaseModalOpen(true)}
              title="Release this sealed interview set to eligible applicants"
            >
              <Icon name={detail.interview_mode === "AUDIO" ? "mic" : "user-check"} size={16} />
              <span>Release Interview Set</span>
            </button>
          )}

          {isLegacy && (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleRegenerateLegacySet}
              disabled={isRegeneratingLegacy}
            >
              <Icon name="sparkles" size={15} className={isRegeneratingLegacy ? "animate-spin" : ""} />
              <span>{isRegeneratingLegacy ? "Regenerating..." : "Regenerate as AI set"}</span>
            </button>
          )}

          {!isApproved && !isLegacy && (
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleValidateSet}
              disabled={isValidating}
            >
              <Icon name="check-circle" size={15} />
              <span>{isValidating ? "Validating…" : "Validate questions"}</span>
            </button>
          )}

          {canApprove && (
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleApproveSet}
              disabled={isApproving}
            >
              <Icon name="shield-check" size={15} />
              <span>{isApproving ? "Sealing…" : "Approve & Seal"}</span>
            </button>
          )}
        </div>
      </header>

      {/* Provenance & Details Panel Drawer */}
      {showProvenanceDrawer && (
        <div
          className="aiq-panel"
          style={{
            backgroundColor: "#f8fafc",
            borderColor: "#cbd5e1",
            marginBottom: "20px",
            padding: "16px 20px",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" }}>
            <strong style={{ fontSize: "14px", color: "var(--text-primary, #0f172a)" }}>
              Question Set Provenance & Metadata
            </strong>
            <button
              type="button"
              className="btn-icon-subtle"
              onClick={() => setShowProvenanceDrawer(false)}
              aria-label="Close details"
            >
              <Icon name="x" size={14} />
            </button>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: "12px", fontSize: "13px" }}>
            <div>
              <span style={{ color: "var(--text-muted, #64748b)" }}>Generation:</span>
              <div style={{ fontWeight: 600, color: "var(--text-primary, #0f172a)" }}>
                AI-generated question set
              </div>
            </div>
            <div>
              <span style={{ color: "var(--text-muted, #64748b)" }}>Job Revision:</span>
              <div style={{ fontWeight: 600, color: "var(--text-primary, #0f172a)" }}>
                Rev #{detail.job_revision}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--text-muted, #64748b)" }}>Set Version:</span>
              <div style={{ fontWeight: 600, color: "var(--text-primary, #0f172a)" }}>
                v{detail.set_version}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--text-muted, #64748b)" }}>Release Eligibility:</span>
              <div style={{ fontWeight: 600, color: detail.release_eligibility?.is_eligible ? "#065f46" : "#92400e" }}>
                {detail.release_eligibility?.is_eligible ? "Eligible" : "Ineligible for release"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--text-muted, #64748b)" }}>Created:</span>
              <div style={{ fontWeight: 500, color: "var(--text-secondary, #334155)" }}>
                {new Date(detail.created_at).toLocaleDateString()}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Legacy Warning Box */}
      {isLegacy && (
        <div
          className="aiq-panel"
          style={{
            backgroundColor: "#fffbeb",
            borderColor: "#fde68a",
            marginBottom: "20px",
            display: "flex",
            alignItems: "flex-start",
            gap: "12px",
          }}
        >
          <Icon name="alert-triangle" size={18} style={{ color: "#d97706", flexShrink: 0, marginTop: "2px" }} />
          <div>
            <strong style={{ display: "block", color: "#92400e", marginBottom: "2px", fontSize: "15px" }}>
              Legacy Question Set (Not eligible for AI practice release)
            </strong>
            <p style={{ color: "#78350f", fontSize: "14px", margin: "0 0 10px 0" }}>
              This set contains legacy deterministic or manual content without verified local-AI provenance. To make it eligible for release, regenerate it as an AI question set.
            </p>
            <button
              type="button"
              className="btn btn-secondary btn-xs"
              onClick={handleRegenerateLegacySet}
              disabled={isRegeneratingLegacy}
            >
              <Icon name="sparkles" size={13} />
              <span>Regenerate as AI set</span>
            </button>
          </div>
        </div>
      )}

      {/* Stale Blueprint Warning */}
      {isStale && !isLegacy && (
        <div
          className="aiq-panel"
          style={{
            backgroundColor: "var(--status-warn-bg, #fffbeb)",
            borderColor: "var(--status-warn-border, #fde68a)",
            marginBottom: "20px",
            display: "flex",
            alignItems: "flex-start",
            gap: "12px",
          }}
        >
          <Icon name="alert-triangle" size={18} style={{ color: "var(--status-warn, #d97706)", flexShrink: 0, marginTop: "2px" }} />
          <div style={{ flex: 1 }}>
            <strong style={{ display: "block", color: "#92400e", marginBottom: "2px", fontSize: "15px" }}>
              Job Requirements Modified
            </strong>
            <p style={{ color: "#78350f", fontSize: "14px", margin: "0 0 10px 0" }}>
              {detail.stale_reason || "The approved job requirements have changed. Please regenerate or re-verify questions."}
            </p>
            {detail.job_id && (
              <button
                type="button"
                className="btn btn-secondary btn-xs"
                onClick={handleRegenerateForLatestJobVersion}
                disabled={isRegeneratingStale}
              >
                <Icon name="sparkles" size={13} className={isRegeneratingStale ? "animate-spin" : ""} />
                <span>{isRegeneratingStale ? "Regenerating..." : "Regenerate questions for latest job version"}</span>
              </button>
            )}
          </div>
        </div>
      )}

      {/* Validation Result Box */}
      {validationResult && (
        <div
          className={`aiq-panel ${validationResult.is_valid ? "" : "feedback-box error"}`}
          style={{
            backgroundColor: validationResult.is_valid ? "#ecfdf5" : undefined,
            borderColor: validationResult.is_valid ? "#a7f3d0" : undefined,
            color: validationResult.is_valid ? "#065f46" : undefined,
            marginBottom: "20px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px", fontWeight: 600 }}>
            <Icon name={validationResult.is_valid ? "check-circle" : "alert-circle"} size={16} />
            <span>{validationResult.safe_message}</span>
          </div>
          {validationResult.blocking_errors && validationResult.blocking_errors.length > 0 && (
            <ul style={{ paddingLeft: "20px", marginTop: "8px", fontSize: "14px" }}>
              {validationResult.blocking_errors.map((err, i) => (
                <li key={i}>{err}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Action Notification */}
      {actionFeedback && (
        <div
          className="aiq-panel"
          style={{
            backgroundColor: "var(--accent-subtle, #eef1fc)",
            borderColor: "var(--accent-border, #d4dbf8)",
            color: "var(--accent-primary)",
            padding: "12px 16px",
            marginBottom: "20px",
            display: "flex",
            alignItems: "center",
            gap: "8px",
            fontSize: "14px",
            fontWeight: 500,
          }}
        >
          <Icon name="check" size={16} />
          <span>{actionFeedback}</span>
        </div>
      )}

      {/* Segmented Workspace Tabs (Only shown if mode is TEXT or MCQ tab is relevant) */}
      {detail.interview_mode !== "AUDIO" && (
        <div
          style={{
            display: "flex",
            gap: "8px",
            marginBottom: "20px",
            borderBottom: "1px solid var(--border-subtle, #e2e8f0)",
            paddingBottom: "12px",
          }}
          role="tablist"
          aria-label="Interview Question Formats"
        >
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "CONVERSATIONAL"}
            className={`btn ${activeTab === "CONVERSATIONAL" ? "btn-primary" : "btn-secondary"} btn-sm`}
            onClick={() => setActiveTab("CONVERSATIONAL")}
          >
            <Icon name="message-square" size={15} />
            <span>Conversational Questions ({conversationalSlots.length}/5)</span>
          </button>

          <button
            type="button"
            role="tab"
            aria-selected={activeTab === "MCQ"}
            className={`btn ${activeTab === "MCQ" ? "btn-primary" : "btn-secondary"} btn-sm`}
            onClick={() => setActiveTab("MCQ")}
          >
            <Icon name="badge-check" size={15} />
            <span>Multiple Choice Questions ({mcqSlots.length}/5)</span>
          </button>
        </div>
      )}

      {/* Tab 1: Conversational Questions (2-Column Layout) */}
      {activeTab === "CONVERSATIONAL" && (
        <div className="aiq-workspace-layout">
          {/* Left: Question Navigator */}
          <nav className="aiq-question-navigation" aria-label="Question slots navigator">
            <div className="aiq-nav-header">
              <span>Conversational</span>
              <span>{conversationalSlots.filter(s => s.review_state !== "UNREVIEWED").length}/{conversationalSlots.length} Reviewed</span>
            </div>

            {conversationalSlots.map((slot, index) => {
              const isActive = index === activeSlotIndex;
              const isReviewed = slot.review_state !== "UNREVIEWED";
              const numStr = String(index + 1).padStart(2, "0");
              const badgeClass = isReviewed ? (slot.review_state === "EDITED" ? "edited" : "reviewed") : "to-review";

              return (
                <button
                  key={slot.slot_id}
                  type="button"
                  className={`aiq-nav-item ${isActive ? "aiq-active" : ""}`}
                  onClick={() => setActiveSlotIndex(index)}
                >
                  <span className="aiq-nav-num">{numStr}</span>
                  <div className="aiq-nav-content">
                    <div className="aiq-nav-title">{humanizeCompetency(slot.competency_name)}</div>
                    <div className="aiq-nav-sub">
                      {humanizeType(slot.question_type)} · {slot.estimated_time_minutes} min
                    </div>
                    <span className={`aiq-nav-state-badge ${badgeClass}`}>
                      {humanizeReviewState(slot.review_state)}
                    </span>
                  </div>
                </button>
              );
            })}
          </nav>

          {/* Right: Question Editor */}
          <main className="aiq-question-editor">
            {activeSlot ? (
              <>
                <div className="aiq-editor-header">
                  <div>
                    <div className="aiq-editor-slot-meta">
                      Question {activeSlotIndex + 1} of {conversationalSlots.length} · {humanizeType(activeSlot.question_type)} · {activeSlot.estimated_time_minutes} minutes
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px", marginTop: "4px" }}>
                      <h2 className="aiq-editor-competency" style={{ margin: 0 }}>
                        {humanizeCompetency(activeSlot.competency_name)}
                      </h2>
                      {(() => {
                        const prov = getProvenanceBadge(activeSlot.authoring_origin, detail.model_display_name);
                        return (
                          <span
                            className={`badge ${prov.className}`}
                            style={{
                              fontSize: "12px",
                              display: "inline-flex",
                              alignItems: "center",
                              gap: "4px",
                              backgroundColor: prov.className === "prov-ai" ? "#eef1fc" : prov.className === "prov-edited" ? "#f1f5f9" : "#fef3c7",
                              color: prov.className === "prov-ai" ? "#3730a3" : prov.className === "prov-edited" ? "#334155" : "#92400e",
                              padding: "2px 8px",
                              borderRadius: "6px",
                              fontWeight: 500,
                            }}
                          >
                            <Icon name={prov.icon as any} size={12} />
                            <span>{prov.label}</span>
                          </span>
                        );
                      })()}
                    </div>
                  </div>

                  {activeSlot.rubric && (
                    <button
                      ref={viewRubricBtnRef}
                      type="button"
                      className="aiq-rubric-btn"
                      onClick={() =>
                        setActiveRubric({
                          rubric: activeSlot.rubric!,
                          competencyName: activeSlot.competency_name,
                        })
                      }
                    >
                      <Icon name="file-text" size={15} />
                      <span>View rubric</span>
                    </button>
                  )}
                </div>

                <section className="aiq-wording-section" aria-label="Question text">
                  {isEditingWording ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                      <label htmlFor="aiq-wording-editor" className="sr-only">
                        Edit interview question wording
                      </label>
                      <textarea
                        id="aiq-wording-editor"
                        className="aiq-wording-textarea"
                        value={editedWordingText}
                        onChange={(e) => setEditedWordingText(e.target.value)}
                        placeholder="Enter tailored question wording for this competency slot..."
                        rows={4}
                      />
                      <div style={{ display: "flex", justifyContent: "flex-end", gap: "8px" }}>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          onClick={() => {
                            setEditedWordingText(activeSlot.ai_wording || "");
                            setIsEditingWording(false);
                          }}
                        >
                          Cancel
                        </button>
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          onClick={() => handleDecision("EDIT", editedWordingText)}
                          disabled={!editedWordingText.trim() || isUpdatingSlot}
                        >
                          Save question
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="aiq-wording-display">
                        {activeSlot.ai_wording ? (
                          activeSlot.ai_wording
                        ) : (
                          <span className="aiq-wording-empty">No question generated yet for this slot.</span>
                        )}
                      </div>

                      {!isApproved && (
                        <div className="aiq-wording-tools">
                          <button
                            type="button"
                            className="aiq-tool-btn"
                            onClick={() => setIsEditingWording(true)}
                          >
                            <Icon name="edit" size={14} />
                            <span>Edit question</span>
                          </button>

                          <button
                            type="button"
                            className="aiq-tool-btn"
                            onClick={handleRegenerateSlot}
                            disabled={isRegenerating || isUpdatingSlot}
                          >
                            <Icon name="refresh" size={14} className={isRegenerating ? "animate-spin" : ""} />
                            <span>{isRegenerating ? "Regenerating…" : "Regenerate"}</span>
                          </button>

                          <button
                            type="button"
                            className="aiq-tool-btn"
                            onClick={() => handleDeleteSlot(activeSlot.slot_id)}
                            disabled={isUpdatingSlot}
                            style={{ color: "var(--status-danger, #63445b)" }}
                            title="Remove this question from the set and database"
                          >
                            <Icon name="trash" size={14} />
                            <span>Remove question</span>
                          </button>
                        </div>
                      )}
                    </>
                  )}
                </section>

                {activeSlot.permitted_probes && activeSlot.permitted_probes.length > 0 && (
                  <section className="aiq-probes-section">
                    <h3 className="aiq-probes-title">Permitted follow-up probes</h3>
                    <ul className="aiq-probes-list">
                      {activeSlot.permitted_probes.map((probe, i) => (
                        <li key={i}>{probe}</li>
                      ))}
                    </ul>
                  </section>
                )}

                {!isApproved && (
                  <div className="aiq-decision-bar">
                    <span className="aiq-decision-prompt">
                      Confirm this question is suitable for the interview.
                    </span>

                    {activeSlot.ai_wording ? (
                      <button
                        type="button"
                        className={`aiq-review-btn ${activeSlot.review_state === "KEPT" ? "is-reviewed" : ""}`}
                        onClick={() => handleDecision("KEEP")}
                        disabled={isUpdatingSlot}
                      >
                        <Icon name="check" size={16} />
                        <span>{activeSlot.review_state === "KEPT" ? "Reviewed" : "Mark as reviewed"}</span>
                      </button>
                    ) : (
                      <button
                        type="button"
                        className="aiq-review-btn"
                        onClick={handleRegenerateSlot}
                        disabled={isRegenerating}
                      >
                        <Icon name="sparkles" size={16} />
                        <span>Generate this question</span>
                      </button>
                    )}
                  </div>
                )}
              </>
            ) : (
              <div style={{ textAlign: "center", padding: "48px 0", color: "var(--text-muted, #64748b)" }}>
                No conversational questions found.
              </div>
            )}
          </main>
        </div>
      )}

      {/* Tab 2: Multiple Choice Questions (Full Width View) */}
      {activeTab === "MCQ" && (
        <section aria-label="Multiple choice question management">
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: "20px",
              padding: "16px 20px",
              backgroundColor: "var(--surface-base, #ffffff)",
              borderRadius: "8px",
              border: "1px solid var(--border-subtle, #e2e8f0)",
            }}
          >
            <div>
              <h2 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: "0 0 4px 0" }}>
                Multiple Choice Questions ({mcqSlots.length} / 5 max)
              </h2>
              <p style={{ fontSize: "13px", color: "var(--text-secondary, #64748b)", margin: 0 }}>
                Author or generate standardized knowledge questions. Each MCQ requires 2–5 distinct options, 1 designated correct answer, and an explanation.
              </p>
            </div>

            {mcqSlots.length > 0 && (
              <div style={{ display: "flex", gap: "10px" }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setShowGenerateMcqDialog(true)}
                  disabled={mcqSlots.length >= 5 || isGeneratingMcqs}
                >
                  <Icon name="sparkles" size={14} className={isGeneratingMcqs ? "animate-spin" : ""} />
                  <span>{isGeneratingMcqs ? "Generating..." : "Generate MCQs with AI"}</span>
                </button>

                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={handleOpenAddMcq}
                  disabled={mcqSlots.length >= 5}
                >
                  <Icon name="plus" size={14} />
                  <span>Add MCQ</span>
                </button>
              </div>
            )}
          </div>

          {/* MCQ Generation Dialog Modal */}
          {showGenerateMcqDialog && (
            <div
              className="aiq-modal-overlay"
              role="dialog"
              aria-modal="true"
              aria-labelledby="gen-mcq-title"
              onClick={(e) => {
                if (e.target === e.currentTarget) setShowGenerateMcqDialog(false);
              }}
            >
              <div className="aiq-modal-dialog" style={{ maxWidth: "480px" }}>
                <div className="aiq-modal-header">
                  <h2 id="gen-mcq-title" className="aiq-modal-title">
                    Generate Multiple Choice Questions
                  </h2>
                  <button
                    type="button"
                    className="aiq-modal-close-btn"
                    onClick={() => setShowGenerateMcqDialog(false)}
                    aria-label="Close dialog"
                  >
                    <Icon name="x" size={18} />
                  </button>
                </div>
                <div className="aiq-modal-body">
                  <p style={{ fontSize: "14px", color: "var(--text-secondary, #475569)", marginBottom: "16px" }}>
                    The local AI model will generate tailored, competency-grounded MCQs with plausible distractors, one unambiguous correct answer, and explanations based on the job requirements.
                  </p>
                  <div>
                    <label className="aiq-form-label" htmlFor="mcq-gen-count">
                      Number of MCQs to generate (1 to {5 - mcqSlots.length})
                    </label>
                    <input
                      id="mcq-gen-count"
                      type="number"
                      min={1}
                      max={Math.min(5, 5 - mcqSlots.length)}
                      value={mcqGenerateCount}
                      onChange={(e) => setMcqGenerateCount(Math.max(1, Math.min(5 - mcqSlots.length, Number(e.target.value))))}
                      className="aiq-input"
                    />
                  </div>
                </div>
                <div className="aiq-modal-footer">
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={() => setShowGenerateMcqDialog(false)}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary btn-sm"
                    onClick={handleGenerateMcqs}
                    disabled={isGeneratingMcqs}
                  >
                    <Icon name="sparkles" size={14} />
                    <span>Generate</span>
                  </button>
                </div>
              </div>
            </div>
          )}

          {mcqSlots.length === 0 ? (
            <div
              className="aiq-panel"
              style={{
                textAlign: "center",
                padding: "48px 24px",
                backgroundColor: "var(--surface-base, #ffffff)",
              }}
            >
              <div style={{ margin: "0 auto 12px auto", width: "40px", height: "40px", borderRadius: "50%", backgroundColor: "var(--accent-subtle, #f0f4ff)", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--accent-primary, #3b82f6)" }}>
                <Icon name="badge-check" size={20} />
              </div>
              <h3 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary, #0f172a)", marginBottom: "6px" }}>
                No Multiple Choice Questions Yet
              </h3>
              <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)", maxWidth: "440px", margin: "0 auto 16px auto" }}>
                Practice sets can contain up to 5 MCQs alongside conversational questions. Click below to author one manually or generate them with local AI.
              </p>
              <div style={{ display: "flex", gap: "10px", justifyContent: "center" }}>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => setShowGenerateMcqDialog(true)}
                >
                  <Icon name="sparkles" size={14} />
                  <span>Generate with AI</span>
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={handleOpenAddMcq}
                >
                  <Icon name="plus" size={14} />
                  <span>Add Manual MCQ</span>
                </button>
              </div>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
              {mcqSlots.map((slot, index) => (
                <div
                  key={slot.slot_id}
                  className="aiq-panel"
                  style={{
                    backgroundColor: "var(--surface-base, #ffffff)",
                    border: "1px solid var(--border-subtle, #e2e8f0)",
                    padding: "20px",
                    borderRadius: "8px",
                  }}
                >
                  {/* MCQ Top Header */}
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "12px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                      <span
                        style={{
                          display: "inline-flex",
                          alignItems: "center",
                          justifyContent: "center",
                          width: "28px",
                          height: "28px",
                          borderRadius: "6px",
                          backgroundColor: "var(--accent-subtle, #f0f4ff)",
                          color: "var(--accent-primary, #3b82f6)",
                          fontWeight: 700,
                          fontSize: "13px",
                        }}
                      >
                        {index + 1}
                      </span>
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                          <strong style={{ fontSize: "14px", color: "var(--text-primary, #0f172a)" }}>
                            {humanizeCompetency(slot.competency_name)}
                          </strong>
                          {slot.difficulty && (
                            <span
                              className="badge"
                              style={{
                                fontSize: "11px",
                                textTransform: "uppercase",
                                backgroundColor: slot.difficulty === "ADVANCED" ? "#fef3c7" : slot.difficulty === "INTERMEDIATE" ? "#e0f2fe" : "#ecfdf5",
                                color: slot.difficulty === "ADVANCED" ? "#92400e" : slot.difficulty === "INTERMEDIATE" ? "#0369a1" : "#065f46",
                                border: "1px solid transparent",
                              }}
                            >
                              {slot.difficulty}
                            </span>
                          )}
                          {(() => {
                            const badge = getProvenanceBadge(slot.authoring_origin, detail.model_display_name);
                            return (
                              <span
                                className="badge"
                                style={{
                                  fontSize: "11px",
                                  backgroundColor: badge.bg,
                                  color: badge.color,
                                  border: `1px solid ${badge.border}`,
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: "4px"
                                }}
                              >
                                <Icon name={badge.icon as any} size={10} />
                                <span>{badge.label}</span>
                              </span>
                            );
                          })()}
                        </div>
                      </div>
                    </div>

                    {/* Reorder and Action Tools */}
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <button
                        type="button"
                        className="btn-icon-subtle"
                        onClick={() => handleMoveMcq(index, "UP")}
                        disabled={index === 0}
                        aria-label={`Move question ${index + 1} up`}
                        title="Move Up"
                      >
                        <Icon name="chevron-up" size={14} />
                      </button>
                      <button
                        type="button"
                        className="btn-icon-subtle"
                        onClick={() => handleMoveMcq(index, "DOWN")}
                        disabled={index === mcqSlots.length - 1}
                        aria-label={`Move question ${index + 1} down`}
                        title="Move Down"
                      >
                        <Icon name="chevron-down" size={14} />
                      </button>
                      <button
                        type="button"
                        className="btn btn-secondary btn-xs"
                        onClick={() => handleOpenEditMcq(slot)}
                        aria-label={`Edit question ${index + 1}`}
                      >
                        <Icon name="edit" size={12} />
                        <span>Edit</span>
                      </button>
                      <button
                        type="button"
                        className="btn btn-secondary btn-xs"
                        onClick={() => handleDeleteMcq(slot.slot_id)}
                        aria-label={`Delete question ${index + 1}`}
                      >
                        <Icon name="trash" size={12} />
                        <span>Delete</span>
                      </button>
                    </div>
                  </div>

                  {/* Question Text */}
                  <p style={{ fontSize: "15px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: "0 0 16px 0", lineHeight: 1.5 }}>
                    {slot.ai_wording}
                  </p>

                  {/* Options List */}
                  <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px", marginBottom: "16px" }}>
                    {slot.options?.map((opt) => {
                      const isCorrect = slot.correct_option_id === opt.option_id;
                      return (
                        <div
                          key={opt.option_id}
                          style={{
                            display: "flex",
                            alignItems: "center",
                            gap: "8px",
                            padding: "8px 12px",
                            borderRadius: "6px",
                            backgroundColor: isCorrect ? "#ecfdf5" : "var(--surface-muted, #f8fafc)",
                            border: isCorrect ? "1px solid #10b981" : "1px solid var(--border-subtle, #e2e8f0)",
                          }}
                        >
                          <span
                            style={{
                              fontWeight: 700,
                              fontSize: "12px",
                              color: isCorrect ? "#065f46" : "var(--text-secondary, #475569)",
                              minWidth: "20px",
                            }}
                          >
                            {opt.option_id}.
                          </span>
                          <span style={{ fontSize: "13px", color: isCorrect ? "#065f46" : "var(--text-primary, #0f172a)", flex: 1 }}>
                            {opt.text}
                          </span>
                          {isCorrect && (
                            <span
                              style={{
                                display: "inline-flex",
                                alignItems: "center",
                                gap: "4px",
                                fontSize: "11px",
                                fontWeight: 600,
                                color: "#065f46",
                                backgroundColor: "rgba(16, 185, 129, 0.15)",
                                padding: "2px 6px",
                                borderRadius: "4px",
                              }}
                            >
                              <Icon name="check" size={11} /> Correct
                            </span>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  {/* Explanation Box */}
                  {slot.answer_explanation && (
                    <div
                      style={{
                        padding: "10px 14px",
                        borderRadius: "6px",
                        backgroundColor: "var(--accent-subtle, #f0f4ff)",
                        borderLeft: "3px solid var(--accent-primary, #3b82f6)",
                        fontSize: "13px",
                        color: "var(--text-secondary, #475569)",
                      }}
                    >
                      <strong style={{ color: "var(--text-primary, #0f172a)", display: "inline-block", marginRight: "6px" }}>
                        Explanation (Recruiter):
                      </strong>
                      {slot.answer_explanation}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}

      {/* Rubric Modal Dialog */}
      {activeRubric && (
        <SafeRubricModal
          rubric={activeRubric.rubric}
          competencyName={activeRubric.competencyName}
          onClose={handleCloseRubric}
        />
      )}

      {/* MCQ Authoring & Editing Modal */}
      {isMcqModalOpen && (
        <McqAuthoringModal
          isOpen={isMcqModalOpen}
          initialData={editingMcqData}
          onSave={handleSaveMcq}
          onClose={() => {
            setIsMcqModalOpen(false);
            setEditingMcqData(null);
          }}
        />
      )}

      {/* Candidate Safe Preview Modal */}
      {isPreviewModalOpen && (
        <CandidateSafePreviewModal
          isOpen={isPreviewModalOpen}
          detail={detail}
          onClose={() => setIsPreviewModalOpen(false)}
        />
      )}

      {/* Release Interview Set Modal */}
      {isReleaseModalOpen && detail && (
        <ReleaseInterviewSetModal
          isOpen={isReleaseModalOpen}
          detail={detail}
          onClose={() => setIsReleaseModalOpen(false)}
          onSuccessRelease={() => {
            if (detail.job_id) {
              loadReleaseStats(detail.job_id, detail.job_revision);
            }
          }}
          onOpenApplicants={onOpenApplicants}
        />
      )}
    </div>
  );
}
