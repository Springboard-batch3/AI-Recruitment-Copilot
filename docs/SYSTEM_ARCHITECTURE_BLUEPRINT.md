# AI-Hiring Platform: System Architecture & Technical Blueprint

**Document Version:** 2.1.0 (Comprehensive Architecture Reference)  
**System Classification:** Enterprise AI-Assisted Candidate Sourcing, Deterministic Matching, Audited Resumé Ingestion, AI Question Studio, Candidate Evaluation & Interactive Practice Simulation Platform  
**Target Runtime:** Python 3.14 (FastAPI + SQLAlchemy 2.0 Async/Sync + PostgreSQL 16) | React 19 + TypeScript 5.5+ (Vite 6.0+ + Vanilla CSS Glassmorphic Design System) | Local MLX/Ollama/OpenAI-compatible Inference

---

## 1. Executive Summary & Core Architectural Philosophy

The **AI-Hiring Platform** is a dual-portal enterprise recruiting and evaluation system engineered to bridge recruiter workflow efficiency with candidate transparency. Unlike legacy Applicant Tracking Systems (ATS) and opaque "black-box" generative AI screeners, this platform enforces a strict separation between **auditable deterministic business logic** and **governed AI assistance**:

```
+---------------------------------------------------------------------------------------------------+
|                                  AI-HIRING PLATFORM ARCHITECTURE                                 |
+--------------------------------------------------+------------------------------------------------+
|          1. DETERMINISTIC CORE ENGINE             |          2. GROUNDED AI ASSISTANCE            |
+--------------------------------------------------+------------------------------------------------+
| * Exact Bounding-Box Evidence Spans (PDF/DOCX)   | * Requisition-Grounded Question Generation     |
| * Multi-Version Taxonomy Graph Normalization     | * 20-Point Server-Side Validation Matrix       |
| * Transparent Mathematical Scoring Equations     | * Zero-Leakage Candidate Simulation Sandbox    |
| * Immutable Job & Profile Revision Histories     | * Real-Time Adaptive Turn-Taking (Follow-ups)  |
| * Cryptographic Tenant Isolation & RBAC (Admin,  | * Multi-Dimensional Radar Coaching Analytics   |
|   Recruiter, Candidate)                          | * Strict Provenance & Model Audit Logging      |
+--------------------------------------------------+------------------------------------------------+
```

### Architectural Tenets
1. **Evidence-Based Grounding**: Resumé parsing must persist character-level and bounding-box evidence spans (`start_offset`, `end_offset`, `section`, `bbox`). No candidate profile assertion is accepted without audit proof in the source document.
2. **Deterministic, Explainable Matching**: Job match scores are calculated strictly via transparent category-weighted mathematical formulas. The system produces exact missing-skill gap breakdowns, never vague LLM approximations.
3. **20-Point Server-Side AI Guardrails**: AI-generated interview items (conversational and MCQ) must pass 20 automated checks (character lengths, injection prevention, protected attribute filters, hallucination traps, MCQ structure) before becoming eligible for recruiter approval or candidate release.
4. **Zero-Leakage Candidate Privacy**: Candidate-facing endpoints strip all internal calibration rubrics, target answers, and recruiter evaluation thresholds. Candidates receive diagnostic practice feedback without exposing hiring criteria.
5. **Complete Auditability & Provenance**: Every AI action logs `model_identifier`, `provider_class`, `prompt_template_version`, `guardrail_policy_version`, and user timestamps.

---

## 2. Complete Project Directory Tree & File Inventory

Below is the exhaustive, verified file map for every single file in the repository:

