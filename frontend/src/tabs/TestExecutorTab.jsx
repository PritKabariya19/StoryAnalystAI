import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Play, CheckCircle, XCircle, AlertCircle, Image, Download, Save,
         ChevronLeft, ChevronRight, Activity, Cpu, Zap, ListFilter, Hash } from "lucide-react";
import { startTestJob, startBatchTestJob, pollTestJob, saveReport } from "../services/api";
import useStore from "../state/store";
import Button from "../components/Button";
import Card from "../components/Card";
import Modal from "../components/Modal";

// Full backend URL so <img> tags resolve against Node API, not Vite dev server
const BACKEND_URL = import.meta.env.VITE_BACKEND_URL || "http://localhost:5001";

const TYPE_COLORS = {
  Positive:   "text-emerald-400 bg-emerald-500/10",
  Negative:   "text-red-400 bg-red-500/10",
  Boundary:   "text-amber-400 bg-amber-500/10",
  "Edge Case":"text-purple-400 bg-purple-500/10",
};

const STATUS_BADGE = {
  Pending: <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-semibold bg-white/10 text-white/60">Pending</span>,
  Pass:    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/15 text-emerald-400"><CheckCircle size={10}/>Pass</span>,
  Fail:    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-red-500/15 text-red-400"><XCircle size={10}/>Fail</span>,
  Error:   <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-500/15 text-amber-400"><AlertCircle size={10}/>Error</span>,
};

