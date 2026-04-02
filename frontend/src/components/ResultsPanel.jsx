"use client";
import { useState, useEffect, useRef } from "react";
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
        pages: found.source?.pages?.length ? found.source.pages : (found.source?.page ? [found.source.page] : []),
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
  const [expandedIdx, setExpandedIdx] = useState(null);
  const [popup, setPopup] = useState(null);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const exportRef = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (exportRef.current && !exportRef.current.contains(e.target)) setShowExportMenu(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const [polling, setPolling] = useState(false);

  useEffect(() => {
    if (!projectId) return;
    setLoading(true);
    setError("");
    setPolling(false);

    let cancelled = false;
    let timer = null;
    let attempts = 0;
    const MAX_ATTEMPTS = 24; // 24 × 10 s = 4 min max wait

    const fetchParams = () => {
      api.getParameters(token, projectId)
        .then(data => {
          if (cancelled) return;
          const merged = mergeWithRequired(data.parameters);
          setParams(merged);
          setLoading(false);
          // Keep polling while the backend is still processing
          const stillProcessing = data.processing_status === "processing" || data.processing_status === "uploading";
          if (stillProcessing && attempts < MAX_ATTEMPTS) {
            attempts++;
            setPolling(true);
            timer = setTimeout(fetchParams, 10000);
          } else {
            setPolling(false);
          }
        })
        .catch(e => {
          if (cancelled) return;
          setError(e.message);
          setLoading(false);
          setPolling(false);
        });
    };

    fetchParams();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [token, projectId]);

  const found = params.filter(p => p.available);
  const missing = params.filter(p => !p.available);

  const tableRows = () => params.map(p => [
    p.label,
    p.available ? p.value : "Not Available",
    p.unit,
    p.available ? `${p.confidence}%` : "-",
    p.available && p.pages?.length ? p.pages.map(pg => `Pg. ${pg}`).join(", ") : "-",
    p.available ? "Found" : "Not Available",
  ]);
  const tableHead = ["Parameter", "Value", "Unit", "Confidence", "Page", "Status"];

  const exportCSV = () => {
    const rows = [tableHead, ...tableRows()];
    const csv = rows.map(r => r.map(v => `"${v}"`).join(",")).join("\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    a.download = `TenderIQ_${projectId}.csv`; a.click();
  };

  const exportXLS = async () => {
    const XLSX = await import("xlsx");
    const ws = XLSX.utils.aoa_to_sheet([tableHead, ...tableRows()]);
    ws["!cols"] = [{ wch: 30 }, { wch: 40 }, { wch: 18 }, { wch: 12 }, { wch: 8 }, { wch: 14 }];
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Parameters");
    XLSX.writeFile(wb, `TenderIQ_${projectId}.xlsx`);
  };

  const exportPDF = async () => {
    const { default: jsPDF } = await import("jspdf");
    const { default: autoTable } = await import("jspdf-autotable");

    const loadImg = (src) => new Promise(res => {
      const img = new Image(); img.crossOrigin = "anonymous";
      img.onload = () => {
        const c = document.createElement("canvas");
        c.width = img.naturalWidth; c.height = img.naturalHeight;
        c.getContext("2d").drawImage(img, 0, 0);
        res({ data: c.toDataURL("image/png"), w: img.naturalWidth, h: img.naturalHeight });
      };
      img.src = src;
    });

    const [schuecoData, sooruData, teiqData] = await Promise.all([
      loadImg("/schu.png"),
      loadImg("/suru.png"),
      loadImg("/teiq.png"),
    ]);

    const doc = new jsPDF({ orientation: "portrait", unit: "mm", format: "a4" });
    const W = doc.internal.pageSize.getWidth();

    // ── Dark header band ──
    doc.setFillColor(10, 14, 20);
    doc.rect(0, 0, W, 54, "F");

    const cx = W / 2;

    // Aspect-correct sizing helpers
    const schuH = 16; const schuW = (schuecoData.w / schuecoData.h) * schuH;
    const suruH = 8;  const suruW = (sooruData.w  / sooruData.h)  * suruH;
    const teiqH = 10; const teiqW = (teiqData.w   / teiqData.h)   * teiqH;

    // Row 1: TenderIQ logo + name (centered)
    const row1W = teiqW + 3 + 28;
    const row1X = cx - row1W / 2;
    doc.addImage(teiqData.data, "PNG", row1X, 6, teiqW, teiqH);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(18);
    doc.setTextColor(255, 255, 255);
    doc.text("TenderIQ", row1X + teiqW + 3, 14);

    // Row 2: schu (bigger) + × + suru + "Sooru.AI" (centered)
    const row2W = schuW + 6 + 5 + 6 + suruW + 4 + 16;
    const row2X = cx - row2W / 2;
    const row2Y = 22;
    doc.addImage(schuecoData.data, "PNG", row2X, row2Y, schuW, schuH);
    doc.setFontSize(11); doc.setTextColor(180, 180, 180);
    doc.text("×", row2X + schuW + 3, row2Y + schuH / 2 + 1.5);
    doc.addImage(sooruData.data, "PNG", row2X + schuW + 9, row2Y + (schuH - suruH) / 2, suruW, suruH);
    doc.setFontSize(9); doc.setTextColor(200, 200, 200);
    doc.text("Sooru.AI", row2X + schuW + 9 + suruW + 3, row2Y + schuH / 2 + 1.5);

    // Project name subheading (centered)
    doc.setFontSize(10); doc.setFont("helvetica", "normal"); doc.setTextColor(160, 160, 160);
    doc.text(projectName || "Analysis Results", cx, 62, { align: "center" });

    // Summary line (centered)
    const foundCount = params.filter(p => p.available).length;
    doc.setFontSize(9); doc.setTextColor(100, 100, 100);
    doc.text(`${foundCount} Found  ·  ${params.length - foundCount} Not Available  ·  Generated ${new Date().toLocaleDateString()}`, cx, 68, { align: "center" });

    // Parameters table
    autoTable(doc, {
      startY: 73,
      head: [tableHead],
      body: tableRows(),
      styles: { fontSize: 8.5, cellPadding: 3, overflow: "linebreak" },
      headStyles: { fillColor: [10, 14, 20], textColor: [255, 255, 255], fontStyle: "bold", fontSize: 9 },
      alternateRowStyles: { fillColor: [245, 247, 250] },
      columnStyles: {
        0: { fontStyle: "bold", cellWidth: 38 },
        1: { cellWidth: 55 },
        2: { cellWidth: 22 },
        3: { cellWidth: 20 },
        4: { cellWidth: 14 },
        5: { cellWidth: 22 },
      },
      didParseCell: (data) => {
        if (data.section === "body" && data.column.index === 5) {
          data.cell.styles.textColor = data.cell.raw === "Found" ? [34, 197, 94] : [150, 150, 150];
        }
      },
    });

    doc.save(`TenderIQ_${projectId}.pdf`);
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
          {/* Export dropdown */}
          <div style={{ position: "relative" }} ref={exportRef}>
            <button onClick={() => setShowExportMenu(v => !v)}
              style={{ padding: "5px 10px", background: showExportMenu ? C.bg2 : "transparent", border: `1px solid ${C.border}`, borderRadius: 6, color: C.text2, cursor: "pointer", fontSize: 11, fontFamily: F.sans, display: "flex", alignItems: "center", gap: 4, fontWeight: 500, transition: "all 0.15s" }}>
              <DownloadIcon /> Export ▾
            </button>
            {showExportMenu && (
              <div style={{ position: "absolute", top: "calc(100% + 4px)", right: 0, background: C.bg1, border: `1px solid ${C.border}`, borderRadius: 8, overflow: "hidden", zIndex: 50, minWidth: 110, boxShadow: "0 8px 24px rgba(0,0,0,0.4)" }}>
                {[
                  { label: "CSV", fn: exportCSV, ext: ".csv" },
                  { label: "Excel (XLS)", fn: exportXLS, ext: ".xlsx" },
                  { label: "PDF", fn: exportPDF, ext: ".pdf" },
                ].map(opt => (
                  <button key={opt.label}
                    onClick={() => { opt.fn(); setShowExportMenu(false); }}
                    style={{ width: "100%", padding: "9px 14px", background: "none", border: "none", color: C.text2, cursor: "pointer", fontSize: 12, fontFamily: F.sans, textAlign: "left", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, transition: "background 0.1s" }}
                    onMouseEnter={e => e.currentTarget.style.background = C.bg2}
                    onMouseLeave={e => e.currentTarget.style.background = "none"}>
                    {opt.label}
                    <span style={{ fontSize: 10, color: C.text3 }}>{opt.ext}</span>
                  </button>
                ))}
              </div>
            )}
          </div>
          <button onClick={onClose} style={{ padding: 4, background: "none", border: "none", color: C.text3, cursor: "pointer" }}>
            <CloseIcon />
          </button>
        </div>
      </div>

      {/* Summary bar */}
      {polling && (
        <div style={{ padding: "10px 18px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          <div style={{ display: "flex", gap: 4 }}>
            {[0, 1, 2].map(i => (
              <div key={i} style={{ width: 5, height: 5, borderRadius: "50%", background: C.green, animation: `pulse 1.2s ease ${i * 0.2}s infinite` }} />
            ))}
          </div>
          <span style={{ fontSize: 11, color: C.text3 }}>Extracting parameters…</span>
        </div>
      )}
      {!loading && !polling && params.length > 0 && (
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

        {!loading && params.map((r, i) => {
          const isExpanded = expandedIdx === i;
          return (
            <div key={i}
              onClick={() => r.available && setExpandedIdx(isExpanded ? null : i)}
              onDoubleClick={() => r.available && setPopup(r)}
              style={{ padding: "12px 14px", background: r.available ? C.bg1 : "transparent", borderRadius: 8, marginBottom: 6, border: `1px solid ${isExpanded ? C.greenBorder : C.border}`, opacity: r.available ? 1 : 0.5, transition: "border-color 0.15s", cursor: r.available ? "pointer" : "default" }}
              onMouseEnter={e => { if (r.available) e.currentTarget.style.borderColor = C.greenBorder; }}
              onMouseLeave={e => { if (r.available && !isExpanded) e.currentTarget.style.borderColor = C.border; }}>
              <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 10, color: C.text3, marginBottom: 4, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.06em", display: "flex", alignItems: "center", gap: 5 }}>
                    {r.label}
                    <span style={{ fontSize: 9, color: C.text3, fontWeight: 400, textTransform: "none", letterSpacing: 0 }}>· {r.unit}</span>
                  </div>
                  {r.available ? (
                    <div style={{ fontSize: 14, color: C.text1, fontWeight: 600, fontFamily: F.mono, wordBreak: "break-word", whiteSpace: isExpanded ? "pre-wrap" : "nowrap", overflow: isExpanded ? "visible" : "hidden", textOverflow: isExpanded ? "clip" : "ellipsis" }}>
                      {r.value}
                    </div>
                  ) : (
                    <div style={{ fontSize: 13, color: C.text3, fontStyle: "italic" }}>Not available in document</div>
                  )}
                  {r.available && r.section && isExpanded && (
                    <div style={{ marginTop: 5 }}>
                      <span style={{ fontSize: 10, color: C.text3, lineHeight: 1.4 }}>{r.section}</span>
                    </div>
                  )}
                  {r.available && r.notes && (
                    <div style={{ fontSize: 11, color: C.text3, marginTop: 6, lineHeight: 1.5, ...(isExpanded ? {} : { overflow: "hidden", display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" }) }}>
                      {r.notes}
                    </div>
                  )}
                </div>
                {r.available ? (
                  <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4, flexShrink: 0 }}>
                    <div style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "3px 8px", borderRadius: 12, background: `${confidenceColor(r.confidence)}12`, fontSize: 11, fontWeight: 600, color: confidenceColor(r.confidence) }}>
                      <div style={{ width: 5, height: 5, borderRadius: "50%", background: confidenceColor(r.confidence) }} />
                      {r.confidence}%
                    </div>
                    {r.pages?.length > 0 && (
                      <div style={{ display: "flex", flexWrap: "wrap", gap: 3, justifyContent: "flex-end" }}>
                        {r.pages.map(pg => (
                          <span key={pg} style={{ display: "inline-flex", alignItems: "center", padding: "2px 6px", background: C.greenSubtle, border: `1px solid ${C.greenBorder}`, borderRadius: 4, fontSize: 9, fontWeight: 600, color: C.green }}>
                            Pg. {pg}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                ) : (
                  <div style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "3px 8px", borderRadius: 12, background: "rgba(255,255,255,0.04)", fontSize: 11, fontWeight: 600, color: C.text3, flexShrink: 0 }}>
                    —
                  </div>
                )}
              </div>
            </div>
          );
        })}
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

      {/* ── Double-click Popup Modal ── */}
      {popup && (
        <div onClick={() => setPopup(null)}
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.7)", zIndex: 600, display: "flex", alignItems: "center", justifyContent: "center", backdropFilter: "blur(4px)", padding: 24, animation: "fadeUp 0.2s ease" }}>
          <div onClick={e => e.stopPropagation()}
            style={{ background: C.bg1, borderRadius: 16, border: `1px solid ${C.greenBorder}`, width: "100%", maxWidth: 520, boxShadow: "0 24px 64px rgba(0,0,0,0.5)", animation: "fadeUp 0.2s ease" }}>
            {/* Modal header */}
            <div style={{ padding: "18px 20px 14px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 12 }}>
              <div>
                <div style={{ fontSize: 11, color: C.text3, fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4 }}>{popup.label}</div>
                <div style={{ fontSize: 11, color: C.text3 }}>{popup.unit}</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <div style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "4px 10px", borderRadius: 12, background: `${confidenceColor(popup.confidence)}15`, fontSize: 12, fontWeight: 700, color: confidenceColor(popup.confidence) }}>
                  <div style={{ width: 6, height: 6, borderRadius: "50%", background: confidenceColor(popup.confidence) }} />
                  {popup.confidence}% confidence
                </div>
                {popup.pages?.length > 0 && popup.pages.map(pg => (
                  <span key={pg} style={{ display: "inline-flex", alignItems: "center", padding: "4px 10px", background: C.greenSubtle, border: `1px solid ${C.greenBorder}`, borderRadius: 12, fontSize: 11, fontWeight: 600, color: C.green }}>
                    Pg. {pg}
                  </span>
                ))}
                <button onClick={() => setPopup(null)} style={{ background: "none", border: "none", color: C.text3, cursor: "pointer", padding: 2, display: "flex" }}>
                  <CloseIcon />
                </button>
              </div>
            </div>
            {/* Modal body */}
            <div style={{ padding: "20px" }}>
              <div style={{ fontSize: 22, color: C.text1, fontWeight: 700, fontFamily: F.mono, marginBottom: 16, wordBreak: "break-word", lineHeight: 1.4 }}>
                {popup.value}
              </div>
              {popup.section && (
                <div style={{ marginBottom: 14 }}>
                  <span style={{ fontSize: 11, color: C.text2, lineHeight: 1.4 }}>{popup.section}</span>
                </div>
              )}
              {popup.notes && (
                <div style={{ fontSize: 13, color: C.text2, lineHeight: 1.65, padding: "12px 14px", background: C.bg, borderRadius: 8, border: `1px solid ${C.border}`, whiteSpace: "pre-wrap" }}>
                  {popup.notes}
                </div>
              )}
            </div>
            <div style={{ padding: "10px 20px 16px", textAlign: "center" }}>
              <span style={{ fontSize: 11, color: C.text3 }}>Click outside to close</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
