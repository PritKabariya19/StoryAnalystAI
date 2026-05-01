import { useState } from "react";
import { motion } from "framer-motion";
import {
  Zap, Globe, Search, AlertCircle, Download, Save, ChevronDown,
  ChevronUp, User, Target, Flag, FileText, Copy, Play, CheckCircle,
} from "lucide-react";
import { generateSmartTests, saveReport } from "../services/api";
import useStore from "../state/store";
import Button from "../components/Button";
import Card from "../components/Card";
import { CardSkeleton } from "../components/LoadingSkeleton";

/* Priority badge colors */
const PRIORITY_COLORS = {
  Critical: "bg-red-500/20 text-red-300 border border-red-500/30",
  High: "bg-orange-500/20 text-orange-300 border border-orange-500/30",
  Medium: "bg-yellow-500/20 text-yellow-300 border border-yellow-500/30",
  Low: "bg-green-500/20 text-green-300 border border-green-500/30",
};

/* Test type badge colors */
const TYPE_COLORS = {
  UI: "bg-purple-500/20 text-purple-300",
  Functional: "bg-blue-500/20 text-blue-300",
  Validation: "bg-cyan-500/20 text-cyan-300",
  Boundary: "bg-amber-500/20 text-amber-300",
  Negative: "bg-red-500/20 text-red-300",
  Security: "bg-rose-500/20 text-rose-300",
  Performance: "bg-green-500/20 text-green-300",
  Usability: "bg-indigo-500/20 text-indigo-300",
  Positive: "bg-emerald-500/20 text-emerald-300",
  "Edge Case": "bg-pink-500/20 text-pink-300",
};

