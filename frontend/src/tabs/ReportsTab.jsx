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
    // jsPDF's built-in fonts are Latin-1 only: strip characters they can't draw
    const pdfText = (v) => String(v ?? "").replace(/[\u2013\u2014]/g, "-").replace(/[^\x20-\x7E\xA0-\xFF]/g, "");
    doc.text("StoryAnalyst AI - Test Report", 14, 20);

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

      const s = report.results.reduce((acc, r) => {
        acc[r.status === "Pass" ? "p" : r.status === "Fail" ? "f" : "e"]++; return acc;
      }, { p: 0, f: 0, e: 0 });
      doc.text(`Passed: ${s.p}   Failed: ${s.f}   Errors: ${s.e}`, 14, 56);

      let y = 72;
      report.results.forEach((r, i) => {
        const id     = r.tc_id || r.testId || r.id || `#${i + 1}`;
        const title  = r.condition || r.title || r.feature || "";
        const status = r.status || "-";
        doc.setTextColor(status === "Pass" ? 0 : 200, status === "Pass" ? 150 : status === "Fail" ? 0 : 120, 0);
        const lines = doc.splitTextToSize(pdfText(`${i + 1}. [${status}] ${id}  ${title}`), 180);
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
    if (!Array.isArray(report.results)) {
      addToast("No tabular data to export.", "warning");
      return;
    }
    // Union of keys across ALL rows (not just the first one)
    const headers = [...new Set(report.results.flatMap((r) => Object.keys(r || {})))];
    // RFC 4180 quoting: wrap in quotes, double any inner quotes; objects → JSON
    const cell = (v) => {
      if (v === null || v === undefined) return "";
      const str = typeof v === "object" ? JSON.stringify(v) : String(v);
      return /[",\r\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
    };
    const rows = report.results.map((r) => headers.map((h) => cell(r?.[h])).join(","));
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
