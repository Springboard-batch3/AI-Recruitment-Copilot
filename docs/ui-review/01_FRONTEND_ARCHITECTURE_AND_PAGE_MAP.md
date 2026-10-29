# 01: Frontend Architecture and Page Map

## 1. Executive Summary & Stack Overview

The AI-Hiring frontend is a single-page application (SPA) built with React 19 and Vite 6, accompanied by an analytics companion application implemented in Streamlit. The application implements an "Editorial Mineral" aesthetic theme (predominantly bottle green, cream, slate, and gold neutrals) designed without Tailwind CSS or large UI component libraries.

### Key Technology Stack & Dependencies

```
Frontend:
  Runtime / Framework: React ^19.2.8, React DOM ^19.2.8
  Language / Tooling: TypeScript ^7.0.2, Vite ^6.2.0 (ESM modules)
  Testing: Vitest ^4.1.11, JSDOM ^30.0.1, @testing-library/react ^16.3.3
  Styling: Vanilla CSS with custom property design tokens (index.css, evaluation.css, ai-interviews.css)
  Icons: Custom SVG icon sprite mapper (frontend/src/components/Icon.tsx)
  Charts: Native SVG & HTML/CSS (InterviewDimensionRadar.tsx, VisualAnalyticsCharts.tsx) — no Heavy Chart JS libraries

Companion Dashboard:
  Runtime: Python 3.14 (backend/.venv)
  Framework: Streamlit (run via dashboard/app.py)
  Plotting: Native Streamlit charts / Altair
```

---

## 2. Directory Structure & Application Entry Points