/* ── Story Breakdown Card ──────────────────────── */
function StoryBreakdown({ breakdown }) {
  if (!breakdown) return null;
  return (
    <Card hover={false}>
      <h3 className="text-xs font-semibold text-white/40 uppercase tracking-wider mb-3 flex items-center gap-1.5">
        <FileText size={12} /> User Story Breakdown
      </h3>
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        {[
          { icon: User, label: "Actor", value: breakdown.actor, color: "text-purple-400" },
          { icon: Target, label: "Action", value: breakdown.action, color: "text-blue-400" },
          { icon: Flag, label: "Goal", value: breakdown.goal, color: "text-emerald-400" },
        ].map((item) => (
          <div key={item.label} className="glass rounded-lg p-3">
            <div className="flex items-center gap-1.5 mb-1">
              <item.icon size={12} className={item.color} />
              <span className="text-[10px] font-semibold text-white/40 uppercase">{item.label}</span>
            </div>
            <p className="text-xs text-white/80">{item.value}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}

/* ── Component Suite Card ──────────────────────── */
function ComponentSuite({ suite, index, addToast }) {
  const [expanded, setExpanded] = useState(true);
  const cases = suite.test_cases || [];

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.08 }}
    >
      <Card>
        <button
          onClick={() => setExpanded(v => !v)}
          className="w-full flex items-center gap-2 text-left"
        >
          <Zap size={14} className="text-brand-purple" />
          <span className="font-semibold text-white text-sm flex-1">{suite.component}</span>
          <span className="text-[10px] px-2 py-0.5 rounded-full bg-brand-purple/20 text-purple-300">
            {cases.length} tests
          </span>
          {expanded ? <ChevronUp size={14} className="text-white/30" /> : <ChevronDown size={14} className="text-white/30" />}
        </button>

        {expanded && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-white/10">
                  <th className="text-left py-2 px-2 text-white/40 font-medium">TC_ID</th>
                  <th className="text-left py-2 px-2 text-white/40 font-medium">Type</th>
                  <th className="text-left py-2 px-2 text-white/40 font-medium min-w-[200px]">Test Scenario</th>
                  <th className="text-left py-2 px-2 text-white/40 font-medium min-w-[180px]">Test Steps</th>
                  <th className="text-left py-2 px-2 text-white/40 font-medium min-w-[160px]">Expected Result</th>
                  <th className="text-left py-2 px-2 text-white/40 font-medium">Priority</th>
                </tr>
              </thead>
              <tbody>
                {cases.map((tc, i) => {
                  const typeKey = tc.test_type || tc.type || "Functional";
                  const typeColor = TYPE_COLORS[typeKey] || "bg-white/10 text-white/60";
                  const priKey = tc.priority || "Medium";
                  const priColor = PRIORITY_COLORS[priKey] || PRIORITY_COLORS.Medium;
                  const steps = tc.test_steps || tc.manual_steps || [];
                  const scenario = tc.test_scenario || tc.condition || "";
                  const expected = tc.expected_result || "";

                  return (
                    <tr key={i} className="border-b border-white/5 hover:bg-white/5 transition-colors">
                      <td className="py-2 px-2 font-mono text-white/50">{tc.tc_id}</td>
                      <td className="py-2 px-2">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${typeColor}`}>{typeKey}</span>
                      </td>
                      <td className="py-2 px-2 text-white/80">{scenario}</td>
                      <td className="py-2 px-2">
                        <ol className="list-decimal list-inside text-white/60 space-y-0.5">
                          {steps.slice(0, 3).map((s, si) => <li key={si} className="truncate">{s}</li>)}
                          {steps.length > 3 && <li className="text-white/30">+{steps.length - 3} more</li>}
                        </ol>
                      </td>
                      <td className="py-2 px-2 text-white/70">{expected}</td>
                      <td className="py-2 px-2">
                        <span className={`px-1.5 py-0.5 rounded text-[10px] font-medium ${priColor}`}>{priKey}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </motion.div>
  );
}

/* ══════════════════════════════════════════════════
   Main Component
   ══════════════════════════════════════════════════ */
export default function SmartTestGenTab() {
  const { 
    globalUrl: url, setGlobalUrl: setUrl, 
    globalQuery: query, setGlobalQuery: setQuery,
    globalUserStory: userStory, setGlobalUserStory: setUserStory,
    globalDepth: depth, setGlobalDepth: setDepth, 
    smartTestOutput: output, setSmartTestOutput: setOutput,
    addToast, setUsageCount 
  } = useStore();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleGenerate() {
    if (!url.trim()) { addToast("Please enter a website URL.", "warning"); return; }
    if (!url.startsWith("http")) { addToast("URL must start with http:// or https://", "warning"); return; }
    setLoading(true); setError(null); setOutput(null);
    try {
      const data = await generateSmartTests(url, query, userStory, depth);
      setOutput(data);
      if (data.usageCount) setUsageCount(data.usageCount);
      if (data.mock) addToast("Showing demo data — AI service unavailable.", "warning");
      else addToast(`Generated ${data.summary?.total || 0} test cases!`, "success");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    if (!output) return;
    try {
      await saveReport("smart-tests", output.test_suites || [], output, { url, query });
      addToast("Test suites saved!", "success");
    } catch (err) { addToast("Failed to save: " + err.message, "error"); }
  }

  function handleDownload() {
    if (!output) return;
    const blob = new Blob([JSON.stringify(output, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "smart_test_suites.json"; a.click();
  }

  function handleCopy() {
    if (!output) return;
    navigator.clipboard.writeText(JSON.stringify(output, null, 2));
    addToast("Copied to clipboard!", "success");
  }

  const summary = output?.summary;

  return (
    <div className="space-y-6">
      {/* ── Inputs ────────────────────────────────── */}
      <Card>
        <div className="flex items-center gap-2 mb-4">
          <Zap size={20} className="text-brand-purple" />
          <h2 className="font-semibold text-white">Smart Test Generator</h2>
          <span className="text-xs text-white/30 ml-auto">Senior QA Level</span>
        </div>

        <div className="space-y-4">
          {/* User Story */}
          <div>
            <label className="block text-xs font-medium text-white/50 mb-2">User Story</label>
            <textarea
              className="input-field h-20 resize-none"
              placeholder="As a user, I want to log into my account so that I can access my dashboard"
              value={userStory}
              onChange={(e) => setUserStory(e.target.value)}
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* URL */}
            <div>
              <label className="block text-xs font-medium text-white/50 mb-2">Website URL</label>
              <div className="relative">
                <Globe size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
                <input type="url" className="input-field pl-9"
                  placeholder="https://example.com/login" value={url}
                  onChange={(e) => setUrl(e.target.value)} />
              </div>
            </div>

            {/* Query */}
            <div>
              <label className="block text-xs font-medium text-white/50 mb-2">Focus Query (optional)</label>
              <input type="text" className="input-field"
                placeholder="Test login, checkout, search..."
                value={query} onChange={(e) => setQuery(e.target.value)} />
            </div>
          </div>

          <div className="flex items-center gap-4">
            <div className="flex-1">
              <label className="block text-xs font-medium text-white/50 mb-1">Depth: {depth}</label>
              <input type="range" min={1} max={2} value={depth}
                onChange={(e) => setDepth(Number(e.target.value))}
                className="w-full accent-brand-purple" />
            </div>
            <Button onClick={handleGenerate} loading={loading} disabled={!url.trim()} icon={Search}>
              Generate Test Suites
            </Button>
          </div>
        </div>
      </Card>

      {/* ── Loading ────────────────────────────────── */}
      {loading && <div className="space-y-4">{[1, 2, 3].map(i => <CardSkeleton key={i} />)}</div>}

      {/* ── Error ──────────────────────────────────── */}
      {error && !loading && (
        <Card>
          <div className="flex gap-3">
            <AlertCircle size={18} className="text-red-400 flex-shrink-0" />
            <div><p className="font-semibold text-red-400">Generation Failed</p><p className="text-sm text-white/50">{error}</p></div>
          </div>
        </Card>
      )}

      {/* ── Results ────────────────────────────────── */}
      {output && !loading && (
        <>
          {/* Story Breakdown */}
          <StoryBreakdown breakdown={output.story_breakdown} />

          {/* Summary Stats */}
          {summary && (
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
              <Card hover={false}>
                <p className="font-display font-bold text-xl text-white">{summary.total}</p>
                <p className="text-white/40 text-[10px]">Total Tests</p>
              </Card>
              <Card hover={false}>
                <p className="font-display font-bold text-xl text-indigo-400">{summary.components}</p>
                <p className="text-white/40 text-[10px]">Components</p>
              </Card>
              {Object.entries(summary.by_priority || {}).map(([key, val]) => (
                <Card key={key} hover={false}>
                  <p className={`font-display font-bold text-xl ${key === "Critical" ? "text-red-400" : key === "High" ? "text-orange-400" : key === "Medium" ? "text-yellow-400" : "text-green-400"}`}>{val}</p>
                  <p className="text-white/40 text-[10px]">{key}</p>
                </Card>
              ))}
            </div>
          )}

          {/* Test Type Distribution */}
          {summary?.by_type && (
            <Card hover={false}>
              <h3 className="text-xs font-semibold text-white/40 uppercase tracking-wider mb-2">Coverage by Test Type</h3>
              <div className="flex flex-wrap gap-2">
                {Object.entries(summary.by_type).map(([type, count]) => (
                  <span key={type} className={`text-xs px-2.5 py-1 rounded-full font-medium ${TYPE_COLORS[type] || "bg-white/10 text-white/60"}`}>
                    {type}: {count}
                  </span>
                ))}
              </div>
            </Card>
          )}

          {/* Actions */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-white">
                {output.components?.length || 0} Component Suites
              </span>
              {output.ai_generated && <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/20 text-emerald-300">AI Generated</span>}
              {output.mock && <span className="badge badge-free">Demo Data</span>}
            </div>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" onClick={handleCopy} icon={Copy}>Copy</Button>
              <Button variant="secondary" size="sm" onClick={handleDownload} icon={Download}>JSON</Button>
              <Button variant="secondary" size="sm" onClick={handleSave} icon={Save}>Save</Button>
            </div>
          </div>

          {/* Component Test Suites */}
          {output.test_suites?.map((suite, i) => (
            <ComponentSuite key={suite.component + i} suite={suite} index={i} addToast={addToast} />
          ))}
        </>
      )}

      {/* ── Empty State ────────────────────────────── */}
      {!output && !loading && !error && (
        <div className="glass rounded-2xl p-12 text-center">
          <Zap size={40} className="mx-auto text-white/20 mb-3" />
          <p className="text-white/40 text-sm">Component-grouped test suites will appear here</p>
          <p className="text-white/25 text-xs mt-1">8 testing types • Priority-based • Senior QA methodology</p>
        </div>
      )}
    </div>
  );
}
