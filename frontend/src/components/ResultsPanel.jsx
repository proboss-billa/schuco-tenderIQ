"use client";
import { useState, useEffect } from "react";
import { C, F } from "@/lib/design";
import { api } from "@/lib/api";
import { CloseIcon, DownloadIcon } from "@/components/Icons";

function confidenceColor(c) {
  return c >= 85 ? C.ok : c >= 70 ? C.warn : C.err;
}

function normalizeParameters(raw) {
  if (!raw || !Array.isArray(raw)) return { all: [] };
  return raw.map((item) => ({
    p: item.parameter || item.name || item.key || "Parameter",
    v: item.value ?? item.extracted_value ?? "-",
    c: Math.round((item.confidence ?? item.score ?? 0.8) * (item.confidence > 1 ? 1 : 100)),
    category: (item.category || "general").toLowerCase(),
  }));
}

export default function ResultsPanel({ token, projectId, projectName, onClose, isMobile }) {
  const [tab, setTab] = useState("all");
  const [params, setParams] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!token || !projectId) return;
    setLoading(true);
    setError("");
    api.getParameters(token, projectId)
      .then(data => setParams(normalizeParameters(data.parameters)))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [token, projectId]);

  const categories = ["all", ...new Set(params.map(p => p.category).filter(c => c !== "general" && c !== "all"))];
  const filtered = tab === "all" ? params : params.filter(p => p.category === tab);
  const avg = filtered.length ? Math.round(filtered.reduce((a, b) => a + b.c, 0) / filtered.length) : 0;

  const exportCSV = () => {
    const rows = [["Parameter", "Value", "Confidence", "Category"]];
    params.forEach(p => rows.push([p.p, p.v, `${p.c}%`, p.category]));
    const csv = rows.map(r => r.map(v => `"${v}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = `tender_${projectId}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  const tabLabel = (id) => id === "all" ? "All" : id.charAt(0).toUpperCase() + id.slice(1);

  return (
    <div style={{ height: "100%", display: "flex", flexDirection: "column", background: C.bg, borderLeft: isMobile ? "none" : `1px solid ${C.border}`, fontFamily: F.sans }}>
      {/* Header */}
      <div style={{ padding: "14px 18px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
        <div>
          <div style={{ fontSize: 14, fontWeight: 600, color: C.text1 }}>Extracted Parameters</div>
          <div style={{ fontSize: 11, color: C.text3, marginTop: 2 }}>{projectName || "Analysis Results"}</div>
        </div>
        <div style={{ display: "flex", gap: 5, alignItems: "center" }}>
          <button onClick={exportCSV} style={{ padding: "5px 10px", background: "transparent", border: `1px solid ${C.border}`, borderRadius: 6, color: C.text2, cursor: "pointer", fontSize: 11, fontFamily: F.sans, display: "flex", alignItems: "center", gap: 4, fontWeight: 500 }}>
            <DownloadIcon /> CSV
          </button>
          <button onClick={onClose} style={{ padding: 4, background: "none", border: "none", color: C.text3, cursor: "pointer" }}>
            <CloseIcon />
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: "flex", borderBottom: `1px solid ${C.border}`, padding: "0 18px", overflowX: "auto", flexShrink: 0 }}>
        {categories.map(t => (
          <button key={t} onClick={() => setTab(t)}
            style={{ padding: "10px 14px", background: "none", border: "none", borderBottom: tab === t ? `2px solid ${C.green}` : "2px solid transparent", color: tab === t ? C.text1 : C.text3, fontSize: 12, fontWeight: 500, cursor: "pointer", fontFamily: F.sans, transition: "all 0.2s", whiteSpace: "nowrap" }}>
            {tabLabel(t)}
          </button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto", padding: "10px 14px" }}>
        {loading && (
          <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: 120, color: C.text3, fontSize: 13 }}>
            <div style={{ display: "flex", gap: 6 }}>
              {[0, 1, 2].map(i => (
                <div key={i} style={{ width: 7, height: 7, borderRadius: "50%", background: C.green, animation: `pulse 1.2s ease ${i * 0.2}s infinite` }} />
              ))}
            </div>
          </div>
        )}
        {error && (
          <div style={{ padding: "12px 14px", background: "rgba(255,90,90,0.06)", border: `1px solid rgba(255,90,90,0.15)`, borderRadius: 8, color: C.err, fontSize: 13, marginTop: 8 }}>
            {error}
          </div>
        )}
        {!loading && !error && filtered.length === 0 && (
          <div style={{ textAlign: "center", padding: "40px 20px", color: C.text3, fontSize: 13 }}>No parameters extracted yet.</div>
        )}
        {!loading && filtered.map((r, i) => {
          const cc = confidenceColor(r.c);
          return (
            <div key={i}
              style={{ padding: "12px 14px", background: C.bg1, borderRadius: 8, marginBottom: 5, border: `1px solid ${C.border}`, display: "flex", alignItems: "center", justifyContent: "space-between", transition: "border-color 0.15s", cursor: "default" }}
              onMouseEnter={e => e.currentTarget.style.borderColor = C.greenBorder}
              onMouseLeave={e => e.currentTarget.style.borderColor = C.border}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 10, color: C.text3, marginBottom: 3, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em" }}>{r.p}</div>
                <div style={{ fontSize: 14, color: C.text1, fontWeight: 600, fontFamily: F.mono, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{String(r.v)}</div>
              </div>
              <div style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "3px 8px", borderRadius: 12, background: `${cc}12`, fontSize: 11, fontWeight: 600, color: cc, flexShrink: 0, marginLeft: 10 }}>
                <div style={{ width: 5, height: 5, borderRadius: "50%", background: cc }} />{r.c}%
              </div>
            </div>
          );
        })}
      </div>

      {/* Footer */}
      <div style={{ padding: "12px 18px", borderTop: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
        <div style={{ fontSize: 11, color: C.text3 }}>{filtered.length} parameters</div>
        {filtered.length > 0 && (
          <div style={{ fontSize: 11, color: C.ok, fontWeight: 500, display: "flex", alignItems: "center", gap: 4 }}>
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: C.ok }} />Avg: {avg}%
          </div>
        )}
      </div>
    </div>
  );
}
