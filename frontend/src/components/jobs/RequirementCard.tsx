import React, { useState, useRef } from "react";
import {
  RequirementCategory,
  RequirementEvidenceType,
  RequirementItem,
  RequirementNecessity,
  RequirementPriority,
} from "../../api/jobs";
import { SkillSearchItem } from "../../api/taxonomy";
import { Icon } from "../Icon";
import { TaxonomySkillAutocomplete } from "./TaxonomySkillAutocomplete";

interface RequirementCardProps {
  requirement: RequirementItem;
  index: number;
  totalCount: number;
  isExpanded: boolean;
  readOnly?: boolean;
  onToggleExpand: () => void;
  onChange: (updated: RequirementItem) => void;
  onRemove: () => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
}

const ALL_EVIDENCE_TYPES: { type: RequirementEvidenceType; label: string }[] = [
  { type: "EMPLOYMENT", label: "Work Experience" },
  { type: "PROJECT", label: "Projects / Code" },
  { type: "EDUCATION", label: "Education" },
  { type: "CERTIFICATION", label: "Certifications" },
  { type: "SKILL_LIST", label: "Skills List" },
  { type: "PUBLICATION", label: "Publications" },
  { type: "PATENT", label: "Patents" },
  { type: "OTHER", label: "Other Sections" },
];

