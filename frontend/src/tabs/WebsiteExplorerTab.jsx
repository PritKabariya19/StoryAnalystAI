import { useState } from "react";
import { motion } from "framer-motion";
import {
  Globe, Search, AlertCircle, CheckCircle, Copy, Download, Save,
  ExternalLink, ChevronDown, ChevronUp, Type, Hash, MousePointer,
  FormInput, ToggleLeft, Link2,
} from "lucide-react";
import { exploreWebsite, saveReport } from "../services/api";
import useStore from "../state/store";
import Button from "../components/Button";
import Card from "../components/Card";
import { CardSkeleton } from "../components/LoadingSkeleton";

/* ── Type badge color mapping ────────────────────────── */
const TYPE_COLORS = {
  text:     "bg-blue-500/20 text-blue-300",
  email:    "bg-cyan-500/20 text-cyan-300",
  password: "bg-red-500/20 text-red-300",
  number:   "bg-amber-500/20 text-amber-300",
  tel:      "bg-green-500/20 text-green-300",
  url:      "bg-indigo-500/20 text-indigo-300",
  search:   "bg-violet-500/20 text-violet-300",
  checkbox: "bg-pink-500/20 text-pink-300",
  radio:    "bg-pink-500/20 text-pink-300",
  select:   "bg-orange-500/20 text-orange-300",
  textarea: "bg-teal-500/20 text-teal-300",
  date:     "bg-lime-500/20 text-lime-300",
  file:     "bg-rose-500/20 text-rose-300",
  button:   "bg-purple-500/20 text-purple-300",
  submit:   "bg-emerald-500/20 text-emerald-300",
};

/* ── Copy to clipboard helper ────────────────────────── */
function CopyBtn({ text, addToast }) {
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); addToast?.("Copied!", "success"); }}
      className="text-white/20 hover:text-white/60 transition-colors p-0.5"
      title="Copy selector"
    >
      <Copy size={11} />
    </button>
  );
}