```
AI-Hiring/
├── compose.yaml                                 # Docker Compose configuration for PostgreSQL 16 database service
├── package-lock.json                            # Root npm lockfile for root workspace scripts
├── postgres.md                                  # PostgreSQL setup, connection strings, credentials & operational notes
├── THIRD_PARTY_DATA_NOTICES.md                  # Licensing and third-party notices for taxonomy datasets (O*NET, etc.)
├── Local_data/                                  # Seed datasets for occupational taxonomy & skill graph
│   ├── job_titles.csv                           # O*NET standardized job titles and concept identifiers
│   ├── occupation_data.csv                      # Occupation hierarchy, codes, and family categories
│   └── software_skills.csv                      # Software skills, tools, frameworks, and aliases
├── local_ats_demo/                              # Standalone demo ATS folder for local file-based ATS integration
│   ├── README.txt                               # Instructions for local folder ATS inbox/outbox syncing
│   └── inbox/                                   # 28 Sample test resumes in PDF and DOCX formats
│       ├── Candidate_A_Alex_Morgan.docx
│       ├── Candidate_B_Priya_Shah.pdf
│       ├── Candidate_C_Daniel_Lee.docx
│       ├── Phase_1E2_Integrity_Table_Reordered_Resume.docx
│       ├── Phase_1E2_Integrity_Two_Column_Resume.pdf
│       ├── R001_backend_developer.docx / .pdf
│       ├── R002_data_analyst.docx / .pdf
│       ├── R003_machine_learning_engineer.docx / .pdf
│       ├── R004_frontend_developer.docx / .pdf
│       ├── R005_devops_engineer.docx / .pdf
│       ├── R006_cybersecurity_analyst.docx / .pdf
│       ├── R007_product_designer.docx / .pdf
│       ├── R008_human_resources_specialist.docx / .pdf
│       ├── R009_finance_analyst.docx / .pdf
│       ├── R010_graduate_software_intern.docx / .pdf
│       ├── resume_test_A_table_reordered.docx
│       └── resume_test_B_two_column.pdf
├── models/                                      # Local Speech & Inference Models
│   └── whisper/                                 # Whisper STT weights directory
│       └── ggml-small.en.bin                    # Quantized Whisper small.en model for local audio transcription
├── docs/                                        # Master Platform Documentation Suite
│   ├── SYSTEM_ARCHITECTURE_BLUEPRINT.md         # [This File] Master architectural blueprint & inter-file relationships
│   ├── BACKEND_SERVICES_AND_API_REFERENCE.md    # Exhaustive reference for 18 Routers, 13 Models, 52 Schemas, 78 Services, 93 Tests
│   ├── FRONTEND_UI_AND_USER_JOURNEYS.md         # Exhaustive reference for 64 Components, 12 API Clients, 42 Tests, User Journeys
│   └── VOICE_INTERVIEW_ARCHITECTURE.md          # Voice-Based AI Interview System: End-to-End Implementation Architecture
├── backend/                                     # Backend Application (Python 3.14, FastAPI, SQLAlchemy 2.0, Alembic)
│   ├── alembic.ini                              # Alembic database migration configuration
│   ├── pytest.ini                               # Pytest execution configuration, markers, and async settings
│   ├── requirements.txt                         # Python dependencies (FastAPI, SQLAlchemy, psycopg2, pdfplumber, python-docx, etc.)
│   ├── app/                                     # Backend Application Package
│   │   ├── __init__.py                          # Package initialization
│   │   ├── config.py                            # Pydantic Settings (DB URL, Auth secrets, AI & Speech provider configs)
│   │   ├── database.py                          # SQLAlchemy engine, session maker, base model, and DB lifecycle
│   │   ├── main.py                              # FastAPI app assembly, CORS, validation handlers, 18 router registrations
│   │   ├── api/                                 # 18 REST API Routers & Endpoint Controllers
│   │   │   ├── __init__.py
│   │   │   ├── admin.py                         # System health, provider management, security audit logs
│   │   │   ├── ai_interviews.py                 # AI Question Studio generation, validation, approval, and safe previews
│   │   │   ├── ats.py                           # ATS integration (Greenhouse, Lever, Local Folder import/export)
│   │   │   ├── auth.py                          # Recruiter/Admin authentication, login, logout, password change
│   │   │   ├── candidate_applications.py        # Candidate job applications, draft creation, resume upload, submission
│   │   │   ├── candidate_auth.py                # Candidate portal authentication, magic activation, login, session
│   │   │   ├── candidate_evaluation.py          # Candidate interview evaluation, scoring breakdown, and feedback review
│   │   │   ├── candidate_invitations.py         # Recruiter invitations, single-use token generation, candidate magic links
│   │   │   ├── candidate_jobs.py                # Candidate public/published job requisition discovery and detail
│   │   │   ├── candidate_practice.py            # Recruiter practice release management & candidate practice sessions
│   │   │   ├── gap_reports.py                   # Requirement gap analysis and missing skill report endpoints
│   │   │   ├── interviews.py                    # Deterministic interview templates, question generation, and simulations
│   │   │   ├── job_imports.py                   # Document/PDF text extraction for raw job description imports
│   │   │   ├── jobs.py                          # Job requisition CRUD, immutable versioning, requirements, readiness
│   │   │   ├── matches.py                       # Deterministic candidate-job matching execution & results
│   │   │   ├── recruiter_applications.py        # Recruiter applicant review workflow & state transitions
│   │   │   ├── resumes.py                       # Resumé upload, parsing, profile review, evidence span queries
│   │   │   └── taxonomy.py                      # Skill taxonomy search, occupation concept resolution
│   │   ├── cli/                                 # Command-Line Utilities & Data Seeders
│   │   │   ├── bootstrap_admin.py               # CLI bootstrap script for default org, admin user, and taxonomy load
│   │   │   ├── import_onet.py                   # CLI tool to ingest O*NET job titles and occupation hierarchy CSVs
│   │   │   └── matching_evaluation.py           # CLI benchmark runner evaluating parser precision/recall and matching
│   │   ├── models/                              # 13 SQLAlchemy 2.0 Declarative Relational Models
│   │   │   ├── __init__.py                      # Model registry and export definitions
│   │   │   ├── ats.py                           # ATS connections, object mappings, import runs, export receipts
│   │   │   ├── auth.py                          # Organizations, Users, User Sessions, Security Audit Events
│   │   │   ├── base.py                          # Declarative Base class with UUID helpers and timestamp mixins
│   │   │   ├── candidate.py                     # Candidate Identities, Candidate Profiles, Profile Corrections
│   │   │   ├── candidate_access.py              # Candidate Bindings, Invitations, Applications, Practice & Evaluation Records
│   │   │   ├── extraction.py                    # Resume Extraction metadata and text extraction blocks
│   │   │   ├── gap_report.py                    # Gap Reports, Gap Report Items, and Gap Audit Events
│   │   │   ├── interview.py                     # Deterministic templates, questions, rubrics, provider profiles
│   │   │   ├── job.py                           # Job Requisitions, Job Versions, Grounded Requirements, Document Imports
│   │   │   ├── matching.py                      # Match Runs, Candidate Match Results, Match Audit Events
│   │   │   ├── resume.py                        # Resume Artifacts and Intake Processing Jobs
│   │   │   └── taxonomy.py                      # Skill Concepts, Skill Aliases, Occupations, Taxonomy Snapshots
│   │   ├── schemas/                             # 52 Pydantic v2 Request/Response Data Transfer Objects (DTOs)
│   │   │   ├── __init__.py
│   │   │   ├── ai_question_set.py               # AI Question set authoring, items, 20-point validation, approval DTOs
│   │   │   ├── answer_assessment_contract.py    # Answer evaluation and scoring rubric schemas
│   │   │   ├── assessment_contract.py           # Evaluation criteria and dimension assessment contracts
│   │   │   ├── ats.py                           # ATS connection payloads, sync status, mapping DTOs
│   │   │   ├── ats_dto.py                       # ATS transfer schemas, candidate import/export payloads
│   │   │   ├── auth.py                          # Login, session, token, and user profile schemas
│   │   │   ├── candidate.py                     # Candidate profile, extraction summary, evidence span DTOs
│   │   │   ├── candidate_access.py              # Invitation creation, account activation, credential setup
│   │   │   ├── candidate_application.py         # Application draft creation, resume submission, status DTOs
│   │   │   ├── candidate_evaluation.py          # Candidate evaluation scoring breakdown and feedback schemas
│   │   │   ├── candidate_interview.py           # Candidate interview turn-taking, speech synthesis, and transcription DTOs
│   │   │   ├── candidate_job.py                 # Public job listing and requirement summaries for candidates
│   │   │   ├── correction.py                    # Manual profile correction request and audit schemas
│   │   │   ├── education_project_certification.py # Education, project, and certification extraction DTOs
│   │   │   ├── employment.py                    # Employment history extraction DTOs
│   │   │   ├── evaluation.py                    # Benchmark evaluation metric schemas (precision, recall, F1)
│   │   │   ├── explanation.py                   # Transparent match score explanation schemas
│   │   │   ├── extraction.py                    # Low-level extraction block and coordinate schemas
│   │   │   ├── human_review.py                  # Human review score override and calibration schemas
│   │   │   ├── interview_api.py                 # Interview template and question API contracts
│   │   │   ├── interview_blueprint.py           # Blueprint structures for requisition interview generation
│   │   │   ├── interview_snapshot.py            # Immutable point-in-time snapshot schemas
│   │   │   ├── job.py                           # Job requisition, version, requirement, and readiness DTOs
│   │   │   ├── lifecycle.py                     # Job and candidate lifecycle transition schemas
│   │   │   ├── live_interview_contract.py       # Live interview turn-taking contracts
│   │   │   ├── match_run.py                     # Match run creation, ranked scores, comparison DTOs
│   │   │   ├── matching.py                      # Low-level candidate matching data contracts
│   │   │   ├── mcq_contract.py                  # MCQ options, answer keys, and explanation schemas
│   │   │   ├── phase3_audit.py                  # Phase 3 audit trail schemas
│   │   │   ├── phase3_enums.py                  # Phase 3 domain enumerations
│   │   │   ├── phase3_protocols.py              # Phase 3 protocol type specifications
│   │   │   ├── practice_follow_up_contract.py   # Adaptive 1-turn follow-up probe schemas
│   │   │   ├── practice_report_contract.py      # Multi-dimensional radar coaching report schemas
│   │   │   ├── practice_review_contract.py      # Per-answer real-time AI review schemas
│   │   │   ├── practice_session_contract.py     # Practice sandbox turn-taking schemas
│   │   │   ├── profile_assembly.py              # Profile assembly orchestration schemas
│   │   │   ├── provider_admin.py                # AI Model provider registration and test schemas
│   │   │   ├── provider_capability.py           # Provider capability and adapter schemas
│   │   │   ├── provider_profile.py              # Provider profile configuration schemas
│   │   │   ├── question_generation_contract.py  # LLM prompt and response generation contracts
│   │   │   ├── recruiter_application.py         # Recruiter applicant list and review stage schemas
│   │   │   ├── requirement_gap.py               # Requirement gap request, filter, and breakdown DTOs
│   │   │   ├── requirement_match.py             # Grounded requirement matching schemas
│   │   │   ├── resume.py                        # Resume artifact and upload payload schemas
│   │   │   ├── safety_policy.py                 # 20-point validation guardrail policy schemas
│   │   │   ├── scoring.py                       # Score decomposition and mathematical metric schemas
│   │   │   ├── section_and_identity.py          # Section detection and candidate identity schemas
│   │   │   ├── simulation_assessment.py         # Interview simulation assessment schemas
│   │   │   ├── skill_and_summary.py             # Skill extraction and summary schemas
│   │   │   ├── taxonomy.py                      # Skill concept autocomplete and search result DTOs
│   │   │   └── wording_contract.py              # AI question wording assistance schemas
│   │   └── services/                            # 78 Domain Services & Algorithmic Engines (71 Root + 7 ATS)
│   │       ├── __init__.py
│   │       ├── ai_provenance_service.py         # 20-point server-side validation matrix & release eligibility
│   │       ├── ai_question_set_service.py       # Manages lifecycle (draft/approved) of AI question sets
│   │       ├── answer_assessment_service.py     # Evaluates candidate answers against anchored rubrics
│   │       ├── ats/                             # ATS Sub-package (7 Adapter Modules)
│   │       │   ├── adapter_interface.py         # Abstract base class for ATS adapters
│   │       │   ├── adapter_registry.py          # Dynamic adapter factory registry (Greenhouse, Lever, Local)
│   │       │   ├── csv_json_adapter.py          # CSV/JSON file import and export adapter
│   │       │   ├── export_service.py            # Exports scored candidates and results to external ATS
│   │       │   ├── import_service.py            # Imports requisitions and resumes from external ATS
│   │       │   ├── local_folder_adapter.py      # Local filesystem folder ATS adapter for demo files
│   │       │   └── mock_adapter.py              # In-memory mock adapter for unit and integration testing
│   │       ├── candidate_access_service.py      # Magic invitation token generation and candidate activation
│   │       ├── candidate_application_service.py # Manages candidate job applications & resume attachments
│   │       ├── candidate_authorization_kernel.py # Cryptographic authorization kernel for candidate tokens
│   │       ├── candidate_evaluation_service.py  # Computes candidate interview evaluation score breakdowns
│   │       ├── candidate_interview_service.py   # Manages formal candidate interview sessions and submissions
│   │       ├── candidate_job_service.py         # Retrieves published requisitions for candidate discovery
│   │       ├── candidate_plan_generator_service.py # Generates candidate-specific interview evaluation plans
│   │       ├── candidate_practice_service.py    # Recruiter practice release authorization & catalog queries
│   │       ├── canonical_summary.py             # Generates canonical candidate summaries from extracted entities
│   │       ├── date_extraction.py               # Robust date parser for employment spans and graduation dates
│   │       ├── destination_policy.py            # Destination validation policy for file uploads and artifacts
│   │       ├── deterministic_matching.py        # Core category-weighted candidate-job matching algorithm
│   │       ├── docx_text_extraction.py          # Microsoft Word DOCX parser with paragraph/table span detection
│   │       ├── docx_validation.py               # Validates DOCX structure, MIME types, and file safety
│   │       ├── education_project_certification_extraction.py # Extracts degrees, institutions, projects, certs
│   │       ├── education_project_certification_rules.py # Extraction rule engine for degrees and certifications
│   │       ├── employment_extraction.py         # Extracts work history, employer names, job titles, date spans
│   │       ├── evaluation.py                    # Benchmarks parser precision/recall against ground-truth labels
│   │       ├── explanation.py                   # Generates human-readable explanations for match scores
│   │       ├── extraction_orchestration.py      # Master pipeline running text extraction, spans, & profile assembly
│   │       ├── extraction_persistence.py        # Persists parsed candidate profiles and evidence spans
│   │       ├── gap_derivation.py                # Compares job requirements against candidate skills to find gaps
│   │       ├── gap_report_service.py            # Compiles structured gap reports with categorical grouping
│   │       ├── gap_validation.py                # Validates consistency of requirement gap breakdowns
│   │       ├── human_review_service.py          # Handles recruiter overrides and human-in-the-loop review
│   │       ├── identity_extraction.py           # Extracts candidate name, email, phone, location from resume
│   │       ├── interview_catalogue.py           # Catalog of pre-approved deterministic interview questions
│   │       ├── interview_generator_service.py   # Generates structured interview question banks
│   │       ├── interview_lifecycle_service.py   # Manages interview template statuses (draft, published, archived)
│   │       ├── interview_segmentation_service.py # Segregates interview questions into technical/behavioral blocks
│   │       ├── interview_simulation_service.py  # Simulates interview responses for pre-release calibration
│   │       ├── interview_snapshot_service.py    # Creates immutable point-in-time snapshots of question sets
│   │       ├── job_description_parser.py        # Parses raw job descriptions into structured requirements
│   │       ├── job_document_extraction.py       # Extracts text from uploaded job description PDF/Word files
│   │       ├── job_lifecycle.py                 # Manages job requisition states (draft, ready, published, archived)
│   │       ├── job_readiness.py                 # Validates job requisition completeness before matching
│   │       ├── live_interview_service.py        # Orchestrates real-time live interview sessions
│   │       ├── match_orchestration.py           # Manages bulk match execution across multiple candidates
│   │       ├── matching_preflight.py            # Preflight validation ensuring jobs and candidates are matchable
│   │       ├── mcq_validation_service.py        # Validates Multiple-Choice Question options, keys, & explanations
│   │       ├── pdf_text_extraction.py           # PDFPlumber text and character bounding box extractor
│   │       ├── pdf_validation.py                # Validates PDF headers, encryption, and page boundaries
│   │       ├── practice_follow_up_service.py    # Evaluates answers and generates 1-turn adaptive follow-up probes
│   │       ├── practice_report_service.py       # Compiles multi-dimensional practice coaching reports
│   │       ├── practice_review_service.py       # Real-time per-answer AI review (clarity, depth, STAR structure)
│   │       ├── practice_session_service.py      # Candidate practice sandbox turn-taking state machine
│   │       ├── profile_assembly.py              # Assembles extracted entities into cohesive CandidateProfile
│   │       ├── profile_correction.py            # Recruiter granular manual correction engine (patch operations)
│   │       ├── provider_compatibility_service.py # Validates local LLM API endpoints and model response formats
│   │       ├── provider_eligibility.py          # Assesses AI provider suitability for question authoring
│   │       ├── provider_registry.py             # In-memory and DB registry of active AI inference adapters
│   │       ├── provisional_assessment_service.py # Computes provisional scores during interview simulations
│   │       ├── recruiter_applicant_service.py   # Recruiter workflow for reviewing applications and stages
│   │       ├── requirement_validation.py        # Validates grounded requirement syntax, weights, and categories
│   │       ├── scoring.py                       # Mathematical scoring functions for skills and experience
│   │       ├── secret_resolver.py               # Resolves encrypted secrets, API keys, and environment tokens
│   │       ├── section_detection.py             # Identifies resume section headers (Experience, Education, Skills)
│   │       ├── security.py                      # Cryptographic helpers, PBKDF2 hashing, HMAC signature checks
│   │       ├── skill_catalog.py                 # Hierarchical catalog of technical and soft skills
│   │       ├── skill_summary_extraction.py      # Summarizes candidate skill profiles and calculates years of exp
│   │       ├── speech_service.py                # Speech runtime capability, local TTS (macOS say/Piper), Whisper STT adapter & EphemeralAudioProcessor
│   │       ├── taxonomy_normalization.py        # Normalizes raw skill text against standardized taxonomy graph
│   │       ├── text_normalization.py            # Text cleaning, unicode normalization, whitespace stripping
│   │       ├── wording_assistance_service.py    # Generates recruiter question wording variants via LLM
│   │       ├── wording_prompt_package.py        # Manages prompt templates for question wording assistance
│   │       ├── wording_provider.py              # LLM client adapter for question wording generation
│   │       └── wording_validation_service.py    # Validates rewritten questions against safety & length rules
│   ├── docs/
│   │   └── evaluation_reviewer_guide.md         # Guide for human reviewers evaluating parser ground-truth
│   ├── migrations/                              # Alembic Database Migration System
│   │   ├── env.py                               # Alembic environment runner loading SQLAlchemy MetaData
│   │   ├── script.py.mako                       # Template for auto-generated migration scripts
│   │   └── versions/                            # 46 Sequential Migration Scripts (Complete Database Evolution)
│   ├── runtime/                                 # Ephemeral storage for file uploads and quarantine
│   │   ├── quarantine/                          # Quarantined malformed or suspicious file uploads (pdf, docx)
│   │   └── resume_intake/                       # Temporary staging directory for active resume parsing
│   ├── tests/                                   # Pytest Test Suite (93 Test Files, 950+ Tests)
│   │   ├── evaluation_support/                  # 6 Helper modules for parser benchmarking
│   │   ├── fixtures/                            # Test resumes (PDF/DOCX) and ground truth JSON files
│   │   └── test_*.py                            # 93 Test suites covering all domains, routers, models, services, and dual-mode audio
│   └── tools/                                   # Administrative & Evaluation Scripts
│       ├── bootstrap_admin.py                   # Script to create initial organization and admin user
│       └── run_evaluation.py                    # Parser precision/recall benchmark runner
├── frontend/                                    # Frontend Application (React 19, TypeScript 5.5+, Vite 6.0+)
│   ├── index.html                               # HTML entry point with viewport and typography links
│   ├── package.json                             # Frontend dependencies (React, Lucide icons, Vitest, etc.)
│   ├── tsconfig.json                            # TypeScript compiler configuration (strict mode)
│   ├── vite.config.ts                           # Vite bundler configuration and proxy settings
│   └── src/                                     # Frontend Source Directory
│       ├── App.tsx                              # Main application router and shell container
│       ├── main.tsx                             # React DOM entry point mounting root App
│       ├── index.css                            # Master Vanilla CSS Design System (Glassmorphic dark-mode)
│       ├── setupTests.ts                        # Vitest testing setup and DOM matchers
│       ├── api/                                 # 12 Typed API Client Modules
│       │   ├── ats.ts                           # ATS connection, sync, and import/export API client
│       │   ├── candidateApplications.ts         # Candidate application creation, resume upload, submission
│       │   ├── candidateAuth.ts                 # Candidate activation, login, session API client
│       │   ├── candidateEvaluations.ts          # Candidate interview evaluation, feedback & scoring client
│       │   ├── candidateInterviews.ts           # Formal candidate interview session, speech synthesis, and transcription client
│       │   ├── candidateJobs.ts                 # Candidate job catalog and published job detail API client
│       │   ├── candidatePractice.ts             # Recruiter practice release and candidate catalog API client
│       │   ├── candidatePracticeSession.ts      # Candidate practice sandbox turn-taking & report API client
│       │   ├── client.ts                        # Core HTTP fetch wrapper (CSRF injection, HttpOnly cookies)
│       │   ├── jobs.ts                          # Recruiter job requisition, versioning, requirement API client
│       │   ├── recruiterApplications.ts         # Recruiter applicant review and stage transition API client
│       │   └── taxonomy.ts                      # Skill autocomplete and taxonomy resolution API client
│       ├── components/                          # 64 UI Components & Styling Across 9 Specialized Subsystems
│       │   ├── AuthenticatedShell.tsx           # Recruiter/Admin top navigation bar and session header
│       │   ├── CandidateProfileReview.tsx       # Detailed candidate profile inspector with evidence viewer
│       │   ├── CandidatesDirectory.tsx          # Candidate roster table with search, filter, and upload
│       │   ├── EvidenceDrawer.tsx               # Slide-out drawer highlighting parsed resume text spans
│       │   ├── ExtractionReview.tsx             # Resumé parsing audit drawer for raw text vs. extracted entities
│       │   ├── Icon.tsx                         # Lightweight SVG icon system (50+ vector icons)
│       │   ├── ResumeUpload.tsx                 # Drag-and-drop resume uploader supporting PDF/DOCX
│       │   ├── ReviewDrawer.tsx                 # Recruiter manual profile correction drawer
│       │   ├── VersionHistoryDrawer.tsx         # Audit drawer showing revision history of candidate profiles
│       │   ├── admin/                           # Administrator Workspace Components
│       │   │   ├── AdminWorkspace.tsx           # Admin console (Users, Events, AI Providers, ATS tabs)
│       │   │   └── AtsConnectionsAdmin.tsx      # ATS connection management (Greenhouse, Lever, Local)
│       │   ├── ai_interviews/                   # AI Interview Studio Components
│       │   │   ├── AiInterviewsDirectory.tsx    # List of job requisitions with AI interview readiness status
│       │   │   ├── AiQuestionSetWorkspace.tsx   # AI Question Studio (Conversational & MCQ cards, approvals, mode selector)
│       │   │   ├── CandidateSafePreviewModal.tsx # Zero-leakage preview modal showing candidate view
│       │   │   ├── McqAuthoringModal.tsx        # Modal for authoring and validating MCQ items
│       │   │   ├── ReleaseInterviewSetModal.tsx # Modal to release validated AI interview sets (Text vs. Audio)
│       │   │   ├── SafeRubricModal.tsx          # Recruiter-only modal for inspecting evaluation rubrics
│       │   │   └── ai-interviews.css            # Studio-specific styles and animations
│       │   ├── ats/                             # ATS Modal Dialogs
│       │   │   ├── AtsExportModal.tsx           # Modal for exporting candidates/jobs to external ATS
│       │   │   └── AtsImportModal.tsx           # Modal for importing requisitions/resumes from ATS
│       │   ├── auth/                            # Authentication Views
│       │   │   ├── ChangePasswordPage.tsx       # Force-change password view for newly invited users
│       │   │   └── LoginPage.tsx                # Recruiter and Admin login portal
│       │   ├── candidate/                       # Candidate Portal Views & Components
│       │   │   ├── CandidateActivationPage.tsx  # Magic token account activation and password setup
│       │   │   ├── CandidateApplicationDetail.tsx # Candidate application detail & withdrawal view
│       │   │   ├── CandidateApplicationForm.tsx # Resume-first job application modal
│       │   │   ├── CandidateFeedbackModal.tsx   # Candidate practice feedback modal
│       │   │   ├── CandidateInterviewWorkspace.tsx # Formal candidate interview execution workspace (Text & Audio playback/recording)
│       │   │   ├── CandidateInvitationModal.tsx # Recruiter modal to send portal invitations to candidates
│       │   │   ├── CandidateJobDetail.tsx       # Candidate view of published requisition details
│       │   │   ├── CandidateLoginPage.tsx       # Candidate portal login page
│       │   │   ├── CandidateMyApplications.tsx  # Candidate dashboard of submitted applications
│       │   │   ├── CandidateOpenJobs.tsx        # Candidate job discovery board
│       │   │   ├── CandidatePracticeDirectory.tsx # Candidate practice catalog view
│       │   │   ├── CandidatePracticeReleaseModal.tsx # Recruiter modal to release practice sets to candidate
│       │   │   ├── CandidatePracticeReport.tsx  # Multi-dimensional radar coaching scorecard view
│       │   │   ├── CandidatePracticeRoom.tsx    # Interactive practice simulation sandbox (turn-taking)
│       │   │   ├── CandidateRegisterPage.tsx    # Candidate self-registration page
│       │   │   └── CandidateShell.tsx           # Candidate portal layout and navigation header
│       │   ├── common/                          # Reusable UI Primitives
│       │   │   ├── Button.tsx                   # Accessible button component with variant and size states
│       │   │   ├── Dialog.tsx                   # Accessible modal dialog with backdrop blur
│       │   │   ├── EmptyState.tsx               # Standardized empty state card with icon and action button
│       │   │   ├── FormField.tsx                # Labeled form field container with error text
│       │   │   ├── InlineNotice.tsx             # Alert banner (success, warning, info, error)
│       │   │   ├── InterviewDimensionRadar.tsx  # Radar chart visualization for candidate competency dimensions
│       │   │   ├── PageHeader.tsx               # Standardized view header with title, subtitle, and actions
│       │   │   └── StatusBadge.tsx              # Colored status pill badge
│       │   ├── evaluation/                      # Candidate Evaluation Workspace
│       │   │   ├── EvaluationWorkspace.tsx      # Comprehensive candidate evaluation review and scoring workspace
│       │   │   └── evaluation.css               # Evaluation workspace specific styling
│       │   ├── interviews/                      # Requisition Interview Builder & Simulation
│       │   │   ├── AnchoredRubricModal.tsx      # Modal displaying anchored 1/3/5 rating criteria
│       │   │   ├── InterviewReviewWorkspace.tsx # Workspace for human evaluation of completed interviews
│       │   │   ├── InterviewWorkspace.tsx       # Requisition interview template builder
│       │   │   ├── SimulationWorkspace.tsx      # Pre-release interview simulation tool
│       │   │   └── WordingComparisonModal.tsx   # Side-by-side comparison modal for wording variants
│       │   ├── jobs/                            # Job Requisition & Applicant Workflow Components
│       │   │   ├── CandidateComparisonView.tsx  # Multi-candidate match comparison and ranking matrix
│       │   │   ├── JobApplicantsView.tsx        # Recruiter applicant roster for a specific job requisition
│       │   │   ├── JobApplicationDetailView.tsx # Recruiter detailed view of candidate application
│       │   │   ├── JobEditor.tsx                # Master requisition editor with versioning and requirements
│       │   │   ├── JobsDirectory.tsx            # Table of all job requisitions with status filters
│       │   │   ├── OccupationSelector.tsx       # O*NET occupation taxonomy search and selector
│       │   │   ├── RequirementCard.tsx          # Card representing a single grounded job requirement
│       │   │   ├── RequirementGapReportView.tsx # Categorical requirement gap report inspector
│       │   │   ├── TaxonomySkillAutocomplete.tsx # Typeahead skill search component with taxonomy pills
│       │   │   └── VisualAnalyticsCharts.tsx    # Visual skill gap charts and score distribution bars
│       │   └── reviewer/                        # Evaluation Reviewer Workspace
│       │       └── ReviewerWorkspace.tsx        # Internal reviewer workspace for evaluation benchmarking
│       ├── context/                             # React Context State Providers
│       │   ├── AuthContext.tsx                  # Recruiter/Admin authentication, tenant context, CSRF token
│       │   └── CandidateAuthContext.tsx         # Candidate authentication and session state provider
│       ├── types/                               # Frontend TypeScript Type Definitions
│       │   ├── ai_question_set.ts               # AI Question set, item, slot, and validation interfaces
│       │   ├── ats.ts                           # ATS integration and connection interfaces
│       │   └── interview.ts                     # Interview template, question, and rubric interfaces
│       ├── utils/                               # Client Utility Functions
│       │   ├── navigation.ts                    # URL query parameter and tab routing helpers
│       │   └── portal.ts                        # DOM portal helpers for modal rendering
│       └── __tests__/                           # 42 Vitest & React Testing Library Test Suites
│           ├── auth_workflow.test.tsx
│           ├── candidate_comparison_gap_workflow.test.tsx
│           ├── candidate_portal_auth.test.tsx
│           ├── candidate_practice_coaching.test.tsx
│           ├── candidate_practice_engine.test.tsx
│           ├── candidate_practice_report.test.tsx
│           ├── candidates_directory.test.tsx
│           ├── color_policy.test.ts
│           ├── confidence_display.test.tsx
│           ├── correction_workflow.test.tsx
│           ├── dashboard_rendering.test.tsx
│           ├── evidence_drawer.test.tsx
│           ├── extraction_review.test.tsx
│           ├── interview_lifecycle_workflow.test.tsx
│           ├── isolated_portals.test.tsx
│           ├── jobs_workspace.test.tsx
│           ├── mcq_and_practice_release.test.tsx
│           ├── phase3d3_curated_and_review.test.tsx
│           ├── phase3d4_final_reconstruction_and_evaluation.test.tsx
│           ├── phase3d5_ai_workspace.test.tsx
│           ├── phase3e1_ui_foundation.test.tsx
│           ├── phase3e3_jobs_and_ats.test.tsx
│           ├── phase3e3_local_folder_ats.test.tsx
│           ├── phase3g1_provenance_and_release.test.tsx
│           ├── phase4b3_candidate_job_discovery.test.tsx
│           ├── phase4b4_candidate_applications.test.tsx
│           ├── phase4b5_recruiter_applicant_workflow.test.tsx
│           ├── phase4c1_interview_release.test.tsx
│           ├── phase4c2_candidate_interview_workspace.test.tsx
│           ├── phase4c3_corrective_ui.test.tsx
│           ├── phase4c4_evaluation_workspace.test.tsx
│           ├── phase4d1_dual_mode_ui.test.tsx
│           ├── phase4d2_dual_mode_release_ui.test.tsx
│           ├── phase4d3_audio_execution_ui.test.tsx
│           ├── phase4d4_mode_aware_evaluation_ui.test.tsx
│           ├── provider_admin_and_interview_shell.test.tsx
│           ├── recruiter_wording_assistance.test.tsx
│           ├── requirement_gap_report_view.test.tsx
│           ├── review_drawer.test.tsx
│           ├── simulation_and_template_repairs.test.tsx
│           ├── version_history.test.tsx
│           └── visual_analytics_navigation.test.tsx
```

