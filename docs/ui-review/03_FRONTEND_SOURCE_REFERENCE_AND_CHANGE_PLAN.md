# 03: Frontend Source Reference and Change Plan

## 1. Central Design Foundation Sources

### 1.1 Global Design System Tokens (`frontend/src/index.css`)

```css
/* Path: frontend/src/index.css (Lines 1 to 136) */
/* ==========================================================================
   Smart Hiring Platform — Editorial Mineral Design System
   ========================================================================== */

:root {
  /* ── Surface & Canvas ── */
  --bg-app: #f3f0e6;
  --bg-surface: #fcfbf6;
  --bg-surface-subtle: #e9e5d9;
  --bg-surface-hover: #edeadf;
  --bg-surface-active: #e0ddd2;
  --bg-surface-raised: #ffffff;

  /* ── Text ── */
  --text-primary: #202927;
  --text-secondary: #5b6863;
  --text-muted: #6d7772;
  --text-inverse: #ffffff;

  /* ── Primary (Bottle Green) ── */
  --accent-primary: #174f4a;
  --accent-primary-hover: #113e3a;
  --accent-primary-active: #0d302d;
  --accent-subtle: #dce8e3;
  --accent-border: #b5cec5;

  /* Legacy alias kept so existing component code keeps compiling */
  --brand-primary: #174f4a;

  /* ── Accent (Gold / Ochre) ── */
  --color-accent: #e7d8b2;
  --color-accent-strong: #9a6f22;

  /* ── Semantic: Info ── */
  --color-info: #355c72;
  --color-info-soft: #dce8ed;

  /* ── Semantic: Success ── */
  --status-success: #2f6b4f;
  --status-success-bg: #dce9df;
  --status-success-border: #aad1b7;

  /* ── Semantic: Warning ── */
  --status-warn: #806022;
  --status-warn-bg: #eee3c5;
  --status-warn-border: #d9c98f;

  /* ── Semantic: Critical / Destructive (Aubergine — no red) ── */
  --status-danger: #63445b;
  --status-danger-bg: #eadfe7;
  --status-danger-border: #c9b5c3;

  /* ── Requirement Gap Tokens (Warm Accessible Palette) ── */
  --gap-covered: #2f6b4f;
  --gap-covered-bg: #dce9df;
  --gap-covered-border: #aad1b7;

  --gap-partial: #806022;
  --gap-partial-bg: #eee3c5;
  --gap-partial-border: #d9c98f;

  --gap-not-evidenced: #5b6863;
  --gap-not-evidenced-bg: #e9e5d9;
  --gap-not-evidenced-border: #cbc8ba;

  --gap-unverified: #355c72;
  --gap-unverified-bg: #dce8ed;
  --gap-unverified-border: #a8c8d8;

  --gap-conflicting: #9a6f22;
  --gap-conflicting-bg: #eee3c5;
  --gap-conflicting-border: #d9c98f;

  --gap-not-applicable: #6d7772;
  --gap-not-applicable-bg: #e9e5d9;
  --gap-not-applicable-border: #cbc8ba;

  --status-neutral: #6d7772;
  --status-neutral-bg: #e9e5d9;
  --status-neutral-border: #cbc8ba;

  /* ── Borders & Dividers ── */
  --border-subtle: #cbc8ba;
  --border-medium: #aaa799;
  --border-focus: #355c72;

  /* ── Spacing (8px rhythm) ── */
  --space-1: 0.25rem;   /* 4px  */
  --space-2: 0.5rem;    /* 8px  */
  --space-3: 0.75rem;   /* 12px */
  --space-4: 1rem;      /* 16px */
  --space-5: 1.25rem;   /* 20px */
  --space-6: 1.5rem;    /* 24px */
  --space-8: 2rem;      /* 32px */
  --space-10: 2.5rem;   /* 40px */
  --space-12: 3rem;     /* 48px */

  /* ── Layout Widths ── */
  --max-content-width: 1280px;
  --max-reading-width: 760px;
  --interview-max: 1100px;

  /* ── Radii ── */
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --radius-xl: 16px;
  --radius-full: 9999px;

  /* ── Shadows (warm base, no glow) ── */
  --shadow-subtle: 0 1px 2px rgba(32, 41, 39, 0.06);
  --shadow-card: 0 1px 3px rgba(32, 41, 39, 0.06);
  --shadow-md: 0 4px 8px rgba(32, 41, 39, 0.07);
  --shadow-raised: 0 10px 30px rgba(32, 41, 39, 0.09);
  --shadow-drawer: -4px 0 24px rgba(32, 41, 39, 0.10);
  --shadow-modal: 0 20px 30px rgba(32, 41, 39, 0.12);

  /* ── Typography ── */
  --font-body: "Avenir Next", "Segoe UI", "Helvetica Neue", Arial, sans-serif;
  --font-heading: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
  --font-sans: var(--font-body);
  --font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;

  /* Type Scale */
  --text-xs: 0.875rem;    /* 14px */
  --text-sm: 0.9375rem;   /* 15px */
  --text-base: 1.0625rem; /* 17px */
  --text-lg: 1.1875rem;   /* 19px */
  --text-xl: 1.375rem;    /* 22px */
  --text-2xl: 1.625rem;   /* 26px */
  --text-3xl: 2rem;       /* 32px */

  /* ── Transitions ── */
  --transition-fast: 150ms cubic-bezier(0.4, 0, 0.2, 1);
  --transition-normal: 200ms cubic-bezier(0.4, 0, 0.2, 1);
}
```

