# 02: Design System and UI Issue Audit

## 1. Current Design System Tokens & Base Values

The application's foundational tokens are declared in `frontend/src/index.css` under `:root`.

### Color Tokens
| Token Category | Variable Name | Current Hex Value | Purpose / Notes |
| :--- | :--- | :--- | :--- |
| **Canvas** | `--bg-app` | `#f3f0e6` | Main page parchment background |
| **Surface** | `--bg-surface` | `#fcfbf6` | Warm off-white card/container background |
| **Surface Raised** | `--bg-surface-raised`| `#ffffff` | Pure white dialogs, popovers, active inputs |
| **Surface Subtle** | `--bg-surface-subtle`| `#e9e5d9` | Subtle section backings, table headers |
| **Text Primary** | `--text-primary` | `#202927` | Deep dark slate/charcoal for headings and body |
| **Text Secondary**| `--text-secondary` | `#5b6863` | Medium muted slate for subtitles and metadata |
| **Text Muted** | `--text-muted` | `#6d7772` | Deeper secondary text for captions and timestamps |
| **Accent Primary**| `--accent-primary` | `#174f4a` | Bottle green brand primary color |
| **Accent Hover** | `--accent-primary-hover`| `#113e3a`| Darker green hover state |
| **Accent Subtle** | `--accent-subtle` | `#dce8e3` | Pale sage green container tint |
| **Accent Border** | `--accent-border` | `#b5cec5` | Soft green border for primary highlights |
| **Status Success**| `--status-success` | `#2f6b4f` | Forest green success label color |
| **Status Success BG**| `--status-success-bg`| `#dce9df`| Pale green success container background |
| **Status Warning**| `--status-warn` | `#806022` | Ochre / mustard warning label color |
| **Status Warning BG**| `--status-warn-bg` | `#eee3c5` | Warm sand warning background |
| **Status Danger** | `--status-danger` | `#63445b` | Aubergine / muted plum (strictly zero pure red) |
| **Status Danger BG**| `--status-danger-bg`| `#eadfe7` | Muted mauve danger background |
| **Borders** | `--border-subtle` | `#cbc8ba` | Primary hairline border for cards and panels |
| **Borders** | `--border-medium` | `#aaa799` | Emphasized divider/input boundary |

### Typography Tokens
- **Body Font (`--font-body`)**: `"Avenir Next", "Segoe UI", "Helvetica Neue", Arial, sans-serif`
- **Heading Font (`--font-heading`)**: `"Iowan Old Style", "Palatino Linotype", Georgia, serif`
- **Monospace Font (`--font-mono`)**: `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace`
- **Scale**:
  - `--text-xs`: `0.875rem` (14px)
  - `--text-sm`: `0.9375rem` (15px)
  - `--text-base`: `1.0625rem` (17px)
  - `--text-lg`: `1.1875rem` (19px)
  - `--text-xl`: `1.375rem` (22px)
  - `--text-2xl`: `1.625rem` (26px)
  - `--text-3xl`: `2rem` (32px)

### Button Dimensions & Spacing Rhythm
- **Base Rhythm**: 8px (`--space-1`: 4px, `--space-2`: 8px, `--space-3`: 12px, `--space-4`: 16px, `--space-6`: 24px, `--space-8`: 32px)
- **Radii**: `--radius-sm`: `6px`, `--radius-md`: `10px`, `--radius-lg`: `14px`, `--radius-xl`: `16px`, `--radius-full`: `9999px`
- **Button Heights & Paddings**:
  - `btn-xs`: `min-height: 30px`, `padding: 4px 10px`, `font-size: 14px`
  - `btn-sm`: `min-height: 36px`, `padding: 6px 14px`, `font-size: 14px`
  - `btn-md`: `min-height: 44px`, `padding: 10px 18px`, `font-size: 15px`
  - `btn-lg`: `min-height: 48px`, `padding: 12px 24px`, `font-size: 16px`

---

## 2. Token Architecture & Hardcoding / Override Audit

While `index.css` defines the central tokens, there are significant style overrides and hardcoded values across secondary stylesheets and component inline styles:

