import React, { useState, useRef, ChangeEvent, FormEvent, DragEvent } from "react";
import { apiFetch } from "../api/client";
import { Icon } from "./Icon";
import { CandidateProfileData } from "./CandidateProfileReview";
import { ExtractionResultData } from "./ExtractionReview";

type UploadState =
  | "idle"
  | "selected"
  | "uploading"
  | "validating"
  | "reading"
  | "assembling"
  | "ready"
  | "needs_review"
  | "failed";

type UploadResponseData = {
  artifactId: string;
  jobId: string;
  status: string;
};

type ProcessResponseData = {
  artifactId: string;
  jobId: string;
  extractionId: string;
  processingStatus: string;
  reviewRequired: boolean;
  blockCount: number;
};

const ERROR_MESSAGES: Record<string, string> = {
  FILE_TOO_LARGE: "The selected file exceeds the 10 MB limit.",
  INVALID_FILENAME: "The selected file has an invalid filename.",
  EMPTY_FILE: "The selected file is empty.",
  UNSUPPORTED_FILE_TYPE: "Only text-based PDF (.pdf) and native DOCX (.docx) files are supported.",
  FILE_TYPE_MISMATCH: "Declared file format does not match the actual document signature.",
  INVALID_PDF_SIGNATURE: "The file is not a valid PDF document.",
  MALFORMED_PDF: "The PDF could not be read or validated.",
  ENCRYPTED_PDF: "Password-protected PDFs cannot be processed.",
  PDF_PAGE_LIMIT_EXCEEDED: "The PDF exceeds the maximum five-page limit.",
  UNSAFE_PDF_CONTENT: "The document contains unsupported active or embedded content.",
  INVALID_DOCX_SIGNATURE: "The file is not a valid DOCX document.",
  MALFORMED_DOCX: "The DOCX document is corrupted or malformed.",
  INVALID_DOCX_STRUCTURE: "The document structure could not be parsed.",
  ENCRYPTED_DOCX: "Password-protected DOCX documents cannot be processed.",
  DOCX_ARCHIVE_LIMIT_EXCEEDED: "The DOCX package exceeds safe processing limits.",
  UNSAFE_DOCX_CONTENT: "The document contains unsupported active or embedded content.",
  INTAKE_UNAVAILABLE: "The intake service is temporarily unavailable. Please try again.",
  PDF_VALIDATION_UNAVAILABLE: "The service is temporarily unavailable. Please try again.",
  DOCX_VALIDATION_UNAVAILABLE: "The service is temporarily unavailable. Please try again.",
  DIRECT_TEXT_UNAVAILABLE: "No readable digital text found. Please upload a text-based PDF or DOCX file.",
  RESOURCE_LIMIT_EXCEEDED: "The document exceeded safe processing limits.",
  INTERNAL_PROCESSING_ERROR: "An error occurred while processing the document.",
};

const MAX_BYTES = 10 * 1024 * 1024; // 10 MB

function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

import { AtsImportModal } from "./ats/AtsImportModal";

interface ResumeUploadProps {
  onCandidateProcessed: (artifactId: string) => void;
  onNavigateCandidates?: () => void;
}

