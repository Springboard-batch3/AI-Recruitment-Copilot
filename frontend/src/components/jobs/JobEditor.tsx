import React, { useState, useEffect, useMemo, FormEvent } from "react";
import {
  fetchJobDetail,
  createJobRequisition,
  updateJobDraft,
  saveDraftRequirements,
  checkJobReadiness,
  markJobReady,
  releaseJob,
  createSuccessorVersion,
  JobDetail,
  JobCreatePayload,
  JobDraftUpdatePayload,
  RequirementItem,
  RequirementCategory,
  JobReadinessResponse,
  JobReadinessFinding,
} from "../../api/jobs";
import { OccupationSearchItem, OccupationSkillSuggestion, getOccupationSkillSuggestions } from "../../api/taxonomy";
import { Icon } from "../Icon";
import { OccupationSelector } from "./OccupationSelector";
import { RequirementCard } from "./RequirementCard";

interface JobEditorProps {
  jobId?: string | null; // null if creating a new job
  onNavigateBack: () => void;
  onJobCreated?: (newJobId: string) => void;
  onJobUpdated?: (jobId: string) => void;
  onCompareCandidates?: (params: {
    jobId: string;
    jobTitle: string;
    jobVersionId: string;
    revision: number;
    requirementsCount: number;
  }) => void;
  onOpenAiInterviews?: (jobId: string) => void;
  onOpenApplicants?: (jobId: string, jobTitle: string) => void;
}

