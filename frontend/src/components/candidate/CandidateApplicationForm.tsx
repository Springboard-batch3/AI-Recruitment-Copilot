import React, { useState, useEffect, useRef } from "react";
import {
  CandidateApplication,
  CandidateResumeStatus,
  createOrGetApplicationDraft,
  uploadApplicationResume,
  fetchApplicationResumeStatus,
  submitApplicationDraft,
} from "../../api/candidateApplications";
import { CandidateJobDetail } from "../../api/candidateJobs";
import { Icon } from "../Icon";

interface CandidateApplicationFormProps {
  job: CandidateJobDetail;
  onBack: () => void;
  onSuccess: (applicationId: string) => void;
}

export function CandidateApplicationForm({
  job,
  onBack,
  onSuccess,
}: CandidateApplicationFormProps) {
  const [draft, setDraft] = useState<CandidateApplication | null>(null);
  const [resumeStatus, setResumeStatus] = useState<CandidateResumeStatus | null>(null);
  const [isInitializing, setIsInitializing] = useState<boolean>(true);
  const [isUploading, setIsUploading] = useState<boolean>(false);
  const [isSubmitting, setIsSubmitting] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const pollingRef = useRef<any>(null);

  // 1. Initialize draft on mount
  useEffect(() => {
    let mounted = true;
    const initDraft = async () => {
      try {
        setIsInitializing(true);
        setError(null);
        const app = await createOrGetApplicationDraft({
          jobId: job.jobId,
          expectedRevision: job.revision,
        });
        if (!mounted) return;
        setDraft(app);

        // If draft already has an artifact/processing state, check status
        if (app.artifactId || app.status !== "DRAFT") {
          const statusRes = await fetchApplicationResumeStatus(app.applicationId);
          if (mounted) {
            setResumeStatus(statusRes);
          }
        }
      } catch (err: any) {
        if (mounted) {
          setError(err?.message || "Failed to initialize application draft.");
        }
      } finally {
        if (mounted) {
          setIsInitializing(false);
        }
      }
    };

    initDraft();

    return () => {
      mounted = false;
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
      }
    };
  }, [job.jobId, job.revision]);

  // 2. Poll resume processing status if in RESUME_PROCESSING
  useEffect(() => {
    if (!draft) return;

    const isProcessing =
      draft.status === "RESUME_PROCESSING" ||
      (resumeStatus && resumeStatus.status === "RESUME_PROCESSING");

    if (isProcessing) {
      if (!pollingRef.current) {
        pollingRef.current = setInterval(async () => {
          try {
            const statusRes = await fetchApplicationResumeStatus(draft.applicationId);
            setResumeStatus(statusRes);
            if (statusRes.status !== "RESUME_PROCESSING") {
              clearInterval(pollingRef.current);
              pollingRef.current = null;
              setDraft((prev) =>
                prev
                  ? {
                      ...prev,
                      status: statusRes.status,
                      canSubmit: statusRes.canSubmit,
                      artifactId: statusRes.artifactId,
                      profileId: statusRes.profileId,
                      profileVersion: statusRes.profileVersion,
                    }
                  : null
              );
            }
          } catch (e) {
            // keep polling or fail silently
          }
        }, 2000);
      }
    } else {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    }

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, [draft, resumeStatus]);

  // Handle file selection
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files.length > 0) {
      const file = e.target.files[0];
      validateAndSetFile(file);
    }
  };

  const handleDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const file = e.dataTransfer.files[0];
      validateAndSetFile(file);
    }
  };

  const validateAndSetFile = (file: File) => {
    setError(null);
    const validExtensions = [".pdf", ".docx"];
    const fileExt = file.name.substring(file.name.lastIndexOf(".")).toLowerCase();

    if (!validExtensions.includes(fileExt)) {
      setError("Invalid file format. Please upload a PDF or DOCX resume document.");
      return;
    }

    const maxBytes = 10 * 1024 * 1024;
    if (file.size > maxBytes) {
      setError("File size exceeds 10 MiB limit. Please upload a smaller file.");
      return;
    }

    setSelectedFile(file);
    if (resumeStatus?.status === "PROCESSING_FAILED" || draft?.status === "PROCESSING_FAILED") {
      setResumeStatus(null);
    }
  };

  // Upload Resume
  const handleUploadResume = async () => {
    if (!draft || !selectedFile || isUploading) return;

    try {
      setIsUploading(true);
      setError(null);
      const res = await uploadApplicationResume(draft.applicationId, selectedFile);
      setResumeStatus(res);
      setDraft((prev) =>
        prev
          ? {
              ...prev,
              status: res.status,
              canSubmit: res.canSubmit,
              artifactId: res.artifactId,
              profileId: res.profileId,
              profileVersion: res.profileVersion,
            }
          : null
      );
    } catch (err: any) {
      setError(err?.message || "Failed to upload and process resume. Please try again.");
    } finally {
      setIsUploading(false);
    }
  };

  // Final Application Submission
  const handleSubmitApplication = async () => {
    if (!draft || isSubmitting) return;

    try {
      setIsSubmitting(true);
      setError(null);
      const submittedApp = await submitApplicationDraft(draft.applicationId);
      onSuccess(submittedApp.applicationId);
    } catch (err: any) {
      setError(err?.message || "Failed to submit application. Please try again.");
      setIsSubmitting(false);
    }
  };

  const currentStatus = resumeStatus?.status || draft?.status || "DRAFT";
  const canSubmit = currentStatus === "READY_TO_SUBMIT" && !isUploading && !isSubmitting;

  return (
    <div
      className="candidate-application-form-container"
      style={{
        maxWidth: "800px",
        margin: "0 auto",
        padding: "24px 20px",
      }}
    >
      {/* Header & Back Button */}
      <div style={{ marginBottom: "20px" }}>
        <button
          type="button"
          className="btn btn-secondary btn-sm"
          onClick={onBack}
          style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
          aria-label="Back to Job Description"
        >
          <Icon name="arrow-left" size={14} />
          <span>Back to Job</span>
        </button>
      </div>

      {/* Main Card */}
      <div
        style={{
          backgroundColor: "var(--bg-surface, #ffffff)",
          borderRadius: "var(--radius-lg, 12px)",
          border: "1px solid var(--border-subtle, #e2e8f0)",
          padding: "32px",
          boxShadow: "var(--shadow-card, 0 1px 3px rgba(16, 24, 40, 0.05))",
        }}
      >
        {/* Job Header Summary */}
        <div
          style={{
            borderBottom: "1px solid var(--border-subtle, #e2e8f0)",
            paddingBottom: "20px",
            marginBottom: "24px",
          }}
        >
          <span
            style={{
              fontSize: "12px",
              fontWeight: 600,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              color: "var(--accent-primary)",
              display: "block",
              marginBottom: "4px",
            }}
          >
            Apply for Position
          </span>
          <h1
            style={{
              fontSize: "22px",
              fontWeight: 700,
              color: "var(--text-primary, #0f172a)",
              margin: "0 0 8px 0",
            }}
          >
            {job.title}
          </h1>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "16px", color: "var(--text-secondary, #64748b)", fontSize: "14px" }}>
            {job.department && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                <Icon name="folder" size={14} />
                {job.department}
              </span>
            )}
            {job.location && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
                <Icon name="map-pin" size={14} />
                {job.location}
              </span>
            )}
          </div>
        </div>

        {/* Global Error Notice */}
        {error && (
          <div
            className="feedback-box error"
            role="alert"
            style={{
              marginBottom: "20px",
              display: "flex",
              alignItems: "center",
              gap: "8px",
              padding: "12px 16px",
              borderRadius: "8px",
              backgroundColor: "var(--status-danger-bg, #eadfe7)",
              color: "var(--status-danger, #63445b)",
              border: "1px solid var(--status-danger-border, #c9b5c3)",
            }}
          >
            <Icon name="alert-circle" size={18} />
            <span style={{ fontSize: "14px", fontWeight: 500 }}>{error}</span>
          </div>
        )}

        {isInitializing ? (
          <div style={{ textAlign: "center", padding: "40px 0" }}>
            <div className="loading-spinner" style={{ margin: "0 auto 12px" }} />
            <p style={{ fontSize: "14px", color: "var(--text-secondary, #64748b)" }}>
              Initializing application draft...
            </p>
          </div>
        ) : (
          <div>
            {/* Step 1: Resume Upload Section */}
            <div style={{ marginBottom: "28px" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                <h2 style={{ fontSize: "16px", fontWeight: 600, color: "var(--text-primary, #0f172a)", margin: 0 }}>
                  1. Upload Resume
                </h2>
                <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)" }}>
                  PDF or DOCX (Max 10MB)
                </span>
              </div>
              <p style={{ fontSize: "13px", color: "var(--text-secondary, #64748b)", margin: "0 0 16px 0" }}>
                Every application requires a valid text-based resume document. Scanned images (OCR) are not supported.
              </p>

              {/* Upload Dropzone */}
              <div
                onDragOver={(e) => e.preventDefault()}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                style={{
                  border: "2px dashed var(--border-subtle, #cbd5e1)",
                  borderRadius: "var(--radius-md, 8px)",
                  padding: "28px 20px",
                  textAlign: "center",
                  backgroundColor: "var(--bg-surface-subtle, #f8fafc)",
                  cursor: "pointer",
                  transition: "border-color 0.2s, background-color 0.2s",
                }}
              >
                <input
                  type="file"
                  ref={fileInputRef}
                  onChange={handleFileChange}
                  accept=".pdf,.docx,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  style={{ display: "none" }}
                />
                <div style={{ color: "var(--accent-primary)", marginBottom: "8px" }}>
                  <Icon name="upload" size={32} />
                </div>
                <p style={{ fontSize: "14px", fontWeight: 500, color: "var(--text-primary, #1e293b)", margin: "0 0 4px 0" }}>
                  {selectedFile ? selectedFile.name : "Click or drag & drop resume here"}
                </p>
                <p style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", margin: 0 }}>
                  {selectedFile
                    ? `${(selectedFile.size / (1024 * 1024)).toFixed(2)} MB • Click to replace`
                    : "Supports PDF and DOCX documents"}
                </p>
              </div>

              {/* Upload Button */}
              {selectedFile && currentStatus !== "RESUME_PROCESSING" && (
                <div style={{ marginTop: "12px", display: "flex", justifyContent: "flex-end" }}>
                  <button
                    type="button"
                    className="btn btn-secondary btn-sm"
                    onClick={handleUploadResume}
                    disabled={isUploading}
                    style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}
                  >
                    <Icon name="upload" size={14} />
                    <span>{isUploading ? "Uploading & Extracting..." : "Process Resume"}</span>
                  </button>
                </div>
              )}
            </div>

            {/* Step 2: Processing & Extraction Progress */}
            {currentStatus === "RESUME_PROCESSING" && (
              <div
                style={{
                  backgroundColor: "var(--accent-subtle, #f0f4ff)",
                  border: "1px solid var(--accent-subtle-border, #d0e0fd)",
                  borderRadius: "8px",
                  padding: "20px",
                  marginBottom: "28px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "12px", marginBottom: "12px" }}>
                  <div className="loading-spinner" style={{ width: "20px", height: "20px" }} />
                  <div>
                    <h3 style={{ fontSize: "14px", fontWeight: 600, color: "var(--accent-primary, #3b50c4)", margin: 0 }}>
                      Extracting Information from Resume
                    </h3>
                    <p style={{ fontSize: "12px", color: "var(--text-secondary, #4b5563)", margin: "2px 0 0 0" }}>
                      Validating document text, extracting skills, and preparing your candidate profile...
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* Step 3: Ready to Submit / Profile Review Preview */}
            {currentStatus === "READY_TO_SUBMIT" && resumeStatus && (
              <div
                style={{
                  backgroundColor: "var(--status-success-bg, #f0fdf4)",
                  border: "1px solid var(--status-success-border, #bbf7d0)",
                  borderRadius: "8px",
                  padding: "20px",
                  marginBottom: "28px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "12px" }}>
                  <Icon name="check-circle" size={20} color="var(--status-success, #16a34a)" />
                  <h3 style={{ fontSize: "15px", fontWeight: 600, color: "var(--status-success, #15803d)", margin: 0 }}>
                    Resume Processed & Profile Ready
                  </h3>
                </div>

                <div style={{ fontSize: "13px", color: "var(--text-secondary, #374151)", lineHeight: 1.6 }}>
                  {resumeStatus.candidateName && (
                    <div style={{ marginBottom: "4px" }}>
                      <strong>Identified Name:</strong> {resumeStatus.candidateName}
                    </div>
                  )}
                  {resumeStatus.candidateEmail && (
                    <div style={{ marginBottom: "4px" }}>
                      <strong>Email:</strong> {resumeStatus.candidateEmail}
                    </div>
                  )}
                  {resumeStatus.filename && (
                    <div style={{ marginBottom: "4px" }}>
                      <strong>Resume File:</strong> {resumeStatus.filename}
                    </div>
                  )}
                  {resumeStatus.skillsFound && resumeStatus.skillsFound.length > 0 && (
                    <div style={{ marginTop: "8px" }}>
                      <strong>Extracted Skills ({resumeStatus.skillsFound.length}):</strong>
                      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "6px" }}>
                        {resumeStatus.skillsFound.slice(0, 12).map((skill, idx) => (
                          <span
                            key={idx}
                            style={{
                              fontSize: "12px",
                              padding: "2px 8px",
                              backgroundColor: "var(--bg-surface, #ffffff)",
                              border: "1px solid var(--status-success-border, #bbf7d0)",
                              borderRadius: "4px",
                              color: "var(--text-primary, #1e293b)",
                            }}
                          >
                            {skill}
                          </span>
                        ))}
                        {resumeStatus.skillsFound.length > 12 && (
                          <span style={{ fontSize: "12px", color: "var(--text-muted, #64748b)", alignSelf: "center" }}>
                            +{resumeStatus.skillsFound.length - 12} more
                          </span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}

            {/* Error Processing State */}
            {currentStatus === "PROCESSING_FAILED" && (
              <div
                style={{
                  backgroundColor: "var(--status-danger-bg, #eadfe7)",
                  border: "1px solid var(--status-danger-border, #c9b5c3)",
                  borderRadius: "8px",
                  padding: "20px",
                  marginBottom: "28px",
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
                  <Icon name="alert-circle" size={20} color="var(--status-danger, #63445b)" />
                  <h3 style={{ fontSize: "15px", fontWeight: 600, color: "var(--status-danger, #63445b)", margin: 0 }}>
                    Resume Extraction Failed
                  </h3>
                </div>
                <p style={{ fontSize: "13px", color: "var(--text-secondary, #4b5563)", margin: "0 0 12px 0" }}>
                  {resumeStatus?.errorMessage || "Could not extract readable text from the uploaded resume. Scanned images and OCR are not supported. Please upload a standard text PDF or DOCX file."}
                </p>
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={() => fileInputRef.current?.click()}
                >
                  Choose Another Resume
                </button>
              </div>
            )}

            {/* Action Buttons */}
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                borderTop: "1px solid var(--border-subtle, #e2e8f0)",
                paddingTop: "20px",
                marginTop: "12px",
              }}
            >
              <button
                type="button"
                className="btn btn-secondary"
                onClick={onBack}
                disabled={isSubmitting}
              >
                Cancel
              </button>

              <button
                type="button"
                className="btn btn-primary"
                onClick={handleSubmitApplication}
                disabled={!canSubmit}
                style={{ minWidth: "160px" }}
              >
                {isSubmitting ? "Submitting Application..." : "Submit Application"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
