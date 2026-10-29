import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { CandidateProfileReview, CandidateProfileData } from "../components/CandidateProfileReview";

const mockProfile: CandidateProfileData = {
  artifactId: "91710c77-2ec6-4ebd-bee3-495b29ebabbd",
  profileId: "810813c1-7391-4da7-8543-eeb7880b993d",
  profileVersion: 1,
  schemaVersion: "1.0",
  parserPipelineVersion: "pipeline-v1.3",
  provenance: "PARSER_EXTRACTED",
  parentProfileId: null,
  parentProfileVersion: null,
  createdAt: "2026-08-30T12:00:00Z",
  reviewRequired: false,
  warnings: [],
  identity: {
    resumeArtifactId: "91710c77-2ec6-4ebd-bee3-495b29ebabbd",
    fullName: {
      fieldName: "fullName",
      value: "Nisha Rao",
      normalizedValue: "Nisha Rao",
      confidence: 0.95,
      confidenceBand: "HIGH",
      evidenceIds: [],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    emails: [
      {
        fieldName: "email",
        value: "nisha@example.com",
        normalizedValue: "nisha@example.com",
        confidence: 0.9,
        confidenceBand: "HIGH",
        evidenceIds: [],
        extractionMethod: "RULE_BASED",
        reviewStatus: "CONFIRMED",
      },
    ],
    phoneNumbers: [],
    location: {
      fieldName: "location",
      value: "Seattle",
      normalizedValue: "Seattle",
      confidence: 0.8,
      confidenceBand: "HIGH",
      evidenceIds: [],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    linkedin: {
      fieldName: "linkedin",
      value: null,
      normalizedValue: null,
      confidence: null,
      confidenceBand: "LOW",
      evidenceIds: [],
      extractionMethod: null,
      reviewStatus: "UNCONFIRMED",
    },
    github: {
      fieldName: "github",
      value: null,
      normalizedValue: null,
      confidence: null,
      confidenceBand: "LOW",
      evidenceIds: [],
      extractionMethod: null,
      reviewStatus: "UNCONFIRMED",
    },
    portfolio: {
      fieldName: "portfolio",
      value: null,
      normalizedValue: null,
      confidence: null,
      confidenceBand: "LOW",
      evidenceIds: [],
      extractionMethod: null,
      reviewStatus: "UNCONFIRMED",
    },
  },
  professionalProfile: {
    headline: {
      fieldName: "headline",
      value: "Frontend Engineer",
      normalizedValue: "Frontend Engineer",
      confidence: 0.9,
      confidenceBand: "HIGH",
      evidenceIds: [],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    summary: {
      fieldName: "summary",
      value: "Passionate developer.",
      normalizedValue: "Passionate developer.",
      confidence: 0.85,
      confidenceBand: "HIGH",
      evidenceIds: [],
      extractionMethod: "RULE_BASED",
      reviewStatus: "CONFIRMED",
    },
    skills: [
      {
        fieldName: "skill",
        value: "React",
        normalizedValue: "React",
        confidence: 0.95,
        confidenceBand: "HIGH",
        evidenceIds: [],
        extractionMethod: "CANONICAL_MATCH",
        reviewStatus: "CONFIRMED",
      },
    ],
    employment: [],
    education: [],
    projects: [],
    certifications: [],
    languages: [],
    awards: [],
    publications: [],
  },
  evidence: [],
};

describe("Correction Workflow", () => {
  it("enters correction workspace when clicking Correct Profile", () => {
    render(
      <CandidateProfileReview
        profile={mockProfile}
        onNavigateBack={vi.fn()}
      />
    );

    const correctBtn = screen.getByText(/Correct Profile/i);
    fireEvent.click(correctBtn);

    expect(screen.getByText("Audited Profile Correction")).toBeInTheDocument();
    expect(screen.getByText("Drafting Version 2")).toBeInTheDocument();
    expect(screen.getByLabelText(/Correction Reason/i)).toBeInTheDocument();
  });

  it("adds a new skill in correction mode", () => {
    render(
      <CandidateProfileReview
        profile={mockProfile}
        onNavigateBack={vi.fn()}
      />
    );

    fireEvent.click(screen.getByText(/Correct Profile/i));

    const skillInput = screen.getByPlaceholderText(/Type new skill/i);
    fireEvent.change(skillInput, { target: { value: "GraphQL" } });
    const addBtns = screen.getAllByText(/^Add$/i);
    fireEvent.click(addBtns[0]);

    // Toggle Review Changes diff preview
    fireEvent.click(screen.getByText(/Review Changes/i));
    expect(screen.getByText("skills")).toBeInTheDocument();
    expect(screen.getByText("ADD_SET_ITEM")).toBeInTheDocument();
    expect(screen.getByText(/GraphQL/i)).toBeInTheDocument();
  });
});