export function ResumeUpload({ onCandidateProcessed, onNavigateCandidates }: ResumeUploadProps) {
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadState, setUploadState] = useState<UploadState>("idle");
  const [isDragOver, setIsDragOver] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [currentArtifactId, setCurrentArtifactId] = useState<string | null>(null);
  const [isImportModalOpen, setIsImportModalOpen] = useState<boolean>(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const validateAndSelectFile = (file: File) => {
    const lowerName = file.name.toLowerCase();

    if (!lowerName.endsWith(".pdf") && !lowerName.endsWith(".docx")) {
      setSelectedFile(null);
      setUploadState("failed");
      setErrorMessage("Only PDF (.pdf) and DOCX (.docx) files are supported.");
      return;
    }

    if (file.size === 0) {
      setSelectedFile(null);
      setUploadState("failed");
      setErrorMessage("The selected file is empty.");
      return;
    }

    if (file.size > MAX_BYTES) {
      setSelectedFile(null);
      setUploadState("failed");
      setErrorMessage("The selected file exceeds the 10 MB limit.");
      return;
    }

    setSelectedFile(file);
    setUploadState("selected");
    setErrorMessage(null);
  };

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      validateAndSelectFile(files[0]);
    }
  };

  const handleDragOver = (e: DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = () => {
    setIsDragOver(false);
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      validateAndSelectFile(files[0]);
    }
  };

  const handleReset = () => {
    setSelectedFile(null);
    setUploadState("idle");
    setErrorMessage(null);
    setCurrentArtifactId(null);
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  };

  const handleStartProcessing = async (e: FormEvent) => {
    e.preventDefault();
    if (!selectedFile || uploadState === "uploading") return;

    setUploadState("uploading");
    setErrorMessage(null);

    const formData = new FormData();
    formData.append("file", selectedFile);

    try {
      // 1. Upload to intake endpoint
      const uploadRes = await apiFetch<UploadResponseData>("/api/v1/resumes", {
        method: "POST",
        body: formData,
      });

      setUploadState("validating");
      const artifactId = uploadRes.artifactId;
      setCurrentArtifactId(artifactId);

      // 2. Trigger asynchronous background extraction processing
      setUploadState("reading");
      const processRes = await apiFetch<ProcessResponseData>(
        `/api/v1/resumes/${artifactId}/process`,
        { method: "POST" }
      );

      if (processRes.reviewRequired) {
        setUploadState("needs_review");
      } else {
        setUploadState("ready");
      }

      // Automatically transition to candidate workspace
      onCandidateProcessed(artifactId);
    } catch (err) {
      console.error("Upload workflow error", err);
      setUploadState("failed");
      setErrorMessage("A network error occurred while uploading. Please check connection and retry.");
    }
  };

  const isProcessing =
    uploadState === "uploading" ||
    uploadState === "validating" ||
    uploadState === "reading" ||
    uploadState === "assembling";

  return (
    <div className="upload-workspace-container">
      {/* Top Header */}
      <div className="upload-workspace-header">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", flexWrap: "wrap", gap: "12px" }}>
          <div>
            {onNavigateCandidates && (
              <button type="button" onClick={onNavigateCandidates} className="btn-back-link">
                <Icon name="arrow-left" size={16} />
                <span>Back to Candidates</span>
              </button>
            )}
            <h2 className="upload-workspace-title">Upload Candidate Resume</h2>
            <p className="upload-workspace-subtitle">
              Upload text-based PDF or native DOCX resumes. The platform extracts structured profile records with full evidence traceability.
            </p>
          </div>

          <button
            type="button"
            className="btn btn-secondary btn-md"
            onClick={() => setIsImportModalOpen(true)}
            style={{ marginTop: "8px" }}
          >
            <Icon name="external-link" size={15} />
            <span>Import from ATS / File</span>
          </button>
        </div>
      </div>

      <AtsImportModal
        isOpen={isImportModalOpen}
        onClose={() => setIsImportModalOpen(false)}
        onImportComplete={() => {
          setIsImportModalOpen(false);
          if (onNavigateCandidates) {
            onNavigateCandidates();
          }
        }}
      />

      {/* Main Upload Dropzone Card */}
      <div className="upload-card-wrapper">
        <form onSubmit={handleStartProcessing}>
          <div
            className={`upload-dropzone ${isDragOver ? "dropzone-active" : ""} ${
              selectedFile ? "dropzone-selected" : ""
            }`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => {
              if (!selectedFile && fileInputRef.current) {
                fileInputRef.current.click();
              }
            }}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx"
              onChange={handleFileChange}
              className="sr-only"
              id="resume-file-picker"
              disabled={isProcessing}
            />

            {!selectedFile ? (
              <div className="dropzone-empty-content">
                <div className="dropzone-icon-circle">
                  <Icon name="upload" size={28} />
                </div>
                <h3 className="dropzone-primary-text">
                  Drag and drop resume here, or <span className="dropzone-browse-link">browse</span>
                </h3>
                <p className="dropzone-supported-text">
                  Supported formats: <strong>PDF</strong> (.pdf) and <strong>DOCX</strong> (.docx) up to 10 MB.
                </p>
              </div>
            ) : (
              <div className="dropzone-selected-content">
                <div className="selected-file-icon">
                  <Icon name="file-text" size={32} />
                </div>
                <div className="selected-file-details">
                  <span className="selected-file-name">{selectedFile.name}</span>
                  <span className="selected-file-size">{formatFileSize(selectedFile.size)}</span>
                </div>

                {!isProcessing && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleReset();
                    }}
                    className="btn-icon btn-remove-file"
                    aria-label="Remove selected file"
                  >
                    <Icon name="x" size={16} />
                  </button>
                )}
              </div>
            )}
          </div>

          {/* Privacy & Security Note */}
          <div className="upload-privacy-note">
            <Icon name="shield" size={15} className="text-muted" />
            <span>
              Documents are processed securely in local intake storage. Upload only documents you have permission to process.
            </span>
          </div>

          {/* Action Row */}
          <div className="upload-actions-bar">
            {selectedFile && !isProcessing && (
              <button
                type="submit"
                className="btn btn-primary btn-md"
              >
                <Icon name="sparkles" size={15} /> Process Resume
              </button>
            )}

            {selectedFile && !isProcessing && (
              <button
                type="button"
                onClick={handleReset}
                className="btn btn-secondary btn-md"
              >
                Clear Selection
              </button>
            )}
          </div>
        </form>

        {/* Processing Progress State */}
        {isProcessing && (
          <div className="processing-progress-card" role="status" aria-live="polite">
            <div className="processing-spinner" />
            <div className="processing-step-info">
              <span className="processing-step-title">
                {uploadState === "uploading" && "Uploading document to secure intake..."}
                {uploadState === "reading" && "Reading digital text..."}
                {uploadState === "assembling" && "Building structured candidate profile & evidence spans..."}
              </span>
              <span className="processing-step-desc">
                Extracting contact info, employment timeline, education, and technical skills.
              </span>
            </div>
          </div>
        )}

        {/* Error Feedback */}
        {uploadState === "failed" && errorMessage && (
          <div className="feedback-box error" role="alert">
            <Icon name="alert-triangle" size={18} />
            <div className="error-content">
              <p className="feedback-text error">{errorMessage}</p>
              <div className="action-row" style={{ marginTop: "10px" }}>
                <button
                  type="button"
                  onClick={handleReset}
                  className="btn btn-secondary btn-sm"
                >
                  Choose Another Document
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