/* ── Single field row ────────────────────────────────── */
function FieldRow({ field, addToast }) {
  const typeColor = TYPE_COLORS[field.type] || "bg-white/10 text-white/60";
  return (
    <div className="flex items-center gap-2 py-1.5 px-2 rounded-lg hover:bg-white/5 transition-colors group text-xs">
      <FormInput size={12} className="text-white/30 flex-shrink-0" />
      <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-medium flex-shrink-0 ${typeColor}`}>
        {field.type}
      </span>
      <span className="text-white/80 font-medium truncate" title={field.name}>
        {field.name}
      </span>
      {field.required && <span className="text-red-400 text-[10px] font-bold flex-shrink-0">*REQ</span>}
      {field.placeholder && (
        <span className="text-white/25 text-[10px] truncate hidden sm:inline" title={field.placeholder}>
          "{field.placeholder}"
        </span>
      )}
      <span className="ml-auto font-mono text-white/20 text-[10px] truncate hidden md:inline group-hover:text-white/40" title={field.selector}>
        {field.selector}
      </span>
      <CopyBtn text={field.selector} addToast={addToast} />
    </div>
  );
}

/* ── Button row ──────────────────────────────────────── */
function ButtonRow({ btn, addToast }) {
  return (
    <div className="flex items-center gap-2 py-1.5 px-2 rounded-lg hover:bg-white/5 transition-colors group text-xs">
      <MousePointer size={12} className="text-purple-400/50 flex-shrink-0" />
      <span className="px-1.5 py-0.5 rounded text-[10px] font-mono font-medium bg-purple-500/20 text-purple-300 flex-shrink-0">
        {btn.type}
      </span>
      <span className="text-white/80 font-medium truncate">"{btn.text}"</span>
      {btn.id && <span className="font-mono text-white/20 text-[10px] hidden md:inline">#{btn.id}</span>}
      <span className="ml-auto font-mono text-white/20 text-[10px] hidden md:inline group-hover:text-white/40">{btn.selector}</span>
      <CopyBtn text={btn.selector} addToast={addToast} />
    </div>
  );
}

/* ── Form card (collapsible) ─────────────────────────── */
function FormCard({ form, index, addToast }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.05 }}
      className="glass rounded-xl overflow-hidden"
    >
      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-white/5 transition-colors"
      >
        <FormInput size={14} className="text-indigo-400" />
        <span className="text-xs font-semibold text-white flex-1 truncate">
          {form.name || "form"}
        </span>
        <span className="text-[10px] text-white/30 font-mono">{form.method}</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300">
          {form.fields?.length || 0} fields
        </span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300">
          {form.buttons?.length || 0} buttons
        </span>
        {expanded ? <ChevronUp size={13} className="text-white/30" /> : <ChevronDown size={13} className="text-white/30" />}
      </button>
      {expanded && (
        <div className="px-3 pb-3 space-y-0.5 border-t border-white/5">
          {form.action && (
            <p className="text-[10px] text-white/25 pt-1.5 font-mono truncate" title={form.action}>
              action="{form.action}"
            </p>
          )}
          {form.fields?.map((f, i) => <FieldRow key={`${f.name}-${i}`} field={f} addToast={addToast} />)}
          {form.buttons?.map((b, i) => <ButtonRow key={`${b.text}-${i}`} btn={b} addToast={addToast} />)}
        </div>
      )}
    </motion.div>
  );
}

/* ── Standalone input card ───────────────────────────── */
function StandaloneCard({ inputs, addToast }) {
  const [expanded, setExpanded] = useState(true);
  if (!inputs?.length) return null;
  return (
    <div className="glass rounded-xl overflow-hidden">
      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center gap-2 px-3 py-2.5 text-left hover:bg-white/5 transition-colors"
      >
        <ToggleLeft size={14} className="text-amber-400" />
        <span className="text-xs font-semibold text-white flex-1">Standalone Elements</span>
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/20 text-amber-300">
          {inputs.length} elements
        </span>
        {expanded ? <ChevronUp size={13} className="text-white/30" /> : <ChevronDown size={13} className="text-white/30" />}
      </button>
      {expanded && (
        <div className="px-3 pb-3 space-y-0.5 border-t border-white/5">
          <p className="text-[10px] text-white/25 pt-1.5 italic">Interactive elements outside of any &lt;form&gt; tag</p>
          {inputs.map((el, i) => {
            const typeColor = TYPE_COLORS[el.type] || TYPE_COLORS[el.tag] || "bg-white/10 text-white/60";
            return (
              <div key={i} className="flex items-center gap-2 py-1.5 px-2 rounded-lg hover:bg-white/5 transition-colors group text-xs">
                <Hash size={12} className="text-white/30 flex-shrink-0" />
                <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-medium flex-shrink-0 ${typeColor}`}>
                  {el.tag === "button" ? "btn" : el.type}
                </span>
                <span className="text-white/80 font-medium truncate">
                  {el.name || el.text || el.id || el.placeholder || el.tag}
                </span>
                {el.required && <span className="text-red-400 text-[10px] font-bold flex-shrink-0">*REQ</span>}
                {el.placeholder && (
                  <span className="text-white/25 text-[10px] truncate hidden sm:inline">"{el.placeholder}"</span>
                )}
                <span className="ml-auto font-mono text-white/20 text-[10px] hidden md:inline group-hover:text-white/40">{el.selector}</span>
                <CopyBtn text={el.selector} addToast={addToast} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ══════════════════════════════════════════════════════════
   Main Component
   ══════════════════════════════════════════════════════════ */
export default function WebsiteExplorerTab() {
  const { 
    globalUrl: url, setGlobalUrl: setUrl, 
    globalDepth: depth, setGlobalDepth: setDepth, 
    explorerOutput: output, setExplorerOutput: setOutput,
    addToast, setUsageCount 
  } = useStore();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  async function handleExplore() {
    if (!url.trim()) { addToast("Please enter a website URL.", "warning"); return; }
    if (!url.startsWith("http")) { addToast("URL must start with http:// or https://", "warning"); return; }
    setLoading(true); setError(null); setOutput(null);
    try {
      const data = await exploreWebsite(url, depth);
      setOutput(data);
      if (data.usageCount) setUsageCount(data.usageCount);
      if (data.mock) addToast("Showing demo data — explorer unavailable.", "warning");
      else addToast("Website explored successfully!", "success");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    if (!output) return;
    try {
      await saveReport("explorer", output.pages || [], output, { url });
      addToast("Exploration report saved!", "success");
    } catch (err) { addToast("Failed to save: " + err.message, "error"); }
  }

  function handleDownload() {
    if (!output) return;
    const blob = new Blob([JSON.stringify(output, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "website_exploration.json"; a.click();
  }

  // Count totals for stats
  const totalForms = output?.pages?.reduce((s, p) => s + (p.forms?.length || 0), 0) || 0;
  const totalFields = output?.pages?.reduce((s, p) => s + (p.forms || []).reduce((fs, f) => fs + (f.fields?.length || 0), 0), 0) || 0;
  const totalButtons = output?.pages?.reduce((s, p) => s + (p.forms || []).reduce((fs, f) => fs + (f.buttons?.length || 0), 0), 0) || 0;
  const totalStandalone = output?.pages?.reduce((s, p) => s + (p.standalone_inputs?.length || 0), 0) || 0;
  const totalLinks = output?.pages?.reduce((s, p) => s + (p.links?.length || 0), 0) || 0;

  return (
    <div className="space-y-6">
      {/* ── Input ──────────────────────────────────────── */}
      <Card>
        <div className="flex items-center gap-2 mb-4">
          <Globe size={20} className="text-brand-purple" />
          <h2 className="font-semibold text-white">Website Explorer</h2>
          <span className="text-xs text-white/30 ml-auto">Web Inspect Mode</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-medium text-white/50 mb-2" htmlFor="explore-url">Website URL</label>
            <div className="relative">
              <Globe size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
              <input
                id="explore-url" type="url" className="input-field pl-9"
                placeholder="https://example.com" value={url}
                onChange={(e) => setUrl(e.target.value)}
              />
            </div>
          </div>
          <div>
            <label className="block text-xs font-medium text-white/50 mb-2">Crawl Depth: {depth}</label>
            <input type="range" min={1} max={2} value={depth}
              onChange={(e) => setDepth(Number(e.target.value))}
              className="w-full accent-brand-purple" />
            <div className="flex justify-between text-xs text-white/30 mt-1">
              <span>Surface (1 page)</span><span>Deep (2 levels)</span>
            </div>
          </div>
        </div>

        <Button onClick={handleExplore} loading={loading} disabled={!url.trim()} className="mt-4" icon={Search}>
          Explore &amp; Inspect Website
        </Button>
      </Card>

      {/* ── Loading ────────────────────────────────────── */}
      {loading && <div className="space-y-4">{[1, 2].map((i) => <CardSkeleton key={i} />)}</div>}

      {/* ── Error ──────────────────────────────────────── */}
      {error && !loading && (
        <Card>
          <div className="flex gap-3">
            <AlertCircle size={18} className="text-red-400 flex-shrink-0" />
            <div><p className="font-semibold text-red-400">Exploration Failed</p><p className="text-sm text-white/50">{error}</p></div>
          </div>
        </Card>
      )}

      {/* ── Results ────────────────────────────────────── */}
      {output && !loading && (
        <>
          {/* Summary stats */}
          <div className="grid grid-cols-3 sm:grid-cols-6 gap-3">
            {[
              { label: "Pages", value: output.pages?.length || 0, color: "text-white" },
              { label: "Forms", value: totalForms, color: "text-indigo-400" },
              { label: "Fields", value: totalFields, color: "text-blue-400" },
              { label: "Buttons", value: totalButtons, color: "text-purple-400" },
              { label: "Standalone", value: totalStandalone, color: "text-amber-400" },
              { label: "Links", value: totalLinks, color: "text-cyan-400" },
            ].map((s) => (
              <Card key={s.label} hover={false}>
                <p className={`font-display font-bold text-xl ${s.color}`}>{s.value}</p>
                <p className="text-white/40 text-[10px]">{s.label}</p>
              </Card>
            ))}
          </div>

          {/* Actions */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-white">{output.pages?.length} pages inspected</span>
              {output.mock && <span className="badge badge-free">Demo Data</span>}
            </div>
            <div className="flex gap-2">
              <Button variant="secondary" size="sm" onClick={handleDownload} icon={Download}>JSON</Button>
              <Button variant="secondary" size="sm" onClick={handleSave} icon={Save}>Save</Button>
            </div>
          </div>

          {/* Pages */}
          {output.pages?.map((page, pi) => (
            <motion.div key={pi} initial={{ opacity: 0, y: 15 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: pi * 0.1 }}>
              <Card>
                {/* Page header */}
                <div className="flex items-center gap-2 mb-3">
                  <Globe size={14} className="text-brand-purple" />
                  <span className="font-semibold text-white text-sm truncate">{page.title || "Page"}</span>
                  <a href={page.url} target="_blank" rel="noopener noreferrer" className="ml-auto text-white/30 hover:text-white/60">
                    <ExternalLink size={13} />
                  </a>
                </div>
                <p className="text-[10px] text-white/25 mb-3 font-mono truncate">{page.url}</p>

                <div className="space-y-2">
                  {/* Forms with full field detail */}
                  {page.forms?.map((form, fi) => (
                    <FormCard key={`${form.name}-${fi}`} form={form} index={fi} addToast={addToast} />
                  ))}

                  {/* Standalone inputs */}
                  {page.standalone_inputs?.length > 0 && (
                    <StandaloneCard inputs={page.standalone_inputs} addToast={addToast} />
                  )}

                  {/* Links summary */}
                  {page.links?.length > 0 && (
                    <details className="glass rounded-xl overflow-hidden group">
                      <summary className="flex items-center gap-2 px-3 py-2.5 cursor-pointer hover:bg-white/5 transition-colors">
                        <Link2 size={14} className="text-cyan-400" />
                        <span className="text-xs font-semibold text-white flex-1">Links</span>
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/20 text-cyan-300">
                          {page.links.length}
                        </span>
                      </summary>
                      <div className="px-3 pb-3 border-t border-white/5 space-y-0.5">
                        {page.links.slice(0, 15).map((link, li) => (
                          <div key={li} className="flex items-center gap-2 py-1 px-2 rounded-lg hover:bg-white/5 text-xs">
                            <ExternalLink size={10} className="text-white/20 flex-shrink-0" />
                            <span className="text-white/70 truncate">{link.text}</span>
                            <span className="ml-auto text-white/20 font-mono text-[10px] truncate hidden md:inline">{link.href}</span>
                          </div>
                        ))}
                        {page.links.length > 15 && (
                          <p className="text-[10px] text-white/25 text-center pt-1">+{page.links.length - 15} more</p>
                        )}
                      </div>
                    </details>
                  )}

                  {/* No interactive elements message */}
                  {!page.forms?.length && !page.standalone_inputs?.length && (
                    <p className="text-xs text-white/30 italic py-2">No interactive input elements found on this page</p>
                  )}
                </div>
              </Card>
            </motion.div>
          ))}
        </>
      )}

      {/* ── Empty state ────────────────────────────────── */}
      {!output && !loading && !error && (
        <div className="glass rounded-2xl p-12 text-center">
          <Globe size={40} className="mx-auto text-white/20 mb-3" />
          <p className="text-white/40 text-sm">Web inspection results will appear here</p>
          <p className="text-white/25 text-xs mt-1">All forms, inputs, buttons, and interactive elements</p>
        </div>
      )}
    </div>
  );
}
