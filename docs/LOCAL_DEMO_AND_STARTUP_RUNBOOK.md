# Local Demo and Startup Runbook

## 1. System Prerequisites

- **macOS / Linux / Windows (WSL2 or PowerShell)**
- **Python 3.11+** (Tested on Python 3.14.6)
- **Node.js 18+ & npm** (Vite / React 18)
- **PostgreSQL 15+** (Local native service or Docker container)
- **Memory**: 16 GB+ recommended (Verified with ~87% headroom on 24 GB Apple Silicon Mac)

---

## 2. Standard Service Ports

| Service | Port | Description |
| :--- | :--- | :--- |
| **Frontend (React)** | `5173` | http://localhost:5173 |
| **FastAPI Backend** | `8000` | http://localhost:8000 |
| **Streamlit Analytics** | `8501` | http://localhost:8501 |
| **PostgreSQL Database** | `5432` | localhost:5432 |
| **Local LLM (LM Studio / llama.cpp)** | `8080` or `1234` | Configured in `.env` |

---

## 3. macOS / Linux Startup Commands

### Step 1: Start PostgreSQL & Apply Migrations
```bash
# If using Docker for PostgreSQL:
docker compose up -d postgres

# In backend directory:
cd backend
.venv/bin/alembic upgrade head
```

### Step 2: Bootstrap System Administrator (Initial Setup Only)
```bash
cd backend
.venv/bin/python -m app.cli.bootstrap_admin --email admin@local.test --password "AdminPass123!Safe" --name "System Admin"
```

### Step 3: Start FastAPI Backend
```bash
cd backend
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Step 4: Start Streamlit Dashboard
```bash
cd backend
.venv/bin/streamlit run ../dashboard/app.py --server.port 8501 --server.headless true
```

### Step 5: Start React Frontend
```bash
cd frontend
npm run dev -- --port 5173
```

---

## 4. Windows (PowerShell) Startup Commands

### Step 1: Database & Migrations
```powershell
# In backend folder
.\.venv\Scripts\alembic.exe upgrade head
```

### Step 2: Start Backend
```powershell
cd backend
.\.venv\Scripts\uvicorn.exe app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Step 3: Start Streamlit
```powershell
cd backend
.\.venv\Scripts\streamlit.exe run ..\dashboard\app.py --server.port 8501 --server.headless true
```

### Step 4: Start Frontend
```powershell
cd frontend
npm run dev -- --port 5173
```

---

## 5. End-to-End Demonstration Sequence

1. **Recruiter Workflow:**
   - Log in as recruiter (`recruiter@example.com` / `password`).
   - Navigate to **Jobs Workspace** (`/recruiter/jobs`).
   - Create a Job Requisition, add structured requirements (Skills/Experience), mark **READY**, and **PUBLISH**.
2. **Candidate Workflow:**
   - In an incognito window or separate browser, navigate to Candidate portal (`/candidate/jobs`).
   - Register a candidate account and log in.
   - Discover the published job, upload a PDF/DOCX resume, and view extracted profile skills.
   - Click **Submit Application** (freezing Candidate Profile Version and Job Revision).
3. **Recruiter Review & Match:**
   - Switch back to Recruiter window, open the Applicant detail.
   - Review deterministic match tier and skill gap breakdown.
   - Advance candidate review state: `RECEIVED` → `IN_REVIEW` → `INTERVIEW_READY`.
4. **Dual-Mode Interview Preparation:**
   - Generate and approve **Text** and **Audio** question sets independently (Audio set strictly has 0 MCQs).
   - Release Text or Audio interview to the candidate.
5. **Candidate Interview:**
   - Candidate opens **Assigned Interviews**, launches the session, answers questions, and submits official responses.
   - Answers are saved immediately; local AI evaluation executes post-submission.
6. **Recruiter Evaluation & Streamlit Analytics:**
   - Recruiter inspects multi-criteria evaluation rubrics in the **Evaluation Workspace**.
   - Recruiter clicks **Launch Analytics**; single-use token exchanges securely and opens Streamlit dashboard showing real-time pipeline and matching analytics.

---

## 6. Safe Shutdown & Troubleshooting

- **Graceful Shutdown**: Use `Ctrl + C` in each service terminal.
- **Microphone Access Notice**: In browsers, microphone capture for audio interviews requires `http://localhost` (supported for local dev) or `https://`.
- **Local AI Notice**: If local LM Studio is offline, all fallback mechanisms display honest "AI Unavailable" notifications without breaking application flow or generating fake responses.