1. **`evaluation.css` Overrides**:
   - Re-defines custom colors that break the Editorial Mineral palette, introducing soft blues (`#dbeafe`), bright purples/lavenders (`#faf5ff`, `#d8b4fe`, `#581c87`), and light pastel greens (`#ecfdf5`, `#6ee7b7`) instead of using `--bg-surface`, `--accent-primary`, and `--border-subtle`.
2. **`ai-interviews.css` Overrides**:
   - Introduces independent badge styling (`.aiq-status-badge`, `.aiq-nav-state-badge`) with hardcoded RGB/hex values (`#4f5bd5`, `#e0e7ff`, `#fef3c7`) bypassing `StatusBadge.tsx` and `--status-*` tokens.
3. **Component Inline Styles**:
   - `CandidateInterviewWorkspace.tsx` contains dozens of inline `style={{ ... }}` blocks for audio setup boxes, recording waveforms, consent containers, and action footers.
   - `JobEditor.tsx` contains inline styles on the Requisition Readiness card and finding groups.

---

## 3. Ten Observed Issues: Root-Cause Audit & Recommendations

### Issue 1: Faint Text and Pale Green Interview-Summary Panels
- **Observed Problem**: Interview assessment overview panels display with washed-out, pale green backings and faint low-contrast text.
- **Exact Location**:
  - `frontend/src/components/evaluation/evaluation.css` (lines 794–801, 811–813):
    ```css
    .eval-overview-strengths {
      background-color: #ecfdf5;
      border-color: #6ee7b7;
    }
    .eval-overview-strengths .eval-metric-label {
      color: #064e3b;
    }
    .eval-overview-strengths .eval-ai-list {
      color: #064e3b;
    }
    ```
  - `frontend/src/components/evaluation/EvaluationWorkspace.tsx` (lines 1408–1415)
- **Other Pages Sharing Pattern**: `CandidatePracticeReport.tsx` (feedback callout panels) and `CandidateComparisonView.tsx` (candidate strengths summary box).
- **Intended Recommendation**: Replace the pale green background (`#ecfdf5`) with an off-white card surface (`--bg-surface`, `#fcfbf6`) with a crisp mineral border (`--border-subtle`, `#cbc8ba`). Set the metric label and text to `--text-primary` (`#202927`) and `--text-secondary` (`#5b6863`), with a subtle forest green indicator badge or accent icon.

---

### Issue 2: Nested Coloured Sections in Interview Evaluations
- **Observed Problem**: The evaluation workspace nests multiple colored boxes inside each other (e.g. green strengths box inside a gray card inside a beige page), creating visual visual noise.
- **Exact Location**:
  - `frontend/src/components/evaluation/evaluation.css` (lines 647–656, 786–818):
    ```css
    .eval-evidence-card { background-color: #ffffff; padding: 22px; }
    .eval-overview-item { background-color: var(--bg-surface-subtle, #f8fafc); ... }
    .eval-overview-strengths { background-color: #ecfdf5; ... }
    .eval-overview-growth { background-color: var(--accent-subtle, #e0e7ff); ... }
    ```
  - `frontend/src/components/evaluation/EvaluationWorkspace.tsx` (lines 1353–1425)
- **Other Pages Sharing Pattern**: `RequirementGapReportView.tsx` (nested requirement evidence and gap breakdown boxes).
- **Intended Recommendation**: Flatten the visual hierarchy. Keep the parent container on `--bg-surface` (`#fcfbf6`), and use clean, un-nested white rows (`--bg-surface-raised`, `#ffffff`) separated by subtle borders (`--border-subtle`, `#cbc8ba`) rather than stacking alternating pastel colored backgrounds.

---