```
frontend/
├── index.html                   # Base HTML shell with root mount point (#root)
├── vite.config.ts               # Vite configuration (port 5173, dev server proxy for /api)
├── src/
│   ├── main.tsx                 # Application bootstrap (mounts <App /> into #root)
│   ├── App.tsx                  # Top-level portal switch, auth routing, and view state management
│   ├── index.css                # Global design system tokens, resets, shared button/form/card utilities (10,300+ lines)
│   ├── setupTests.ts            # Vitest DOM matchers setup
│   ├── api/                     # Backend API client adapters (fetch wrappers with credentials)
│   │   ├── client.ts            # Base apiFetch with error handling, session interceptors
│   │   ├── auth.ts              # Staff auth (login, logout, session, password change)
│   │   ├── candidateAuth.ts     # Candidate session & authentication endpoints
│   │   ├── jobs.ts              # Job requisition management & O*NET taxonomy queries
│   │   ├── candidateMatching.ts # Resume matching, runs, gap analysis endpoints
│   │   ├── candidateEvaluations.ts # Official interview evaluation workspace endpoints
│   │   ├── aiInterviews.ts      # AI interview question set generation, review, release
│   │   ├── interviews.ts        # Practice room, audio setup, and simulation API
│   │   ├── analytics.ts         # Executive dashboard & recruitment metric summaries
│   │   └── admin.ts             # User administration & system event inspection
│   ├── context/
│   │   ├── AuthContext.tsx      # Staff authentication context (ADMIN, RECRUITER, REVIEWER)
│   │   └── CandidateAuthContext.tsx # Candidate authentication context (token/cookie-based)
│   ├── utils/
│   │   ├── navigation.ts        # Return URL sanitization & validation (prevents open redirects)
│   │   └── portal.ts            # Portal detection ('candidate' vs 'recruiter' / 'admin' via hostname or path)
│   ├── types/
│   │   ├── candidateEvaluation.ts # DTOs for interview evaluation, scoring, rubric anchors
│   │   └── jobs.ts              # Job requisition, requirement, and gap analysis interfaces
│   └── components/
│       ├── Icon.tsx             # Universal 16px/24px SVG icon library (zero third-party icon dependencies)
│       ├── AuthenticatedShell.tsx # Staff navigation header, user status pill, tab switcher
│       ├── CandidatesDirectory.tsx # Recruiter candidate directory & search
│       ├── CandidateProfileReview.tsx # Parsed resume viewer with evidence provenance
│       ├── ExtractionReview.tsx  # Direct JSON/evidence extraction inspection drawer
│       ├── ResumeUpload.tsx     # PDF/DOCX resume file upload and parser launcher
│       ├── admin/               # AdminWorkspace, AtsConnectionsAdmin
│       ├── ai_interviews/       # AI question set directory, authoring, and release modals
│       │   ├── AiInterviewsDirectory.tsx
│       │   ├── AiQuestionSetWorkspace.tsx
│       │   ├── CandidateSafePreviewModal.tsx
│       │   ├── McqAuthoringModal.tsx
│       │   ├── ReleaseInterviewSetModal.tsx
│       │   ├── SafeRubricModal.tsx
│       │   └── ai-interviews.css
│       ├── analytics/           # AnalyticsLauncher (launches or links to Streamlit dashboard)
│       ├── ats/                 # AtsExportModal, AtsImportModal
│       ├── auth/                # LoginPage, ChangePasswordPage
│       ├── candidate/           # Candidate Portal pages & interview execution components
│       │   ├── CandidateShell.tsx
│       │   ├── CandidateLoginPage.tsx, CandidateRegisterPage.tsx
│       │   ├── CandidateOpenJobs.tsx, CandidateJobDetail.tsx
│       │   ├── CandidateApplicationForm.tsx, CandidateMyApplications.tsx, CandidateApplicationDetail.tsx
│       │   ├── CandidatePracticeDirectory.tsx, CandidatePracticeRoom.tsx, CandidatePracticeReport.tsx
│       │   ├── CandidateInterviewWorkspace.tsx (Dual-mode text and audio interview runner)
│       │   └── VoiceAnalogDisplay.tsx (Live audio waveform canvas visualizer)
│       ├── common/              # Shared design system components
│       │   ├── Button.tsx
│       │   ├── Dialog.tsx
│       │   ├── FormField.tsx
│       │   ├── InlineNotice.tsx
│       │   ├── StatusBadge.tsx
│       │   ├── EmptyState.tsx
│       │   ├── PageHeader.tsx
│       │   └── InterviewDimensionRadar.tsx
│       ├── evaluation/          # Official Interview Evaluation Workspace
│       │   ├── EvaluationWorkspace.tsx (Level 1: Job Directory, Level 2: Candidate List, Level 3: Detail Workspace)
│       │   └── evaluation.css
│       ├── interviews/          # Practice interviews, simulation, anchored rubric modals
│       │   ├── InterviewWorkspace.tsx
│       │   ├── SimulationWorkspace.tsx
│       │   ├── AnchoredRubricModal.tsx
│       │   └── WordingComparisonModal.tsx
│       ├── jobs/                # Requisition creation, applicant tracking, comparison
│       │   ├── JobsDirectory.tsx
│       │   ├── JobEditor.tsx
│       │   ├── JobApplicantsView.tsx, JobApplicationDetailView.tsx
│       │   ├── CandidateComparisonView.tsx
│       │   ├── RequirementGapReportView.tsx
│       │   └── VisualAnalyticsCharts.tsx
│       └── reviewer/            # ReviewerWorkspace
```

---

## 3. Routing, Navigation, & Portal Separation

The application routes without a third-party router (e.g. `react-router`), instead using lightweight URL query parameters, HTML5 history (`window.location.pathname`, `pushState`, `popState`), and portal context detection.

### Dual-Portal Separation:
1. **Candidate Portal (`isCandidateRoute = true`)**:
   - Triggered when `pathname.startsWith("/candidate")` or when `getPortalContext() === "candidate"`.
   - Wrapped by `<CandidateAuthProvider>`.
   - Paths:
     - `/candidate/login` → `CandidateLoginPage`
     - `/candidate/register` → `CandidateRegisterPage`
     - `/candidate/jobs` → `CandidateShell` (Jobs Tab)
     - `/candidate/applications` → `CandidateShell` (Applications Tab)
     - `/candidate/interviews` → `CandidateShell` (Interviews Tab)
     - `/candidate/profile` → `CandidateShell` (Profile Tab)
