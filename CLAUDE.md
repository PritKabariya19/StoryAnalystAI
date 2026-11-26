# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

# Project: StoryAnalystAI

AI-assisted QA platform: user story + website URL → test cases → headless Chromium execution → HTML report.

## Layout

```
frontend/     React + Vite UI (:5173)
backend/      Django API gateway (:5001): auth, rate limits, payments, reports, test jobs
ai-service/   Flask AI service (:10000): story analysis, crawling, test generation, Playwright execution, report HTML
docs/report/  College report: generator, verifier, spec and built DOCX/PDF
test-web/     Static login/signup page used as a local test target
```

Requests flow `frontend → backend → ai-service`. The backend calls the AI service at `PYTHON_AI_URL` and authenticates with the `X-Internal-Secret` header.

- `backend/`: one Django app per `/api/<area>/` prefix (`accounts`, `ai`, `testruns`, `payments`, `reports`, `contact`), shared code in `core/`. No SQL database: sign-in is Firebase Auth, with local accounts in `backend/.data/users.json` as a fallback; profiles (`users/{uid}`) live in Firestore, reports in Firestore or memory. Without Firebase credentials it runs in mock mode.
- `ai-service/`: `app.py` holds the routes, `agents/` the pipeline steps, `engines/` the rule-based fallback used by `orchestrator.py` when Gemini fails, `models/` the shared data classes.

## Commands

Each Python service has its own `venv/` (Python 3.13). The AI service also needs `./venv/bin/playwright install chromium`. Run from the repo root, one terminal each:

```bash
cd ai-service && ./venv/bin/python app.py
cd backend    && ./venv/bin/python manage.py runserver 5001
cd frontend   && npm run dev
```

Local test target: `python3 -m http.server 3000 --directory test-web`

## Verifying changes

Run the checks for what you touched. Tests don't call Firebase, Razorpay or Gemini.

- Backend: `cd backend && ./venv/bin/python manage.py check && ./venv/bin/python manage.py test tests --settings=config.test_settings` (always pass the test settings; the normal ones load real keys from `.env`)
- AI service (needs Chromium): `cd ai-service && ./venv/bin/python -m unittest discover -s tests -t .`
- Frontend (no tests): `cd frontend && npm run lint && npm run build`
- End to end: run all three services and the local test target, then explore, execute and generate a report from the UI.

## Gotchas

- If the AI service fails, the backend returns an error and does not charge usage. With `AI_DEMO_DATA=true` it returns demo data with `"mock": true` instead.
- If Gemini fails, the AI service uses rule-based engines, logs why and returns `"ai_generated": false`. The free tier allows very few requests per day.
- Payments return 503 without both Razorpay keys. `PAYMENTS_MOCK=true` gives free upgrades for local testing.
- The AI service listens on 127.0.0.1 (`HOST` overrides it). The Dockerfile sets `BLOCK_PRIVATE_URLS=true`, which blocks `localhost` targets; it is off locally.
- Use port 5001 for the backend. macOS AirPlay takes 5000.
- `INTERNAL_API_SECRET` must match in `backend/.env` and `ai-service/.env`. If unset, both fall back to a public dev value, which the AI service accepts only from the same machine.
- Login tokens are signed with `JWT_SECRET`. If unset, a random key is created in `backend/.data/jwt_secret`.
- Test jobs and rate limits are held in memory, so keep gunicorn at one worker (see `backend/Procfile`).
- Screenshots are written to `ai-service/screenshots/` (gitignored), and the backend reads them from there. They are deleted after 7 days, on the next test run.

## Secrets

Secrets belong only in the gitignored `.env` files. Never add a key as a code default and never commit key files. Add new variable names to the matching `.env.example`.