---

## 2. Core Reusable UI Component Sources

### 2.1 Button Component (`frontend/src/components/common/Button.tsx`)
```tsx
/* Path: frontend/src/components/common/Button.tsx (Complete File) */
import React, { ButtonHTMLAttributes, ReactNode } from "react";
import { Icon, IconName } from "../Icon";

export type ButtonVariant = "primary" | "secondary" | "quiet" | "destructive";
export type ButtonSize = "xs" | "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: IconName;
  iconPosition?: "left" | "right";
  isLoading?: boolean;
  loadingText?: string;
  children?: ReactNode;
}

export function Button({
  variant = "secondary",
  size = "md",
  icon,
  iconPosition = "left",
  isLoading = false,
  loadingText,
  disabled,
  className = "",
  children,
  ...rest
}: ButtonProps) {
  const variantClass =
    variant === "primary"
      ? "btn-primary"
      : variant === "quiet"
      ? "btn-quiet"
      : variant === "destructive"
      ? "btn-destructive"
      : "btn-secondary";

  const sizeClass =
    size === "xs"
      ? "btn-xs"
      : size === "sm"
      ? "btn-sm"
      : size === "lg"
      ? "btn-lg"
      : "btn-md";

  const isDisabled = disabled || isLoading;

  return (
    <button
      type="button"
      className={`btn ${variantClass} ${sizeClass} ${className}`}
      disabled={isDisabled}
      aria-busy={isLoading ? "true" : undefined}
      {...rest}
    >
      {isLoading ? (
        <>
          <span className="loading-spinner" aria-hidden="true" style={{ width: "14px", height: "14px" }} />
          <span>{loadingText || children || "Loading..."}</span>
        </>
      ) : (
        <>
          {icon && iconPosition === "left" && <Icon name={icon} size={size === "xs" ? 12 : size === "sm" ? 14 : 16} />}
          {children}
          {icon && iconPosition === "right" && <Icon name={icon} size={size === "xs" ? 12 : size === "sm" ? 14 : 16} />}
        </>
      )}
    </button>
  );
}
```

