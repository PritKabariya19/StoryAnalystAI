import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { FileText, Download, Trash2, RefreshCw, Filter, CheckCircle, XCircle, AlertCircle } from "lucide-react";
import { listReports, deleteReport, generateHtmlReport } from "../services/api";
import useStore from "../state/store";
import Button from "../components/Button";
import Card from "../components/Card";
import { TableRowSkeleton } from "../components/LoadingSkeleton";
import jsPDF from "jspdf";

const TYPE_LABELS = { story: "User Stories", explorer: "Website Explorer", "smart-tests": "Smart Tests", test: "Test Execution" };

// Only test runs have a Pass / Fail / Error status. Other reports are lists.
const isTestRun = (report) => report.type === "test";

/** The report's individual items. Smart-test reports are saved grouped by component. */
function reportRows(report) {
  if (!Array.isArray(report.results)) return null;
  if (report.type !== "smart-tests") return report.results;
  return report.results.flatMap((suite) =>
    (suite?.test_cases || []).map((tc) => ({ ...tc, component: suite.component })));
}

const rowTitle = (r) =>
  r.condition || r.title || r.test_scenario || r.url || r.description || r.feature || "";

export default function ReportsTab() {
  const [reports, setReports] = useState([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState(null);
  const { addToast } = useStore();

  async function fetchReports() {
    setLoading(true);
    try {
      const data = await listReports();
      setReports(Array.isArray(data?.reports) ? data.reports : []);
      if (data?.warning) addToast(data.warning, "warning");
    } catch (err) {
      addToast("Failed to load reports: " + err.message, "error");
      setReports([]);
    } finally {
      setLoading(false);
    }
  }

  // Fetch on mount and whenever the tab regains focus
  useEffect(() => {
    fetchReports();
    const onFocus = () => fetchReports();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, []);

  async function handleDelete(id) {
    try {
      await deleteReport(id);
      setReports((r) => r.filter((rep) => rep.id !== id));
      if (selected?.id === id) setSelected(null);
      addToast("Report deleted.", "info");
    } catch (err) {
      addToast("Delete failed: " + err.message, "error");
    }
  }

  function exportPDF(report) {
    const doc = new jsPDF();
    doc.setFontSize(18);
    doc.setFont("helvetica", "bold");
    // jsPDF's built-in fonts are Latin-1 only: strip characters they can't draw
    const pdfText = (v) => String(v ?? "").replace(/[\u2013\u2014]/g, "-").replace(/[^\x20-\x7E\xA0-\xFF]/g, "");
    doc.text("StoryAnalyst AI - Report", 14, 20);
    const rows = reportRows(report);

    doc.setFontSize(11);
    doc.setFont("helvetica", "normal");
    doc.setTextColor(100);
    doc.text(`Type: ${TYPE_LABELS[report.type] || report.type}`, 14, 32);
    doc.text(`Date: ${new Date(report.createdAt).toLocaleDateString()}`, 14, 40);
    doc.text(`Total Results: ${rows ? rows.length : "-"}`, 14, 48);

    if (rows) {
      doc.setTextColor(0);
      doc.setFontSize(12);
      doc.setFont("helvetica", "bold");
      doc.text("Results", 14, 62);
      doc.setFontSize(10);
      doc.setFont("helvetica", "normal");

      if (isTestRun(report)) {
        const s = rows.reduce((acc, r) => {
          acc[r.status === "Pass" ? "p" : r.status === "Fail" ? "f" : "e"]++; return acc;
        }, { p: 0, f: 0, e: 0 });
        doc.text(`Passed: ${s.p}   Failed: ${s.f}   Errors: ${s.e}`, 14, 56);
      }

      let y = 72;
      rows.forEach((r, i) => {
        const id     = r.tc_id || r.testId || r.id || "";
        const status = isTestRun(report) ? r.status || "-" : null;
        if (status) doc.setTextColor(status === "Pass" ? 0 : 200, status === "Pass" ? 150 : status === "Fail" ? 0 : 120, 0);
        else doc.setTextColor(0);
        const label = [status && `[${status}]`, id, rowTitle(r)].filter(Boolean).join("  ");
        const lines = doc.splitTextToSize(pdfText(`${i + 1}. ${label}`), 180);
        const err = r.error_message || r.error;
        const errLines = err && status !== "Pass" ? doc.splitTextToSize(pdfText(`   ${err}`), 175) : [];
        if (y + (lines.length + errLines.length) * 6 > 285) { doc.addPage(); y = 20; }
        doc.text(lines, 14, y);
        y += lines.length * 6;
        if (errLines.length) {
          doc.setTextColor(110);
          doc.setFontSize(8);
          doc.text(errLines, 14, y);
          doc.setFontSize(10);
          y += errLines.length * 5;
        }
        y += 2;
      });
    }

    doc.save(`report-${report.id}.pdf`);
    addToast("PDF downloaded!", "success");
  }

  function exportCSV(report) {
    const items = reportRows(report);
    if (!items) {
      addToast("No tabular data to export.", "warning");
      return;
    }
    // Union of keys across ALL rows (not just the first one)
    const headers = [...new Set(items.flatMap((r) => Object.keys(r || {})))];
    // RFC 4180 quoting: wrap in quotes, double any inner quotes; objects → JSON
    const cell = (v) => {
      if (v === null || v === undefined) return "";
      const str = typeof v === "object" ? JSON.stringify(v) : String(v);
      return /[",\r\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
    };
    const rows = items.map((r) => headers.map((h) => cell(r?.[h])).join(","));
    // BOM so Excel opens UTF-8 (emoji/accents) correctly
    const csv = "\uFEFF" + [headers.map(cell).join(","), ...rows].join("\r\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `report-${report.id}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    addToast("CSV downloaded!", "success");
  }

  async function exportHTML(report) {
    if (!report.results) {
      addToast("No results available to generate HTML.", "warning");
      return;
    }
    try {
      addToast("Generating HTML Report...", "info");
      const htmlContent = await generateHtmlReport(report.results, report.summary || {});
      // Create a blob and trigger download via anchor click (works in all browsers)
      const blob = new Blob([htmlContent], { type: "text/html" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `storyanalyst_report_${report.id}.html`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      addToast("HTML Report downloaded successfully!", "success");
    } catch (err) {
      let msg = err.response?.data?.error || err.message;
      if (typeof err.response?.data === "string") {
        try { msg = JSON.parse(err.response.data).error || msg; } catch { /* keep msg */ }
      }
      addToast("HTML Generation Failed: " + msg, "error");
    }
  }

  const filteredReports = filter === "all" ? reports : reports.filter((r) => r.type === filter);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h2 className="font-semibold text-white">Your Reports</h2>
          <p className="text-white/40 text-sm">{reports.length} total reports saved</p>
        </div>
        <div className="flex items-center gap-2">
          {/* Filter */}
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="input-field py-2 text-xs w-40"
          >
            <option value="all">All Types</option>
            {Object.entries(TYPE_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
          <Button variant="secondary" size="sm" onClick={fetchReports} loading={loading} icon={RefreshCw}>Refresh</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        {/* Table */}
        <div className="xl:col-span-2">
          <div className="glass rounded-2xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-surface-border">
                    {["Type", "Date", "Results", "Actions"].map((h) => (
                      <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-white/40 uppercase tracking-wider">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {loading && [1, 2, 3].map((i) => <TableRowSkeleton key={i} cols={4} />)}
                  {!loading && filteredReports.length === 0 && (
                    <tr>
                      <td colSpan={4} className="px-4 py-12 text-center text-white/30">
                        No reports yet. Generate stories or run tests to create reports.
                      </td>
                    </tr>
                  )}
                  {!loading && filteredReports.map((report, i) => (
                    <motion.tr
                      key={report.id}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      transition={{ delay: i * 0.05 }}
                      onClick={() => setSelected(report)}
                      className={`border-b border-surface-border cursor-pointer transition-colors hover:bg-white/5 ${selected?.id === report.id ? "bg-white/8" : ""}`}
                    >
                      <td className="px-4 py-3">
                        <span className="badge badge-free">{TYPE_LABELS[report.type] || report.type}</span>
                      </td>
                      <td className="px-4 py-3 text-white/50 text-xs">
                        {report.createdAt ? new Date(report.createdAt).toLocaleDateString() : "—"}
                      </td>
                      <td className="px-4 py-3 text-white/70">
                        {reportRows(report)?.length ?? "—"}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1" onClick={(e) => e.stopPropagation()}>
                          <button onClick={() => exportPDF(report)} className="p-1.5 rounded-lg text-white/40 hover:text-white hover:bg-white/10 transition-all" title="Export PDF">
                            <Download size={14} />
                          </button>
                          <button onClick={() => handleDelete(report.id)} className="p-1.5 rounded-lg text-white/40 hover:text-red-400 hover:bg-red-500/10 transition-all" title="Delete">
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </motion.tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        {/* Detail Panel */}
        <div>
          {selected ? (
            <Card>
              <h3 className="font-semibold text-white mb-3">Report Detail</h3>
              <div className="space-y-2 text-sm mb-4">
                <div className="flex justify-between"><span className="text-white/40">Type</span><span className="text-white">{TYPE_LABELS[selected.type] || selected.type}</span></div>
                <div className="flex justify-between"><span className="text-white/40">Date</span><span className="text-white">{selected.createdAt ? new Date(selected.createdAt).toLocaleString() : "—"}</span></div>
                <div className="flex justify-between"><span className="text-white/40">Total Results</span><span className="text-white">{reportRows(selected)?.length ?? "—"}</span></div>
                {/* Pass / Fail breakdown */}
                {isTestRun(selected) && Array.isArray(selected.results) && (() => {
                  const p = selected.results.filter(r => r.status === "Pass").length;
                  const f = selected.results.filter(r => r.status === "Fail" || r.status === "Error").length;
                  const rate = selected.results.length > 0 ? Math.round(p / selected.results.length * 100) : 0;
                  return (
                    <>
                      <div className="flex justify-between"><span className="text-white/40">Passed</span><span className="text-emerald-400 font-semibold">{p}</span></div>
                      <div className="flex justify-between"><span className="text-white/40">Failed / Errors</span><span className="text-red-400 font-semibold">{f}</span></div>
                      <div className="flex justify-between"><span className="text-white/40">Pass Rate</span><span className={`font-semibold ${rate >= 80 ? "text-emerald-400" : rate >= 50 ? "text-amber-400" : "text-red-400"}`}>{rate}%</span></div>
                    </>
                  );
                })()}
              </div>
              <div className="flex flex-col gap-2 mb-4">
                {isTestRun(selected) && (
                  <Button variant="primary" size="sm" onClick={() => exportHTML(selected)} icon={Download}>Export HTML</Button>
                )}
                <Button variant="secondary" size="sm" onClick={() => exportPDF(selected)} icon={Download}>Export PDF</Button>
                <Button variant="secondary" size="sm" onClick={() => exportCSV(selected)} icon={FileText}>Export CSV</Button>
                <Button variant="danger" size="sm" onClick={() => handleDelete(selected.id)} icon={Trash2}>Delete Report</Button>
              </div>

              {/* Per-result list */}
              {reportRows(selected)?.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-white/40 uppercase tracking-wider mb-2">{isTestRun(selected) ? "Test Results" : "Items"}</p>
                  <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
                    {reportRows(selected).map((r, i) => {
                      const tcId   = r.tc_id || r.testId || r.id || `#${i + 1}`;
                      const title  = rowTitle(r) || tcId;
                      const status = r.status || "—";
                      const time   = r.duration_seconds || r.execTime || null;
                      const statusColor =
                        status === "Pass"  ? "text-emerald-400 bg-emerald-500/10" :
                        status === "Fail"  ? "text-red-400 bg-red-500/10" :
                        status === "Error" ? "text-amber-400 bg-amber-500/10" :
                        "text-white/40 bg-white/5";
                      return (
                        <div key={i} className="flex items-start gap-2 text-xs p-2 rounded-lg bg-white/3 hover:bg-white/5 transition-colors">
                          {isTestRun(selected) && (
                            <span className={`mt-0.5 flex-shrink-0 px-1.5 py-0.5 rounded-full font-semibold ${statusColor}`}>{status}</span>
                          )}
                          <span className="flex-1 text-white/70 line-clamp-2" title={title}>{title}</span>
                          {time != null && <span className="text-white/30 flex-shrink-0">{Number(time).toFixed(1)}s</span>}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </Card>
          ) : (
            <div className="glass rounded-2xl p-8 text-center">
              <FileText size={32} className="mx-auto text-white/20 mb-3" />
              <p className="text-white/40 text-sm">Select a report to view details</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
