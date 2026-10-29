import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { vi, describe, it, expect, beforeEach, afterEach } from "vitest";
import { AiQuestionSetWorkspace } from "../components/ai_interviews/AiQuestionSetWorkspace";
import { AiInterviewsDirectory } from "../components/ai_interviews/AiInterviewsDirectory";
import * as clientApi from "../api/client";
import { AiQuestionSetDetail } from "../types/ai_question_set";

vi.mock("../api/client", () => ({
  apiFetch: vi.fn(),
}));

const mockAiProvenanceDetail: AiQuestionSetDetail = {
  ai_set_id: "set-prov-1",
  set_version: 1,
  interview_mode: "TEXT",
  job_id: "job-101",
  job_title: "Senior Backend Engineer",
  job_revision: 3,
  lifecycle_status: "VALIDATED",
  is_immutable: false,
  total_slots_count: 2,
  generated_slots_count: 2,
  reviewed_slots_count: 2,
  conversation_count: 1,
  mcq_count: 1,
  is_source_stale: false,
  is_legacy: false,
  source_content_hash: "mock_sha256_hash",
  model_display_name: "Qwen3 4B (Local LM Studio)",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
  release_eligibility: {
    is_eligible: true,
    safe_message: "Question set is eligible for Phase 3G release.",
    blocking_items: []
  },
  slots: [
    {
      slot_id: "slot-conv-1",
      display_order: 0,
      competency_name: "SYSTEM_DESIGN",
      question_type: "TECHNICAL",
      estimated_time_minutes: 5,
      permitted_probes: [],
      ai_wording: "Explain how you implement distributed locking in Redis.",
      review_state: "KEPT",
      authoring_origin: "LOCAL_LLM_GENERATED",
      validation_status: "PASSED",
      question_format: "CONVERSATION",
    },
    {
      slot_id: "slot-mcq-1",
      display_order: 1,
      competency_name: "DATABASE_INDEXING",
      question_type: "KNOWLEDGE_MCQ",
      estimated_time_minutes: 2,
      permitted_probes: [],
      ai_wording: "Which index type is optimal for range queries in PostgreSQL?",
      review_state: "KEPT",
      authoring_origin: "LOCAL_LLM_GENERATED_RECRUITER_EDITED",
      validation_status: "PASSED",
      question_format: "MCQ_SINGLE_ANSWER",
      difficulty: "INTERMEDIATE",
      options: [
        { option_id: "A", text: "B-Tree" },
        { option_id: "B", text: "Hash Index" },
      ],
      correct_option_id: "A",
      answer_explanation: "B-Trees are the default and optimal index for range queries.",
    },
  ],
};

const mockLegacySetDetail: AiQuestionSetDetail = {
  ...mockAiProvenanceDetail,
  ai_set_id: "set-legacy-1",
  is_legacy: true,
  lifecycle_status: "APPROVED",
  release_eligibility: {
    is_eligible: false,
    reason_code: "LEGACY_DETERMINISTIC_PRESENT",
    safe_message: "Question set contains legacy deterministic questions.",
    blocking_items: [{ slot_number: 1, slot_id: "slot-1", reason_code: "LEGACY_DETERMINISTIC_PRESENT", message: "Question #1 is legacy." }]
  },
  slots: [
    {
      slot_id: "slot-legacy-1",
      display_order: 0,
      competency_name: "CODING",
      question_type: "BEHAVIORAL_TECHNICAL",
      estimated_time_minutes: 3,
      permitted_probes: [],
      ai_wording: "Legacy deterministic interview question.",
      review_state: "KEPT",
      authoring_origin: "LEGACY_DETERMINISTIC",
      validation_status: "PASSED",
      question_format: "CONVERSATION",
    }
  ]
};

describe("Phase 3G.1 Frontend Provenance & Release Eligibility", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders provenance badge for local LLM generated question and recruiter edited MCQ", async () => {
    vi.mocked(clientApi.apiFetch).mockResolvedValue(mockAiProvenanceDetail);

    render(
      <AiQuestionSetWorkspace
        templateId="set-prov-1"
        onNavigateBack={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
      expect(screen.getByText(/Generated locally with Qwen3/i)).toBeInTheDocument();
    });

    // Check MCQ tab for Recruiter edited provenance
    const mcqTab = await screen.findByRole("tab", { name: /Multiple Choice/i });
    fireEvent.click(mcqTab);

    await waitFor(() => {
      expect(screen.getByText(/Generated locally, recruiter edited/i)).toBeInTheDocument();
    });
  });

  it("displays legacy set banner with regenerate action and blocks approval", async () => {
    vi.mocked(clientApi.apiFetch).mockResolvedValue(mockLegacySetDetail);

    render(
      <AiQuestionSetWorkspace
        templateId="set-legacy-1"
        onNavigateBack={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Legacy set (Ineligible)")).toBeInTheDocument();
      expect(screen.getByText(/Legacy Question Set \(Not eligible for AI practice release\)/i)).toBeInTheDocument();
      expect(screen.getAllByRole("button", { name: /Regenerate as AI set/i }).length).toBeGreaterThan(0);
    });
  });

  it("opens compact provenance details drawer when clicking View Provenance", async () => {
    vi.mocked(clientApi.apiFetch).mockResolvedValue(mockAiProvenanceDetail);

    render(
      <AiQuestionSetWorkspace
        templateId="set-prov-1"
        onNavigateBack={vi.fn()}
      />
    );

    await waitFor(() => {
      expect(screen.getByText("Senior Backend Engineer")).toBeInTheDocument();
    });

    const detailsBtn = screen.getByRole("button", { name: /Provenance Details/i });
    fireEvent.click(detailsBtn);

    await waitFor(() => {
      expect(screen.getByText(/Question Set Provenance & Metadata/i)).toBeInTheDocument();
    });
  });
});