### 2.2 StatusBadge Component (`frontend/src/components/common/StatusBadge.tsx`)
```tsx
/* Path: frontend/src/components/common/StatusBadge.tsx (Complete File) */
import React from "react";
import { Icon, IconName } from "../Icon";

export type BadgeVariant =
  | "indigo"
  | "teal"
  | "amber"
  | "neutral"
  | "draft"
  | "approved"
  | "stale"
  | "validated"
  | "reviewed";

export interface StatusBadgeProps {
  status: string;
  variant?: BadgeVariant;
  icon?: IconName;
  className?: string;
}

export function humanizeStatus(raw: string): string {
  if (!raw) return "";
  const mappings: Record<string, string> = {
    APPROVED: "Approved",
    VALIDATED: "Ready for Review",
    DRAFT: "Draft",
    STALE: "Stale",
    KEPT: "Reviewed",
    EDITED: "Edited",
    REVISED: "Regenerated",
    RECRUITER_AUTHORED: "Written by Recruiter",
    UNREVIEWED: "To Review",
    PENDING: "Pending",
    PASSED: "Passed",
    FAILED: "Failed",
    CONNECTED: "Connected",
    UNAVAILABLE: "Unavailable",
  };

  if (mappings[raw.toUpperCase()]) {
    return mappings[raw.toUpperCase()];
  }

  return raw
    .replace(/[_-]+/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

export function StatusBadge({
  status,
  variant,
  icon,
  className = "",
}: StatusBadgeProps) {
  const displayLabel = humanizeStatus(status);

  const effectiveVariant: BadgeVariant =
    variant ||
    (status.toUpperCase() === "APPROVED" || status.toUpperCase() === "PASSED" || status.toUpperCase() === "KEPT"
      ? "approved"
      : status.toUpperCase() === "VALIDATED" || status.toUpperCase() === "EDITED"
      ? "validated"
      : status.toUpperCase() === "STALE" || status.toUpperCase() === "FAILED"
      ? "stale"
      : "neutral");

  const badgeClass =
    effectiveVariant === "approved" || effectiveVariant === "teal"
      ? "badge-status-approved"
      : effectiveVariant === "validated" || effectiveVariant === "indigo"
      ? "badge-status-validated"
      : effectiveVariant === "stale" || effectiveVariant === "amber"
      ? "badge-status-stale"
      : "badge-status-neutral";

  return (
    <span className={`badge ${badgeClass} ${className}`}>
      {icon && <Icon name={icon} size={12} className="badge-icon" />}
      <span>{displayLabel}</span>
    </span>
  );
}
```

### 2.3 InlineNotice Component (`frontend/src/components/common/InlineNotice.tsx`)
```tsx
/* Path: frontend/src/components/common/InlineNotice.tsx (Complete File) */
import React, { ReactNode } from "react";
import { Icon, IconName } from "../Icon";

export type NoticeVariant = "info" | "success" | "warning" | "error";

export interface InlineNoticeProps {
  variant?: NoticeVariant;
  title?: string;
  children: ReactNode;
  icon?: IconName;
  actions?: ReactNode;
  className?: string;
}

export function InlineNotice({
  variant = "info",
  title,
  children,
  icon,
  actions,
  className = "",
}: InlineNoticeProps) {
  const isAlert = variant === "error" || variant === "warning";
  const defaultIcon: IconName =
    variant === "error" || variant === "warning"
      ? "alert-triangle"
      : variant === "success"
      ? "check-circle"
      : "info";

  const effectiveIcon = icon || defaultIcon;

  return (
    <div
      className={`inline-notice inline-notice-${variant} ${className}`}
      role={isAlert ? "alert" : "status"}
      aria-live={isAlert ? "assertive" : "polite"}
    >
      <div className="inline-notice-icon-wrapper" aria-hidden="true">
        <Icon name={effectiveIcon} size={18} />
      </div>

      <div className="inline-notice-content">
        {title && <h4 className="inline-notice-title">{title}</h4>}
        <div className="inline-notice-message">{children}</div>
        {actions && <div className="inline-notice-actions">{actions}</div>}
      </div>
    </div>
  );
}
```

---

## 3. Targeted Style Rules for the Ten Observed Issues

