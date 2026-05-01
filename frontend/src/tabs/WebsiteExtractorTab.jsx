import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Search, Globe, AlertCircle, CheckCircle, Download, Save,
  ChevronDown, ChevronRight, Mail, Phone, MapPin, Image,
  Link2, FileText, Table2, Code2, Sparkles, ExternalLink,
} from "lucide-react";
import { extractWebsiteInfo, saveReport } from "../services/api";
import useStore from "../state/store";
import Button from "../components/Button";
import Card from "../components/Card";
import { CardSkeleton } from "../components/LoadingSkeleton";

/* ── Collapsible section ──────────────────────────────────────── */
function Section({ title, icon: Icon, count, children, defaultOpen = false }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Card hover={false}>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-2 text-left"
      >
        {open ? <ChevronDown size={14} className="text-white/40" /> : <ChevronRight size={14} className="text-white/40" />}
        <Icon size={16} className="text-brand-purple" />
        <span className="font-semibold text-white text-sm flex-1">{title}</span>
        {count !== undefined && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-white/10 text-white/50">{count}</span>
        )}
      </button>
      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.22 }}
            className="overflow-hidden"
          >
            <div className="mt-4 space-y-3">{children}</div>
          </motion.div>
        )}
      </AnimatePresence>
    </Card>
  );
}

/* ── Tag badge ────────────────────────────────────────────────── */
function Tag({ children, color = "purple" }) {
  const colors = {
    purple: "bg-purple-500/20 text-purple-300",
    blue: "bg-blue-500/20 text-blue-300",
    green: "bg-emerald-500/20 text-emerald-300",
    amber: "bg-amber-500/20 text-amber-300",
    red: "bg-red-500/20 text-red-300",
  };
  return (
    <span className={`text-xs px-2.5 py-1 rounded-full ${colors[color] || colors.purple}`}>
      {children}
    </span>
  );
}

/* ══════════════════════════════════════════════════════════════════
   Main Component
   ══════════════════════════════════════════════════════════════════ */