---

## 3. Global Tenancy, RBAC & Security Model

The system enforces strict multi-tenant data isolation and role-based access control across all database queries and API endpoints:

```mermaid
flowchart TD
    subgraph OrganizationTenant ["Organization Tenancy Boundary (UUID)"]
        Users["Users (Admin, Recruiter)"]
        Jobs["Job Requisitions & Versions"]
        Candidates["Candidate Identities & Profiles"]
        Matches["Match Runs & Gap Reports"]
        AISets["AI Question Sets & Rubrics"]
        Apps["Job Applications & Practice Releases"]
        Eval["Candidate Evaluations & Official Interviews"]
    end

    subgraph SecurityShield ["Cryptographic Security & Auth Guard"]
        CSRF["Double-Submit HMAC CSRF Token Verification"]
        Cookie["HttpOnly, SameSite=Lax Session Cookies"]
        RBAC["FastAPI Dependency Guards (`require_recruiter`, `require_candidate`, etc.)"]
        Isolation["Cross-Tenant Shield (Reject Cross-Org UUIDs with 403)"]
    end

    SecurityShield --> OrganizationTenant
```

### 3.1 Tenancy Enforcement Rules
1. **Organization Isolation**: Every primary database entity (`JobRequisition`, `CandidateIdentity`, `MatchRun`, `AIQuestionSet`, `CandidateApplication`, `CandidateInterviewSessionRecord`) possesses an `organization_id` foreign key.
2. **Cross-Tenant Shielding**: Any attempt to associate an entity from Organization A with an entity from Organization B raises a `403 Forbidden` (`FORBIDDEN_ORGANIZATION`).
3. **Session Management**: Session tokens are stored in `user_sessions` or `candidate_account_bindings`, authenticated via signed HttpOnly cookies, and expired after a configurable window (`SESSION_EXPIRE_HOURS=24`).
4. **CSRF Protection**: All mutating HTTP requests (`POST`, `PUT`, `PATCH`, `DELETE`) require a valid `X-CSRF-Token` header that matches the cryptographically signed CSRF cookie.