### 3.1 Issues 1 & 2: Pale Green Panels & Nested Colored Boxes (`evaluation.css`)
```css
/* Path: frontend/src/components/evaluation/evaluation.css (Lines 647-656, 786-818) */
/* [SECTION EXCERPT: Remaining evaluation.css rules omitted] */

.eval-evidence-card {
  background-color: #ffffff;
  border: 1px solid var(--border-subtle, #cbd5e1);
  border-radius: var(--radius-lg, 10px);
  padding: 22px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.eval-overview-item {
  background-color: var(--bg-surface-subtle, #f8fafc);
  border: 1px solid var(--border-subtle, #cbd5e1);
  border-radius: 8px;
  padding: 14px;
}

.eval-overview-strengths {
  background-color: #ecfdf5;  /* <-- AUDIT ISSUE 1: Pale mint green */
  border-color: #6ee7b7;
}

.eval-overview-strengths .eval-metric-label {
  color: #064e3b;             /* <-- AUDIT ISSUE 1: Low contrast label */
}

.eval-overview-growth {
  background-color: var(--accent-subtle, #e0e7ff); /* <-- AUDIT ISSUE 2: Blue/violet */
  border-color: var(--accent-border, #c7d2fe);
}

.eval-overview-growth .eval-metric-label {
  color: var(--accent-primary-hover, #3730a3);
}
```

### 3.2 Issue 3: Pink & Lavender AI Backgrounds & Badges (`evaluation.css`)
```css
/* Path: frontend/src/components/evaluation/evaluation.css (Lines 987-1028) */
/* [SECTION EXCERPT: Remaining evaluation.css rules omitted] */

.eval-ai-provisional-box {
  background-color: #faf5ff;   /* <-- AUDIT ISSUE 3: Lavender backing */
  border: 1px solid #d8b4fe;   /* <-- AUDIT ISSUE 3: Pink/violet border */
  border-radius: 8px;
  padding: 18px;
  margin: 0 22px 8px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.eval-ai-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 14px;
  font-weight: 800;
  color: #581c87;              /* <-- AUDIT ISSUE 3: Purple text */
  background-color: #f3e8ff;   /* <-- AUDIT ISSUE 3: Lavender tag */
  padding: 4px 10px;
  border-radius: 6px;
}

.eval-ai-score-badge {
  font-size: 14px;
  font-weight: 700;
  color: #581c87;
}
```

### 3.3 Issue 4: Coloured Evaluation Counters (`evaluation.css`)
```css
/* Path: frontend/src/components/evaluation/evaluation.css (Lines 333-380) */
/* [SECTION EXCERPT: Remaining evaluation.css rules omitted] */

.eval-metric-box {
  background-color: var(--bg-surface-subtle, #f5f2ea);
  border: 1px solid var(--border-subtle, #d9d4c7);
  border-radius: 6px;
  padding: 8px 14px;
  min-width: 84px;
  text-align: center;
}

.eval-metric-box.ready {
  background-color: #f1edfa;   /* <-- AUDIT ISSUE 4: Lavender counter fill */
  border-color: #d8cde9;
}

.eval-metric-box.ready .eval-metric-label {
  color: #554477;
}

.eval-metric-box.ready .eval-metric-value {
  color: #433363;
}
```

### 3.4 Issue 5 & 6: Inconsistent & Light Green Badges (`index.css` & `ai-interviews.css`)
```css
/* Path: frontend/src/index.css (Lines 678-701) */
.badge-status-approved {
  background-color: var(--status-success-bg); /* #dce9df - AUDIT ISSUE 6: Faded light green */
  color: var(--status-success);               /* #2f6b4f */
  border: 1px solid var(--status-success-border);
}

.badge-status-validated {
  background-color: var(--accent-subtle);
  color: var(--accent-primary);
  border: 1px solid var(--accent-border);
}

/* Path: frontend/src/components/ai_interviews/ai-interviews.css (Lines 449-470) */
/* [SECTION EXCERPT: Remaining ai-interviews.css rules omitted] */
.aiq-status-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 8px;
  border-radius: 4px;          /* <-- Inconsistent with 6px/12px in index.css */
  font-size: 12px;
  font-weight: 600;
}
```