2. **Staff / Recruiter / Admin Portal (`isCandidateRoute = false`)**:
   - Wrapped by `<AuthProvider>`.
   - Top-level switcher `MainContent` checks auth state:
     - `UNAUTHENTICATED` → `LoginPage`
     - `PASSWORD_CHANGE_REQUIRED` → `ChangePasswordPage`
     - `AUTHENTICATED` → `AuthenticatedApp` inside `AuthenticatedShell`

---

## 4. Comprehensive Page & Component Mapping

| Portal / Role | Page / View Name | Route / View Identifier | Source File Path | Shared Components Used | Associated Stylesheets |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Auth** | Staff Login | `/` (unauthenticated) | `src/components/auth/LoginPage.tsx` | `Button`, `FormField`, `InlineNotice`, `Icon` | `index.css` |
| **Auth** | Password Change | `/` (`PASSWORD_CHANGE_REQUIRED`) | `src/components/auth/ChangePasswordPage.tsx` | `Button`, `FormField`, `InlineNotice`, `Icon` | `index.css` |
| **Admin** | User & System Management | `tab=users` / `tab=events` | `src/components/admin/AdminWorkspace.tsx` | `Button`, `Dialog`, `InlineNotice`, `StatusBadge`, `Icon` | `index.css` |
| **Admin** | ATS Connections Admin | `tab=ats_integrations` | `src/components/admin/AtsConnectionsAdmin.tsx` | `Button`, `Dialog`, `InlineNotice`, `StatusBadge`, `Icon` | `index.css` |
| **Recruiter** | Candidates Directory | `tab=candidates` (`activeView="candidates"`) | `src/components/CandidatesDirectory.tsx` | `Button`, `EmptyState`, `PageHeader`, `Icon` | `index.css` |
| **Recruiter** | Resume Upload | `tab=upload` (`activeView="upload"`) | `src/components/ResumeUpload.tsx` | `Button`, `InlineNotice`, `Icon` | `index.css` |
| **Recruiter** | Candidate Profile Review | `activeView="profile"` | `src/components/CandidateProfileReview.tsx` | `Button`, `Dialog`, `StatusBadge`, `Icon` | `index.css` |
| **Recruiter** | Extraction & Evidence Drawer | `activeView="extraction"` | `src/components/ExtractionReview.tsx` | `Button`, `Icon` | `index.css` |
| **Recruiter** | Jobs Directory | `tab=jobs` (`activeView="jobs"`) | `src/components/jobs/JobsDirectory.tsx` | `Button`, `PageHeader`, `EmptyState`, `StatusBadge`, `Icon` | `index.css` |
| **Recruiter** | Job Editor & Readiness | `activeView="new_job"` / `edit_job"` | `src/components/jobs/JobEditor.tsx` | `Button`, `FormField`, `Dialog`, `StatusBadge`, `Icon` | `index.css` |
| **Recruiter** | Job Applicants Tracking | `activeView="job_applicants"` | `src/components/jobs/JobApplicantsView.tsx` | `Button`, `PageHeader`, `StatusBadge`, `Icon` | `index.css` |
| **Recruiter** | Applicant Detail & Pipeline | `activeView="job_application_detail"` | `src/components/jobs/JobApplicationDetailView.tsx` | `Button`, `Dialog`, `StatusBadge`, `Icon` | `index.css` |
| **Recruiter** | Candidate Comparison & Ranking | `activeView="compare_candidates"` | `src/components/jobs/CandidateComparisonView.tsx` | `Button`, `Dialog`, `StatusBadge`, `Icon` | `index.css` |
| **Recruiter** | Requirement Gap Analysis Report | `activeView="gap_report"` | `src/components/jobs/RequirementGapReportView.tsx` | `Button`, `Dialog`, `InlineNotice`, `Icon` | `index.css` |
| **Recruiter** | AI Interview Set Directory | `tab=ai_interviews` (`/ai-interviews`) | `src/components/ai_interviews/AiInterviewsDirectory.tsx` | `Button`, `Dialog`, `StatusBadge`, `PageHeader`, `Icon` | `index.css`, `ai-interviews.css` |
| **Recruiter** | AI Question Set Workspace & Release | `activeView="ai_interview_workspace"` | `src/components/ai_interviews/AiQuestionSetWorkspace.tsx` | `Button`, `Dialog`, `StatusBadge`, `Icon` | `index.css`, `ai-interviews.css` |
| **Recruiter** | Interview Practice Workspace | `activeView="interview_workspace"` | `src/components/interviews/InterviewWorkspace.tsx` | `Button`, `Dialog`, `StatusBadge`, `Icon` | `index.css` |
| **Recruiter** | Candidate Interview Evaluation | `tab=evaluation` (`/evaluation`) | `src/components/evaluation/EvaluationWorkspace.tsx` | `Button`, `Dialog`, `InlineNotice`, `EmptyState`, `InterviewDimensionRadar`, `Icon` | `index.css`, `evaluation.css` |
| **Candidate** | Candidate Sign In | `/candidate/login` | `src/components/candidate/CandidateLoginPage.tsx` | `Button`, `FormField`, `InlineNotice`, `Icon` | `index.css` |
| **Candidate** | Candidate Registration | `/candidate/register` | `src/components/candidate/CandidateRegisterPage.tsx` | `Button`, `FormField`, `InlineNotice`, `Icon` | `index.css` |
| **Candidate** | Candidate Portal Shell | `/candidate/jobs` | `src/components/candidate/CandidateShell.tsx` | `Icon` | `index.css` |
| **Candidate** | Open Jobs Marketplace | `CandidateShell` (Jobs Tab) | `src/components/candidate/CandidateOpenJobs.tsx` | `Button`, `EmptyState`, `Icon` | `index.css` |
| **Candidate** | Job Detail & Application Apply | `CandidateShell` (Job Apply View) | `src/components/candidate/CandidateApplicationForm.tsx` | `Button`, `FormField`, `InlineNotice`, `Icon` | `index.css` |
| **Candidate** | Candidate Applications List | `CandidateShell` (Applications Tab) | `src/components/candidate/CandidateMyApplications.tsx` | `Button`, `EmptyState`, `Icon` | `index.css` |
| **Candidate** | Candidate Interviews List | `CandidateShell` (Interviews Tab) | `src/components/candidate/CandidatePracticeDirectory.tsx` | `Button`, `StatusBadge`, `Icon` | `index.css` |
| **Candidate** | Official Dual-Mode Interview Runner | Full-screen modal from Interviews | `src/components/candidate/CandidateInterviewWorkspace.tsx` | `Button`, `VoiceAnalogDisplay`, `Icon` | `index.css` |
| **Candidate** | Candidate Practice Feedback Report | Full-screen modal from Interviews | `src/components/candidate/CandidatePracticeReport.tsx` | `Button`, `Icon` | `index.css` |
| **Executive** | Streamlit Analytics Dashboard | Port 8501 (`/analytics`) | `dashboard/app.py` | Streamlit Native components (`dashboard/ui/`) | `dashboard/ui/styles.py`, `dashboard/.streamlit/config.toml` |

