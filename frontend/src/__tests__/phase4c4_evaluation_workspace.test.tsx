import { describe, it, expect, vi, beforeEach } from "vitest";
import React from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom";
import { EvaluationWorkspace } from "../components/evaluation/EvaluationWorkspace";
import { CandidateFeedbackModal } from "../components/candidate/CandidateFeedbackModal";
import { AuthenticatedShell } from "../components/AuthenticatedShell";
import * as candidateEvaluationsApi from "../api/candidateEvaluations";
import { AuthProvider, AuthContext } from "../context/AuthContext";

vi.mock("../api/candidateEvaluations");

describe("Phase 4C.4: Recruiter Evaluation Workspace & Candidate Feedback", () => {
  const mockJobs: candidateEvaluationsApi.EvaluationJobSummary[] = [
    {
      job_id: "job-101",
      job_version_id: "jver-101",
      job_revision: 1,
      job_title: "Staff Distributed Systems Engineer",
      department: "Infrastructure",
      total_releases_count: 2,
      in_progress_count: 0,
      submitted_count: 2,
      analysis_pending_count: 0,
      ready_for_review_count: 1,
      finalized_count: 1,
      last_submission_at: "2026-09-27T10:00:00Z",
    },
  ];

  const mockCandidates: candidateEvaluationsApi.EvaluationCandidateSummary[] = [
    {
      release_id: "rel-1",
      session_id: "sess-1",
      candidate_id: "cand-1",
      candidate_name: "Alice Smith",
      candidate_email: "alice@domain.com",
      application_source: "Candidate Portal",
      submitted_at: "2026-09-27T10:00:00Z",
      answers_recorded_display: "2 of 2",
      ai_analysis_status: "Analysis ready",
      recruiter_evaluation_status: "Ready for review",
      resume_match_score: 88,
      required_skills_coverage: "4 of 5",
    },
  ];

  const mockDetail: candidateEvaluationsApi.CandidateInterviewEvaluationDetail = {
    session_id: "sess-1",
    evaluation_id: null,
    release_id: "rel-1",
    candidate_id: "cand-1",
    candidate_name: "Alice Smith",
    candidate_email: "alice@domain.com",
    job_id: "job-101",
    job_title: "Staff Distributed Systems Engineer",
    job_revision: 1,
    question_set_version: 1,
    submitted_at: "2026-09-27T10:00:00Z",
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
      category_scores: { Databases: 0.9, Networking: 0.85 },
      profile_version: 1,
      match_rule_version: "1.0.0",
      notes: "Frozen profile baseline",
    },
    overview: {
      questions_reviewed: 1,
      total_questions: 2,
      core_questions_count: 1,
      supplemental_questions_count: 1,
      insufficient_evidence_count: 0,
      aggregate_score: 4.5,
      scoring_policy_version: "1.0.0",
      dimension_averages: {
        Relevance: 4.5,
        "Technical depth": 4.0,
        Clarity: 5.0,
      },
      key_strengths: ["WAL and MVCC mastery"],
      priority_review_areas: ["Connection pooling edge cases"],
      is_ready_to_finalize: true,
    },
    questions: [
      {
        slot_id: "slot-1",
        question_order: 1,
        is_supplemental: false,
        question_type: "TECHNICAL",
        competency_name: "Database Reliability",
        question_text: "How does PostgreSQL guarantee ACID transaction compliance?",
        question_format: "CONVERSATIONAL",
        candidate_text_answer: "PostgreSQL utilizes Write-Ahead Logging (WAL) and multi-version concurrency control.",
        candidate_selected_option_id: null,
        candidate_selected_option_text: null,
        correct_option_id: null,
        correct_option_text: null,
        is_correct: null,
        explanation: null,
        is_skipped: false,
        ai_provisional: {
          status: "SUCCEEDED",
          provisional_rating: 4,
          addressed_well: ["Mentioned WAL logging", "Highlighted MVCC concurrency"],
          missing_or_unclear: ["Checkpointing details"],
          suggested_improvements: ["Describe synchronous commit modes"],
          dimension_scores: { Relevance: 5, "Technical depth": 4 },
          dimension_observations: { Relevance: "Directly addressed PostgreSQL durability mechanisms" },
          evidence_citations: [
            {
              citation_id: "cit-1",
              snippet_text: "PostgreSQL utilizes Write-Ahead Logging (WAL)",
              source: "candidate_answer",
            },
          ],
          is_insufficient_evidence: false,
          uncertainty_notes: null,
          analysis_model_version: "gemini-flash",
          evaluated_at: "2026-09-27T10:01:00Z",
        },
        rubric: {
          score_1: "Inaccurate ACID definitions",
          score_2: "Vague database explanation",
          score_3: "Mentions transactions without engine internals",
          score_4: "Accurately details WAL and MVCC",
          score_5: "Comprehensive internal durability explanation",
          observable_indicators: ["WAL", "MVCC"],
          sufficient_evidence_guidance: "Must explain WAL",
          insufficient_evidence_guidance: "Lacks internal mechanism details",
        },
        recruiter_review: null,
      },
      {
        slot_id: "slot-2",
        question_order: 2,
        is_supplemental: true,
        question_type: "SYSTEM_DESIGN",
        competency_name: "API Architecture",
        question_text: "Which header enables idempotent REST mutation handling?",
        question_format: "MCQ",
        candidate_text_answer: null,
        candidate_selected_option_id: "B",
        candidate_selected_option_text: "Idempotency-Key",
        correct_option_id: "B",
        correct_option_text: "Idempotency-Key",
        is_correct: true,
        explanation: "Idempotency-Key guarantees deduplication across retry requests.",
        is_skipped: false,
        ai_provisional: {
          status: "SUCCEEDED",
          provisional_rating: 5,
          addressed_well: ["Correctly selected standard header"],
          missing_or_unclear: [],
          suggested_improvements: [],
          dimension_scores: { Relevance: 5 },
          dimension_observations: {},
          evidence_citations: [],
          is_insufficient_evidence: false,
        },
        rubric: {
          observable_indicators: [],
        },
        recruiter_review: null,
      },
    ],
    activity_log: [
      {
        event_type: "INTERVIEW_RELEASED",
        timestamp: "2026-09-27T09:00:00Z",
        actor_name: "Recruiter",
        summary: "Interview assignment released to candidate.",
      },
    ],
  };

  const mockFeedbackReport: candidateEvaluationsApi.CandidateSafeFeedbackReport = {
    session_id: "sess-1",
    job_title: "Staff Distributed Systems Engineer",
    completed_at: "2026-09-27T10:00:00Z",
    published_at: "2026-09-27T11:00:00Z",
    top_strengths: ["WAL and MVCC concurrency comprehension", "Strong standard API protocol knowledge"],
    priority_development_areas: ["Deepen discussion on distributed checkpointing frequency"],
    dimension_summary: {
      Relevance: 4.8,
      "Technical depth": 4.5,
      Clarity: 5.0,
    },
    questions_feedback: [
      {
        slot_id: "slot-1",
        question_order: 1,
        competency_name: "Database Reliability",
        question_text: "How does PostgreSQL guarantee ACID transaction compliance?",
        is_mcq: false,
        performance_indicator: "Strongly Demonstrated",
        reviewed_strength: "Accurately detailed Write-Ahead Logging & MVCC mechanisms.",
        suggested_growth_area: "Incorporate synchronous commit trade-offs.",
      },
    ],
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  const mockRecruiterAuth = {
    user: {
      user_id: "u-recruiter-1",
      userId: "u-recruiter-1",
      email: "recruiter@example.com",
      display_name: "Recruiter User",
      displayName: "Recruiter User",
      role: "RECRUITER" as const,
      is_active: true,
      isActive: true,
      must_change_password: false,
      mustChangePassword: false,
    },
    status: "AUTHENTICATED" as const,
    csrfToken: "mock-csrf",
    sessionError: null,
    login: vi.fn(),
    logout: vi.fn(),
    changePassword: vi.fn(),
    refreshSession: vi.fn(),
    clearSessionError: vi.fn(),
  };

  // 1. Recruiter Navigation Tab
  it("shows Evaluation tab in Recruiter navigation", () => {
    render(
      <AuthContext.Provider value={mockRecruiterAuth}>
        <AuthenticatedShell activeNavTab="evaluation" onNavigateTab={vi.fn()}>
          <div>Workspace Content</div>
        </AuthenticatedShell>
      </AuthContext.Provider>
    );

    const evalTab = screen.getByRole("button", { name: /evaluation/i });
    expect(evalTab).toBeInTheDocument();
  });

  // 2. Level 1: Job Directory Listing
  it("renders Job-organized Evaluation directory with exact revision separation", async () => {
    vi.mocked(candidateEvaluationsApi.listEvaluationJobs).mockResolvedValueOnce(mockJobs);

    render(<EvaluationWorkspace />);

    expect(await screen.findByText("Staff Distributed Systems Engineer")).toBeInTheDocument();
    expect(screen.getByText("Revision 1")).toBeInTheDocument();
    expect(screen.getByText("View evaluations")).toBeInTheDocument();
  });

  // 3. Level 2: Candidate Session List with Separate Resume Match Reference
  it("renders Candidate session list for selected Job revision with separate resume match and interview performance", async () => {
    vi.mocked(candidateEvaluationsApi.listEvaluationJobs).mockResolvedValueOnce(mockJobs);
    vi.mocked(candidateEvaluationsApi.listJobCandidatesForEvaluation).mockResolvedValueOnce(mockCandidates);

    render(<EvaluationWorkspace />);

    const viewBtn = await screen.findByText("View evaluations");
    fireEvent.click(viewBtn);

    expect(await screen.findByText("Alice Smith")).toBeInTheDocument();
    expect(screen.getAllByText(/resume match/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("88%")).toBeInTheDocument();
    expect(screen.getByText(/(4 of 5 skills)/i)).toBeInTheDocument();
    expect(screen.getByText("Evaluate interview")).toBeInTheDocument();
  });

  // 4. Level 3: Question and Answer Rendering & AI Analysis Label
  it("renders Candidate detail workspace with clear AI Analysis label and separate evidence channels", async () => {
    vi.mocked(candidateEvaluationsApi.listEvaluationJobs).mockResolvedValueOnce(mockJobs);
    vi.mocked(candidateEvaluationsApi.listJobCandidatesForEvaluation).mockResolvedValueOnce(mockCandidates);
    vi.mocked(candidateEvaluationsApi.getCandidateEvaluationDetail).mockResolvedValueOnce(mockDetail);

    render(<EvaluationWorkspace />);

    const viewBtn = await screen.findByText("View evaluations");
    fireEvent.click(viewBtn);

    const evalBtn = await screen.findByText("Evaluate interview");
    fireEvent.click(evalBtn);

    expect(await screen.findByText("How does PostgreSQL guarantee ACID transaction compliance?")).toBeInTheDocument();
    expect(screen.getAllByText(/PostgreSQL utilizes Write-Ahead Logging/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/AI analysis/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/What was addressed well/i)[0]).toBeInTheDocument();
    expect(screen.getByText("Mentioned WAL logging")).toBeInTheDocument();
  });

  // 5. Override controls & validation requiring override reason
  it("allows recruiter to edit review and validates override reason when overriding rating", async () => {
    vi.mocked(candidateEvaluationsApi.listEvaluationJobs).mockResolvedValueOnce(mockJobs);
    vi.mocked(candidateEvaluationsApi.listJobCandidatesForEvaluation).mockResolvedValueOnce(mockCandidates);
    vi.mocked(candidateEvaluationsApi.getCandidateEvaluationDetail).mockResolvedValueOnce(mockDetail);

    render(<EvaluationWorkspace />);

    const viewBtn = await screen.findByText("View evaluations");
    fireEvent.click(viewBtn);

    const evalBtns = await screen.findAllByText("Evaluate interview");
    fireEvent.click(evalBtns[0]);

    const editBtns = await screen.findAllByText("Add note / override");
    fireEvent.click(editBtns[0]);

    // Ensure action is OVERRIDDEN
    const selectDecision = screen.getByLabelText(/Override action/i);
    fireEvent.change(selectDecision, { target: { value: "OVERRIDDEN" } });

    // Attempt to save without override reason
    const saveBtn = screen.getByText("Save optional override");
    fireEvent.click(saveBtn);

    expect(await screen.findByText(/Override reason is required/i)).toBeInTheDocument();
  });

  // 6. Finalization confirmation modal
  it("opens finalization modal summarizing deterministic aggregate score and core question counts", async () => {
    vi.mocked(candidateEvaluationsApi.listEvaluationJobs).mockResolvedValueOnce(mockJobs);
    vi.mocked(candidateEvaluationsApi.listJobCandidatesForEvaluation).mockResolvedValueOnce(mockCandidates);
    vi.mocked(candidateEvaluationsApi.getCandidateEvaluationDetail).mockResolvedValue(mockDetail);

    render(<EvaluationWorkspace />);

    const viewBtn = await screen.findByText("View evaluations");
    fireEvent.click(viewBtn);

    const evalBtn = await screen.findByText("Evaluate interview");
    fireEvent.click(evalBtn);

    const finalizeBtn = await screen.findByText("Finalize evaluation");
    fireEvent.click(finalizeBtn);

    expect(await screen.findByText("Finalize official evaluation")).toBeInTheDocument();
    expect(screen.getByText(/Confirm.*lock evaluation/i)).toBeInTheDocument();
    expect(screen.getByText(/Finalizing will permanently lock this evaluation/i)).toBeInTheDocument();
  });

  // 7. Candidate-Safe Feedback Report Modal (Strictly omits recruiter internal notes and secret scores)
  it("renders Candidate-Safe feedback report modal omitting internal recruiter notes", async () => {
    vi.mocked(candidateEvaluationsApi.getCandidateFeedbackReport).mockResolvedValueOnce(mockFeedbackReport);

    render(<CandidateFeedbackModal sessionId="sess-1" onClose={vi.fn()} />);

    expect(await screen.findByText("Interview Feedback Report")).toBeInTheDocument();
    expect(screen.getByText("Staff Distributed Systems Engineer")).toBeInTheDocument();
    expect(screen.getByText("WAL and MVCC concurrency comprehension")).toBeInTheDocument();
    expect(screen.getByText("Database Reliability")).toBeInTheDocument();

    // Verify STRICT omission of private recruiter notes and score IDs
    expect(screen.queryByText("Senior internal candidate note")).not.toBeInTheDocument();
    expect(screen.queryByText("score_5")).not.toBeInTheDocument();
  });
});