---

## 4. Subsystem Topology & Data Flow

```mermaid
flowchart LR
    subgraph Ingestion ["1. Ingestion & Grounding"]
        PDF_DOCX["Resume File (PDF/DOCX)"] --> DocParser["pdf/docx_text_extraction.py"]
        DocParser --> Spans["Evidence Spans (start, end, bbox)"]
        Spans --> Profile["CandidateProfile Assembly"]
    end

    subgraph Requisition ["2. Job Authoring"]
        JobRaw["Job Description"] --> JParser["job_description_parser.py"]
        JParser --> Reqs["Grounded Requirements + Taxonomy"]
        Reqs --> JVersion["JobVersion (Immutable)"]
    end

    subgraph Matching ["3. Deterministic Matching"]
        Profile --> Matcher["deterministic_matching.py"]
        JVersion --> Matcher
        Matcher --> MatchResult["MatchRun (Score Math + Gaps)"]
    end

    subgraph Studio ["4. AI Question Studio"]
        JVersion --> AIOrch["ai_question_set_service.py"]
        AIOrch --> LocalLLM["Local LLM Inference (MLX/Ollama)"]
        LocalLLM --> Checks["ai_provenance_service.py (20 Checks)"]
        Checks --> ApprovedSet["Approved AI Question Set"]
    end

    subgraph Simulation ["5. Candidate Practice & Evaluation"]
        ApprovedSet --> Release["candidate_practice_service.py"]
        Release --> Sandbox["Candidate Practice Room / Workspace"]
        Sandbox --> TurnEng["practice_session_service.py"]
        TurnEng --> Report["practice_report_service.py + candidate_evaluation_service.py"]
    end
```