export default function WebsiteExtractorTab() {
  const [url, setUrl] = useState("");
  const [query, setQuery] = useState("");
  const [depth, setDepth] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [output, setOutput] = useState(null);
  const { addToast, setUsageCount } = useStore();

  /* ── Actions ──────────────────────────────────────────────────── */
  async function handleExtract() {
    if (!url.trim()) { addToast("Please enter a website URL.", "warning"); return; }
    if (!url.startsWith("http")) { addToast("URL must start with http:// or https://", "warning"); return; }
    setLoading(true); setError(null); setOutput(null);
    try {
      const data = await extractWebsiteInfo(url, query, depth);
      setOutput(data);
      if (data.usageCount) setUsageCount(data.usageCount);
      if (data.mock) addToast("Showing demo data — extractor unavailable.", "warning");
      else addToast("Website info extracted successfully!", "success");
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    if (!output) return;
    try {
      await saveReport("extractor", output.aggregated || {}, output, { url, query });
      addToast("Extraction report saved!", "success");
    } catch (err) {
      addToast("Failed to save: " + err.message, "error");
    }
  }

  function handleDownload() {
    if (!output) return;
    const blob = new Blob([JSON.stringify(output, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
    a.download = "website_extraction.json"; a.click();
  }

  const agg = output?.aggregated || {};

  /* ══════════════════════════════════════════════════════════════ */
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
      {/* ── Left: Input Panel ──────────────────────────────────── */}
      <div className="space-y-4">
        <Card>
          <div className="flex items-center gap-2 mb-4">
            <Search size={20} className="text-brand-purple" />
            <h2 className="font-semibold text-white">Website Extractor</h2>
          </div>

          <div className="space-y-4">
            {/* URL */}
            <div>
              <label className="block text-xs font-medium text-white/50 mb-2" htmlFor="extract-url">Website URL</label>
              <div className="relative">
                <Globe size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/30" />
                <input
                  id="extract-url"
                  type="url"
                  className="input-field pl-9"
                  placeholder="https://example.com"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                />
              </div>
            </div>

            {/* Query */}
            <div>
              <label className="block text-xs font-medium text-white/50 mb-2" htmlFor="extract-query">
                What are you looking for? <span className="text-white/30">(optional)</span>
              </label>
              <textarea
                id="extract-query"
                className="input-field resize-none"
                rows={3}
                placeholder="e.g. Find product prices and specifications, Extract contact emails and phone numbers..."
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>

            {/* Depth slider */}
            <div>
              <label className="block text-xs font-medium text-white/50 mb-2">Crawl Depth: {depth}</label>
              <input type="range" min={1} max={2} value={depth} onChange={(e) => setDepth(Number(e.target.value))} className="w-full accent-brand-purple" />
              <div className="flex justify-between text-xs text-white/30 mt-1">
                <span>Surface (1 page)</span>
                <span>Deep (2 levels)</span>
              </div>
            </div>

            <Button onClick={handleExtract} loading={loading} disabled={!url.trim()} className="w-full" icon={Search}>
              Extract Information
            </Button>
          </div>
        </Card>

        {/* Info card */}
        <Card hover={false}>
          <h3 className="text-xs font-semibold text-white/40 uppercase tracking-wider mb-3">What gets extracted</h3>
          <ul className="space-y-2 text-xs text-white/50">
            {[
              "Page text: headings, paragraphs, list items",
              "Images with alt text and context",
              "Internal & external links",
              "Metadata: title, description, OG tags, language",
              "Contact info: emails, phones, addresses",
              "Structured data: JSON-LD, Schema.org",
              "HTML tables as structured arrays",
              "AI-powered query-focused summary",
            ].map((t) => (
              <li key={t} className="flex items-start gap-2">
                <CheckCircle size={10} className="text-emerald-400 mt-0.5 flex-shrink-0" />{t}
              </li>
            ))}
          </ul>
        </Card>
      </div>

      {/* ── Right: Output Panel ────────────────────────────────── */}
      <div className="space-y-4">
        {loading && <div className="space-y-4">{[1, 2, 3].map((i) => <CardSkeleton key={i} />)}</div>}

        {error && !loading && (
          <Card>
            <div className="flex gap-3">
              <AlertCircle size={18} className="text-red-400 flex-shrink-0" />
              <div><p className="font-semibold text-red-400">Extraction Failed</p><p className="text-sm text-white/50">{error}</p></div>
            </div>
          </Card>
        )}

        {output && !loading && (
          <>
            {/* Header bar */}
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-sm font-semibold text-white">{agg.pages_crawled || 0} page(s) crawled</span>
                {output.mock && <span className="badge badge-free">Demo Data</span>}
              </div>
              <div className="flex gap-2">
                <Button variant="secondary" size="sm" onClick={handleDownload} icon={Download}>JSON</Button>
                <Button variant="secondary" size="sm" onClick={handleSave} icon={Save}>Save</Button>
              </div>
            </div>

            {/* ── AI Summary ───────────────────────────────────── */}
            {output.ai_summary && (
              <Card gradient>
                <div className="flex items-center gap-2 mb-3">
                  <Sparkles size={16} className="text-amber-400" />
                  <h3 className="font-semibold text-white text-sm">AI Summary</h3>
                  {output.query && <span className="text-xs text-white/30 ml-auto truncate max-w-48">"{output.query}"</span>}
                </div>
                <div className="text-sm text-white/70 leading-relaxed whitespace-pre-wrap prose prose-invert prose-sm max-w-none">
                  {output.ai_summary}
                </div>
              </Card>
            )}

            {/* ── Metadata ─────────────────────────────────────── */}
            {agg.metadata?.length > 0 && (
              <Section title="Page Metadata" icon={FileText} count={agg.metadata.length} defaultOpen>
                {agg.metadata.map((m, i) => (
                  <div key={i} className="space-y-1.5">
                    <p className="text-sm font-medium text-white">{m.title || "Untitled"}</p>
                    {m.description && <p className="text-xs text-white/50">{m.description}</p>}
                    <div className="flex flex-wrap gap-1.5">
                      {m.language && <Tag color="blue">🌐 {m.language}</Tag>}
                      {m.author && <Tag color="green">✍️ {m.author}</Tag>}
                      {m.canonical && (
                        <a href={m.canonical} target="_blank" rel="noopener noreferrer" className="text-xs text-brand-purple hover:underline truncate max-w-60">
                          {m.canonical}
                        </a>
                      )}
                    </div>
                    {m.keywords?.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {m.keywords.map((k, j) => <Tag key={j} color="purple">{k}</Tag>)}
                      </div>
                    )}
                    {m.og && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {Object.entries(m.og).map(([k, v]) => <Tag key={k} color="blue">{k}: {typeof v === "string" ? v.slice(0, 40) : v}</Tag>)}
                      </div>
                    )}
                  </div>
                ))}
              </Section>
            )}

            {/* ── Contact Info ─────────────────────────────────── */}
            {(agg.contact_info?.emails?.length > 0 || agg.contact_info?.phones?.length > 0 || agg.contact_info?.addresses?.length > 0) && (
              <Section
                title="Contact Information"
                icon={Mail}
                count={(agg.contact_info?.emails?.length || 0) + (agg.contact_info?.phones?.length || 0) + (agg.contact_info?.addresses?.length || 0)}
                defaultOpen
              >
                {agg.contact_info.emails?.length > 0 && (
                  <div>
                    <p className="text-xs text-white/40 mb-1.5 flex items-center gap-1"><Mail size={11} /> Emails</p>
                    <div className="flex flex-wrap gap-1.5">
                      {agg.contact_info.emails.map((e) => <Tag key={e} color="blue">{e}</Tag>)}
                    </div>
                  </div>
                )}
                {agg.contact_info.phones?.length > 0 && (
                  <div>
                    <p className="text-xs text-white/40 mb-1.5 flex items-center gap-1"><Phone size={11} /> Phones</p>
                    <div className="flex flex-wrap gap-1.5">
                      {agg.contact_info.phones.map((p) => <Tag key={p} color="green">{p}</Tag>)}
                    </div>
                  </div>
                )}
                {agg.contact_info.addresses?.length > 0 && (
                  <div>
                    <p className="text-xs text-white/40 mb-1.5 flex items-center gap-1"><MapPin size={11} /> Addresses</p>
                    <ul className="space-y-1">
                      {agg.contact_info.addresses.map((a, i) => (
                        <li key={i} className="text-xs text-white/60 bg-white/5 rounded-lg px-3 py-2">{a}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </Section>
            )}

            {/* ── Text Content ─────────────────────────────────── */}
            {(Object.keys(agg.text?.headings || {}).length > 0 || agg.text?.paragraphs?.length > 0) && (
              <Section
                title="Text Content"
                icon={FileText}
                count={(agg.text?.paragraphs?.length || 0) + Object.values(agg.text?.headings || {}).flat().length}
              >
                {Object.entries(agg.text?.headings || {}).map(([level, items]) => (
                  <div key={level}>
                    <p className="text-xs text-white/40 mb-1 uppercase">{level} ({items.length})</p>
                    <ul className="space-y-1">
                      {items.slice(0, 10).map((h, i) => (
                        <li key={i} className="text-xs text-white/70 flex items-start gap-1.5">
                          <span className="text-brand-purple">›</span> {h}
                        </li>
                      ))}
                      {items.length > 10 && <li className="text-xs text-white/30">…and {items.length - 10} more</li>}
                    </ul>
                  </div>
                ))}
                {agg.text?.paragraphs?.length > 0 && (
                  <div>
                    <p className="text-xs text-white/40 mb-1">Paragraphs ({agg.text.paragraphs.length})</p>
                    <div className="max-h-48 overflow-y-auto space-y-1.5 scrollbar-thin">
                      {agg.text.paragraphs.slice(0, 15).map((p, i) => (
                        <p key={i} className="text-xs text-white/50 border-l-2 border-white/10 pl-2">{p.slice(0, 200)}{p.length > 200 ? "…" : ""}</p>
                      ))}
                      {agg.text.paragraphs.length > 15 && <p className="text-xs text-white/30">…and {agg.text.paragraphs.length - 15} more paragraphs</p>}
                    </div>
                  </div>
                )}
              </Section>
            )}

            {/* ── Images ───────────────────────────────────────── */}
            {agg.images?.length > 0 && (
              <Section title="Images" icon={Image} count={agg.images.length}>
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 max-h-64 overflow-y-auto scrollbar-thin">
                  {agg.images.slice(0, 12).map((img, i) => (
                    <a key={i} href={img.src} target="_blank" rel="noopener noreferrer" className="group relative rounded-lg overflow-hidden bg-white/5 border border-white/10 hover:border-brand-purple/40 transition-colors">
                      <img
                        src={img.src}
                        alt={img.alt || "Image"}
                        className="w-full h-20 object-cover opacity-80 group-hover:opacity-100 transition-opacity"
                        loading="lazy"
                        onError={(e) => { e.target.style.display = "none"; }}
                      />
                      <div className="p-1.5">
                        <p className="text-[10px] text-white/50 truncate">{img.alt || img.src.split("/").pop()}</p>
                      </div>
                    </a>
                  ))}
                </div>
                {agg.images.length > 12 && <p className="text-xs text-white/30">…and {agg.images.length - 12} more images</p>}
              </Section>
            )}

            {/* ── Links ────────────────────────────────────────── */}
            {(agg.links?.internal?.length > 0 || agg.links?.external?.length > 0) && (
              <Section
                title="Links"
                icon={Link2}
                count={(agg.links?.internal?.length || 0) + (agg.links?.external?.length || 0)}
              >
                {agg.links.internal?.length > 0 && (
                  <div>
                    <p className="text-xs text-white/40 mb-1.5">Internal ({agg.links.internal.length})</p>
                    <div className="max-h-36 overflow-y-auto space-y-1 scrollbar-thin">
                      {agg.links.internal.slice(0, 15).map((l, i) => (
                        <a key={i} href={l.href} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 text-xs text-blue-300/70 hover:text-blue-300 truncate">
                          <ExternalLink size={10} className="flex-shrink-0" /> {l.text || l.href}
                        </a>
                      ))}
                    </div>
                  </div>
                )}
                {agg.links.external?.length > 0 && (
                  <div>
                    <p className="text-xs text-white/40 mb-1.5">External ({agg.links.external.length})</p>
                    <div className="max-h-36 overflow-y-auto space-y-1 scrollbar-thin">
                      {agg.links.external.slice(0, 15).map((l, i) => (
                        <a key={i} href={l.href} target="_blank" rel="noopener noreferrer" className="flex items-center gap-1.5 text-xs text-emerald-300/70 hover:text-emerald-300 truncate">
                          <ExternalLink size={10} className="flex-shrink-0" /> {l.text || l.href}
                        </a>
                      ))}
                    </div>
                  </div>
                )}
              </Section>
            )}

            {/* ── Tables ───────────────────────────────────────── */}
            {agg.tables?.length > 0 && (
              <Section title="Tables" icon={Table2} count={agg.tables.length}>
                {agg.tables.slice(0, 5).map((tbl, i) => (
                  <div key={i} className="overflow-x-auto rounded-lg border border-white/10">
                    <table className="w-full text-xs text-white/60">
                      <tbody>
                        {tbl.rows.slice(0, 10).map((row, ri) => (
                          <tr key={ri} className={ri === 0 && tbl.has_header ? "bg-white/10 font-semibold text-white/80" : "border-t border-white/5"}>
                            {row.map((cell, ci) => (
                              ri === 0 && tbl.has_header
                                ? <th key={ci} className="px-3 py-1.5 text-left">{cell}</th>
                                : <td key={ci} className="px-3 py-1.5">{cell}</td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                    {tbl.rows.length > 10 && <p className="text-[10px] text-white/30 px-3 py-1">…{tbl.rows.length - 10} more rows</p>}
                  </div>
                ))}
              </Section>
            )}

            {/* ── Structured Data ──────────────────────────────── */}
            {agg.structured_data?.length > 0 && (
              <Section title="Structured Data" icon={Code2} count={agg.structured_data.length}>
                {agg.structured_data.slice(0, 5).map((sd, i) => (
                  <div key={i} className="bg-white/5 rounded-lg p-3">
                    <div className="flex items-center gap-2 mb-1.5">
                      <Tag color={sd.type === "json-ld" ? "amber" : "green"}>{sd.type}</Tag>
                      {sd.item_type && <span className="text-[10px] text-white/30 truncate">{sd.item_type}</span>}
                    </div>
                    <pre className="text-[10px] text-white/40 overflow-x-auto max-h-32 scrollbar-thin whitespace-pre-wrap">
                      {JSON.stringify(sd.data || sd.properties, null, 2)?.slice(0, 600)}
                    </pre>
                  </div>
                ))}
              </Section>
            )}
          </>
        )}

        {/* ── Empty state ──────────────────────────────────────── */}
        {!output && !loading && !error && (
          <div className="glass rounded-2xl p-12 text-center">
            <Search size={40} className="mx-auto text-white/20 mb-3" />
            <p className="text-white/40 text-sm">Extraction results will appear here</p>
            <p className="text-white/25 text-xs mt-1">Enter a URL and optionally describe what you're looking for</p>
          </div>
        )}
      </div>
    </div>
  );
}
