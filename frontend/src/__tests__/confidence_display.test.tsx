import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ExtractionReview, ExtractionResultData } from "../components/ExtractionReview";

const mockExtractionWithVariedConfidence: ExtractionResultData = {
  artifactId: "art-1",
  extractionId: "ext-1",
  extractorVersion: "extractor-v1.3",
  reviewRequired: false,
  blockCount: 6,
  blocks: [
    {
      blockId: "b-1",
      sequenceIndex: 1,
      pageNumber: 1,
      structuralLocation: "p1",
      blockType: "PARAGRAPH",
      extractionMethod: "PDF_TEXT",
      rawText: "First paragraph with real 0.95 confidence",
      normalizedText: "First paragraph with real 0.95 confidence",
      confidence: 0.95,
      reviewRequired: false,
    },
    {
      blockId: "b-2",
      sequenceIndex: 2,
      pageNumber: 1,
      structuralLocation: "p2",
      blockType: "HEADING",
      extractionMethod: "DOCX_TEXT",
      rawText: "Second block with real 0.80 confidence",
      normalizedText: "Second block with real 0.80 confidence",
      confidence: 0.8,
      reviewRequired: false,
    },
    {
      blockId: "b-3",
      sequenceIndex: 3,
      pageNumber: 1,
      structuralLocation: "p3",
      blockType: "PARAGRAPH",
      extractionMethod: "PDF_TEXT",
      rawText: "Third block with 0.0 confidence",
      normalizedText: "Third block with 0.0 confidence",
      confidence: 0.0,
      reviewRequired: false,
    },
    {
      blockId: "b-4",
      sequenceIndex: 4,
      pageNumber: 1,
      structuralLocation: "p4",
      blockType: "LIST_ITEM",
      extractionMethod: "DOCX_TEXT",
      rawText: "Fourth block with null confidence",
      normalizedText: "Fourth block with null confidence",
      confidence: null,
      reviewRequired: false,
    },
    {
      blockId: "b-5",
      sequenceIndex: 5,
      pageNumber: 1,
      structuralLocation: "p5",
      blockType: "TABLE_CELL",
      extractionMethod: "PDF_LAYOUT",
      rawText: "Fifth block with undefined confidence",
      normalizedText: "Fifth block with undefined confidence",
      confidence: undefined as unknown as null,
      reviewRequired: false,
    },
    {
      blockId: "b-6",
      sequenceIndex: 6,
      pageNumber: 1,
      structuralLocation: "p6",
      blockType: "PARAGRAPH",
      extractionMethod: "OCR",
      rawText: "Sixth block with invalid confidence > 1",
      normalizedText: "Sixth block with invalid confidence > 1",
      confidence: 1.5,
      reviewRequired: false,
    },
  ],
};

describe("Honest Confidence Display & Label Polish", () => {
  it("renders 95% confidence for real 0.95 confidence value", () => {
    render(
      <ExtractionReview
        extraction={mockExtractionWithVariedConfidence}
        onNavigateBack={vi.fn()}
      />
    );

    expect(screen.getByText("95% confidence")).toBeInTheDocument();
  });

  it("renders 80% confidence for real 0.80 value and 0% confidence for 0.0 value", () => {
    render(
      <ExtractionReview
        extraction={mockExtractionWithVariedConfidence}
        onNavigateBack={vi.fn()}
      />
    );

    expect(screen.getByText("80% confidence")).toBeInTheDocument();
    expect(screen.getByText("0% confidence")).toBeInTheDocument();
  });

  it("omits percentage badge completely when confidence is null, undefined, or invalid", () => {
    render(
      <ExtractionReview
        extraction={mockExtractionWithVariedConfidence}
        onNavigateBack={vi.fn()}
      />
    );

    // Only blocks 1, 2, 3 have valid confidence in [0, 1]
    const confBadges = screen.getAllByText(/% confidence$/);
    expect(confBadges.length).toBe(3);
  });

  it("polishes technical badges into human-readable strings", () => {
    render(
      <ExtractionReview
        extraction={mockExtractionWithVariedConfidence}
        onNavigateBack={vi.fn()}
      />
    );

    expect(screen.getAllByText("Paragraph").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Heading")).toBeInTheDocument();
    expect(screen.getByText("List item")).toBeInTheDocument();
    expect(screen.getByText("Table cell")).toBeInTheDocument();
    expect(screen.getAllByText("Direct PDF text").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Direct DOCX text").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("PDF layout")).toBeInTheDocument();
    expect(screen.getByText("OCR text")).toBeInTheDocument();
  });
});