---

## 5. Technology Stack & Runtime Infrastructure

| Subsystem | Technology | Version | Key Responsibilities & Capabilities |
|---|---|---|---|
| **Backend API** | FastAPI + Python | 3.14 / 0.115+ | High-performance ASGI async server, Pydantic v2 strict serialization, dependency injection security guards |
| **Database ORM** | PostgreSQL + SQLAlchemy | 16 / 2.0+ | Relational persistence, JSONB document snapshots, Alembic schema migrations, strict FK cascades |
| **Document Parsing** | pdfplumber + python-docx | 0.11+ / 1.1+ | Character-level coordinate extraction, font metadata, multi-column layout recognition, evidence offsets |
| **Frontend UI** | React + TypeScript + Vite | 19 / 5.5+ / 6.0+ | Strict type safety, Vanilla CSS Glassmorphic design tokens, zero external UI framework bloat |
| **Local AI Inference** | MLX / Ollama / OpenAI API | Native / 0.3+ | Apple Silicon MLX local inference, Ollama OpenAI-compatible endpoints, zero resume data egress |
| **Testing Engine** | Pytest + Vitest | 8.0+ / 4.1+ | 89 Python backend test suites (900+ tests), 38 TypeScript frontend test suites (200+ tests) |

---

## 6. Comprehensive Inter-File Relationships & Architectural Coupling