### Issue 3: Pink/Lavender AI-Analysis Backgrounds and Rubric Badges
- **Observed Problem**: AI provisional analysis cards and badges use decorative pink, violet, and lavender tones (`#faf5ff`, `#d8b4fe`, `#f3e8ff`, `#581c87`), which clash with the platform's restrained Editorial Mineral brand identity.
- **Exact Location**:
  - `frontend/src/components/evaluation/evaluation.css` (lines 987–1028):
    ```css
    .eval-ai-provisional-box {
      background-color: #faf5ff;
      border: 1px solid #d8b4fe;
    }
    .eval-ai-tag {
      color: #581c87;
      background-color: #f3e8ff;
    }
    .eval-ai-score-badge { color: #581c87; }
    .eval-ai-disclaimer { color: #6b21a8; }
    ```
  - Mode badge styling in `evaluation.css` (lines 1561–1572):
    ```css
    .eval-badge-mode-audio {
      color: #6b21a8;
      background-color: #f3e8ff;
      border: 1px solid #e9d5ff;
    }
    ```
  - `frontend/src/components/evaluation/EvaluationWorkspace.tsx` (lines 443–455)
- **Other Pages Sharing Pattern**: `CandidateSafePreviewModal.tsx` and `AiQuestionSetWorkspace.tsx` (uses `.aiq-score-badge` and lavender icons).
- **Intended Recommendation**: Strip decorative violet and lavender colors entirely. Style AI provisional analysis boxes using off-white surfaces (`--bg-surface`, `#fcfbf6`), an authoritative dark forest green or slate border (`#b5cec5` / `#cbc8ba`), and a restrained mineral tag with `--accent-primary` (`#174f4a`) text and `--accent-subtle` (`#dce8e3`) background.

---

### Issue 4: Coloured Evaluation Summary Counters
- **Observed Problem**: The top-level evaluation counters for job readiness and status ("Ready for review", "Finalized") use arbitrary lavender and mint tints (`#f1edfa`, `#d8cde9`, `#433363`).
- **Exact Location**:
  - `frontend/src/components/evaluation/evaluation.css` (lines 342–350, 361–379):
    ```css
    .eval-metric-box.ready {
      background-color: #f1edfa;
      border-color: #d8cde9;
    }
    .eval-metric-box.ready .eval-metric-label { color: #554477; }
    .eval-metric-box.ready .eval-metric-value { color: #433363; }
    .eval-metric-box.finalized {
      background-color: var(--status-success-bg, #dce9df);
      border-color: var(--status-success-border, #aad1b7);
    }
    ```
  - `frontend/src/components/evaluation/EvaluationWorkspace.tsx` (lines 834–852)
- **Other Pages Sharing Pattern**: `AiInterviewsDirectory.tsx` metrics strip (`.aiq-stat-card`) and `AdminWorkspace.tsx` system metrics.
- **Intended Recommendation**: Standardize metric boxes to a uniform off-white surface (`--bg-surface`, `#fcfbf6`) with consistent typography. Differentiate statuses with a small clean dot indicator or a standardized `StatusBadge` instead of flood-filling the entire metric box background with tinted pastels.

---

### Issue 5: Inconsistent Information/Status Tags throughout AI Interviews and Jobs
- **Observed Problem**: Inconsistent tag dimensions, font sizes, border radii, and color mappings between AI Interviews, Job Editor, and Evaluations. AI Interviews defines `.aiq-status-badge`, Job Editor uses `.badge`, and Evaluation uses `.eval-status-badge`.
- **Exact Location**:
  - `frontend/src/components/ai_interviews/ai-interviews.css` (lines 449–485)
  - `frontend/src/components/evaluation/evaluation.css` (lines 555–595)
  - `frontend/src/index.css` (lines 678–702, 868–955)
  - `frontend/src/components/common/StatusBadge.tsx` (lines 54–88)
- **Other Pages Sharing Pattern**: `CandidateComparisonView.tsx` (`.badge-status-neutral`, `.pill-score`) and `JobApplicantsView.tsx`.
- **Intended Recommendation**: Consolidate all tag styling to rely on the shared `StatusBadge` component and shared CSS classes in `index.css`. Standardize on a single height (24px), font size (12px / 0.75rem), font weight (600), and border radius (4px or 6px), mapped exclusively to `--status-*` tokens.

---