---

## 5. Shared UI Component Library (`frontend/src/components/common/`)

All reusable UI components are encapsulated under `frontend/src/components/common/`:

1. **`Button.tsx`**:
   - **Variants**: `primary`, `secondary`, `quiet`, `destructive`.
   - **Sizes**: `xs` (30px), `sm` (36px), `md` (44px), `lg` (48px).
   - Features: Built-in `isLoading` spinner with accessible `loadingText`, `aria-busy`, left/right `Icon` alignment.
2. **`Dialog.tsx`**:
   - Modal backdrop and panel with focus trapping (Shift+Tab / Tab cycle).
   - Closes on `Escape` key and backdrop click. Restores focus to trigger element on exit.
3. **`FormField.tsx`**:
   - Accessible label with required asterisk (`*`), optional hint text, error message with `role="alert"`, and automatic `aria-describedby` wiring.
4. **`InlineNotice.tsx`**:
   - Status / alert banner supporting `info`, `success`, `warning`, `error`.
   - Distinct ARIA roles (`role="alert"` for errors/warnings vs `role="status"` for info/success).
5. **`StatusBadge.tsx`**:
   - Renders state pills with automatic string humanization (`humanizeStatus()`).
   - Variants: `approved`, `validated`, `stale`, `neutral`, `draft`, `teal`, `indigo`, `amber`.
