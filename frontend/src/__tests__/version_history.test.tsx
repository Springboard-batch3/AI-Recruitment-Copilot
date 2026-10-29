import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { VersionHistoryDrawer, ProfileSummary } from "../components/VersionHistoryDrawer";

const mockHistoryList: ProfileSummary[] = [
  {
    profileId: "prof-2",
    profileVersion: 2,
    schemaVersion: "1.0",
    parserPipelineVersion: "pipeline-v1.3",
    provenance: "HUMAN_CORRECTED",
    reviewRequired: false,
    createdAt: "2026-08-30T13:00:00Z",
    parentProfileId: "prof-1",
    parentProfileVersion: 1,
    correctionSummary: {
      correctionId: "corr-1",
      reasonCategory: "INCORRECT_EXTRACTION",
      reviewerNote: "Corrected organization name.",
      operationCount: 1,
    },
  },
  {
    profileId: "prof-1",
    profileVersion: 1,
    schemaVersion: "1.0",
    parserPipelineVersion: "pipeline-v1.3",
    provenance: "PARSER_EXTRACTED",
    reviewRequired: true,
    createdAt: "2026-08-30T12:00:00Z",
    parentProfileId: null,
    parentProfileVersion: null,
    correctionSummary: null,
  },
];

describe("VersionHistoryDrawer Component", () => {
  it("renders chronological version history and provenance badges", () => {
    render(
      <VersionHistoryDrawer
        isOpen={true}
        onClose={vi.fn()}
        historyList={mockHistoryList}
        currentVersion={2}
        onSelectVersion={vi.fn()}
      />
    );

    expect(screen.getByText("Profile Version Lineage")).toBeInTheDocument();
    expect(screen.getByText("Version 2")).toBeInTheDocument();
    expect(screen.getByText("Human Corrected")).toBeInTheDocument();
    expect(screen.getByText("Version 1")).toBeInTheDocument();
    expect(screen.getByText("Parser Generated")).toBeInTheDocument();
    expect(screen.getByText("Incorrect Extraction")).toBeInTheDocument();
    expect(screen.getByText(/Corrected organization name/i)).toBeInTheDocument();
  });

  it("calls onSelectVersion when viewing past snapshot", () => {
    const onSelect = vi.fn();
    render(
      <VersionHistoryDrawer
        isOpen={true}
        onClose={vi.fn()}
        historyList={mockHistoryList}
        currentVersion={2}
        onSelectVersion={onSelect}
      />
    );

    const viewV1Btn = screen.getByText(/View Snapshot v1/i);
    fireEvent.click(viewV1Btn);
    expect(onSelect).toHaveBeenCalledWith(1);
  });
});