### 3.5 Issue 7: Wide Green Readiness Messages & Small Fonts (`index.css`)
```css
/* Path: frontend/src/index.css (Lines 2776-2795) */
.feedback-box {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px 16px;
  border-radius: var(--radius-md);
  font-size: 13px; /* <-- AUDIT ISSUE 7: Disproportionately small font */
}

.feedback-box.success {
  background-color: var(--status-success-bg); /* Full width sprawling green */
  border: 1px solid var(--status-success-border);
  color: #065f46;
}
```

### 3.6 Issues 8, 9, 10: Audio Notice, Return Button, & Consent Checkbox (`CandidateInterviewWorkspace.tsx`)
```tsx
/* Path: frontend/src/components/candidate/CandidateInterviewWorkspace.tsx */
/* [SECTION EXCERPT: Remaining lines omitted] */

// ISSUE 8: Hardcoded Blue Notice Panel (Lines 837-850)
<div
  style={{
    backgroundColor: "#eff6ff",  // <-- AUDIT ISSUE 8: Hardcoded bright blue
    border: "1px solid #bfdbfe",
    borderRadius: "8px",
    padding: "16px 20px",
    marginBottom: "24px",
    fontSize: "13px",            // <-- Low readability
    color: "#1e3a8a",
  }}
>
  <strong>Zero Audio Retention Policy:</strong> Your spoken recording is processed entirely locally...
</div>

// ISSUE 10: Fluorescent / Pale Green Consent Shift (Lines 869-887)
<label
  style={{
    display: "flex",
    alignItems: "flex-start",
    gap: "12px",
    padding: "12px 14px",
    borderRadius: "8px",
    backgroundColor: micAcknowledged ? "#f0fdf4" : "var(--surface-muted, #f8fafc)", // <-- AUDIT ISSUE 10: Fluorescent green shift
    border: micAcknowledged ? "1px solid #bbf7d0" : "1px solid var(--border-subtle, #e2e8f0)",
  }}
>
  <input
    type="checkbox"
    checked={micAcknowledged}
    onChange={(e) => setMicAcknowledged(e.target.checked)}
    style={{ marginTop: "3px", accentColor: "#2563eb" }} // <-- Hardcoded blue accent
  />
  <span style={{ fontSize: "13px" }}>I understand that my microphone will be used solely...</span>
</label>

// ISSUE 9: Insufficient Padding Around "Return to Interviews" (Lines 902-910)
<div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "16px" }}>
  <button
    type="button"
    className="btn btn-secondary"
    onClick={onExit} // <-- AUDIT ISSUE 9: No top container padding or clear separation
  >
    Return to Interviews
  </button>
```

---

## 4. CSS Precedence & Dependency Model

To execute the redesign safely without cascading regression bugs, the replacement assistant must understand the CSS hierarchy:

1. **Hierarchy Order**:
   - Level 0: `:root` tokens in `frontend/src/index.css`.
   - Level 1: Global resets and shared button/form/dialog classes (`.btn`, `.badge`, `.inline-notice`, `.feedback-box`) in `index.css`.
   - Level 2: Scoped stylesheets loaded per feature: `evaluation.css` (scoped under `.eval-*`), `ai-interviews.css` (scoped under `.aiq-*`).
   - Level 3: Inline component styles (`style={{ ... }}`) which take highest CSS specificity precedence over stylesheets.
2. **Precedence Pitfalls to Avoid**:
   - Editing `.eval-metric-box` in `evaluation.css` will not take effect if an inline style is present.
   - Removing classes like `.badge-status-approved` from `index.css` will break `StatusBadge.tsx` across 12 distinct pages.
   - Do not use `!important` to force theme colors; instead, remove the hardcoded inline styles in the JSX files.

---

## 5. Recommended Implementation Order

The recommended sequence for implementing the visual-polish update:

1. **Phase 1: Foundational Tokens (`frontend/src/index.css`)**:
   - Normalize `--status-success`, `--status-success-bg`, and `--status-success-border` to darker, higher-contrast forest green values.
   - Ensure `--text-xs` is at least 14px and `--text-sm` is 15px.
