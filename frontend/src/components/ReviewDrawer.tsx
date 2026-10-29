import React, { useEffect, useRef, useState } from "react";
import { Icon } from "./Icon";
import { ProfileWarning } from "./CandidateProfileReview";

interface ReviewDrawerProps {
  isOpen: boolean;
  onClose: () => void;
  warnings: ProfileWarning[];
  onNavigateToSection: (sectionKey: string, fieldName?: string | null) => void;
  onOpenEvidence?: (evidenceId: string, label: string) => void;
  onStartCorrection?: () => void;
}

type GroupedSection = {
  sectionKey: string;
  sectionTitle: string;
  iconName: "user" | "sparkles" | "briefcase" | "academic-cap" | "layers" | "badge-check" | "globe" | "alert-triangle";
  items: ProfileWarning[];
};

export function ReviewDrawer({
  isOpen,
  onClose,
  warnings,
  onNavigateToSection,
  onOpenEvidence,
  onStartCorrection,
}: ReviewDrawerProps) {
  const [expandedTechIdx, setExpandedTechIdx] = useState<number | null>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  const drawerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    closeBtnRef.current?.focus();

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        onClose();
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const getWarningTitle = (code: string, category: string, fieldName?: string | null): string => {
    switch (code) {
      // Skills
      case "UNRESOLVED_SKILL_CANDIDATE":
        return "Skill could not be matched to the current catalogue";
      case "SKILL_DELIMITER_AMBIGUITY":
        return "Skill list punctuation or delimiter ambiguity";
      case "HEADLINE_SYNTHESIZED":
        return "Professional headline inferred from career experience";
      case "SUMMARY_MISSING":
        return "No professional summary detected in document";

      // Employment
      case "EMPLOYMENT_ORGANIZATION_MISSING":
        return "Organization missing from employment record";
      case "EMPLOYMENT_JOB_TITLE_MISSING":
        return "Job title missing from employment record";
      case "EMPLOYMENT_DATE_UNRESOLVED":
        return "Employment date range could not be fully parsed";
      case "EMPLOYMENT_FUTURE_DATE":
        return "Employment date is set in the future";
      case "EMPLOYMENT_OVERLAPPING_ROLES":
        return "Overlapping employment date ranges detected";

      // Education
      case "EDUCATION_DEGREE_MISSING":
        return "Degree missing from education record";
      case "EDUCATION_INSTITUTION_MISSING":
        return "Institution missing from education record";
      case "EDUCATION_DATE_UNRESOLVED":
        return "Education date interval could not be fully parsed";

      // Projects
      case "PROJECT_NAME_MISSING":
        return "A project is missing its title";
      case "PROJECT_DESCRIPTION_CONTINUATION":
        return "Project description formatting requires verification";

      // Certifications
      case "CERTIFICATION_NAME_MISSING":
        return "Certification name missing from record";
      case "CERTIFICATION_ISSUER_MISSING":
        return "Certification issuer could not be determined";

      // Languages
      case "LANGUAGE_PROFICIENCY_UNRESOLVED":
        return "Language proficiency level could not be determined";

      // Identity
      case "MULTIPLE_NAME_CANDIDATES":
        return "Multiple candidate full name options detected";
      case "MULTIPLE_EMAIL_CANDIDATES":
        return "Multiple email addresses found in document";
      case "MULTIPLE_PHONE_CANDIDATES":
        return "Multiple phone numbers detected";
      case "PROHIBITED_IDENTITY_IN_PROFESSIONAL_PROFILE":
        return "Personal contact info detected in professional profile section";
      case "NAME_MISSING":
        return "Candidate full name was not detected";

      // Evidence & System
      case "PROFILE_EVIDENCE_CROSS_ARTIFACT":
        return "Evidence span references another document artifact";
      case "PROFILE_EVIDENCE_NOT_FOUND":
        return "Evidence text could not be located in source blocks";
      case "SECTION_WARNING":
        return "Section layout or ordering anomaly detected";

      default:
        if (fieldName) {
          return `${fieldName.replace(/_/g, " ")} requires verification`;
        }
        return code.replace(/_/g, " ").toLowerCase();
    }
  };

  const getWarningDescription = (w: ProfileWarning, title: string): string => {
    if (w.message && w.message !== title) return w.message;
    switch (w.code) {
      case "UNRESOLVED_SKILL_CANDIDATE":
        return "The parser recognized this term as a potential skill, but it is not in the canonical skill taxonomy. Review to confirm or adjust.";
      case "EMPLOYMENT_ORGANIZATION_MISSING":
        return "An employment record has a recognized job title and dates, but the company or employer organization was omitted or unreadable.";
      case "EMPLOYMENT_JOB_TITLE_MISSING":
        return "An employment record is missing a specific job title or role name.";
      case "EMPLOYMENT_DATE_UNRESOLVED":
        return "The dates for this position could not be normalized into standard calendar intervals.";
      case "EDUCATION_DEGREE_MISSING":
        return "An education institution was recognized, but the specific degree title (e.g. BS, BA, MS) was not identified.";
      case "EDUCATION_INSTITUTION_MISSING":
        return "A degree or field of study was recognized without an associated university or school name.";
      case "PROJECT_NAME_MISSING":
        return "A technical project description was identified without an explicit title.";
      case "LANGUAGE_PROFICIENCY_UNRESOLVED":
        return "A language was listed without standard CEFR or working proficiency markers.";
      default:
        return w.message || "Quality checks flagged this field for human verification before final profile approval.";
    }
  };

  // Group warnings by user-facing section
  const groupWarnings = (): GroupedSection[] => {
    const groups: Record<string, GroupedSection> = {
      IDENTITY: { sectionKey: "identity", sectionTitle: "Identity & Contacts", iconName: "user", items: [] },
      SKILLS: { sectionKey: "skills", sectionTitle: "Skills & Summary", iconName: "sparkles", items: [] },
      EMPLOYMENT: { sectionKey: "employment", sectionTitle: "Employment Experience", iconName: "briefcase", items: [] },
      EDUCATION: { sectionKey: "education", sectionTitle: "Education", iconName: "academic-cap", items: [] },
      PROJECTS: { sectionKey: "projects", sectionTitle: "Projects", iconName: "layers", items: [] },
      CERTIFICATIONS: { sectionKey: "certifications", sectionTitle: "Certifications", iconName: "badge-check", items: [] },
      LANGUAGES: { sectionKey: "languages", sectionTitle: "Languages", iconName: "globe", items: [] },
      OTHER: { sectionKey: "general", sectionTitle: "Document Structure & Evidence", iconName: "alert-triangle", items: [] },
    };

    warnings.forEach((w) => {
      const cat = (w.category || "").toUpperCase();
      const code = (w.code || "").toUpperCase();

      if (cat.includes("PROJ") || code.includes("PROJ")) {
        groups.PROJECTS.items.push(w);
      } else if (cat.includes("SKILL") || code.includes("SKILL") || code.includes("SUMMARY") || code.includes("HEADLINE")) {
        groups.SKILLS.items.push(w);
      } else if (cat.includes("EMPLOY") || code.includes("EMPLOY")) {
        groups.EMPLOYMENT.items.push(w);
      } else if (cat.includes("EDU") || code.includes("EDU") || code.includes("DEGREE")) {
        groups.EDUCATION.items.push(w);
      } else if (cat.includes("CERT") || code.includes("CERT")) {
        groups.CERTIFICATIONS.items.push(w);
      } else if (cat.includes("LANG") || code.includes("LANG")) {
        groups.LANGUAGES.items.push(w);
      } else if (cat.includes("IDENT") || code.includes("IDENT") || code.includes("NAME") || code.includes("EMAIL") || code.includes("PHONE")) {
        groups.IDENTITY.items.push(w);
      } else {
        groups.OTHER.items.push(w);
      }
    });

    return Object.values(groups).filter((g) => g.items.length > 0);
  };

  const groupedSections = groupWarnings();

  const handleViewField = (sectionKey: string, fieldName?: string | null) => {
    onNavigateToSection(sectionKey, fieldName);
    onClose();
  };

  return (
    <div
      className="drawer-backdrop"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="review-drawer-title"
    >
      <div
        className="drawer-panel drawer-panel-wide"
        ref={drawerRef}
        onClick={(e) => e.stopPropagation()}
        tabIndex={-1}
      >
        {/* Drawer Header */}
        <div className="drawer-header">
          <div className="drawer-title-group">
            <div className="drawer-badge-row">
              <span className="badge badge-review-warn">
                <Icon name="alert-triangle" size={13} className="badge-icon" />
                Quality &amp; Normalization Review
              </span>
              <span className="badge badge-neutral">
                {warnings.length} item{warnings.length === 1 ? "" : "s"}
              </span>
            </div>
            <h3 id="review-drawer-title" className="drawer-title">
              Profile Review Items
            </h3>
          </div>

          <button
            type="button"
            ref={closeBtnRef}
            onClick={onClose}
            className="btn-icon drawer-close-btn"
            aria-label="Close profile review drawer"
          >
            <Icon name="x" size={18} />
          </button>
        </div>

        {/* Drawer Body */}
        <div className="drawer-body">
          <p className="drawer-intro-text">
            These items highlight fields where the parser encountered formatting ambiguity or non-canonical terms. You can inspect the source section, check evidence, or apply corrections.
          </p>

          {groupedSections.map((group) => (
            <div key={group.sectionKey} className="review-section-group">
              <div className="review-section-header">
                <Icon name={group.iconName} size={16} className="card-icon" />
                <h4 className="review-section-title">
                  {group.sectionTitle} ({group.items.length})
                </h4>
              </div>

              <div className="review-items-stack">
                {group.items.map((item, idx) => {
                  const title = getWarningTitle(item.code, item.category, item.fieldName);
                  const description = getWarningDescription(item, title);
                  const isTechOpen = expandedTechIdx === idx;

                  return (
                    <div key={idx} className="review-item-card">
                      <div className="review-item-main">
                        <div className="review-item-title-row">
                          <h5 className="review-item-title">{title}</h5>
                          {item.fieldName && (
                            <span className="badge badge-neutral mono">{item.fieldName}</span>
                          )}
                        </div>

                        <p className="review-item-desc">{description}</p>
                      </div>

                      {/* Action Row */}
                      <div className="review-item-actions">
                        <button
                          type="button"
                          onClick={() => handleViewField(group.sectionKey, item.fieldName)}
                          className="btn btn-secondary btn-xs"
                        >
                          <Icon name="eye" size={12} /> View in Profile
                        </button>

                        {item.evidence_id && onOpenEvidence && (
                          <button
                            type="button"
                            onClick={() => {
                              onOpenEvidence(item.evidence_id!, title);
                              onClose();
                            }}
                            className="btn btn-secondary btn-xs"
                          >
                            <Icon name="search" size={12} /> Evidence
                          </button>
                        )}

                        {onStartCorrection && (
                          <button
                            type="button"
                            onClick={() => {
                              onStartCorrection();
                              onClose();
                            }}
                            className="btn btn-primary btn-xs"
                          >
                            <Icon name="edit" size={12} /> Correct Field
                          </button>
                        )}

                        <button
                          type="button"
                          onClick={() => setExpandedTechIdx((prev) => (prev === idx ? null : idx))}
                          className="btn-tech-link"
                          aria-expanded={isTechOpen}
                        >
                          <Icon name={isTechOpen ? "chevron-down" : "chevron-right"} size={12} />
                          Details
                        </button>
                      </div>

                      {/* Technical Details Disclosure */}
                      {isTechOpen && (
                        <div className="tech-details-panel" style={{ marginTop: "8px" }}>
                          <div className="tech-row">
                            <span className="tech-key">Warning Code:</span>
                            <span className="tech-val mono">{item.code}</span>
                          </div>
                          <div className="tech-row">
                            <span className="tech-key">Category:</span>
                            <span className="tech-val mono">{item.category}</span>
                          </div>
                          {item.count !== undefined && item.count !== null && (
                            <div className="tech-row">
                              <span className="tech-key">Candidate Count:</span>
                              <span className="tech-val mono">{item.count}</span>
                            </div>
                          )}
                          {item.evidence_id && (
                            <div className="tech-row">
                              <span className="tech-key">Evidence ID:</span>
                              <span className="tech-val mono">{item.evidence_id}</span>
                            </div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        {/* Drawer Footer */}
        <div className="drawer-footer">
          <button type="button" onClick={onClose} className="btn btn-secondary btn-sm">
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