### Issue 6: Light Green Job-Readiness Tags
- **Observed Problem**: Requisition readiness pills and candidate match indicators use low-contrast, light lime/mint green backgrounds that feel faded and are hard to read on parchment backgrounds.
- **Exact Location**:
  - `frontend/src/index.css` (lines 679–683):
    ```css
    .badge-status-approved {
      background-color: var(--status-success-bg); /* #dce9df */
      color: var(--status-success);               /* #2f6b4f */
      border: 1px solid var(--status-success-border);
    }
    ```
  - `frontend/src/components/jobs/JobEditor.tsx` (lines 1433–1439): `.feedback-box.success` using `#065f46` on `#dce9df`.
- **Other Pages Sharing Pattern**: `CandidatesDirectory.tsx` match badges and `CandidatePracticeDirectory.tsx` completed interview tags.
- **Intended Recommendation**: Increase contrast by using white backgrounds with a solid 1px border (`--accent-border`, `#b5cec5`) and dark forest green typography (`#174f4a` or `#134e4a`), or use a deep bottle green fill with crisp white text (`#ffffff`).

---

### Issue 7: Wide Green Readiness Messages, Small Fonts, and Excessive Empty Space
- **Observed Problem**: In `JobEditor.tsx`, the Requisition Readiness banner stretches across the full container width with a pale green fill, tiny 13px text, and disproportionate empty padding.
- **Exact Location**:
  - `frontend/src/index.css` (lines 2776–2795, 6283–6315):
    ```css
    .feedback-box {
      display: flex;
      align-items: flex-start;
      gap: 10px;
      padding: 12px 16px;
      border-radius: var(--radius-md);
      font-size: 13px; /* Too small */
    }
    .feedback-box.success {
      background-color: var(--status-success-bg);
      border: 1px solid var(--status-success-border);
      color: #065f46;
    }
    ```
  - `frontend/src/components/jobs/JobEditor.tsx` (lines 1430–1455)
- **Other Pages Sharing Pattern**: `ResumeUpload.tsx` (completion banner) and `ExtractionReview.tsx` (validation notice).
- **Intended Recommendation**: Increase text size from 13px to 15px (`--text-sm`). Cap the maximum content reading width or use an `InlineNotice` with a compact icon badge, eliminating sprawling horizontal empty green space.

---

### Issue 8: Candidate Audio-Interview Typography and Coloured Information Panels
- **Observed Problem**: In `CandidateInterviewWorkspace.tsx`, the audio landing screen displays a bright blue notice box (`#eff6ff`, `#bfdbfe`, `#1e3a8a`, `#2563eb`) with small 12px/13px font sizes, jarring against the platform's cream/green palette.
- **Exact Location**:
  - `frontend/src/components/candidate/CandidateInterviewWorkspace.tsx` (lines 837–865):
    ```tsx
    style={{
      backgroundColor: "#eff6ff",
      border: "1px solid #bfdbfe",
      borderRadius: "8px",
      padding: "16px 20px",
      fontSize: "13px",
      color: "#1e3a8a",
      lineHeight: 1.5,
    }}
    ```
- **Other Pages Sharing Pattern**: `CandidateApplicationDetail.tsx` and `CandidatePracticeRoom.tsx`.
- **Intended Recommendation**: Harmonize the notice box with Editorial Mineral tokens: background `--bg-surface` (`#fcfbf6`), border `--border-subtle` (`#cbc8ba`), typography `--text-primary` (`#202927`), and icon color `--accent-primary` (`#174f4a`). Increase font size to 14px–15px for enhanced readability.

---

### Issue 9: Insufficient Padding Around "Return to Interviews"
- **Observed Problem**: The "Return to Interviews" button in `CandidateInterviewWorkspace.tsx` is crowded tightly against surrounding layout containers without sufficient breathing room.
- **Exact Location**:
  - `frontend/src/components/candidate/CandidateInterviewWorkspace.tsx` (lines 902–910):
    ```tsx
    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "16px" }}>
      <button type="button" className="btn btn-secondary" onClick={onExit}>
        Return to Interviews
      </button>
    ```
  - Submissions screen (lines 1067–1075) and error screen (lines 929–936).
- **Other Pages Sharing Pattern**: `SimulationWorkspace.tsx` exit button and `CandidatePracticeReport.tsx` back button.
- **Intended Recommendation**: Wrap the action footer in a dedicated action bar container with explicit top margin/padding (`padding-top: var(--space-6)` / 24px, `margin-top: var(--space-8)` / 32px) and a subtle top divider rule (`border-top: 1px solid var(--border-subtle)`).