2. **Phase 2: Shared Component Harmonization (`src/components/common/`)**:
   - Update `StatusBadge.tsx` and `InlineNotice.tsx` to strictly use the normalized design tokens.
   - Eliminate lavender/indigo variants in favor of bottle green, slate, and gold neutrals.
3. **Phase 3: Evaluation Workspace Restraint (`src/components/evaluation/`)**:
   - In `evaluation.css`, replace all lavender (`#faf5ff`, `#d8b4fe`, `#581c87`), pale green (`#ecfdf5`), and blue tints with clean off-white surfaces (`--bg-surface`, `#fcfbf6`) and crisp borders (`#cbc8ba`).
   - Flatten nested evidence cards into un-nested white rows.
4. **Phase 4: Candidate Audio & Interview Refinements (`src/components/candidate/`)**:
   - In `CandidateInterviewWorkspace.tsx`, remove inline hardcoded blue notices, eliminate the fluorescent green consent background shift, and add generous 32px top padding to the action footer containing "Return to Interviews".
5. **Phase 5: Job Readiness & Banner Improvements (`src/components/jobs/`)**:
   - Update `.feedback-box` in `index.css` and `JobEditor.tsx` to use clean 15px text and restrained notification styling instead of sprawling full-width pale green banners.

---

## 6. Functional Behaviors That Must Remain Intact

The redesign must strictly preserve the following functional mechanisms:
- **Authentication & RBAC**: Automatic cookie session verification, password change requirement enforcement, and role-based tab access (`ADMIN`, `RECRUITER`, `REVIEWER`).
- **Job Eligibility & O*NET Taxonomy**: Occupation selection, skill mapping, and requisition revision locking.
- **Same-Job Comparison & Gap Analysis**: Match score calculations, frozen result retrieval, and deterministic requirement contributions.
- **Dual-Mode Interview Execution**: Text interview question recording and audio speech-to-text transcription via Whisper.cpp.
- **Candidate Consent & Zero-Retention**: Explicit microphone acknowledgement checkbox and candidate transcript confirmation before submission.
- **Evaluation Integrity**: Deterministic MCQ grading, AI provisional rubric observations, and recruiter review overrides with required justification notes.

---

## 7. Additional Source Files Needed by the Reviewing Assistant

Before generating replacement code, the reviewing assistant should inspect:
- `frontend/src/components/jobs/CandidateComparisonView.tsx`: For score pill and candidate card styling.
- `frontend/src/components/jobs/RequirementGapReportView.tsx`: For gap state badge styling.
- `frontend/src/components/candidate/CandidatePracticeReport.tsx`: For candidate feedback report visual styling.
- `dashboard/ui/styles.py`: For Streamlit companion dashboard alignment.

---

## 8. Verification Checklist

- [ ] **TypeScript & Vite Build**: Execute `npm --prefix frontend run build` to confirm zero compilation errors.
- [ ] **Evaluation Workspace Tests**: Run `npm --prefix frontend test -- --run src/__tests__/phase4c4_evaluation_workspace.test.tsx` and `src/__tests__/phase4d4_mode_aware_evaluation_ui.test.tsx`.
- [ ] **Backend Test Compatibility**: Run `pytest backend/tests/test_phase4c4_evaluation_workspace.py backend/tests/test_phase4d4_mode_aware_evaluation.py`.
- [ ] **Visual Verification: No Pale Green Panels**: Confirm interview summary cards use clean off-white surfaces with high-contrast text.
- [ ] **Visual Verification: No Lavender/Pink**: Confirm AI analysis cards and tags use dark forest green and slate tones with zero purple or pink.
- [ ] **Visual Verification: Restrained Consent Box**: Confirm clicking candidate audio consent does not turn the container fluorescent green.
- [ ] **Visual Verification: Button Breathing Room**: Confirm "Return to Interviews" has at least 24px–32px of spacing above it.
