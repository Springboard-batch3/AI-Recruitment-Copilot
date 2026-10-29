import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { CandidatesDirectory, CandidateSummary } from "../components/CandidatesDirectory";

const mockCandidates: CandidateSummary[] = [
  {
    artifactId: "art-1",
    originalFilename: "Nisha_Rao_Resume.pdf",
    detectedFormat: "pdf",
    validationStatus: "VALIDATED",
    createdAt: "2026-08-30T12:00:00Z",
    hasExtraction: true,
    latestProfileVersion: 2,
    fullName: "Nisha Rao",
    headline: "Frontend Engineer",
    reviewRequired: false,
    provenance: "HUMAN_CORRECTED",
  },
  {
    artifactId: "art-2",
    originalFilename: "Aarav_Mehta_Resume.docx",
    detectedFormat: "docx",
    validationStatus: "VALIDATED",
    createdAt: "2026-08-30T11:00:00Z",
    hasExtraction: true,
    latestProfileVersion: 1,
    fullName: "Aarav Mehta",
    headline: "Backend Architect",
    reviewRequired: true,
    provenance: "PARSER_EXTRACTED",
  },
];

describe("CandidatesDirectory Component", () => {
  it("renders candidate rows with name, headline, version, and status", () => {
    render(
      <CandidatesDirectory
        candidates={mockCandidates}
        isLoading={false}
        onSelectCandidate={vi.fn()}
        onNavigateUpload={vi.fn()}
      />
    );

    expect(screen.getByText("Candidates")).toBeInTheDocument();
    expect(screen.getByText("Nisha Rao")).toBeInTheDocument();
    expect(screen.getByText("Frontend Engineer")).toBeInTheDocument();
    expect(screen.getByText("Aarav Mehta")).toBeInTheDocument();
    expect(screen.getByText("Backend Architect")).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
    // Needs Review badge is reserved for the candidate's detailed profile view
    expect(screen.queryByText("Needs Review")).toBeNull();
  });

  it("filters candidate list by search input", () => {
    render(
      <CandidatesDirectory
        candidates={mockCandidates}
        isLoading={false}
        onSelectCandidate={vi.fn()}
        onNavigateUpload={vi.fn()}
      />
    );

    const searchInput = screen.getByPlaceholderText(/Search candidates/i);
    fireEvent.change(searchInput, { target: { value: "Nisha" } });

    expect(screen.getByText("Nisha Rao")).toBeInTheDocument();
    expect(screen.queryByText("Aarav Mehta")).toBeNull();
  });

  it("renders empty state when no candidates exist", () => {
    render(
      <CandidatesDirectory
        candidates={[]}
        isLoading={false}
        onSelectCandidate={vi.fn()}
        onNavigateUpload={vi.fn()}
      />
    );

    expect(screen.getByText("No candidate profiles yet")).toBeInTheDocument();
  });
});