export function RequirementCard({
  requirement,
  index,
  totalCount,
  isExpanded,
  readOnly = false,
  onToggleExpand,
  onChange,
  onRemove,
  onMoveUp,
  onMoveDown,
}: RequirementCardProps) {
  const [showAdvancedOptions, setShowAdvancedOptions] = useState(false);
  const [isEditingLabel, setIsEditingLabel] = useState(!requirement.label.trim());
  const [equivalentInput, setEquivalentInput] = useState("");
  const rationaleTextareaRef = useRef<HTMLTextAreaElement>(null);

  const isSkillCategory = requirement.category === "SKILL";
  const isMustHave = requirement.necessity === "MUST_HAVE";
  const isCritical = requirement.priority === "CRITICAL";
  const hasRationale = Boolean(requirement.rationale && requirement.rationale.trim());
  const needsRationale = (isMustHave || isCritical) && !hasRationale;
  const activeWarnings = (requirement.warningMetadata || []).filter((w) => {
    if (hasRationale && (w.code === "MISSING_MUST_HAVE_RATIONALE" || w.code === "MISSING_CRITICAL_RATIONALE")) {
      return false;
    }
    return true;
  });
  const hasWarnings = activeWarnings.length > 0;

  const handleSelectTaxonomySkill = (skill: SkillSearchItem) => {
    onChange({
      ...requirement,
      label: skill.canonicalLabel,
      taxonomySkillConceptId: skill.conceptId,
      category: "SKILL",
      origin: "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED",
    });
    setIsEditingLabel(false);
  };

  const handleUseCustomText = (text: string) => {
    onChange({
      ...requirement,
      label: text,
      taxonomySkillConceptId: null,
      origin: "RECRUITER_ENTERED",
    });
    setIsEditingLabel(false);
  };

  const handleCategoryChange = (newCat: RequirementCategory) => {
    const isNewSkill = newCat === "SKILL";
    onChange({
      ...requirement,
      category: newCat,
      taxonomySkillConceptId: isNewSkill ? requirement.taxonomySkillConceptId : null,
      origin: isNewSkill && requirement.taxonomySkillConceptId ? "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED" : "RECRUITER_ENTERED",
    });
  };

  const handleToggleEvidenceType = (evType: RequirementEvidenceType) => {
    const current = requirement.acceptedEvidenceTypes || [];
    const updated = current.includes(evType)
      ? current.filter((t) => t !== evType)
      : [...current, evType];
    onChange({ ...requirement, acceptedEvidenceTypes: updated });
  };

  const handleAddEquivalent = () => {
    const trimmed = equivalentInput.trim();
    if (!trimmed) return;
    const current = requirement.acceptedEquivalents || [];
    if (!current.includes(trimmed) && trimmed.toLowerCase() !== requirement.label.toLowerCase()) {
      onChange({ ...requirement, acceptedEquivalents: [...current, trimmed] });
    }
    setEquivalentInput("");
  };

  const handleRemoveEquivalent = (eq: string) => {
    const current = requirement.acceptedEquivalents || [];
    onChange({ ...requirement, acceptedEquivalents: current.filter((e) => e !== eq) });
  };

  const displayTitle = requirement.label.trim() || `(Untitled requirement #${index + 1})`;

  // =========================================================================
  // 1. Collapsed Compact Row Mode
  // =========================================================================
  if (!isExpanded) {
    return (
      <div
        className={`requirement-card-compact ${hasWarnings ? "has-review-warning" : ""}`}
        data-testid={`requirement-card-${index}`}
      >
        <div className="req-compact-left">
          <div className="requirement-order-badge">#{index + 1}</div>
          <span className="req-compact-title" title={displayTitle}>
            {displayTitle}
          </span>
          <div className="req-compact-badges">
            <span className="badge badge-custom-entered">{requirement.category}</span>
            <span
              className={requirement.necessity === "MUST_HAVE" ? "badge-necessity-must" : "badge-necessity-pref"}
            >
              {requirement.necessity === "MUST_HAVE" ? "Must-have" : "Preferred"}
            </span>
            <span className={`badge-prio ${requirement.priority === "CRITICAL" ? "badge-prio-critical" : ""}`}>
              {requirement.priority}
            </span>
            {requirement.minExperienceMonths && requirement.minExperienceMonths > 0 && (
              <span className="badge-exp-months">{requirement.minExperienceMonths}m exp</span>
            )}
            {requirement.origin === "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED" && (
              <span className="badge badge-onet-confirmed">O*NET</span>
            )}
            {needsRationale && (
              <span className="badge badge-meta" style={{ backgroundColor: "#fef3c7", color: "#92400e" }}>
                Needs reason
              </span>
            )}
            {hasWarnings && (
              <span className="badge badge-meta" style={{ backgroundColor: "var(--gap-partial-bg)", color: "var(--gap-conflicting)", border: "1px solid var(--gap-partial-border)" }}>
                Review warning
              </span>
            )}
          </div>
        </div>

        <div className="req-compact-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onToggleExpand}
            aria-label={`Edit requirement #${index + 1}: ${displayTitle}`}
          >
            <Icon name="edit" size={13} />
            <span>Edit</span>
          </button>
          {!readOnly && (
            <>
              <button
                type="button"
                className="btn-icon-control"
                disabled={index === 0}
                onClick={onMoveUp}
                aria-label={`Move requirement ${index + 1} up`}
                title="Move up"
              >
                <Icon name="chevron-left" size={14} style={{ transform: "rotate(90deg)" }} />
              </button>
              <button
                type="button"
                className="btn-icon-control"
                disabled={index === totalCount - 1}
                onClick={onMoveDown}
                aria-label={`Move requirement ${index + 1} down`}
                title="Move down"
              >
                <Icon name="chevron-down" size={14} />
              </button>
              <button
                type="button"
                className="btn-icon-control text-destructive"
                onClick={onRemove}
                aria-label={`Remove requirement #${index + 1}`}
                title="Remove requirement"
              >
                <Icon name="trash" size={14} />
              </button>
            </>
          )}
        </div>
      </div>
    );
  }

  // =========================================================================
  // 2. Expanded Editing Mode
  // =========================================================================
  return (
    <div
      className={`requirement-card ${hasWarnings ? "has-review-warning" : ""}`}
      data-testid={`requirement-card-${index}`}
    >
      {/* Header Row: Order Number, Name/Autocomplete, Badges, Collapse & Actions */}
      <div className="requirement-card-header">
        <div className="requirement-order-badge">#{index + 1}</div>

        <div className="requirement-main-label-area">
          {isEditingLabel ? (
            isSkillCategory ? (
              <TaxonomySkillAutocomplete
                initialValue={requirement.label}
                onSelectSkill={handleSelectTaxonomySkill}
                onUseCustomText={handleUseCustomText}
                autoFocus={!requirement.label.trim()}
                placeholder="Search O*NET skills or type custom skill..."
              />
            ) : (
              <div className="custom-req-input-wrapper">
                <input
                  type="text"
                  className="form-input"
                  placeholder={`Enter ${requirement.category.toLowerCase()} requirement...`}
                  value={requirement.label}
                  onChange={(e) =>
                    onChange({
                      ...requirement,
                      label: e.target.value,
                      origin: "RECRUITER_ENTERED",
                      taxonomySkillConceptId: null,
                    })
                  }
                  onBlur={() => {
                    if (requirement.label.trim()) setIsEditingLabel(false);
                  }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && requirement.label.trim()) {
                      setIsEditingLabel(false);
                    }
                  }}
                  autoFocus={!requirement.label.trim()}
                  aria-label="Requirement description"
                />
              </div>
            )
          ) : (
            <div className="requirement-label-display">
              <span className="requirement-title-text">{requirement.label}</span>
              {requirement.origin === "TAXONOMY_SUGGESTED_RECRUITER_CONFIRMED" ? (
                <span className="badge badge-onet-confirmed">O*NET Skill</span>
              ) : (
                <span className="badge badge-custom-entered">Custom</span>
              )}
              {!readOnly && (
                <button
                  type="button"
                  className="btn-icon-subtle"
                  onClick={() => setIsEditingLabel(true)}
                  aria-label={`Edit title for requirement ${requirement.label || index + 1}`}
                  title="Edit title"
                >
                  <Icon name="edit" size={14} />
                </button>
              )}
            </div>
          )}
        </div>

        {/* Action Controls: Done/Collapse button, Reorder, Delete */}
        <div className="requirement-card-actions">
          <button
            type="button"
            className="btn btn-secondary btn-sm"
            onClick={onToggleExpand}
            aria-label={`Done editing requirement #${index + 1}`}
          >
            <Icon name="check" size={13} />
            <span>Done</span>
          </button>
          {!readOnly && (
            <>
              <button
                type="button"
                className="btn-icon-control"
                disabled={index === 0}
                onClick={onMoveUp}
                aria-label={`Move requirement ${requirement.label || index + 1} up`}
                title="Move up"
              >
                <Icon name="chevron-left" size={14} style={{ transform: "rotate(90deg)" }} />
              </button>
              <button
                type="button"
                className="btn-icon-control"
                disabled={index === totalCount - 1}
                onClick={onMoveDown}
                aria-label={`Move requirement ${requirement.label || index + 1} down`}
                title="Move down"
              >
                <Icon name="chevron-down" size={14} />
              </button>
              <button
                type="button"
                className="btn-icon-control text-destructive"
                onClick={onRemove}
                aria-label={`Remove requirement ${requirement.label || index + 1}`}
                title="Remove requirement"
              >
                <Icon name="trash" size={14} />
              </button>
            </>
          )}
        </div>
      </div>

      {/* Streamlined Primary Configuration Grid with Segmented Controls */}
      <div className="requirement-primary-controls-grid" style={{ alignItems: "flex-end" }}>
        {/* Category Dropdown */}
        <div className="form-group-compact">
          <label htmlFor={`cat-${index}`} className="form-label-xs">
            Category
          </label>
          <select
            id={`cat-${index}`}
            className="form-select-sm"
            value={requirement.category}
            disabled={readOnly}
            onChange={(e) => handleCategoryChange(e.target.value as RequirementCategory)}
          >
            <option value="SKILL">Skill</option>
            <option value="EXPERIENCE">Experience</option>
            <option value="RESPONSIBILITY">Responsibility</option>
            <option value="EDUCATION">Education</option>
            <option value="CERTIFICATION">Certification</option>
            <option value="LANGUAGE">Language</option>
            <option value="OTHER">Other</option>
          </select>
        </div>

        {/* Necessity: Segmented Controls [Must-have] [Preferred] */}
        <div className="form-group-compact">
          <span className="form-label-xs">Necessity</span>
          <div className="segmented-control" role="group" aria-label="Requirement necessity">
            <button
              type="button"
              className={`segmented-btn ${requirement.necessity === "MUST_HAVE" ? "active must-have" : ""}`}
              onClick={() => onChange({ ...requirement, necessity: "MUST_HAVE" })}
              disabled={readOnly}
              aria-pressed={requirement.necessity === "MUST_HAVE"}
            >
              Must-have
            </button>
            <button
              type="button"
              className={`segmented-btn ${requirement.necessity === "PREFERRED" ? "active preferred" : ""}`}
              onClick={() => onChange({ ...requirement, necessity: "PREFERRED" })}
              disabled={readOnly}
              aria-pressed={requirement.necessity === "PREFERRED"}
            >
              Preferred
            </button>
          </div>
        </div>

        {/* Priority: Segmented Controls [Critical] [High] [Medium] [Low] */}
        <div className="form-group-compact">
          <span className="form-label-xs">Priority</span>
          <div className="segmented-control" role="group" aria-label="Requirement priority">
            {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as RequirementPriority[]).map((prio) => (
              <button
                key={prio}
                type="button"
                className={`segmented-btn ${requirement.priority === prio ? "active" : ""}`}
                onClick={() => onChange({ ...requirement, priority: prio })}
                disabled={readOnly}
                aria-pressed={requirement.priority === prio}
              >
                {prio === "CRITICAL" ? "Critical" : prio === "HIGH" ? "High" : prio === "MEDIUM" ? "Med" : "Low"}
              </button>
            ))}
          </div>
        </div>

        {/* Min Experience Months */}
        <div className="form-group-compact">
          <label htmlFor={`exp-${index}`} className="form-label-xs">
            Min. Experience (Months)
          </label>
          <input
            id={`exp-${index}`}
            type="number"
            min={1}
            max={600}
            className="form-input-sm"
            placeholder="e.g. 24"
            disabled={readOnly}
            value={requirement.minExperienceMonths ?? ""}
            onChange={(e) => {
              const val = e.target.value ? parseInt(e.target.value, 10) : null;
              onChange({ ...requirement, minExperienceMonths: val });
            }}
          />
        </div>
      </div>

      {/* Immediate Mandatory Rationale when Must-Have or Critical */}
      {(isMustHave || isCritical) && (
        <div className="rationale-mandatory-box">
          <label htmlFor={`rat-${index}`} className="rationale-label-required">
            <Icon name="alert-circle" size={14} />
            <span>
              {isMustHave ? "Must-Have Rationale" : "Critical Priority Rationale"}{" "}
              <span className="required-star">*</span>
            </span>
          </label>
          <p style={{ fontSize: "11px", color: "var(--text-secondary)", marginBottom: "6px" }}>
            State why this criterion is an absolute requirement for this role to maintain defensible, job-related criteria.
          </p>
          <textarea
            id={`rat-${index}`}
            ref={rationaleTextareaRef}
            className="form-textarea-sm"
            rows={2}
            placeholder="e.g., Required for immediate production microservices maintenance and on-call rotation..."
            disabled={readOnly}
            value={requirement.rationale ?? ""}
            onChange={(e) => onChange({ ...requirement, rationale: e.target.value })}
            aria-required="true"
          />
        </div>
      )}

      {/* Review Warnings Indicator */}
      {hasWarnings && (
        <div className="requirement-warning-banner" role="alert">
          <Icon name="alert-triangle" size={15} className="warning-icon" />
          <div className="warning-content">
            {activeWarnings.map((w, wIdx) => (
              <p key={wIdx} className="warning-msg-text">
                {w.message}
              </p>
            ))}
          </div>
        </div>
      )}

      {/* Advanced Options Disclosure */}
      <div className="requirement-advanced-toggle-row">
        <button
          type="button"
          className="advanced-toggle-btn"
          onClick={() => setShowAdvancedOptions(!showAdvancedOptions)}
          aria-expanded={showAdvancedOptions}
        >
          <Icon name={showAdvancedOptions ? "chevron-down" : "chevron-right"} size={13} />
          <span>Advanced options (recency, equivalents, evidence locations)</span>
        </button>
      </div>

      {showAdvancedOptions && (
        <div className="requirement-advanced-panel">
          {/* Optional Rationale for Preferred criteria if not already shown */}
          {!(isMustHave || isCritical) && (
            <div className="form-group">
              <label htmlFor={`rat-opt-${index}`} className="form-label-xs">
                Job-related reason <span className="text-muted">(optional)</span>
              </label>
              <textarea
                id={`rat-opt-${index}`}
                className="form-textarea-sm"
                rows={2}
                placeholder="Optional explanation for why this preferred criterion is valued..."
                disabled={readOnly}
                value={requirement.rationale ?? ""}
                onChange={(e) => onChange({ ...requirement, rationale: e.target.value })}
              />
            </div>
          )}

          <div className="advanced-two-col-grid">
            {/* Recency Input */}
            <div className="form-group-compact">
              <label htmlFor={`rec-${index}`} className="form-label-xs">
                Recency requirement (Years)
              </label>
              <input
                id={`rec-${index}`}
                type="number"
                min={1}
                max={50}
                className="form-input-sm"
                placeholder="e.g. 3 (within last 3 years)"
                disabled={readOnly}
                value={requirement.recencyYears ?? ""}
                onChange={(e) => {
                  const val = e.target.value ? parseInt(e.target.value, 10) : null;
                  onChange({ ...requirement, recencyYears: val });
                }}
              />
            </div>
          </div>

          {/* Accepted Equivalents Tag Editor */}
          <div className="form-group">
            <label htmlFor={`equiv-${index}`} className="form-label-xs">
              Accepted equivalent terms <span className="text-muted">(Alternative titles or synonyms)</span>
            </label>
            {!readOnly && (
              <div className="tag-input-row">
                <input
                  id={`equiv-${index}`}
                  type="text"
                  className="form-input-sm"
                  placeholder="Add synonym or alias..."
                  value={equivalentInput}
                  onChange={(e) => setEquivalentInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      handleAddEquivalent();
                    }
                  }}
                />
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={handleAddEquivalent}
                >
                  Add
                </button>
              </div>
            )}
            {requirement.acceptedEquivalents && requirement.acceptedEquivalents.length > 0 && (
              <div className="tag-pill-container">
                {requirement.acceptedEquivalents.map((eq, eqIdx) => (
                  <span key={eqIdx} className="tag-pill">
                    {eq}
                    {!readOnly && (
                      <button
                        type="button"
                        className="tag-remove-btn"
                        onClick={() => handleRemoveEquivalent(eq)}
                        aria-label={`Remove equivalent ${eq}`}
                      >
                        <Icon name="x" size={11} />
                      </button>
                    )}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Accepted Evidence Types Checkboxes */}
          <div className="form-group">
            <label className="form-label-xs">Accepted Evidence Locations</label>
            <div className="evidence-checkboxes-grid">
              {ALL_EVIDENCE_TYPES.map(({ type, label }) => {
                const checked = (requirement.acceptedEvidenceTypes || []).includes(type);
                return (
                  <label key={type} className="checkbox-item-label">
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={readOnly}
                      onChange={() => handleToggleEvidenceType(type)}
                    />
                    <span>{label}</span>
                  </label>
                );
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