The power of the AI-Hiring platform stems from its tightly coordinated, strictly layered interactions across frontend views, typed clients, backend endpoints, domain engines, and relational tables. The diagram and catalog below explain how the files are interconnected:

### 6.1 End-to-End Inter-Relationship Topology

```mermaid
graph TD
    subgraph FrontendLayer ["Frontend Layer (React 19 + TypeScript)"]
        UI_Jobs["components/jobs/JobEditor.tsx"]
        UI_Resume["components/ResumeUpload.tsx"]
        UI_Match["components/jobs/CandidateComparisonView.tsx"]
        UI_AIStudio["components/ai_interviews/AiQuestionSetWorkspace.tsx"]
        UI_Practice["components/candidate/CandidatePracticeRoom.tsx"]
        UI_Eval["components/evaluation/EvaluationWorkspace.tsx"]
        
        API_JobsClient["api/jobs.ts"]
        API_CandClient["api/candidateApplications.ts"]
        API_AIClient["api/candidateInterviews.ts / candidatePractice.ts"]
        API_EvalClient["api/candidateEvaluations.ts"]
    end

    subgraph BackendTransport ["Transport Layer (FastAPI Routers)"]
        R_Jobs["api/jobs.py"]
        R_Resumes["api/resumes.py"]
        R_Matches["api/matches.py"]
        R_AI["api/ai_interviews.py"]
        R_Practice["api/candidate_practice.py"]
        R_Eval["api/candidate_evaluation.py"]
    end

    subgraph DomainServices ["Domain Services Engine (Pure Business Logic)"]
        S_DocParser["services/pdf_text_extraction.py + docx_text_extraction.py"]
        S_ProfileAssy["services/profile_assembly.py + extraction_persistence.py"]
        S_Match["services/deterministic_matching.py + gap_derivation.py"]
        S_AIProv["services/ai_provenance_service.py + ai_question_set_service.py"]
        S_Practice["services/practice_session_service.py + practice_follow_up_service.py"]
        S_Eval["services/candidate_evaluation_service.py + practice_report_service.py"]
    end

    subgraph PersistenceLayer ["Persistence Layer (SQLAlchemy 2.0 & PostgreSQL 16)"]
        M_Job["models/job.py (JobRequisition, JobVersion, JobRequirement)"]
        M_Candidate["models/candidate.py (CandidateIdentity, Profile, Evidence)"]
        M_Match["models/matching.py + gap_report.py"]
        M_Interview["models/interview.py + candidate_access.py (AIQuestionSet, PracticeSession, Evaluation)"]
    end

    UI_Jobs --> API_JobsClient --> R_Jobs --> M_Job
    UI_Resume --> API_CandClient --> R_Resumes --> S_DocParser --> S_ProfileAssy --> M_Candidate
    UI_Match --> R_Matches --> S_Match --> M_Match
    UI_AIStudio --> R_AI --> S_AIProv --> M_Interview
    UI_Practice --> API_AIClient --> R_Practice --> S_Practice --> M_Interview
    UI_Eval --> API_EvalClient --> R_Eval --> S_Eval --> M_Interview
```