function ResultRow({ test, onViewScreenshot }) {
  const rawType = test.original_tc?.type || "";
  const formattedType = rawType ? rawType.charAt(0).toUpperCase() + rawType.slice(1).replace('_', ' ') : "";
  const typeColor = TYPE_COLORS[formattedType] || "text-white/40 bg-white/5";
  const screenshotUrl = test.screenshot
    ? (test.screenshot.startsWith("http") ? test.screenshot : `${BACKEND_URL}${test.screenshot}`)
    : null;

  return (
    <div className="flex items-center gap-3 px-4 py-3 border-b border-surface-border last:border-0 hover:bg-white/5 transition-colors">
      {/* TC ID */}
      <div className="w-20 font-mono text-xs text-white/40 flex-shrink-0">{test.testId}</div>

      {/* Condition Name */}
      <div className="flex-1 min-w-0">
        <p className="font-medium text-sm text-white truncate" title={test.description}>{test.description}</p>
        {test.errorMsg && (
          <p className="text-xs text-red-400/70 truncate mt-0.5" title={test.errorMsg}>⚠ {test.errorMsg}</p>
        )}
        {!test.errorMsg && test.execTime > 0 && (
          <p className="text-xs text-white/30">{Number(test.execTime).toFixed(2)}s</p>
        )}
      </div>

      {/* Type */}
      <div className="w-24 flex-shrink-0 hidden sm:block">
        <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${typeColor}`}>
          {formattedType || "—"}
        </span>
      </div>

      {/* Status */}
      <div className="w-24 flex-shrink-0">
        {STATUS_BADGE[test.status] || STATUS_BADGE.Pending}
      </div>

      {/* Screenshot Action */}
      <div className="w-10 flex justify-end flex-shrink-0">
        {screenshotUrl ? (
          <button
            onClick={() => onViewScreenshot(screenshotUrl, test.testId)}
            className="text-amber-400/70 hover:text-amber-400 transition-colors"
            title="View Screenshot"
          >
            <Image size={16} />
          </button>
        ) : (
          <span className="text-white/10"><Image size={16}/></span>
        )}
      </div>
    </div>
  );
}


export default function TestExecutorTab() {
  const {
    addToast,
    smartTestOutput,
    generatedTests,
    setGeneratedTests,
    executedResults,
    setExecutedResults,
  } = useStore(state => ({
    addToast:          state.addToast,
    smartTestOutput:   state.smartTestOutput,
    generatedTests:    state.generatedTests,
    setGeneratedTests: state.setGeneratedTests,
    executedResults:   state.executedResults,
    setExecutedResults:state.setExecutedResults,
  }));

  const [job, setJob]           = useState(null);
  const [loading, setLoading]   = useState(false);
  const [saving, setSaving]     = useState(false);
  const [workers, setWorkers]   = useState(1);       // parallel browser instances
  const [page, setPage]         = useState(1);
  const [screenshotModal, setScreenshotModal] = useState({ open: false, url: "", title: "" });
  const [useBatchMode, setUseBatchMode] = useState(false);
  const [startIndex, setStartIndex] = useState(0);
  const [endIndex, setEndIndex] = useState("");
  const [batchSize, setBatchSize] = useState(5);
  const [showBrowser, setShowBrowser] = useState(false); // headed browser → solve CAPTCHAs manually
  const ITEMS_PER_PAGE = 20;

  const pollRef      = useRef(null);
  // ref to latest generatedTests to avoid stale closure in polling callback
  const testsRef     = useRef(generatedTests);
  useEffect(() => { testsRef.current = generatedTests; }, [generatedTests]);

  // Map smartTestOutput → normalised generatedTests whenever it changes
  useEffect(() => {
    // smartTestOutput has shape { test_suites: [ { component: "...", test_cases: [...] } ] }
    if (smartTestOutput?.test_suites?.length > 0) {
      // flatten tests from components
      const allTests = [];
      smartTestOutput.test_suites.forEach(suite => {
        if (Array.isArray(suite.test_cases)) {
          allTests.push(...suite.test_cases);
        }
      });
      
      if (allTests.length > 0) {
        const mapped = allTests.map((tc, idx) => {
          const testId = tc.tc_id || tc.testId || `TC-${String(idx + 1).padStart(3, "0")}`;
          const safeName = String(tc.test_scenario || tc.condition || tc.title || tc.feature || "test")
            .replace(/[^a-zA-Z0-9 ]/g, " ")
            .trim()
            .slice(0, 40);
          return {
            id:          crypto.randomUUID?.() ?? `uid-${idx}-${Math.random()}`,
            testId,
            name:        `${testId} — ${safeName}`,
            description: tc.test_scenario || tc.condition || tc.title || "(no description)",
            status:      "Pending",
            screenshot:  null,
            execTime:    0,
            original_tc: tc,
          };
        });
        setGeneratedTests(mapped);
        setExecutedResults([]);
        setJob(null);
        setPage(1);
      }
    }
  }, [smartTestOutput, setGeneratedTests, setExecutedResults]);

  // Cleanup polling on unmount
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  function viewScreenshot(url, title) {
    setScreenshotModal({ open: true, url, title });
  }

  async function handleRun() {
    if (!generatedTests?.length) { addToast("Generate test cases first.", "warning"); return; }
    if (loading) return;

    setLoading(true);
    setExecutedResults([]);
    setJob({ status: "running", progress: 0, total: generatedTests.length, results: [] });

    // Reset all statuses to Pending
    const reset = generatedTests.map(t => ({ ...t, status: "Pending", screenshot: null, execTime: 0 }));
    setGeneratedTests(reset);
    testsRef.current = reset;

    if (pollRef.current) clearInterval(pollRef.current);

    const payload = generatedTests.map(t => t.original_tc);
    console.log("[TestExecutor] Starting job with", payload.length, "test cases, workers:", workers);
    console.log("[TestExecutor] Payload sample:", payload.slice(0, 2));

    try {
      let startResp;
      if (useBatchMode) {
        startResp = await startBatchTestJob(payload, {
          startIndex: parseInt(startIndex, 10) || 0,
          endIndex: endIndex ? parseInt(endIndex, 10) : payload.length,
          batchSize: parseInt(batchSize, 10) || 5,
          workers,
          headless: !showBrowser,
        });
      } else {
        startResp = await startTestJob(payload, workers, !showBrowser);
      }
      console.log("[TestExecutor] Job started:", startResp);
      const { jobId } = startResp;

      pollRef.current = setInterval(async () => {
        try {
          const status = await pollTestJob(jobId);
          console.log("[TestExecutor] Poll response:", { status: status.status, progress: status.progress, total: status.total, resultsCount: status.results?.length });
          setJob(status);

          if (status.results?.length) {
            setExecutedResults(status.results);

            // Update rows using the ref (avoids stale closure)
            const latest = testsRef.current.map(t => ({ ...t }));
            let matched = 0;
            status.results.forEach(res => {
              // Match on testId (set by backend normaliseResults), tc_id, or id
              const idx = latest.findIndex(
                t =>
                  t.testId === res.testId ||
                  t.testId === res.tc_id  ||
                  t.testId === res.id
              );
              if (idx !== -1) {
                matched++;
                const screenshotUrl = res.screenshot
                  ? (res.screenshot.startsWith("http") ? res.screenshot : `${BACKEND_URL}${res.screenshot}`)
                  : null;
                latest[idx] = {
                  ...latest[idx],
                  status:     res.status,
                  screenshot: screenshotUrl,
                  execTime:   res.duration_seconds ?? res.execTime ?? 0,
                  errorMsg:   res.error || res.error_message || null,
                };
              } else {
                console.warn("[TestExecutor] No row match for result:", { testId: res.testId, tc_id: res.tc_id, id: res.id });
              }
            });
            console.log(`[TestExecutor] Reconciled ${matched}/${status.results.length} results to rows`);
            setGeneratedTests(latest);
            testsRef.current = latest;
          }

          if (status.status === "done" || status.status === "failed") {
            clearInterval(pollRef.current);
            setLoading(false);
            if (status.status === "done") {
              addToast(
                `Done: ${status.summary?.passed ?? 0} passed, ${status.summary?.failed ?? 0} failed`,
                status.summary?.failed > 0 ? "warning" : "success"
              );
            } else {
              addToast(`Execution failed: ${status.error || "Unknown error"}`, "error");
            }
          }
        } catch (pollErr) {
          console.error("[TestExecutor] Polling error:", pollErr);
          clearInterval(pollRef.current);
          setLoading(false);
          addToast(`Polling error: ${pollErr.message || "Check backend connection."}`, "error");
        }
      }, 4000);
    } catch (err) {
      console.error("[TestExecutor] Failed to start job:", err);
      setLoading(false);
      addToast("Failed to start execution: " + err.message, "error");
    }
  }

  async function handleSave() {
    if (!executedResults?.length) {
      addToast("No executed results to save.", "warning");
      return;
    }
    setSaving(true);
    try {
      await saveReport("test", executedResults, job?.summary || {}, {});
      addToast("Report saved! View it in the Reports tab.", "success");
    } catch (err) {
      addToast("Save failed: " + err.message, "error");
    } finally {
      setSaving(false);
    }
  }

  const totalPages = Math.ceil((generatedTests?.length || 0) / ITEMS_PER_PAGE);
  const currentTests = useMemo(() => {
    const start = (page - 1) * ITEMS_PER_PAGE;
    return (generatedTests || []).slice(start, start + ITEMS_PER_PAGE);
  }, [generatedTests, page]);

  const progressPercent = job?.total ? Math.round((job.progress / job.total) * 100) : 0;

  // Derived counts from live table
