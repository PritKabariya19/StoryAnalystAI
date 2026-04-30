import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import { FileText, Download, Trash2, RefreshCw, Filter, CheckCircle, XCircle, AlertCircle } from "lucide-react";
import { listReports, deleteReport, generateHtmlReport } from "../services/api";
import useStore from "../state/store";
import Button from "../components/Button";
import Card from "../components/Card";
import { TableRowSkeleton } from "../components/LoadingSkeleton";
import jsPDF from "jspdf";

const TYPE_LABELS = { story: "User Stories", explorer: "Website Explorer", combined: "Combined", test: "Test Execution" };

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
    doc.text("StoryAnalyst AI — Test Report", 14, 20);

    doc.setFontSize(11);
    doc.setFont("helvetica", "normal");
    doc.setTextColor(100);
    doc.text(`Type: ${TYPE_LABELS[report.type] || report.type}`, 14, 32);
    doc.text(`Date: ${new Date(report.createdAt).toLocaleDateString()}`, 14, 40);
    doc.text(`Total Results: ${Array.isArray(report.results) ? report.results.length : "-"}`, 14, 48);

    if (Array.isArray(report.results)) {
      doc.setTextColor(0);
      doc.setFontSize(12);
      doc.setFont("helvetica", "bold");
      doc.text("Results", 14, 62);
      doc.setFontSize(10);
      doc.setFont("helvetica", "normal");

      let y = 72;
      report.results.slice(0, 35).forEach((r, i) => {
        const title = r.title || r.id || `Result ${i + 1}`;
        const status = r.status || "—";
        doc.setTextColor(status === "Pass" ? 0 : 200, status === "Pass" ? 150 : 0, 0);
        doc.text(`${i + 1}. [${status}] ${title.slice(0, 70)}`, 14, y);
        y += 8;
        if (y > 280) { doc.addPage(); y = 20; }
      });
    }

    doc.save(`report-${report.id}.pdf`);
    addToast("PDF downloaded!", "success");
  }

  function exportCSV(report) {
    if (!Array.isArray(report.results)) {
      addToast("No tabular data to export.", "warning");
      return;
    }
    const headers = Object.keys(report.results[0] || {});
    const rows = report.results.map((r) => headers.map((h) => JSON.stringify(r[h] ?? "")).join(","));
    const csv = [headers.join(","), ...rows].join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `report-${report.id}.csv`;
    a.click();
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
      addToast("HTML Generation Failed: " + err.message, "error");
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
                        {Array.isArray(report.results) ? report.results.length : "—"}
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
                <div className="flex justify-between"><span className="text-white/40">Total Results</span><span className="text-white">{Array.isArray(selected.results) ? selected.results.length : "—"}</span></div>
                {/* Pass / Fail breakdown */}
                {Array.isArray(selected.results) && (() => {
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
                <Button variant="primary" size="sm" onClick={() => exportHTML(selected)} icon={Download}>Export HTML</Button>
                <Button variant="secondary" size="sm" onClick={() => exportPDF(selected)} icon={Download}>Export PDF</Button>
                <Button variant="secondary" size="sm" onClick={() => exportCSV(selected)} icon={FileText}>Export CSV</Button>
                <Button variant="danger" size="sm" onClick={() => handleDelete(selected.id)} icon={Trash2}>Delete Report</Button>
              </div>

              {/* Per-result list */}
              {Array.isArray(selected.results) && selected.results.length > 0 && (
                <div>
                  <p className="text-xs font-semibold text-white/40 uppercase tracking-wider mb-2">Test Results</p>
                  <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
                    {selected.results.map((r, i) => {
                      const tcId   = r.tc_id || r.testId || r.id || `#${i + 1}`;
                      const title  = r.title || r.condition || r.description || tcId;
                      const status = r.status || "—";
                      const time   = r.duration_seconds || r.execTime || null;
                      const statusColor =
                        status === "Pass"  ? "text-emerald-400 bg-emerald-500/10" :
                        status === "Fail"  ? "text-red-400 bg-red-500/10" :
                        status === "Error" ? "text-amber-400 bg-amber-500/10" :
                        "text-white/40 bg-white/5";
                      return (
                        <div key={i} className="flex items-start gap-2 text-xs p-2 rounded-lg bg-white/3 hover:bg-white/5 transition-colors">
                          <span className={`mt-0.5 flex-shrink-0 px-1.5 py-0.5 rounded-full font-semibold ${statusColor}`}>{status}</span>
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
