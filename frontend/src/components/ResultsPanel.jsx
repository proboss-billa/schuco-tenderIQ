"use client";
import { useState, useEffect } from "react";
import { C, F } from "@/lib/design";
import { api } from "@/lib/api";
import { CloseIcon, DownloadIcon } from "@/components/Icons";

// ── Full list of required parameters (always shown) ──────────────────────────
const REQUIRED_PARAMS = [
  { key: "Wind Load",                  label: "Wind Load",                  unit: "kN/m² / Pa" },
  { key: "Water Tightness",            label: "Water Tightness",            unit: "Pa / Class" },
  { key: "Air Permeability",           label: "Air Permeability",           unit: "m³/h·m² / Class" },
  { key: "Seismic Performance",        label: "Seismic Performance",        unit: "mm / g" },
  { key: "Acoustic Rating",            label: "Acoustic Rating",            unit: "dB / Rw" },
  { key: "U-Value",                    label: "U-Value",                    unit: "W/m²K" },
  { key: "Glass Thickness (Openable)", label: "Glass Thickness (Openable)", unit: "mm" },
  { key: "BMU Load",                   label: "BMU Load",                   unit: "kN / kg" },
  { key: "No. of Barriers",            label: "No. of Barriers",            unit: "nos" },
  { key: "Stack Height",               label: "Stack Height",               unit: "mm / m" },
  { key: "Vertical Stack Movement",    label: "Vertical Stack Movement",    unit: "mm" },
  { key: "Signage Load",               label: "Signage Load",               unit: "kN / kg" },
  { key: "Horizontal Movement",        label: "Horizontal Movement",        unit: "mm" },
];

function confidenceColor(c) {
  return c >= 85 ? C.ok : c >= 70 ? C.warn : C.err;
}

function mergeWithRequired(extracted) {
  const map = {};
  (extracted || []).forEach(item => {
    const name = item.parameter_name || item.parameter || item.name || "";
    map[name] = item;
  });

  return REQUIRED_PARAMS.map(req => {
    const found = map[req.key];
    if (found) {
      const rawConf = found.confidence ?? found.score ?? 0;
      const conf = Math.round(rawConf * (rawConf > 1 ? 1 : 100));
      return {
        label: req.label,
        unit: req.unit,
        value: found.value ?? found.value_text ?? "-",
        confidence: conf,
        notes: found.notes || null,
        page: found.source?.page ?? null,
        section: found.source?.section ?? null,
        available: true,
      };
    }
    return {
      label: req.label,
      unit: req.unit,
      value: null,
      confidence: null,
      notes: null,
      available: false,
    };
  });
}

export default function ResultsPanel({ token, projectId, projectName, onClose, isMobile }) {
  const [params, setParams] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!projectId) return;
    setLoading(true);
    setError("");
    api.getParameters(token, projectId)
      .then(data => setParams(mergeWithRequired(data.parameters)))
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [token, projectId]);

  const found = params.filter(p => p.available);
  const missing = params.filter(p => !p.available);

  const exportCSV = () => {
    const rows = [["Parameter", "Value", "Unit", "Confidence", "Status"]];
    params.forEach(p => rows.push([
      p.label,
      p.available ? p.value : "Not Available",
      p.unit,
      p.available ? `${p.confidence}%` : "-",
      p.available ? "Found" : "Not Available",
    ]));
    const csv = rows.map(r => r.map(v => `"${v}"`).join(",")).join("\n");
    const blob = new Blob([csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a"); a.href = url; a.download = `tender_${projectId}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

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

      {/* Summary bar */}
      {!loading && params.length > 0 && (
        <div style={{ padding: "10px 18px", borderBottom: `1px solid ${C.border}`, display: "flex", gap: 16, flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: C.ok }} />
            <span style={{ color: C.text2 }}><strong style={{ color: C.text1 }}>{found.length}</strong> Found</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
            <div style={{ width: 8, height: 8, borderRadius: "50%", background: C.text3 }} />
            <span style={{ color: C.text2 }}><strong style={{ color: C.text1 }}>{missing.length}</strong> Not Available</span>
          </div>
        </div>
      )}

      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto", padding: "12px 14px" }}>
        {loading && (
          <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: 120 }}>
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

        {!loading && params.map((r, i) => (
          <div key={i}
            style={{ padding: "12px 14px", background: r.available ? C.bg1 : "transparent", borderRadius: 8, marginBottom: 6, border: `1px solid ${r.available ? C.border : C.border}`, opacity: r.available ? 1 : 0.5, transition: "border-color 0.15s, opacity 0.15s", cursor: "default" }}
            onMouseEnter={e => { if (r.available) e.currentTarget.style.borderColor = C.greenBorder; }}
            onMouseLeave={e => { if (r.available) e.currentTarget.style.borderColor = C.border; }}>
            <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: 10, color: C.text3, marginBottom: 4, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", display: "flex", alignItems: "center", gap: 5 }}>
                  {r.label}
                  <span style={{ fontSize: 9, color: C.text3, fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>· {r.unit}</span>
                </div>
                {r.available ? (
                  <div style={{ fontSize: 14, color: C.text1, fontWeight: 600, fontFamily: F.mono, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {r.value}
                  </div>
                ) : (
                  <div style={{ fontSize: 13, color: C.text3, fontStyle: "italic" }}>Not available in document</div>
                )}
                {r.available && (r.page || r.section) && (
                  <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 5, flexWrap: "wrap" }}>
                    {r.page && (
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 3, padding: "2px 7px", background: C.greenSubtle, border: `1px solid ${C.greenBorder}`, borderRadius: 4, fontSize: 10, fontWeight: 600, color: C.green }}>
                        Pg. {r.page}
                      </span>
                    )}
                    {r.section && (
                      <span style={{ fontSize: 10, color: C.text3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 200 }}>
                        {r.section}
                      </span>
                    )}
                  </div>
                )}
                {r.available && r.notes && (
                  <div style={{ fontSize: 10, color: C.text3, marginTop: 4, lineHeight: 1.4, overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }}>
                    {r.notes}
                  </div>
                )}
              </div>
              {r.available ? (
                <div style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "3px 8px", borderRadius: 12, background: `${confidenceColor(r.confidence)}12`, fontSize: 11, fontWeight: 600, color: confidenceColor(r.confidence), flexShrink: 0 }}>
                  <div style={{ width: 5, height: 5, borderRadius: "50%", background: confidenceColor(r.confidence) }} />
                  {r.confidence}%
                </div>
              ) : (
                <div style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "3px 8px", borderRadius: 12, background: "rgba(255,255,255,0.04)", fontSize: 11, fontWeight: 600, color: C.text3, flexShrink: 0 }}>
                  —
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {/* Footer */}
      <div style={{ padding: "12px 18px", borderTop: `1px solid ${C.border}`, display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
        <div style={{ fontSize: 11, color: C.text3 }}>{REQUIRED_PARAMS.length} parameters tracked</div>
        {found.length > 0 && (
          <div style={{ fontSize: 11, color: C.ok, fontWeight: 500, display: "flex", alignItems: "center", gap: 4 }}>
            <div style={{ width: 6, height: 6, borderRadius: "50%", background: C.ok }} />
            Avg confidence: {Math.round(found.reduce((a, b) => a + b.confidence, 0) / found.length)}%
          </div>
        )}
      </div>
    </div>
  );
}
