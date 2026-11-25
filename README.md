# StoryAnalystAI

**StoryAnalystAI** is an AI-powered QA platform that generates, executes and analyzes test cases, starting from a user story. A React frontend talks to a Django API, which talks to a Python AI service that crawls pages and runs headless Playwright tests.

## 🏗 System Architecture

| Folder | Service | Port | Responsibility |
|---|---|---|---|
| `frontend/` | React + Vite | 5173 | Dashboard, execution controls, reports |
| `backend/` | Django API | 5001 | Authentication, rate limits, payments, reports, test jobs, Firebase integration |
| `ai-service/` | Flask | 10000 | Story analysis, website explorer, test case generator, parallel test executor, report agent |

Other folders: `docs/report/` (college project report) and `test-web/` (sample page to run tests against).

---

## ⚙️ Prerequisites

- **Node.js** v18 or later (frontend)
- **Python** 3.10 or later
- **Git**

---

## 🚀 How to Run Locally

Open **three terminals**, each starting at the project root. On Windows, replace `./venv/bin/` with `.\venv\Scripts\`.

### 1. AI service (Terminal 1)

```bash
cd ai-service
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/playwright install chromium   # browser for exploring and test runs
cp .env.example .env      # skip if .env exists; set GEMINI_API_KEY
./venv/bin/python app.py
```

Runs on `http://localhost:10000`. On a Linux server, use `./venv/bin/playwright install --with-deps chromium` to also install the system libraries. Re-run the install command after upgrading Playwright.

### 2. Backend (Terminal 2)

```bash
cd backend
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
cp .env.example .env      # skip if .env exists
./venv/bin/python manage.py runserver 5001
```

Runs on `http://localhost:5001` (macOS AirPlay uses port 5000). Use the same `INTERNAL_API_SECRET` here and in `ai-service/.env`. For production, use the command in `backend/Procfile`.

### 3. Frontend (Terminal 3)

```bash
cd frontend
npm install
cp .env.example .env      # skip if .env exists
npm run dev
```

Open `http://localhost:5173`.

### Firebase sign-in (email/password and Google)

Without this, only local email/password login works.

1. In the [Firebase Console](https://console.firebase.google.com), open your project and register a **Web app**.
2. **Authentication → Sign-in method:** enable **Email/Password** and **Google**. Under **Settings → Authorized domains**, make sure `localhost` is listed.
3. **Firestore Database:** create the database. It stores user profiles (`users/{uid}`) and reports.
4. Copy the web app config into the `VITE_FIREBASE_*` variables in `frontend/.env`.
5. **Project settings → Service accounts:** generate a private key. Save it outside the repo, run `base64 -i key.json`, and set `FIREBASE_SERVICE_ACCOUNT_BASE64` and `FIREBASE_PROJECT_ID` in `backend/.env`.
6. Restart the backend and frontend.

`firestore.rules` blocks all direct client access, because only the backend uses Firestore. Deploy it with `npx firebase-tools deploy --only firestore:rules --project <project-id>`.

Accounts created before Firebase still work through the local login, but they can't use password reset.

`.mcp.json` adds the Firebase MCP server for Claude Code. Run `npx -y firebase-tools@latest login` once before using it.

### Optional: local test target

```bash
python3 -m http.server 3000 --directory test-web
```

Then use `http://localhost:3000/index.html` as the website URL.

---

## 📝 Usage Workflow

1. **User Stories:** Describe the testing conditions in the User Story tab.
2. **Website Explorer:** Enter an application URL. StoryAnalystAI maps its buttons, forms and inputs.
3. **Test Generation:** Turn the story into test cases that match the explored page structure.
4. **Execution:** Choose the number of parallel workers and run the tests in headless Chromium. Each test case starts with a clean browser session, so it must include any login steps it needs.
5. **Reports:** Generate HTML/PDF summaries or download the execution results.

---

## 📄 Project Report

```bash
cd docs/report
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python generate_report.py
./venv/bin/python verify_report.py
```

The generator also needs LibreOffice and Graphviz (`dot`), with paths set at the top of `generate_report.py`.
