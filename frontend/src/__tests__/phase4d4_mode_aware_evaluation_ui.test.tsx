import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { EvaluationWorkspace } from "../components/evaluation/EvaluationWorkspace";
import * as candidateEvaluationsApi from "../api/candidateEvaluations";

vi.mock("../api/candidateEvaluations");

describe("Phase 4D.4: Mode-Aware Evaluation UI & Export", () => {
  const mockJobs: candidateEvaluationsApi.EvaluationJobSummary[] = [
    {
      job_id: "job-101",
      job_version_id: "jver-101",
      job_revision: 1,
      job_title: "Senior AI Engineer",
      department: "Engineering",
      total_releases_count: 2,
      in_progress_count: 0,
      submitted_count: 2,
      analysis_pending_count: 0,
      ready_for_review_count: 2,
      finalized_count: 0,
      last_submission_at: "2026-09-28T12:00:00Z",
    },
  ];

  const mockDualModeCandidates: candidateEvaluationsApi.EvaluationCandidateSummary[] = [
    {
      release_id: "rel-text-1",
      session_id: "sess-text-1",
      candidate_id: "cand-1",
      candidate_name: "Jane Doe",
      candidate_email: "jane.doe@example.com",
      application_source: "Candidate Portal",
      submitted_at: "2026-09-28T10:00:00Z",
      answers_recorded_display: "2 of 2",
      ai_analysis_status: "Analysis ready",
      recruiter_evaluation_status: "Ready for review",
      resume_match_score: 88,
      required_skills_coverage: "4 of 5",
      interview_mode: "TEXT",
    },
    {
      release_id: "rel-audio-1",
      session_id: "sess-audio-1",
      candidate_id: "cand-1",
      candidate_name: "Jane Doe",
      candidate_email: "jane.doe@example.com",
      application_source: "Candidate Portal",
      submitted_at: "2026-09-28T11:00:00Z",
      answers_recorded_display: "2 of 2",
      ai_analysis_status: "Analysis ready",
      recruiter_evaluation_status: "Ready for review",
      resume_match_score: 88,
      required_skills_coverage: "4 of 5",
      interview_mode: "AUDIO",
    },
  ];

  const mockAudioDetail: candidateEvaluationsApi.CandidateInterviewEvaluationDetail = {
    session_id: "sess-audio-1",
    evaluation_id: "eval-audio-1",
    release_id: "rel-audio-1",
    candidate_id: "cand-1",
    candidate_name: "Jane Doe",
    candidate_email: "jane.doe@example.com",
    job_id: "job-101",
    job_title: "Senior AI Engineer",
    job_revision: 1,
    question_set_version: 1,
    interview_mode: "AUDIO",
    submitted_at: "2026-09-28T11:00:00Z",
    status: "IN_REVIEW",
    candidate_feedback_status: "UNPUBLISHED",
    candidate_feedback_published_at: null,
    finalized_by_name: null,
    finalized_at: null,
    resume_match_reference: {
      has_match_reference: true,
      overall_compatibility_score: 88,
      required_skills_matched_count: 4,
      total_required_skills_count: 5,
      category_scores: { "System Design": 0.9, ML: 0.85 },
      profile_version: 1,
      match_rule_version: "1.0.0",
      notes: "Frozen profile baseline",
    },
    overview: {
      questions_reviewed: 1,
      total_questions: 2,
      core_questions_count: 2,
      supplemental_questions_count: 0,
      insufficient_evidence_count: 0,
      aggregate_score: 4.0,
      scoring_policy_version: "1.0.0",
      dimension_averages: {
        Relevance: 4.0,
        "Technical depth": 4.5,
        Clarity: 4.0,
      },
      key_strengths: ["Root-cause deadlock diagnosis"],
      priority_review_areas: ["Automated mitigation testing"],
      is_ready_to_finalize: true,
    },
    questions: [
      {
        slot_id: "slot-a1",
        question_order: 1,
        is_supplemental: false,
        question_type: "TECHNICAL",
        competency_name: "Problem Solving",
        question_text: "Describe a distributed systems failure you diagnosed and resolved.",
        question_format: "CONVERSATIONAL",
        submitted_answer_text: "I diagnosed a distributed deadlock using distributed tracing and thread dumps.",
        candidate_text_answer: "I diagnosed a distributed deadlock using distributed tracing and thread dumps.",
        selected_option_id: null,
        selected_option_text: null,
        is_correct: null,
        stored_explanation: null,
        is_skipped: false,
        rubric: {
          observable_indicators: ["Root-cause analysis depth"],
        },
        ai_provisional_analysis: {
          provisional_rating: 4.0,
          analysis_status: "COMPLETED",
          addressed_well: ["Distributed tracing usage", "Root cause breakdown"],
          missing_or_unclear: ["Automated regression test coverage"],
          suggested_improvement: "Include automated validation steps.",
          dimension_scores: { Relevance: 4.0, "Technical depth": 4.5 },
          evidence_citations: [
            {
              citation_id: "c1",
              snippet_text: "I diagnosed a distributed deadlock using distributed tracing",
              source: "candidate_answer",
            },
          ],
        },
        recruiter_review: null,
      },
    ],
    activity_log: [],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(candidateEvaluationsApi.listEvaluationJobs).mockResolvedValue(mockJobs);
    vi.mocked(candidateEvaluationsApi.listJobCandidatesForEvaluation).mockResolvedValue(mockDualModeCandidates);
    vi.mocked(candidateEvaluationsApi.getCandidateEvaluationDetail).mockResolvedValue(mockAudioDetail);
    vi.mocked(candidateEvaluationsApi.exportCandidateEvaluationJson).mockResolvedValue({
      session_id: "sess-audio-1",
      interview_mode: "AUDIO",
      candidate_name: "Jane Doe",
      job_title: "Senior AI Engineer",
      questions: [],
    } as any);
  });

  it("renders dual-mode candidates and allows filtering by interview mode", async () => {
    render(<EvaluationWorkspace />);

    // Navigate from Level 1 to Level 2
    await waitFor(() => {
      expect(screen.getByText("Senior AI Engineer")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /view evaluations for senior ai engineer/i }));

    // By default, Text-based mode is selected
    await waitFor(() => {
      expect(document.querySelector(".eval-badge-mode-text")).toBeInTheDocument();
      expect(document.querySelector(".eval-badge-mode-audio")).not.toBeInTheDocument();
      expect(screen.getAllByText("Jane Doe")).toHaveLength(1);
    });

    // Test Audio-only filter pill
    const audioFilterBtn = screen.getByRole("button", { name: "Audio-based" });
    fireEvent.click(audioFilterBtn);

    await waitFor(() => {
      expect(document.querySelector(".eval-badge-mode-audio")).toBeInTheDocument();
      expect(document.querySelector(".eval-badge-mode-text")).not.toBeInTheDocument();
      expect(screen.getAllByText("Jane Doe")).toHaveLength(1);
    });

    // Test Text-only filter pill
    const textFilterBtn = screen.getByRole("button", { name: "Text-based" });
    fireEvent.click(textFilterBtn);

    await waitFor(() => {
      expect(document.querySelector(".eval-badge-mode-text")).toBeInTheDocument();
      expect(document.querySelector(".eval-badge-mode-audio")).not.toBeInTheDocument();
      expect(screen.getAllByText("Jane Doe")).toHaveLength(1);
    });
  });

  it("displays audio evaluation detail with transcript notice and confirmed transcript label", async () => {
    render(<EvaluationWorkspace />);

    await waitFor(() => {
      expect(screen.getByText("Senior AI Engineer")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /view evaluations for senior ai engineer/i }));

    // Switch to Audio-based filter
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Audio-based" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Audio-based" }));

    await waitFor(() => {
      expect(document.querySelector(".eval-badge-mode-audio")).toBeInTheDocument();
    });

    // Click evaluate on the audio interview card
    const evalButtons = screen.getAllByRole("button", { name: /evaluate interview/i });
    fireEvent.click(evalButtons[0]);

    await waitFor(() => {
      expect(candidateEvaluationsApi.getCandidateEvaluationDetail).toHaveBeenCalledWith("sess-audio-1");
    });

    // Verify Mode Badge and Audio Transcript Notice in Level 3
    await waitFor(() => {
      expect(document.querySelector(".eval-badge-mode-audio")).toBeInTheDocument();
    });

    expect(screen.getByText(/Audio interview:/i)).toBeInTheDocument();
    expect(screen.getByText("Candidate confirmed transcript")).toBeInTheDocument();
    expect(screen.getAllByText(/i diagnosed a distributed deadlock/i).length).toBeGreaterThan(0);

    // Verify zero audio players and zero vocal quality dimension charts in rubric
    expect(screen.queryByRole("audio")).not.toBeInTheDocument();
    const dimensionPills = document.querySelector(".eval-ai-dimension-pills");
    expect(dimensionPills).toBeInTheDocument();
    expect(dimensionPills?.textContent).not.toMatch(/pitch|accent|pronunciation|speech speed|tone/i);
    expect(dimensionPills?.textContent).toMatch(/Relevance/);
    expect(dimensionPills?.textContent).toMatch(/Technical depth/);
  });

  it("triggers sanitized JSON export containing interview_mode on Download JSON click", async () => {
    // Mock URL.createObjectURL and revokeObjectURL
    const createObjectURLMock = vi.fn().mockReturnValue("blob:mock-url");
    const revokeObjectURLMock = vi.fn();
    window.URL.createObjectURL = createObjectURLMock;
    window.URL.revokeObjectURL = revokeObjectURLMock;

    render(<EvaluationWorkspace />);

    await waitFor(() => {
      expect(screen.getByText("Senior AI Engineer")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: /view evaluations for senior ai engineer/i }));

    // Switch to Audio-based filter
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Audio-based" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Audio-based" }));

    await waitFor(() => {
      expect(document.querySelector(".eval-badge-mode-audio")).toBeInTheDocument();
    });

    const evalButtons = screen.getAllByRole("button", { name: /evaluate interview/i });
    fireEvent.click(evalButtons[0]);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /download json/i })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("button", { name: /download json/i }));

    await waitFor(() => {
      expect(candidateEvaluationsApi.exportCandidateEvaluationJson).toHaveBeenCalledWith("sess-audio-1");
      expect(createObjectURLMock).toHaveBeenCalled();
    });
  });
});
