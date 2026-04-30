const express = require("express");
const axios   = require("axios");
const router  = express.Router();
const path    = require("path");
const fs      = require("fs");
const { verifyToken } = require("../middleware/authMiddleware");

const PYTHON_AI_URL   = process.env.PYTHON_AI_URL  || "http://localhost:10000";
const INTERNAL_SECRET = process.env.INTERNAL_API_SECRET || "dev_secret_change_me";

const SCREENSHOTS_DIR = path.join(__dirname, "../../../screenshots");

/** In-memory job store */
const jobs = new Map();

function generateJobId() {
  return `job_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

/** Normalise screenshot_path → URL routed through Node proxy, and unify testId */
function normaliseResults(results = []) {
  console.log("[normaliseResults] Processing", results.length, "results");
  return results.map((r, idx) => {
    // Unify the identifier field so frontend matching always works
    r.testId = r.tc_id || r.id || r.testId || `TC-${String(idx + 1).padStart(3, "0")}`;

    const raw = r.screenshot_path || r.screenshot || "";
    if (raw) {
      const fname = raw.replace(/^[/\\]?screenshots[/\\]/, "").replace(/\\/g, "/");
      r.screenshot = `/api/tests/screenshots/${fname}`;
    }

    console.log(`  [${idx}] tc_id=${r.tc_id} testId=${r.testId} status=${r.status}`);
    return r;
  });
}


// ─── POST /api/tests/execute (synchronous, kept for backward compat) ─────────
router.post("/execute", verifyToken, async (req, res) => {
  const { test_cases = [], headless = true, workers = 1 } = req.body;
  if (!test_cases.length)
    return res.status(400).json({ error: "No test cases provided." });

  try {
    const { data } = await axios.post(
      `${PYTHON_AI_URL}/execute`,
      { test_cases, headless, workers: Math.max(1, Math.min(Number(workers), 8)) },
      { headers: { "X-Internal-Secret": INTERNAL_SECRET }, timeout: 600000 }
    );
    const results = normaliseResults(data.results || []);
    return res.json({ ...data, results, mock: false });
  } catch (err) {
    console.error("[tests/execute] Python error:", err.message);
    return res.status(500).json({ error: "Python executor failed: " + (err.response?.data?.error || err.message) });
  }
});

// ─── POST /api/tests/start ────────────────────────────────────────────────────
router.post("/start", verifyToken, async (req, res) => {
  const { test_cases = [], workers = 1 } = req.body;
  if (!test_cases.length)
    return res.status(400).json({ error: "No test cases provided." });

  const jobId  = generateJobId();
  const total  = test_cases.length;
  const wCount = Math.max(1, Math.min(Number(workers), 8));

  // Initialise job immediately
  jobs.set(jobId, {
    status:   "running",
    progress: 0,
    total,
    results:  [],
    summary:  null,
    error:    null,
    startedAt: Date.now(),
  });

  // Fire & forget
  (async () => {
    try {
      // Fake trickle progress so UI doesn't look stuck
      let fakeProgress = 0;
      const ticker = setInterval(() => {
        const job = jobs.get(jobId);
        if (!job || job.status !== "running") return clearInterval(ticker);
        fakeProgress = Math.min(fakeProgress + Math.ceil(total * 0.05), Math.floor(total * 0.85));
        jobs.set(jobId, { ...job, progress: fakeProgress });
      }, 3000);

      const { data } = await axios.post(
        `${PYTHON_AI_URL}/execute`,
        { test_cases, headless: true, workers: wCount },
        { headers: { "X-Internal-Secret": INTERNAL_SECRET }, timeout: 600000 }
      );

      clearInterval(ticker);

      const results = normaliseResults(data.results || []);
      const passed  = results.filter(r => r.status === "Pass").length;
      const failed  = results.filter(r => r.status === "Fail").length;
      const errored = results.filter(r => r.status === "Error").length;

      jobs.set(jobId, {
        status:   "done",
        progress: total,
        total,
        results,
        summary: data.summary || { total: results.length, passed, failed, errored },
        error:   null,
      });

    } catch (err) {
      console.error("[tests/start] Python failed:", err.message);
      const errMsg = err.response?.data?.error || err.message;
      jobs.set(jobId, {
        status:   "failed",
        progress: 0,
        total,
        results:  [],
        summary:  { total, passed: 0, failed: total, errored: total },
        error:    errMsg,
      });
    }
  })();

  res.json({ jobId, status: "running", total });
});

// ─── GET /api/tests/status/:jobId ─────────────────────────────────────────────
router.get("/status/:jobId", verifyToken, (req, res) => {
  const job = jobs.get(req.params.jobId);
  if (!job) return res.status(404).json({ error: "Job not found or expired." });

  const passed  = job.results.filter(r => r.status === "Pass").length;
  const failed  = job.results.filter(r => r.status === "Fail").length;
  const errored = job.results.filter(r => r.status === "Error").length;

  res.json({
    status:   job.status,
    progress: job.progress,
    total:    job.total,
    results:  job.results,
    summary:  job.summary || { total: job.total, passed, failed, errored },
    error:    job.error || null,
  });
});

// ─── GET /api/tests/screenshots/:filename ─────────────────────────────────────
// Serves screenshot files — tries Python microservice first, then local disk
router.get("/screenshots/:filename", async (req, res) => {
  const { filename } = req.params;

  // Try local disk first (fastest)
  const localPath = path.join(SCREENSHOTS_DIR, filename);
  if (fs.existsSync(localPath)) {
    res.setHeader("Content-Type", "image/png");
    return res.sendFile(localPath);
  }

  // Proxy through Python
  try {
    const response = await axios({
      method: "get",
      url:    `${PYTHON_AI_URL}/screenshots/${filename}`,
      responseType: "stream",
      timeout: 10000,
    });
    res.setHeader("Content-Type", "image/png");
    response.data.pipe(res);
  } catch (err) {
    console.error("[tests/screenshots] Image not found:", filename, err.message);
    res.status(404).json({ error: "Screenshot not found." });
  }
});

// ─── POST /api/tests/start-batch ─────────────────────────────────────────────
router.post("/start-batch", verifyToken, async (req, res) => {
  const {
    test_cases  = [],
    start_index = 0,
    end_index,
    batch_size  = 5,
    workers     = 1,
  } = req.body;

  if (!test_cases.length)
    return res.status(400).json({ error: "No test cases provided." });

  const wCount    = Math.max(1, Math.min(Number(workers), 8));
  const actualEnd = end_index != null ? Number(end_index) : test_cases.length;
  const total     = Math.max(0, actualEnd - Number(start_index));

  // ── Ask Python to start the batch job ──────────────────────────────────
  let pythonJobId;
  try {
    const { data } = await axios.post(
      `${PYTHON_AI_URL}/execute-batch`,
      {
        test_cases,
        start_index: Number(start_index),
        end_index:   actualEnd,
        batch_size:  Math.max(1, Number(batch_size)),
        headless:    true,
        workers:     wCount,
      },
      { headers: { "X-Internal-Secret": INTERNAL_SECRET }, timeout: 15000 }
    );
    pythonJobId = data.job_id;
  } catch (err) {
    console.error("[tests/start-batch] Could not start Python job:", err.message);
    return res.status(500).json({ error: "Failed to start batch job: " + (err.response?.data?.error || err.message) });
  }

  // ── Create a local job entry ────────────────────────────────────────────
  const nodeJobId = generateJobId();
  jobs.set(nodeJobId, {
    status:       "running",
    progress:     0,
    total,
    results:      [],
    summary:      null,
    error:        null,
    startedAt:    Date.now(),
    _pythonJobId: pythonJobId,  // stored for polling
  });

  // ── Background poller — every 3 s sync from Python ─────────────────────
  const ticker = setInterval(async () => {
    const job = jobs.get(nodeJobId);
    if (!job || job.status !== "running") return clearInterval(ticker);

    try {
      const { data } = await axios.get(
        `${PYTHON_AI_URL}/batch-status/${pythonJobId}`,
        { headers: { "X-Internal-Secret": INTERNAL_SECRET }, timeout: 8000 }
      );

      const normResults = normaliseResults(data.results || []);
      const passed  = normResults.filter(r => r.status === "Pass").length;
      const failed  = normResults.filter(r => r.status === "Fail").length;
      const errored = normResults.filter(r => r.status === "Error").length;

      const update = {
        ...job,
        progress: data.progress ?? job.progress,
        results:  normResults,
        summary:  data.summary || { total, passed, failed, errored },
      };

      if (data.status === "done") {
        clearInterval(ticker);
        update.status   = "done";
        update.progress = total;
      } else if (data.status === "failed") {
        clearInterval(ticker);
        update.status = "failed";
        update.error  = data.error || "Unknown batch error";
      }

      jobs.set(nodeJobId, update);
    } catch (pollErr) {
      console.warn("[tests/start-batch] Poll error:", pollErr.message);
    }
  }, 3000);

  res.json({ jobId: nodeJobId, status: "running", total });
});

module.exports = router;