---

### Issue 10: Consent Containers Turning Fluorescent/Pale Green After Selection
- **Observed Problem**: When candidates check the microphone consent checkbox in `CandidateInterviewWorkspace.tsx`, the entire container dynamically shifts from neutral gray to a bright mint/fluorescent green background (`#f0fdf4`) with a bright green border (`#bbf7d0`).
- **Exact Location**:
  - `frontend/src/components/candidate/CandidateInterviewWorkspace.tsx` (lines 869–881):
    ```tsx
    <label
      style={{
        backgroundColor: micAcknowledged ? "#f0fdf4" : "var(--surface-muted, #f8fafc)",
        border: micAcknowledged ? "1px solid #bbf7d0" : "1px solid var(--border-subtle, #e2e8f0)",
        ...
      }}
    >
      <input
        type="checkbox"
        checked={micAcknowledged}
        style={{ marginTop: "3px", accentColor: "#2563eb" }}
      />
    ```
- **Other Pages Sharing Pattern**: `CandidateApplicationForm.tsx` (terms acknowledgement checkbox).
- **Intended Recommendation**: Keep the container background steady (`--bg-surface`, `#fcfbf6`) regardless of checked state. Reflect the active state solely via the checkbox checkmark (styled with `--accent-primary`, `#174f4a`) and an optional subtle dark forest green border outline (`--accent-border`, `#b5cec5`), avoiding sudden jarring whole-container fluorescent green shifts.

---

## 4. Source-Confirmed Issues vs Visual Inspection Concerns

| Audit Item | Status | Source-Confirmed Evidence | Items Requiring Live Visual Inspection |
| :--- | :--- | :--- | :--- |
| **1. Faint Text / Pale Green Panels** | Source-Confirmed | `evaluation.css:794` (`#ecfdf5`, `#064e3b`), `EvaluationWorkspace.tsx:1408` | Contrast ratio verification in browser under varying display profiles |
| **2. Nested Coloured Sections** | Source-Confirmed | `evaluation.css:647` (`.eval-evidence-card` nesting `.eval-overview-item`) | Visual rhythm and spacing balance across mobile vs desktop viewports |
| **3. Pink/Lavender AI Backgrounds** | Source-Confirmed | `evaluation.css:988` (`#faf5ff`, `#d8b4fe`, `#581c87`), `1561` (`#f3e8ff`) | Verification that removing lavender tint does not reduce AI provenance distinction |
| **4. Coloured Summary Counters** | Source-Confirmed | `evaluation.css:342-379` (`#f1edfa`, `#d8cde9`, `#433363`) | Verification of recruiter scannability on high-density job lists |
| **5. Inconsistent Status Tags** | Source-Confirmed | Discrepancies between `StatusBadge.tsx`, `ai-interviews.css`, `evaluation.css` | Inventory check of badge padding uniformity across all 18 pages |
| **6. Light Green Readiness Tags** | Source-Confirmed | `index.css:679` (`.badge-status-approved`), `JobEditor.tsx:1433` | Accessibility contrast ratio check against WCAG 2.1 AA (4.5:1 for text) |
| **7. Wide Readiness Messages** | Source-Confirmed | `index.css:2776` (`.feedback-box` font size 13px, no max-width) | Screen layout inspection at 1440px wide viewport |
| **8. Audio Typography & Panels** | Source-Confirmed | `CandidateInterviewWorkspace.tsx:837` (`#eff6ff`, `#bfdbfe`, 13px) | Readability on candidate laptop screens |
| **9. Return to Interviews Padding** | Source-Confirmed | `CandidateInterviewWorkspace.tsx:902` (inadequate wrapper spacing) | Button touch-target tap area on tablet/mobile screens |
| **10. Fluorescent Consent Container**| Source-Confirmed | `CandidateInterviewWorkspace.tsx:877` (`#f0fdf4`, `#bbf7d0`, accent `#2563eb`) | Transition animation smoothness upon clicking checkbox |