### 6.2 Key Architectural Inter-File Interaction Chains

1. **Resume Ingestion & Evidence Grounding Chain**:
   - `components/ResumeUpload.tsx` $\rightarrow$ `api/candidateApplications.ts` $\rightarrow$ `api/resumes.py` $\rightarrow$ `services/destination_policy.py` & `services/pdf_validation.py` $\rightarrow$ `services/pdf_text_extraction.py` / `services/docx_text_extraction.py` $\rightarrow$ `services/section_detection.py` $\rightarrow$ `services/identity_extraction.py`, `services/employment_extraction.py`, `services/skill_summary_extraction.py` $\rightarrow$ `services/taxonomy_normalization.py` $\rightarrow$ `services/profile_assembly.py` $\rightarrow$ `services/extraction_persistence.py` $\rightarrow$ `models/candidate.py` (`CandidateIdentityRecord`, `CandidateProfileRecord`, `ProfileCorrectionRecord`) $\rightarrow$ rendered in `components/CandidateProfileReview.tsx` & `components/EvidenceDrawer.tsx`.

2. **Job Requisition & Grounded Requirement Chain**:
   - `components/jobs/JobEditor.tsx` & `components/jobs/TaxonomySkillAutocomplete.tsx` $\rightarrow$ `api/jobs.ts` & `api/taxonomy.ts` $\rightarrow$ `api/jobs.py` & `api/taxonomy.py` $\rightarrow$ `services/job_description_parser.py` & `services/requirement_validation.py` $\rightarrow$ `services/job_readiness.py` $\rightarrow$ `models/job.py` (`JobRequisition`, `JobVersion`, `JobRequirement`) & `models/taxonomy.py` (`SkillConcept`, `OccupationConcept`).