6. **`EmptyState.tsx`**:
   - Centered empty display featuring dashed border, circular icon container, title, description, and primary/secondary action buttons.
7. **`PageHeader.tsx`**:
   - Standard page title with optional back button (`btn-back-link`), subtitle, and action buttons cluster.
8. **`InterviewDimensionRadar.tsx`**:
   - SVG polygon radar chart displaying five rubric dimensions (Relevance, Technical Depth, Clarity, Evidence, Problem-Solving Structure) without external charting dependencies.

---

## 6. Authentication, State, & Role Access Architecture

### Staff Auth Context (`AuthContext.tsx`):
- Controls access for `ADMIN`, `RECRUITER`, and `REVIEWER` roles.
- Backed by cookie-based HTTP session endpoints (`/api/v1/auth/me`).
- Role permissions:
  - `ADMIN`: Unrestricted access to `users`, `events`, `ats_integrations`, jobs, and candidates.
  - `RECRUITER`: Access to `candidates`, `jobs`, `ai_interviews`, `evaluation`, and `analytics`.
  - `REVIEWER`: Read-only access to resume extractions and candidate review queues.

### Candidate Auth Context (`CandidateAuthContext.tsx`):
- Controls candidate portal authentication via candidate session tokens stored in secure cookies (`/api/v1/candidate/auth/me`).
- Isolates candidate records: candidates only access their own jobs, applications, released interviews, and interview feedback reports.

---

## 7. Streamlit Dashboard Architecture

The companion executive analytics dashboard is located in `dashboard/`:
- **Entry point**: `dashboard/app.py`.
- **Theme Configuration**: `dashboard/.streamlit/config.toml` (Editorial Mineral colors: `#174F4A`, `#F3F0E6`, `#FCFBF6`, `#202927`).
- **Injected Styles**: `dashboard/ui/styles.py` injects CSS classes `.dashboard-header-container`, `.mineral-card`, `.mineral-badge`, and table overrides.
- **Backend Communication**: `dashboard/api_client.py` connects to FastAPI `/api/v1/analytics/` endpoints.

---

## 8. Exact Commands to Run Each Interface

```bash
# 1. Start Backend API Server (FastAPI on http://127.0.0.1:8000)
cd /Users/herilvyas/Desktop/AI-Hiring/backend
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# 2. Start Frontend Development Server (Vite on http://localhost:5173)
cd /Users/herilvyas/Desktop/AI-Hiring/frontend
npm run dev

# 3. Start Companion Streamlit Analytics Dashboard (Streamlit on http://localhost:8501)
cd /Users/herilvyas/Desktop/AI-Hiring
./backend/.venv/bin/streamlit run dashboard/app.py --server.port 8501

# 4. Verify Frontend Compilation & TypeScript Types
cd /Users/herilvyas/Desktop/AI-Hiring/frontend
npm run build