export function JobEditor({ jobId, onNavigateBack, onJobCreated, onJobUpdated, onCompareCandidates, onOpenAiInterviews, onOpenApplicants }: JobEditorProps) {
  // Main Draft Data
  const [jobDetail, setJobDetail] = useState<JobDetail | null>(null);
  const [isLoading, setIsLoading] = useState(Boolean(jobId));
  const [loadError, setLoadError] = useState<string | null>(null);

  // Form Fields
  const [title, setTitle] = useState("");
  const [department, setDepartment] = useState("");
  const [location, setLocation] = useState("");
  const [employmentType, setEmploymentType] = useState<string>("FULL_TIME");
  const [workplaceType, setWorkplaceType] = useState<string>("HYBRID");
  const [openingsCount, setOpeningsCount] = useState<string>("1");
  const [internalReference, setInternalReference] = useState("");
  const [summary, setSummary] = useState("");
  const [responsibilities, setResponsibilities] = useState("");
  const [jobDescriptionText, setJobDescriptionText] = useState("");
  const [occupationConceptId, setOccupationConceptId] = useState<string | null>(null);
  const [occupationTitle, setOccupationTitle] = useState<string | null>(null);

  // Occupation Skill Suggestions State
  const [suggestedSkills, setSuggestedSkills] = useState<OccupationSkillSuggestion[]>([]);
  const [isLoadingSuggestions, setIsLoadingSuggestions] = useState(false);

  // Disclosures & Progressive Visibility
  const [showJobDetailsMore, setShowJobDetailsMore] = useState(false);
  const [showOccupationSection, setShowOccupationSection] = useState(false);

  // Requirements State
  const [requirements, setRequirements] = useState<RequirementItem[]>([]);
  const [expandedReqIndex, setExpandedReqIndex] = useState<number | null>(null);

  // Revision & Status Tracking
  const [currentRevision, setCurrentRevision] = useState<number>(1);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccessMsg, setSaveSuccessMsg] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [conflictError, setConflictError] = useState<string | null>(null);
  const [isDirty, setIsDirty] = useState(false);

  // Readiness & Revision Locking State
  const [readinessResult, setReadinessResult] = useState<JobReadinessResponse | null>(null);
  const [isCheckingReadiness, setIsCheckingReadiness] = useState(false);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [isReadyModalOpen, setIsReadyModalOpen] = useState(false);
  const [isMarkingReady, setIsMarkingReady] = useState(false);
  const [isCreatingSuccessor, setIsCreatingSuccessor] = useState(false);

  // Job Release State
  const [isReleasing, setIsReleasing] = useState(false);
  const [isReleaseConfirmOpen, setIsReleaseConfirmOpen] = useState(false);

  const versionStatus = jobDetail?.currentVersion?.versionStatus || "DRAFT";
  const versionNumber = jobDetail?.currentVersion?.versionNumber || 1;
  const isReadOnly = versionStatus === "READY_FOR_MATCHING" || versionStatus === "APPROVED";
  const publicationStatus = jobDetail?.publicationStatus || "NOT_RELEASED";
  const isReleased = publicationStatus === "RELEASED";
  const releasedAt = jobDetail?.releasedAt;

  // Initial Load for Existing Job
  useEffect(() => {
    if (!jobId) {
      setIsLoading(false);
      return;
    }

    let isMounted = true;
    async function loadJob() {
      try {
        setIsLoading(true);
        setLoadError(null);
        const data = await fetchJobDetail(jobId!);
        if (!isMounted) return;

        setJobDetail(data);
        const v = data.currentVersion;
        setTitle(v.title || "");
        setDepartment(v.department || "");
        setLocation(v.location || "");
        setEmploymentType(v.employmentType || "FULL_TIME");
        setWorkplaceType(v.workplaceType || "HYBRID");
        setOpeningsCount(v.openingsCount ? String(v.openingsCount) : "1");
        setInternalReference(v.internalReference || "");
        if (v.internalReference) {
          setShowJobDetailsMore(true);
        }
        setSummary(v.summary || "");
        setResponsibilities(v.responsibilities || "");
        setJobDescriptionText(v.jobDescriptionText || "");
        setOccupationConceptId(v.occupationConceptId || null);
        setOccupationTitle(v.occupationTitle || null);
        if (v.occupationConceptId) {
          setShowOccupationSection(true);
        }
        setCurrentRevision(v.revision || 1);
        setRequirements(v.requirements || []);
        setIsDirty(false);
      } catch (err: any) {
        if (isMounted) {
          setLoadError(err?.message || "Failed to load job requisition.");
        }
      } finally {
        if (isMounted) setIsLoading(false);
      }
    }

    loadJob();
    return () => {
      isMounted = false;
    };
  }, [jobId]);

  // Unsaved changes browser prompt
  useEffect(() => {
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      if (isDirty) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [isDirty]);

  // Fetch suggested skills when occupation is selected
  useEffect(() => {
    if (!occupationConceptId) {
      setSuggestedSkills([]);
      setIsLoadingSuggestions(false);
      return;
    }
    let isCurrent = true;
    setIsLoadingSuggestions(true);
    getOccupationSkillSuggestions(occupationConceptId, 25)
      .then((data) => {
        if (isCurrent) {
          setSuggestedSkills(data || []);
        }
      })
      .catch(() => {
        if (isCurrent) setSuggestedSkills([]);
      })
      .finally(() => {
        if (isCurrent) setIsLoadingSuggestions(false);
      });

    return () => {
      isCurrent = false;
    };
  }, [occupationConceptId]);

  // Handle Occupation Selection
  const handleSelectOccupation = (item: OccupationSearchItem | null) => {
    if (item) {
      setOccupationConceptId(item.conceptId);
      setOccupationTitle(item.canonicalTitle);
    } else {
      setOccupationConceptId(null);
      setOccupationTitle(null);
    }
    setIsDirty(true);
  };

  // Add a suggested skill to requirements
  const handleAddSuggestedSkill = (sug: OccupationSkillSuggestion) => {
    // Check if already added
    const alreadyExists = requirements.some(
      (r) =>
        r.taxonomySkillConceptId === sug.conceptId ||
        r.label.trim().toLowerCase() === sug.canonicalLabel.trim().toLowerCase()
    );
    if (alreadyExists) return;

    const newIndex = requirements.length;
    const newReq: RequirementItem = {
      displayOrder: newIndex,
      category: "SKILL",
      label: sug.canonicalLabel,
      taxonomySkillConceptId: sug.conceptId,
      necessity: "PREFERRED",
      priority: "MEDIUM",
      minExperienceMonths: null,
      recencyYears: null,
      acceptedEvidenceTypes: ["EMPLOYMENT", "PROJECT", "SKILL_LIST"],
      acceptedEquivalents: [],
      origin: "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED",
    };
    setRequirements([...requirements, newReq]);
    setExpandedReqIndex(newIndex);
    setIsDirty(true);
  };

  // Requirement Handlers
  const handleAddRequirement = (category: RequirementCategory = "SKILL") => {
    const newIndex = requirements.length;
    const newReq: RequirementItem = {
      displayOrder: newIndex,
      category,
      label: "",
      necessity: "PREFERRED",
      priority: "MEDIUM",
      minExperienceMonths: null,
      recencyYears: null,
      acceptedEvidenceTypes: ["EMPLOYMENT", "PROJECT"],
      acceptedEquivalents: [],
      origin: "RECRUITER_ENTERED",
    };
    setRequirements([...requirements, newReq]);
    setExpandedReqIndex(newIndex);
    setIsDirty(true);
  };

  const handleUpdateRequirement = (idx: number, updated: RequirementItem) => {
    const copy = [...requirements];
    // Re-evaluate client-side rationale warning metadata on edit so stale warnings from previous draft saves don't persist
    let warningMetadata = updated.warningMetadata ? [...updated.warningMetadata] : [];
    const hasRationale = Boolean(updated.rationale && updated.rationale.trim());
    if (hasRationale) {
      warningMetadata = warningMetadata.filter(
        (w) => w.code !== "MISSING_MUST_HAVE_RATIONALE" && w.code !== "MISSING_CRITICAL_RATIONALE"
      );
    }
    const reviewRequired = warningMetadata.length > 0;
    copy[idx] = {
      ...updated,
      warningMetadata,
      reviewRequired,
    };
    setRequirements(copy);
    setIsDirty(true);
  };

  const handleRemoveRequirement = (idx: number) => {
    const filtered = requirements.filter((_, i) => i !== idx);
    const reordered = filtered.map((r, i) => ({ ...r, displayOrder: i }));
    setRequirements(reordered);
    if (expandedReqIndex === idx) {
      setExpandedReqIndex(null);
    } else if (expandedReqIndex !== null && expandedReqIndex > idx) {
      setExpandedReqIndex(expandedReqIndex - 1);
    }
    setIsDirty(true);
  };

  const handleMoveRequirement = (idx: number, direction: "up" | "down") => {
    const targetIdx = direction === "up" ? idx - 1 : idx + 1;
    if (targetIdx < 0 || targetIdx >= requirements.length) return;

    const copy = [...requirements];
    const temp = copy[idx];
    copy[idx] = copy[targetIdx];
    copy[targetIdx] = temp;

    const reordered = copy.map((r, i) => ({ ...r, displayOrder: i }));
    setRequirements(reordered);
    if (expandedReqIndex === idx) {
      setExpandedReqIndex(targetIdx);
    } else if (expandedReqIndex === targetIdx) {
      setExpandedReqIndex(idx);
    }
    setIsDirty(true);
  };

  // Compute Draft Review Summary Metrics
  const reviewSummary = useMemo(() => {
    let mustHaveCount = 0;
    let preferredCount = 0;
    let onetConfirmedCount = 0;
    let customCount = 0;
    let reviewRequiredCount = 0;
    let missingRationaleCount = 0;

    for (const r of requirements) {
      if (r.necessity === "MUST_HAVE") mustHaveCount++;
      else preferredCount++;

      if (r.origin === "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED") onetConfirmedCount++;
      else customCount++;

      if (r.reviewRequired || (r.warningMetadata && r.warningMetadata.length > 0)) {
        reviewRequiredCount++;
      }

      if ((r.necessity === "MUST_HAVE" || r.priority === "CRITICAL") && !r.rationale?.trim()) {
        missingRationaleCount++;
      }
    }

    return {
      total: requirements.length,
      mustHaveCount,
      preferredCount,
      onetConfirmedCount,
      customCount,
      reviewRequiredCount,
      missingRationaleCount,
    };
  }, [requirements]);

  // Save Flow
  const performSaveInternal = async (): Promise<JobDetail> => {
    if (!title.trim()) {
      setSaveError("Job title is required.");
      handleFocusField("title");
      throw new Error("Job title is required.");
    }

    // Validate requirements labels and constraints
    const seenReqs = new Set<string>();
    for (let i = 0; i < requirements.length; i++) {
      const r = requirements[i];
      const trimmedLabel = r.label.trim();
      if (!trimmedLabel) {
        setSaveError(`Requirement #${i + 1} has an empty name.`);
        throw new Error(`Requirement #${i + 1} has an empty name.`);
      }
      const normKey = `${r.category}:${trimmedLabel.toLowerCase()}`;
      if (seenReqs.has(normKey)) {
        setSaveError(`Duplicate requirement "${trimmedLabel}" in category "${r.category}". Requirements must be unique per category.`);
        throw new Error(`Duplicate requirement "${trimmedLabel}" in category "${r.category}".`);
      }
      seenReqs.add(normKey);

      if (r.minExperienceMonths !== undefined && r.minExperienceMonths !== null) {
        if (isNaN(r.minExperienceMonths) || r.minExperienceMonths < 1) {
          setSaveError(`Requirement #${i + 1} ("${trimmedLabel}"): Minimum experience must be at least 1 month.`);
          throw new Error("Invalid minExperienceMonths");
        }
      }

      if (r.recencyYears !== undefined && r.recencyYears !== null) {
        if (isNaN(r.recencyYears) || r.recencyYears < 1 || r.recencyYears > 50) {
          setSaveError(`Requirement #${i + 1} ("${trimmedLabel}"): Recency must be between 1 and 50 years.`);
          throw new Error("Invalid recencyYears");
        }
      }
    }

    if (!jobId) {
      // 1. Create New Job Requisition + Draft
      const createPayload: JobCreatePayload = {
        title: title.trim(),
        department: department.trim() || null,
        location: location.trim() || null,
        employmentType: employmentType as any,
        workplaceType: workplaceType as any,
        openingsCount: openingsCount ? parseInt(openingsCount, 10) : null,
        internalReference: internalReference.trim() || null,
        summary: summary.trim() || null,
        responsibilities: responsibilities.trim() || null,
        jobDescriptionText: jobDescriptionText.trim() || null,
        occupationConceptId: occupationConceptId || null,
      };

      const createdJob = await createJobRequisition(createPayload);
      const newJobId = createdJob.jobId;

      if (requirements.length > 0) {
        const reqPayload = {
          expectedRevision: createdJob.currentVersion.revision,
          requirements: requirements.map((r, idx) => ({
            displayOrder: idx,
            category: r.category,
            label: r.label.trim(),
            taxonomySkillConceptId: r.taxonomySkillConceptId || null,
            necessity: r.necessity,
            priority: r.priority,
            customWeightOverride:
              typeof r.customWeightOverride === "number" && !isNaN(r.customWeightOverride)
                ? r.customWeightOverride
                : null,
            minExperienceMonths:
              typeof r.minExperienceMonths === "number" && !isNaN(r.minExperienceMonths) && r.minExperienceMonths > 0
                ? r.minExperienceMonths
                : null,
            recencyYears:
              typeof r.recencyYears === "number" && !isNaN(r.recencyYears) && r.recencyYears > 0
                ? r.recencyYears
                : null,
            acceptedEvidenceTypes: Array.isArray(r.acceptedEvidenceTypes) ? r.acceptedEvidenceTypes : [],
            acceptedEquivalents: Array.isArray(r.acceptedEquivalents)
              ? r.acceptedEquivalents.filter((eq) => typeof eq === "string" && eq.trim())
              : [],
            rationale: r.rationale && r.rationale.trim() ? r.rationale.trim() : null,
          })),
        };
        const updatedWithReqs = await saveDraftRequirements(newJobId, reqPayload);
        setJobDetail(updatedWithReqs);
        setCurrentRevision(updatedWithReqs.currentVersion.revision);
        setIsDirty(false);
        if (onJobCreated) onJobCreated(newJobId);
        return updatedWithReqs;
      }

      setJobDetail(createdJob);
      setCurrentRevision(createdJob.currentVersion.revision);
      setIsDirty(false);
      if (onJobCreated) onJobCreated(newJobId);
      return createdJob;
    } else {
      // 2. Update Existing Job Requisition
      const draftPayload: JobDraftUpdatePayload = {
        expectedRevision: currentRevision,
        title: title.trim(),
        department: department.trim() || null,
        location: location.trim() || null,
        employmentType: employmentType as any,
        workplaceType: workplaceType as any,
        openingsCount: openingsCount ? parseInt(openingsCount, 10) : null,
        internalReference: internalReference.trim() || null,
        summary: summary.trim() || null,
        responsibilities: responsibilities.trim() || null,
        jobDescriptionText: jobDescriptionText.trim() || null,
        occupationConceptId: occupationConceptId || null,
      };

      const latestDetail = await updateJobDraft(jobId, draftPayload);
      const nextRev = latestDetail.currentVersion.revision;

      const reqPayload = {
        expectedRevision: nextRev,
        requirements: requirements.map((r, idx) => ({
          displayOrder: idx,
          category: r.category,
          label: r.label.trim(),
          taxonomySkillConceptId: r.taxonomySkillConceptId || null,
          necessity: r.necessity,
          priority: r.priority,
          customWeightOverride:
            typeof r.customWeightOverride === "number" && !isNaN(r.customWeightOverride)
              ? r.customWeightOverride
              : null,
          minExperienceMonths:
            typeof r.minExperienceMonths === "number" && !isNaN(r.minExperienceMonths) && r.minExperienceMonths > 0
              ? r.minExperienceMonths
              : null,
          recencyYears:
            typeof r.recencyYears === "number" && !isNaN(r.recencyYears) && r.recencyYears > 0
              ? r.recencyYears
              : null,
          acceptedEvidenceTypes: Array.isArray(r.acceptedEvidenceTypes) ? r.acceptedEvidenceTypes : [],
          acceptedEquivalents: Array.isArray(r.acceptedEquivalents)
            ? r.acceptedEquivalents.filter((eq) => typeof eq === "string" && eq.trim())
            : [],
          rationale: r.rationale && r.rationale.trim() ? r.rationale.trim() : null,
        })),
      };

      const finalDetail = await saveDraftRequirements(jobId, reqPayload);
      setJobDetail(finalDetail);
      setCurrentRevision(finalDetail.currentVersion.revision);
      setRequirements(finalDetail.currentVersion.requirements || []);
      setIsDirty(false);
      return finalDetail;
    }
  };

  const handleSave = async (e?: FormEvent) => {
    if (e) e.preventDefault();
    setSaveError(null);
    setConflictError(null);
    setSaveSuccessMsg(null);

    try {
      setIsSaving(true);
      await performSaveInternal();
      setSaveSuccessMsg("Job draft saved successfully.");
    } catch (err: any) {
      if (err?.code === "REVISION_CONFLICT") {
        setConflictError(
          "Draft revision conflict: Another recruiter or session updated this requisition. Your local edits are preserved below."
        );
      } else if (err?.message) {
        setSaveError(err.message);
      }
    } finally {
      setIsSaving(false);
    }
  };

  const handleApproveJob = async () => {
    setSaveError(null);
    setConflictError(null);
    setSaveSuccessMsg(null);

    if (requirements.length === 0) {
      setSaveError("Add at least one requirement before approving the job.");
      handleFocusField("requirements");
      return;
    }

    try {
      setIsMarkingReady(true);
      let targetJobId = jobId;
      let targetRev = currentRevision;

      // Save draft first if dirty or new job
      if (!targetJobId || isDirty) {
        const saved = await performSaveInternal();
        targetJobId = saved.jobId;
        targetRev = saved.currentVersion.revision;
      }

      if (targetJobId) {
        const updated = await markJobReady(targetJobId, targetRev);
        setJobDetail(updated);
        setCurrentRevision(updated.currentVersion.revision);
        setIsDirty(false);
        setSaveSuccessMsg(`Job approved successfully (Revision #${updated.currentVersion.revision}).`);
        setReadinessResult(null);
        if (onJobUpdated) onJobUpdated(targetJobId);
      }
    } catch (err: any) {
      if (err?.code === "REVISION_CONFLICT") {
        setConflictError("Draft revision conflict: This requisition was updated or changed status in another session. Please reload.");
      } else if (err?.code === "JOB_READINESS_FAILED") {
        setSaveError(err?.message || "Readiness validation failed on server. Please fix blocking errors.");
      } else {
        setSaveError(err?.message || "Failed to approve job.");
      }
    } finally {
      setIsMarkingReady(false);
    }
  };

  const handleReleaseJob = async () => {
    if (!jobId) return;
    setSaveError(null);
    setConflictError(null);
    try {
      setIsReleasing(true);
      const updated = await releaseJob(jobId);
      setJobDetail(updated);
      setIsReleaseConfirmOpen(false);
      setSaveSuccessMsg("Job released to candidates successfully. Candidates can now discover and apply to this job.");
      if (onJobUpdated) onJobUpdated(jobId);
    } catch (err: any) {
      setSaveError(err?.message || "Failed to release job to candidates.");
    } finally {
      setIsReleasing(false);
    }
  };

  const handleReloadLatest = async () => {
    if (!jobId) return;
    try {
      setIsLoading(true);
      setConflictError(null);
      setSaveError(null);
      const data = await fetchJobDetail(jobId);
      setJobDetail(data);
      const v = data.currentVersion;
      setTitle(v.title || "");
      setDepartment(v.department || "");
      setLocation(v.location || "");
      setEmploymentType(v.employmentType || "FULL_TIME");
      setWorkplaceType(v.workplaceType || "HYBRID");
      setOpeningsCount(v.openingsCount ? String(v.openingsCount) : "1");
      setInternalReference(v.internalReference || "");
      setSummary(v.summary || "");
      setResponsibilities(v.responsibilities || "");
      setJobDescriptionText(v.jobDescriptionText || "");
      setOccupationConceptId(v.occupationConceptId || null);
      setOccupationTitle(v.occupationTitle || null);
      setCurrentRevision(v.revision || 1);
      setRequirements(v.requirements || []);
      setIsDirty(false);
      setReadinessResult(null);
      setSaveSuccessMsg("Draft reloaded from server.");
    } catch (err: any) {
      setLoadError(err?.message || "Failed to reload draft.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleCheckReadiness = async () => {
    setReadinessError(null);
    setSaveSuccessMsg(null);
    if (!jobId) {
      setReadinessError("Please save the draft requisition first before checking readiness.");
      return;
    }
    if (isDirty) {
      setReadinessError("You have unsaved changes. Please save the draft first so server-side checks validate your latest changes.");
      return;
    }
    try {
      setIsCheckingReadiness(true);
      const res = await checkJobReadiness(jobId, currentRevision);
      setReadinessResult(res);
    } catch (err: any) {
      setReadinessError(err?.message || "Failed to check readiness.");
    } finally {
      setIsCheckingReadiness(false);
    }
  };

  const handleCreateSuccessor = async () => {
    if (!jobId) return;
    setSaveError(null);
    setConflictError(null);
    try {
      setIsCreatingSuccessor(true);
      const updated = await createSuccessorVersion(jobId);
      setJobDetail(updated);
      const v = updated.currentVersion;
      setTitle(v.title || "");
      setDepartment(v.department || "");
      setLocation(v.location || "");
      setEmploymentType(v.employmentType || "FULL_TIME");
      setWorkplaceType(v.workplaceType || "HYBRID");
      setOpeningsCount(v.openingsCount ? String(v.openingsCount) : "1");
      setInternalReference(v.internalReference || "");
      setSummary(v.summary || "");
      setResponsibilities(v.responsibilities || "");
      setJobDescriptionText(v.jobDescriptionText || "");
      setOccupationConceptId(v.occupationConceptId || null);
      setOccupationTitle(v.occupationTitle || null);
      setCurrentRevision(v.revision || 1);
      setRequirements(v.requirements || []);
      setIsDirty(false);
      setReadinessResult(null);
      setSaveSuccessMsg(`Created new editable draft successor Version ${v.versionNumber}.`);
      if (onJobUpdated) onJobUpdated(jobId);
    } catch (err: any) {
      setSaveError(err?.message || "Failed to create editable successor version.");
    } finally {
      setIsCreatingSuccessor(false);
    }
  };

  const handleFocusField = (fieldOrReqId?: string | null) => {
    if (!fieldOrReqId) return;
    if (fieldOrReqId === "title") {
      const el = document.getElementById("job-title-input");
      if (el) {
        if (typeof el.focus === "function") el.focus();
        if (typeof el.scrollIntoView === "function") {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }
    } else {
      const reqEl = document.getElementById("section-requirements-title");
      if (reqEl && typeof reqEl.scrollIntoView === "function") {
        reqEl.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }
  };

  const handleSafeNavigateBack = () => {
    if (isDirty) {
      if (
        window.confirm("You have unsaved changes in this job draft. Leave without saving?")
      ) {
        onNavigateBack();
      }
    } else {
      onNavigateBack();
    }
  };

  if (isLoading) {
    return (
      <div className="job-editor-loading">
        <div className="loading-spinner" />
        <p className="loading-text">Loading job draft...</p>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="feedback-box error" style={{ maxWidth: "600px", margin: "40px auto" }}>
        <Icon name="alert-triangle" size={18} />
        <div className="error-content">
          <p className="feedback-text error">{loadError}</p>
          <button type="button" onClick={onNavigateBack} className="btn btn-secondary btn-sm" style={{ marginTop: "10px" }}>
            Back to Jobs
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="job-editor-container">
      {/* Top Navigation */}
      <nav className="job-editor-top-nav" aria-label="Job navigation">
        <button
          type="button"
          onClick={handleSafeNavigateBack}
          className="job-editor-back-nav"
          aria-label="Back to Jobs Directory"
        >
          <Icon name="arrow-left" size={14} />
          <span>Back to Jobs Directory</span>
        </button>
      </nav>

      {/* Main Header Content */}
      <header className="job-editor-header-main">
        <div className="job-editor-header-left">
          <h1 className="job-editor-heading-title">
            {jobId ? (title || "Job Draft") : "Create New Job Requisition"}
          </h1>

          <div className="job-editor-meta-row">
            <span className={`badge ${isReadOnly ? "badge-ready" : "badge-version"}`}>
              <Icon name={isReadOnly ? "check-circle" : "file-text"} size={12} />
              <span>v{versionNumber} ({isReadOnly ? "Ready for matching" : "Draft"})</span>
            </span>
            {jobId && (
              <span className="badge badge-revision">
                <Icon name="layers" size={12} />
                <span>Revision {currentRevision}</span>
              </span>
            )}
            {isReleased ? (
              <span
                className="badge badge-onet-confirmed"
                title={releasedAt ? `Released on ${new Date(releasedAt).toLocaleDateString()}` : "Released to candidates"}
              >
                <Icon name="globe" size={12} />
                <span>
                  Released {releasedAt ? new Date(releasedAt).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : ""}
                </span>
              </span>
            ) : isReadOnly ? (
              <span className="badge badge-warning">
                <Icon name="lock" size={12} />
                <span>Not released</span>
              </span>
            ) : null}
            {isDirty && (
              <span className="badge badge-unsaved">
                <Icon name="alert-circle" size={12} />
                <span>Unsaved Changes</span>
              </span>
            )}
          </div>

          {/* Compact Locked Revision Note */}
          {isReadOnly && (
            <div className="job-locked-note" role="status">
              <Icon name="lock" size={13} />
              <span>
                This revision (v{versionNumber}, Rev #{currentRevision}) is locked and ready for candidate comparison. Create a new revision to make changes.
              </span>
              {!isReleased && (
                <button
                  type="button"
                  className="btn btn-secondary btn-xs"
                  onClick={() => setIsReleaseConfirmOpen(true)}
                  disabled={isReleasing}
                  style={{ marginLeft: "8px", display: "inline-flex", alignItems: "center", gap: "4px" }}
                >
                  <Icon name="globe" size={12} />
                  <span>{isReleasing ? "Releasing..." : "Release to candidates"}</span>
                </button>
              )}
            </div>
          )}
        </div>

        <div className="job-editor-header-actions">
          {isReadOnly ? (
            <>
              <button
                type="button"
                className="btn btn-primary btn-md"
                onClick={() =>
                  onCompareCandidates &&
                  onCompareCandidates({
                    jobId: jobId || "",
                    jobTitle: title || "Job Requisition",
                    jobVersionId: jobDetail?.currentVersion?.jobVersionId || "",
                    revision: currentRevision,
                    requirementsCount: requirements.length,
                  })
                }
              >
                <Icon name="badge-check" size={15} />
                <span>Compare candidates</span>
              </button>

              {jobId && onOpenApplicants && (
                <button
                  type="button"
                  className="btn btn-secondary btn-md"
                  onClick={() => onOpenApplicants(jobId, title || "Job Requisition")}
                  title="View applicants for this job"
                >
                  <Icon name="users" size={14} />
                  <span>Applicants</span>
                </button>
              )}

              {jobId && onOpenAiInterviews && (
                <button
                  type="button"
                  className="btn btn-secondary btn-md"
                  onClick={() => onOpenAiInterviews(jobId)}
                  title="Open AI Interviews workspace for this requisition"
                >
                  <Icon name="sparkles" size={14} />
                  <span>AI Interviews</span>
                </button>
              )}

              <button
                type="button"
                className="btn btn-secondary btn-ghost btn-md"
                onClick={handleCreateSuccessor}
                disabled={isCreatingSuccessor}
                title="Create a new draft revision"
              >
                <Icon name="plus" size={14} />
                <span>{isCreatingSuccessor ? "Creating..." : "Create new revision"}</span>
              </button>
            </>
          ) : (
            <button
              type="button"
              className="btn btn-primary btn-md"
              onClick={handleSave}
              disabled={isSaving}
            >
              <Icon name="check" size={15} />
              <span>{isSaving ? "Saving..." : "Save Draft"}</span>
            </button>
          )}
        </div>
      </header>

      {/* Release Confirmation Dialog Modal */}
      {isReleaseConfirmOpen && (
        <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="release-modal-title">
          <div className="modal-content" style={{ maxWidth: "480px" }}>
            <div className="modal-header">
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                <Icon name="globe" size={18} className="text-primary" />
                <h3 id="release-modal-title" style={{ fontSize: "16px", fontWeight: 700, margin: 0 }}>
                  Release Job to Candidates
                </h3>
              </div>
            </div>
            <div className="modal-body" style={{ padding: "16px 0" }}>
              <p style={{ fontSize: "14px", color: "var(--text-primary)", marginBottom: "12px", lineHeight: "1.5" }}>
                Are you sure you want to release <strong>{title || "this job requisition"}</strong> (Version {versionNumber}, Revision #{currentRevision}) to the public candidate portal?
              </p>
              <div style={{ padding: "12px", background: "var(--bg-surface-subtle)", borderRadius: "6px", fontSize: "13px", color: "var(--text-secondary)", lineHeight: "1.4" }}>
                <p style={{ margin: 0, fontWeight: 500, color: "var(--text-primary)" }}>
                  What happens when released:
                </p>
                <ul style={{ margin: "6px 0 0 16px", padding: 0 }}>
                  <li>Candidates will be able to discover this job in Open Jobs.</li>
                  <li>Eligible candidates can view qualifications and submit applications.</li>
                  <li>Applications will bind to this exact approved revision.</li>
                </ul>
              </div>
            </div>
            <div className="modal-footer" style={{ display: "flex", justifyContent: "flex-end", gap: "10px" }}>
              <button
                type="button"
                className="btn btn-secondary btn-md"
                onClick={() => setIsReleaseConfirmOpen(false)}
                disabled={isReleasing}
              >
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary btn-md"
                onClick={handleReleaseJob}
                disabled={isReleasing}
              >
                {isReleasing ? "Releasing..." : "Confirm & Release"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Notification Banners */}
      {saveSuccessMsg && (
        <div className="feedback-box success" role="status" aria-live="polite">
          <Icon name="check" size={16} />
          <p className="feedback-text">{saveSuccessMsg}</p>
        </div>
      )}

      {saveError && (
        <div className="feedback-box error" role="alert">
          <Icon name="alert-triangle" size={16} />
          <p className="feedback-text error">{saveError}</p>
        </div>
      )}

      {conflictError && (
        <div className="conflict-alert-card" role="alert">
          <div className="conflict-icon-wrap">
            <Icon name="alert-triangle" size={20} />
          </div>
          <div className="conflict-content">
            <h4 className="conflict-title">Revision Conflict</h4>
            <p className="conflict-desc">{conflictError}</p>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleReloadLatest}
              style={{ marginTop: "8px" }}
            >
              <Icon name="refresh" size={14} />
              <span>Reload Latest Draft from Server</span>
            </button>
          </div>
        </div>
      )}

        {/* Single-Column Form */}
      <div className="job-editor-single-col">
        {/* Section: Job details */}
        <section className="job-section-card" aria-labelledby="section-job-details-title">
          <div className="section-card-header">
            <div>
              <h3 id="section-job-details-title" className="section-title">Job details</h3>
              <p className="section-subtitle">Core information for this role.</p>
            </div>
          </div>

          <div className="form-fields-stack">
            <div className="form-group">
              <label htmlFor="job-title-input" className="form-label">
                Job title <span className="required-star">*</span>
              </label>
              <input
                id="job-title-input"
                type="text"
                className="form-input"
                placeholder="e.g. Frontend developer"
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value);
                  setIsDirty(true);
                }}
                disabled={isReadOnly}
                required
              />
            </div>

            <div className="form-two-col-grid">
              <div className="form-group">
                <label htmlFor="job-dept-input" className="form-label">Department</label>
                <input
                  id="job-dept-input"
                  type="text"
                  className="form-input"
                  placeholder="e.g. Product engineering"
                  value={department}
                  onChange={(e) => {
                    setDepartment(e.target.value);
                    setIsDirty(true);
                  }}
                  disabled={isReadOnly}
                />
              </div>

              <div className="form-group">
                <label htmlFor="job-loc-input" className="form-label">Location</label>
                <input
                  id="job-loc-input"
                  type="text"
                  className="form-input"
                  placeholder="e.g. Ahmedabad, Gujarat"
                  value={location}
                  onChange={(e) => {
                    setLocation(e.target.value);
                    setIsDirty(true);
                  }}
                  disabled={isReadOnly}
                />
              </div>
            </div>

            <div className="form-three-col-grid">
              <div className="form-group">
                <label htmlFor="job-emp-type" className="form-label">Employment type</label>
                <select
                  id="job-emp-type"
                  className="form-input"
                  value={employmentType}
                  onChange={(e) => {
                    setEmploymentType(e.target.value);
                    setIsDirty(true);
                  }}
                  disabled={isReadOnly}
                >
                  <option value="FULL_TIME">Full-time</option>
                  <option value="PART_TIME">Part-time</option>
                  <option value="CONTRACT">Contract</option>
                  <option value="INTERNSHIP">Internship</option>
                  <option value="TEMPORARY">Temporary</option>
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="job-work-type" className="form-label">Workplace type</label>
                <select
                  id="job-work-type"
                  className="form-input"
                  value={workplaceType}
                  onChange={(e) => {
                    setWorkplaceType(e.target.value);
                    setIsDirty(true);
                  }}
                  disabled={isReadOnly}
                >
                  <option value="HYBRID">Hybrid</option>
                  <option value="ON_SITE">On-site</option>
                  <option value="REMOTE">Remote</option>
                </select>
              </div>

              <div className="form-group">
                <label htmlFor="job-openings" className="form-label">Openings</label>
                <input
                  id="job-openings"
                  type="number"
                  min={1}
                  max={999}
                  className="form-input"
                  value={openingsCount}
                  onChange={(e) => {
                    setOpeningsCount(e.target.value);
                    setIsDirty(true);
                  }}
                  disabled={isReadOnly}
                />
              </div>
            </div>

            <div className="form-group">
              <label htmlFor="job-ref-input" className="form-label">
                Reference code (optional)
              </label>
              <input
                id="job-ref-input"
                type="text"
                className="form-input"
                placeholder="e.g. ENG-104"
                value={internalReference}
                onChange={(e) => {
                  setInternalReference(e.target.value);
                  setIsDirty(true);
                }}
                disabled={isReadOnly}
              />
              <span style={{ fontSize: "12px", color: "var(--text-secondary, #64748b)", marginTop: "4px", display: "block" }}>
                Internal identifier used by your recruitment team.
              </span>
            </div>
          </div>
        </section>

        {/* Section: About the role */}
        <section className="job-section-card" aria-labelledby="section-about-role-title">
          <div className="section-card-header">
            <div>
              <h3 id="section-about-role-title" className="section-title">About the role</h3>
            </div>
          </div>

          {isReadOnly ? (
            <div className="job-read-only-stack">
              <div className="job-read-only-block">
                <span className="job-read-only-block-label">Role summary</span>
                <div className="job-read-only-block-content">
                  {summary || "No summary provided."}
                </div>
              </div>

              <div className="job-read-only-block">
                <span className="job-read-only-block-label">Responsibilities</span>
                <div className="job-read-only-block-content">
                  {responsibilities || "No responsibilities specified."}
                </div>
              </div>
            </div>
          ) : (
            <div className="form-fields-stack">
              <div className="form-group">
                <label htmlFor="job-summary-input" className="form-label">Role summary</label>
                <textarea
                  id="job-summary-input"
                  className="form-input form-textarea"
                  rows={3}
                  placeholder="Brief summary of the role..."
                  value={summary}
                  onChange={(e) => {
                    setSummary(e.target.value);
                    setIsDirty(true);
                  }}
                />
              </div>

              <div className="form-group">
                <label htmlFor="job-resp-input" className="form-label">Responsibilities</label>
                <textarea
                  id="job-resp-input"
                  className="form-input form-textarea"
                  rows={4}
                  placeholder="Key duties and day-to-day responsibilities..."
                  value={responsibilities}
                  onChange={(e) => {
                    setResponsibilities(e.target.value);
                    setIsDirty(true);
                  }}
                />
              </div>
            </div>
          )}
        </section>

        {/* Optional Collapsed Section: Occupation reference (optional) */}
        <section className="job-section-card" aria-labelledby="section-occupation-title">
          <div
            className="section-card-header section-card-header-toggle"
            onClick={() => setShowOccupationSection(!showOccupationSection)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setShowOccupationSection(!showOccupationSection);
              }
            }}
            aria-expanded={showOccupationSection}
          >
            <div className="section-header-left">
              <div>
                <h3 id="section-occupation-title" className="section-title">
                  Occupation reference <span className="text-muted font-normal">(optional)</span>
                </h3>
                <p className="section-subtitle">
                  {occupationTitle ? `Selected: ${occupationTitle}` : "Optional standard O*NET occupation reference for advisory skill suggestions."}
                </p>
              </div>
            </div>
            <button
              type="button"
              className="btn-icon-subtle"
              aria-label={showOccupationSection ? "Collapse occupation reference" : "Expand occupation reference"}
            >
              <Icon name={showOccupationSection ? "chevron-down" : "chevron-right"} size={16} />
            </button>
          </div>

          {showOccupationSection && (
            <div className="section-collapsible-content">
              {isReadOnly ? (
                <p className="job-read-only-text" style={{ padding: "8px 0", margin: 0, color: "var(--text-primary)" }}>
                  {occupationTitle ? `Selected O*NET occupation: ${occupationTitle}` : "No O*NET occupation selected for this revision."}
                </p>
              ) : (
                <>
                  <OccupationSelector
                    selectedConceptId={occupationConceptId}
                    selectedTitle={occupationTitle}
                    onSelectOccupation={handleSelectOccupation}
                  />

                  {occupationConceptId && (
                    <div className="occupation-suggestions-panel" style={{ marginTop: "14px", paddingTop: "12px", borderTop: "1px solid var(--border-subtle)" }}>
                      <div className="suggestions-header-line" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                        <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-secondary)" }}>
                          O*NET 31.0 Suggested Skills for {occupationTitle} (Click to add as requirement)
                        </span>
                        {isLoadingSuggestions && <span style={{ fontSize: "11px", color: "var(--text-muted)" }}>Loading suggestions...</span>}
                      </div>

                      {suggestedSkills.length > 0 ? (
                        <div className="suggested-skills-chip-grid" style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                          {suggestedSkills.map((sug) => {
                            const isAdded = requirements.some(
                              (r) =>
                                r.taxonomySkillConceptId === sug.conceptId ||
                                r.label.trim().toLowerCase() === sug.canonicalLabel.trim().toLowerCase()
                            );
                            const famLabel = sug.sourceFamily === "essential"
                              ? "Essential"
                              : sug.sourceFamily === "transferable"
                              ? "Transferable"
                              : "Software";

                            return (
                              <button
                                key={sug.conceptId}
                                type="button"
                                className={`btn-skill-suggestion-chip ${isAdded ? "added" : ""}`}
                                onClick={() => handleAddSuggestedSkill(sug)}
                                disabled={isAdded}
                                style={{
                                  display: "inline-flex",
                                  alignItems: "center",
                                  gap: "6px",
                                  padding: "4px 10px",
                                  borderRadius: "16px",
                                  fontSize: "12px",
                                  border: "1px solid var(--border-subtle)",
                                  background: isAdded ? "var(--surface-sunken)" : "var(--surface-elevated)",
                                  color: isAdded ? "var(--text-muted)" : "var(--text-primary)",
                                  cursor: isAdded ? "default" : "pointer",
                                }}
                              >
                                <span>{sug.canonicalLabel}</span>
                                <span
                                  style={{
                                    fontSize: "10px",
                                    padding: "1px 5px",
                                    borderRadius: "8px",
                                    background: sug.sourceFamily === "essential" ? "rgba(59, 130, 246, 0.1)" : sug.sourceFamily === "transferable" ? "rgba(16, 185, 129, 0.1)" : "rgba(168, 85, 247, 0.1)",
                                    color: sug.sourceFamily === "essential" ? "#2563eb" : sug.sourceFamily === "transferable" ? "#059669" : "#7c3aed",
                                  }}
                                >
                                  {famLabel}
                                </span>
                                {isAdded ? <Icon name="check" size={12} /> : <Icon name="plus" size={12} />}
                              </button>
                            );
                          })}
                        </div>
                      ) : (
                        !isLoadingSuggestions && (
                          <span style={{ fontSize: "12px", color: "var(--text-muted)" }}>
                            No specific skills linked for this occupation in the O*NET catalogue.
                          </span>
                        )
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          )}
        </section>

        {/* Section: Requirements */}
        <section className="job-section-card" aria-labelledby="section-requirements-title">
          <div className="section-card-header-row">
            <div className="section-header-left">
              <div>
                <div className="requirements-header-line">
                  <h3 id="section-requirements-title" className="section-title">Requirements</h3>
                  <div className="requirements-count-chips">
                    <span className="chip-req-total">
                      {reviewSummary.total} {reviewSummary.total === 1 ? "requirement" : "requirements"}
                    </span>
                    {reviewSummary.mustHaveCount > 0 && (
                      <span className="chip-req-must-have">
                        {reviewSummary.mustHaveCount} required
                      </span>
                    )}
                    {reviewSummary.preferredCount > 0 && (
                      <span className="chip-req-preferred">
                        {reviewSummary.preferredCount} preferred
                      </span>
                    )}
                  </div>
                </div>
                <p className="section-subtitle">
                  Structured skills, experience, and criteria for this role.
                </p>
              </div>
            </div>

            {!isReadOnly && (
              <button
                type="button"
                className="btn btn-secondary btn-sm"
                onClick={() => handleAddRequirement("SKILL")}
              >
                <Icon name="plus" size={14} />
                <span>Add Requirement</span>
              </button>
            )}
          </div>

          {requirements.length === 0 ? (
            <div className="requirements-empty-card">
              <Icon name="badge-check" size={28} className="empty-icon" />
              <h4 className="empty-title">No requirements added yet</h4>
              <p className="empty-desc">
                Add skills, experience, or qualifications for this requisition.
              </p>
              {!isReadOnly && (
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => handleAddRequirement("SKILL")}
                >
                  <Icon name="plus" size={14} />
                  <span>Add Skill Requirement</span>
                </button>
              )}
            </div>
          ) : (
            <div className="requirements-list-container">
              {requirements.map((req, idx) => (
                <RequirementCard
                  key={req.requirementId || `req-${idx}`}
                  requirement={req}
                  index={idx}
                  totalCount={requirements.length}
                  isExpanded={expandedReqIndex === idx}
                  readOnly={isReadOnly}
                  onToggleExpand={() =>
                    setExpandedReqIndex((current) => (current === idx ? null : idx))
                  }
                  onChange={(updated) => handleUpdateRequirement(idx, updated)}
                  onRemove={() => handleRemoveRequirement(idx)}
                  onMoveUp={() => handleMoveRequirement(idx, "up")}
                  onMoveDown={() => handleMoveRequirement(idx, "down")}
                />
              ))}
            </div>
          )}
        </section>

        {/* Section: Requisition Readiness & Revision Locking */}
        <section className="job-section-card draft-checks-section" aria-labelledby="section-readiness-title">
          <div className="section-card-header-row">
            <div className="section-header-left">
              <div>
                <h3 id="section-readiness-title" className="section-title">Requisition readiness & locking</h3>
                <p className="section-subtitle">Validate authoritative criteria and lock revision for candidate matching.</p>
              </div>
            </div>

            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleCheckReadiness}
              disabled={isCheckingReadiness}
            >
              <Icon name="refresh" size={14} />
              <span>{isCheckingReadiness ? "Validating..." : "Check readiness"}</span>
            </button>
          </div>

          {readinessError && (
            <div className="feedback-box error" style={{ margin: "12px 0" }} role="alert">
              <Icon name="alert-triangle" size={16} />
              <p className="feedback-text error">{readinessError}</p>
            </div>
          )}

          {/* Quick Draft Notes when Readiness checks haven't been manually triggered yet */}
          {!readinessResult && (
            <div className="draft-checks-container" style={{ marginTop: "14px" }}>
              {reviewSummary.mustHaveCount === 0 ? (
                <div className="check-item-row neutral">
                  <Icon name="info" size={15} className="check-icon neutral" />
                  <span className="check-item-text">No required criteria added yet.</span>
                </div>
              ) : reviewSummary.missingRationaleCount > 0 ? (
                <div className="check-item-row warn">
                  <Icon name="alert-triangle" size={15} className="check-icon warn" />
                  <span className="check-item-text">
                    {reviewSummary.missingRationaleCount} required criterion is missing a reason. (Click &quot;Reason suggested&quot; on the requirement card to add one).
                  </span>
                </div>
              ) : (
                <div className="check-item-row pass">
                  <Icon name="check" size={15} className="check-icon pass" />
                  <span className="check-item-text">All required criteria have a reason.</span>
                </div>
              )}

              {reviewSummary.reviewRequiredCount > 0 && (
                <div className="check-item-row warn">
                  <Icon name="info" size={15} className="check-icon warn" />
                  <span className="check-item-text">
                    {reviewSummary.reviewRequiredCount} requirement(s) have human review suggestions.
                  </span>
                </div>
              )}
            </div>
          )}

          {/* Structured Readiness Findings Display */}
          {readinessResult && (
            <div className="readiness-results-container" style={{ marginTop: "16px" }}>
              {readinessResult.ready ? (
                <div className="feedback-box success" role="status">
                  <Icon name="check" size={18} />
                  <p className="feedback-text">
                    Requisition passes all readiness checks and is ready to be locked for candidate matching.
                  </p>
                </div>
              ) : (
                <div className="feedback-box error" role="alert">
                  <Icon name="alert-triangle" size={18} />
                  <p className="feedback-text error">
                    Requisition has {readinessResult.errors.length} blocking error(s) that must be resolved before it can be marked ready.
                  </p>
                </div>
              )}

              {/* Grouped Blocking Errors */}
              {readinessResult.errors.length > 0 && (
                <div className="readiness-group errors-group" style={{ marginTop: "12px" }}>
                  <h4 className="readiness-group-title text-error">Blocking Errors ({readinessResult.errors.length})</h4>
                  <div className="readiness-findings-list">
                    {readinessResult.errors.map((err, errIdx) => (
                      <div key={errIdx} className="finding-card finding-error">
                        <Icon name="alert-circle" size={15} className="finding-icon text-error" />
                        <div className="finding-content">
                          <span className="finding-msg">{err.message}</span>
                          {(err.field || err.requirementId) && (
                            <button
                              type="button"
                              className="btn-link-action"
                              onClick={() => handleFocusField(err.field || err.requirementId)}
                            >
                              Go to field
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Grouped Advisory Warnings */}
              {readinessResult.warnings.length > 0 && (
                <div className="readiness-group warnings-group" style={{ marginTop: "12px" }}>
                  <h4 className="readiness-group-title text-warning">Advisory Warnings ({readinessResult.warnings.length})</h4>
                  <div className="readiness-findings-list">
                    {readinessResult.warnings.map((warn, wIdx) => (
                      <div key={wIdx} className="finding-card finding-warning">
                        <Icon name="info" size={15} className="finding-icon text-warning" />
                        <div className="finding-content">
                          <span className="finding-msg">{warn.message}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Informational Checks */}
              {readinessResult.info && readinessResult.info.length > 0 && (
                <div className="readiness-group info-group" style={{ marginTop: "12px" }}>
                  <h4 className="readiness-group-title text-muted">Notes ({readinessResult.info.length})</h4>
                  <div className="readiness-findings-list">
                    {readinessResult.info.map((inf, iIdx) => (
                      <div key={iIdx} className="finding-card finding-info">
                        <Icon name="info" size={15} className="finding-icon text-muted" />
                        <div className="finding-content">
                          <span className="finding-msg">{inf.message}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Actions Row */}
          <div className="draft-save-action-row" style={{ marginTop: "24px", display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "center" }}>
            {isReadOnly ? (
              <button
                type="button"
                className="btn btn-primary btn-lg"
                onClick={handleCreateSuccessor}
                disabled={isCreatingSuccessor}
              >
                <Icon name="plus" size={18} />
                <span>{isCreatingSuccessor ? "Creating..." : "Create editable revision"}</span>
              </button>
            ) : (
              <>
                <button
                  type="button"
                  className="btn btn-secondary btn-lg save-draft-primary-btn"
                  onClick={handleSave}
                  disabled={isSaving || isMarkingReady}
                >
                  <Icon name="check" size={18} />
                  <span>{isSaving ? "Saving draft..." : "Save as draft"}</span>
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-lg"
                  onClick={handleApproveJob}
                  disabled={isSaving || isMarkingReady || requirements.length === 0}
                  title={requirements.length === 0 ? "Add at least one requirement before approving" : undefined}
                >
                  <Icon name="badge-check" size={18} />
                  <span>{isMarkingReady ? "Approving..." : "Approve job"}</span>
                </button>
              </>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