3. **Deterministic Matching & Gap Derivation Chain**:
   - `components/jobs/CandidateComparisonView.tsx` & `components/jobs/RequirementGapReportView.tsx` $\rightarrow$ `api/jobs.ts` $\rightarrow$ `api/matches.py` & `api/gap_reports.py` $\rightarrow$ `services/matching_preflight.py` $\rightarrow$ `services/deterministic_matching.py` $\rightarrow$ `services/scoring.py` $\rightarrow$ `services/gap_derivation.py` & `services/gap_report_service.py` $\rightarrow$ `services/explanation.py` $\rightarrow$ `models/matching.py` (`MatchRunRecord`, `MatchResultRecord`) & `models/gap_report.py` (`GapReportRecord`, `GapReportItemRecord`).

4. **AI Question Studio & 20-Point Provenance Chain**:
   - `components/ai_interviews/AiQuestionSetWorkspace.tsx`, `components/ai_interviews/McqAuthoringModal.tsx`, `components/ai_interviews/ReleaseInterviewSetModal.tsx` $\rightarrow$ `api/candidateInterviews.ts` $\rightarrow$ `api/ai_interviews.py` $\rightarrow$ `services/provider_registry.py` & `services/secret_resolver.py` $\rightarrow$ `services/wording_provider.py` $\rightarrow$ `services/ai_provenance_service.py` (20 server-side validation matrix checks) $\rightarrow$ `services/ai_question_set_service.py` $\rightarrow$ `models/interview.py` (`JobInterviewTemplateRecord`, `ProviderProfileRecord`) & `models/candidate_access.py` (`CandidatePracticeReleaseRecord`).

5. **Candidate Practice Sandbox & Adaptive Turn-Taking Chain**:
   - `components/candidate/CandidatePracticeRoom.tsx` $\rightarrow$ `api/candidatePracticeSession.ts` $\rightarrow$ `api/candidate_practice.py` $\rightarrow$ `services/candidate_authorization_kernel.py` $\rightarrow$ `services/practice_session_service.py` $\rightarrow$ `services/practice_review_service.py` & `services/practice_follow_up_service.py` $\rightarrow$ `models/candidate_access.py` (`CandidatePracticeSessionRecord`, `CandidatePracticeMessageRecord`, `CandidatePracticeAnswerSegmentRecord`) $\rightarrow$ rendered in `components/candidate/CandidateFeedbackModal.tsx`.

6. **Formal Dual-Mode Interview & Audio Execution Chain (TTS & Ephemeral STT)**:
   - `components/candidate/CandidateInterviewWorkspace.tsx` $\rightarrow$ `api/candidateInterviews.ts` $\rightarrow$ `api/candidate_interviews.py` (or `ai_interviews.py`) $\rightarrow$ `services/candidate_interview_service.py` $\rightarrow$ `services/speech_service.py` (`TextToSpeechAdapter` for question audio generation via macOS `say`/Piper, `EphemeralAudioProcessor` + `SpeechToTextAdapter` for candidate answer audio conversion via `ffmpeg` + `whisper.cpp` with zero storage retention) $\rightarrow$ candidate verifies and accepts transcript $\rightarrow$ saved into `models/candidate_access.py` (`CandidateInterviewSessionRecord`, `CandidateInterviewAnswerRecord`).

7. **Candidate Mode-Aware Evaluation & Coaching Analytics Chain**:
   - `components/evaluation/EvaluationWorkspace.tsx`, `components/candidate/CandidatePracticeReport.tsx`, `components/common/InterviewDimensionRadar.tsx` $\rightarrow$ `api/candidateEvaluations.ts` $\rightarrow$ `api/candidate_evaluation.py` $\rightarrow$ `services/candidate_evaluation_service.py`, `services/answer_assessment_service.py`, `services/practice_report_service.py` $\rightarrow$ evaluates accepted transcript content (strictly prohibiting acoustic/voice bias scoring) $\rightarrow$ persists to `models/candidate_access.py` (`CandidatePracticeReportRecord`, `CandidateInterviewEvaluationRecord`, `CandidateQuestionEvaluationRecord`).

8. **ATS Integration & Local Synchronization Chain**:
   - `components/admin/AtsConnectionsAdmin.tsx`, `components/ats/AtsImportModal.tsx`, `components/ats/AtsExportModal.tsx` $\rightarrow$ `api/ats.ts` $\rightarrow$ `api/ats.py` $\rightarrow$ `services/ats/adapter_registry.py` $\rightarrow `services/ats/local_folder_adapter.py` / `services/ats/csv_json_adapter.py` $\rightarrow `services/ats/import_service.py` & `services/ats/export_service.py` $\rightarrow `models/ats.py` (`ATSConnectionRecord`, `ATSImportRunRecord`, `ATSExportReceiptRecord`).

---

## 7. Environment Configuration & Local Execution

### 7.1 Environment Variables (`.env`)
```ini
# PostgreSQL 16 Connection
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ai_hiring_db

# Security Secrets (Must be min 32 characters in production)
SECRET_KEY=enterprise-super-secret-key-change-in-production-min-32-chars
CSRF_SECRET_KEY=csrf-hmac-salt-enterprise-key
SESSION_EXPIRE_HOURS=24

# Local AI Inference Provider (MLX / Ollama)
DEFAULT_AI_PROVIDER_TYPE=LOCAL_OPENAI_COMPATIBLE
DEFAULT_AI_PROVIDER_URL=http://localhost:8080/v1
DEFAULT_AI_MODEL_IDENTIFIER=qwen/qwen3-4b-2507
```

### 7.2 Quick-Start Execution Commands
```bash
# 1. Start PostgreSQL 16 Database
docker compose up -d postgres

# 2. Setup and Run Backend API
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m app.cli.bootstrap_admin
uvicorn app.main:app --reload --port 8000

# 3. Setup and Run Frontend UI (in a separate terminal)
cd frontend
npm install
npm run dev

# 4. Execute Full Verification Test Suites
# Backend Tests:
cd backend && pytest tests/ -v
# Frontend Tests:
cd frontend && npm test
```
