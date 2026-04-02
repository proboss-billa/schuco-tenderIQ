"use client";
import { useState, useEffect, useRef } from "react";
import { C, F } from "@/lib/design";
import { api } from "@/lib/api";
import { CloseIcon, DownloadIcon } from "@/components/Icons";

// ── Parameter groups (order + category labels) ───────────────────────────────
const PARAM_GROUPS = [
  {
    id: "performance",
    label: "Performance",
    params: [
      { key: "Wind Load",                         label: "Wind Load",                         unit: "kN/m² / Pa" },
      { key: "Water Tightness",                   label: "Water Tightness",                   unit: "Pa / Class" },
      { key: "Air Permeability",                  label: "Air Permeability",                  unit: "m³/h·m² / Class" },
      { key: "Acoustic Rating",                   label: "Acoustic Rating",                   unit: "dB / Rw" },
      { key: "U-Value",                           label: "U-Value",                           unit: "W/m²K" },
      { key: "Deflection Limit",                  label: "Deflection Limit",                  unit: "L/xxx / mm" },
    ],
  },
  {
    id: "glazing",
    label: "Glazing",
    params: [
      { key: "Glass Thickness (Fixed)",           label: "Glass Thickness (Fixed)",           unit: "mm" },
      { key: "Glass Thickness (Openable)",        label: "Glass Thickness (Openable)",        unit: "mm" },
      { key: "Solar Factor / g-Value / SHGC",     label: "Solar Factor / g-Value / SHGC",     unit: "dimensionless / %" },
      { key: "Visible Light Transmittance (VLT)", label: "Visible Light Transmittance (VLT)", unit: "%" },
      { key: "Impact Resistance",                 label: "Impact Resistance",                 unit: "J / Class" },
    ],
  },
  {
    id: "structural",
    label: "Structural & Movement",
    params: [
      { key: "Seismic Performance",               label: "Seismic Performance",               unit: "mm / g" },
      { key: "Horizontal Movement",               label: "Horizontal Movement",               unit: "mm" },
      { key: "Vertical Stack Movement",           label: "Vertical Stack Movement",           unit: "mm" },
      { key: "Stack Height",                      label: "Stack Height",                      unit: "mm / m" },
      { key: "Slab Edge Deflection",              label: "Slab Edge Deflection",              unit: "mm" },
      { key: "Thermal Movement",                  label: "Thermal Movement",                  unit: "mm / °C" },
      { key: "Facade Dead Load / Self Weight",    label: "Facade Dead Load / Self Weight",    unit: "kN/m²" },
    ],
  },
  {
    id: "safety",
    label: "Safety & Fire",
    params: [
      { key: "Fire Rating",                       label: "Fire Rating",                       unit: "min / Class" },
      { key: "No. of Barriers",                   label: "No. of Barriers",                   unit: "nos" },
      { key: "Blast / Explosion Resistance",      label: "Blast / Explosion Resistance",      unit: "kPa / Class" },
    ],
  },
  {
    id: "system",
    label: "System & Project",
    params: [
      { key: "Facade System Type",                label: "Facade System Type",                unit: "type" },
      { key: "BMU Load",                          label: "BMU Load",                          unit: "kN / kg" },
      { key: "Signage Load",                      label: "Signage Load",                      unit: "kN / kg" },
      { key: "Warranty Period",                   label: "Warranty Period",                   unit: "years" },
      { key: "Testing & Mock-up Requirements",    label: "Testing & Mock-up Requirements",    unit: "standard" },
      { key: "Sustainability / Green Rating",     label: "Sustainability / Green Rating",     unit: "rating" },
    ],
  },
];

// Flat list derived from groups (used for export, merge, etc.)
const REQUIRED_PARAMS = PARAM_GROUPS.flatMap(g =>
  g.params.map(p => ({ ...p, group: g.id, groupLabel: g.label }))
);

function confidenceColor(c) {
  return c >= 85 ? C.ok : c >= 70 ? C.warn : C.err;
}

// Shorten a filename for display (max 22 chars)
function shortName(filename) {
  if (!filename) return "Unknown";
  const name = filename.replace(/\.[^.]+$/, ""); // strip extension
  return name.length > 22 ? name.slice(0, 20) + "…" : name;
}

// File-type icon char
function fileIcon(fileType) {
  if (!fileType) return "📄";
  if (fileType.includes("pdf"))  return "📕";
  if (fileType.includes("docx") || fileType.includes("doc")) return "📘";
  if (fileType.includes("excel") || fileType.includes("xlsx")) return "📗";
  return "📄";
}

function mergeWithRequired(extracted) {
  const map = {};
  (extracted || []).forEach(item => {
    const name = item.parameter_name || item.parameter || item.name || "";
    map[name] = item;
  });

  const requiredKeys = new Set(REQUIRED_PARAMS.map(r => r.key));

  const requiredRows = REQUIRED_PARAMS.map(req => {
    const found = map[req.key];
    if (found) {
      const rawConf = found.confidence ?? found.score ?? 0;
      const conf = Math.round(rawConf * (rawConf > 1 ? 1 : 100));
      // Prefer rich sources array; fall back to legacy source field
      const sources = found.sources?.length
        ? found.sources
        : (found.source?.document || found.source?.pages?.length)
          ? [{ document: found.source.document, pages: found.source.pages?.length ? found.source.pages : (found.source.page ? [found.source.page] : []), section: found.source.section }]
          : [];
      return {
        label: req.label,
        unit: req.unit,
        group: req.group,
        groupLabel: req.groupLabel,
        value: found.value ?? found.value_text ?? "-",
        confidence: conf,
        notes: found.notes || null,
        sources,
        section: sources[0]?.section ?? found.source?.section ?? null,
        available: true,
      };
    }
    return {
      label: req.label,
      unit: req.unit,
      group: req.group,
      groupLabel: req.groupLabel,
      value: null,
      confidence: null,
      notes: null,
      sources: [],
      available: false,
    };
  });

  // Append any extra parameters from backend not in the required list
  const extraRows = (extracted || [])
    .filter(item => {
      const name = item.parameter_name || item.parameter || item.name || "";
      const isFound = item.found !== false && (item.value ?? item.value_text);
      return isFound && !requiredKeys.has(name);
    })
    .map(item => {
      const name = item.parameter_name || item.parameter || item.name || "";
      const rawConf = item.confidence ?? item.score ?? 0;
      const conf = Math.round(rawConf * (rawConf > 1 ? 1 : 100));
      const sources = item.sources?.length
        ? item.sources
        : (item.source?.document || item.source?.pages?.length)
          ? [{ document: item.source.document, pages: item.source.pages?.length ? item.source.pages : (item.source.page ? [item.source.page] : []), section: item.source.section }]
          : [];
      return {
        label: item.display_name || name,
        unit: item.unit || item.expected_unit || "",
        group: "extra",
        groupLabel: "Additional",
        value: item.value ?? item.value_text ?? "-",
        confidence: conf,
        notes: item.notes || null,
        sources,
        section: sources[0]?.section ?? null,
        available: true,
        extra: true,
      };
    });

  return [...requiredRows, ...extraRows];
}

// ── Compact source badges component ──────────────────────────────────────────
function SourceBadges({ sources, isExpanded }) {
  if (!sources?.length) return null;

  // Collapsed: show max 3 page pills across all docs
  if (!isExpanded) {
    const allPages = sources.flatMap(s => (s.pages || []).map(pg => ({ pg, doc: s.document })));
    const shown = allPages.slice(0, 3);
    const more  = allPages.length - shown.length;
    return (
      <div style={{ display: "flex", flexWrap: "wrap", gap: 3, justifyContent: "flex-end", marginTop: 4 }}>
        {shown.map(({ pg, doc }, idx) => (
          <span key={idx} title={doc ? `${doc} · Page ${pg}` : `Page ${pg}`}
            style={{ display: "inline-flex", alignItems: "center", padding: "2px 6px", background: C.greenSubtle, border: `1px solid ${C.greenBorder}`, borderRadius: 4, fontSize: 9, fontWeight: 600, color: C.green, cursor: "default" }}>
            Pg.{pg}
          </span>
        ))}
        {more > 0 && (
          <span style={{ padding: "2px 6px", background: "rgba(255,255,255,0.05)", borderRadius: 4, fontSize: 9, color: C.text3 }}>
            +{more}
          </span>
        )}
      </div>
    );
  }

  // Expanded: show each document with its pages
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 5, marginTop: 6, alignItems: "flex-end" }}>
      {sources.map((src, idx) => (
        <div key={idx} style={{ display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap", justifyContent: "flex-end" }}>
          {src.document && (
            <span style={{ fontSize: 9, color: C.text3, fontWeight: 500, maxWidth: 120, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={src.document}>
              {shortName(src.document)}
            </span>
          )}
          {(src.pages || []).map(pg => (
            <span key={pg}
              style={{ display: "inline-flex", alignItems: "center", padding: "2px 6px", background: C.greenSubtle, border: `1px solid ${C.greenBorder}`, borderRadius: 4, fontSize: 9, fontWeight: 600, color: C.green }}>
              Pg.{pg}
            </span>
          ))}
        </div>
      ))}
    </div>
  );
}

export default function ResultsPanel({ token, projectId, projectName, onClose, isMobile }) {
  const [params, setParams] = useState([]);
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [expandedIdx, setExpandedIdx] = useState(null);
  const [popup, setPopup] = useState(null);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const [showDocs, setShowDocs] = useState(false);
  const exportRef = useRef(null);

  useEffect(() => {
    const handler = (e) => { if (exportRef.current && !exportRef.current.contains(e.target)) setShowExportMenu(false); };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const [polling, setPolling] = useState(false);
  const [pipelineStep, setPipelineStep] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [reExtracting, setReExtracting] = useState(false);

  useEffect(() => {
    if (!projectId) return;
    setLoading(true);
    setError("");
    setPolling(false);
    setPipelineStep(null);
    setParams([]);
    setDocuments([]);

    let cancelled = false;
    let timer = null;
    let attempts = 0;
    const MAX_ATTEMPTS = 80;
    const POLL_INTERVAL = 3000;

    const fetchParams = () => {
      api.getParameters(token, projectId)
        .then(data => {
          if (cancelled) return;
          const merged = mergeWithRequired(data.parameters);
          setParams(merged);
          setDocuments(data.documents || []);
          setPipelineStep(data.pipeline_step || null);
          setLoading(false);
          const stillProcessing = data.processing_status === "processing" || data.processing_status === "uploaded";
          if (stillProcessing && attempts < MAX_ATTEMPTS) {
            attempts++;
            setPolling(true);
            timer = setTimeout(fetchParams, POLL_INTERVAL);
          } else {
            setPolling(false);
            setReExtracting(false);
          }
        })
        .catch(() => {
          if (cancelled) return;
          if (attempts < MAX_ATTEMPTS) {
            attempts++;
            timer = setTimeout(fetchParams, POLL_INTERVAL);
          } else {
            setError("Could not load parameters. Please refresh.");
            setLoading(false);
            setPolling(false);
            setReExtracting(false);
          }
        });
    };

    fetchParams();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [token, projectId, refreshKey]);

  const handleReExtract = async () => {
    if (reExtracting || polling) return;
    setReExtracting(true);
    setError("");
    try {
      await api.reExtract(token, projectId);
      setRefreshKey(k => k + 1);
    } catch (e) {
      setError(`Re-extraction failed: ${e.message}`);
      setReExtracting(false);
    }
  };

  const found = params.filter(p => p.available);
  const missing = params.filter(p => !p.available);

  const tableRows = () => params.map(p => {
    // Build "Document: Pg.X, Pg.Y | Document2: Pg.Z" source string
    const srcStr = p.available && p.sources?.length
      ? p.sources.map(s => {
          const doc  = s.document ? shortName(s.document) : "?";
          const pgs  = (s.pages || []).map(pg => `Pg.${pg}`).join(", ");
          return pgs ? `${doc}: ${pgs}` : doc;
        }).join(" | ")
      : "-";
    return [
      p.label,
      p.available ? p.value : "Not Available",
      p.unit,
      p.available ? `${p.confidence}%` : "-",
      srcStr,
      p.available ? "Found" : "Not Available",
    ];
  });
  const tableHead = ["Parameter", "Value", "Unit", "Confidence", "Source (Doc: Pages)", "Status"];

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

    doc.setFillColor(10, 14, 20);
    doc.rect(0, 0, W, 54, "F");

    const cx = W / 2;
    const schuH = 16; const schuW = (schuecoData.w / schuecoData.h) * schuH;
    const suruH = 8;  const suruW = (sooruData.w  / sooruData.h)  * suruH;
    const teiqH = 10; const teiqW = (teiqData.w   / teiqData.h)   * teiqH;

    const row1W = teiqW + 3 + 28;
    const row1X = cx - row1W / 2;
    doc.addImage(teiqData.data, "PNG", row1X, 6, teiqW, teiqH);
    doc.setFont("helvetica", "bold");
    doc.setFontSize(18);
    doc.setTextColor(255, 255, 255);
    doc.text("TenderIQ", row1X + teiqW + 3, 14);

    const row2W = schuW + 6 + 5 + 6 + suruW + 4 + 16;
    const row2X = cx - row2W / 2;
    const row2Y = 22;
    doc.addImage(schuecoData.data, "PNG", row2X, row2Y, schuW, schuH);
    doc.setFontSize(11); doc.setTextColor(180, 180, 180);
    doc.text("×", row2X + schuW + 3, row2Y + schuH / 2 + 1.5);
    doc.addImage(sooruData.data, "PNG", row2X + schuW + 9, row2Y + (schuH - suruH) / 2, suruW, suruH);
    doc.setFontSize(9); doc.setTextColor(200, 200, 200);
    doc.text("Sooru.AI", row2X + schuW + 9 + suruW + 3, row2Y + schuH / 2 + 1.5);

    doc.setFontSize(10); doc.setFont("helvetica", "normal"); doc.setTextColor(160, 160, 160);
    doc.text(projectName || "Analysis Results", cx, 62, { align: "center" });

    const foundCount = params.filter(p => p.available).length;
    doc.setFontSize(9); doc.setTextColor(100, 100, 100);
    doc.text(`${foundCount} Found  ·  ${params.length - foundCount} Not Available  ·  Generated ${new Date().toLocaleDateString()}`, cx, 68, { align: "center" });

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

  // ── Build grouped render list ─────────────────────────────────────────────
  // Inject group-header sentinel objects between param rows
  const buildGroupedList = () => {
    if (!params.length) return [];
    const items = [];
    let lastGroup = null;
    params.forEach((p, i) => {
      const gid = p.group || "extra";
      if (gid !== lastGroup) {
        items.push({ __groupHeader: true, groupLabel: p.groupLabel || "Additional", groupId: gid, idx: i });
        lastGroup = gid;
      }
      items.push({ ...p, __paramIdx: i });
    });
    return items;
  };

  const groupedItems = buildGroupedList();

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

      {/* Summary + Re-extract bar */}
      {polling && (
        <div style={{ padding: "10px 18px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", gap: 8, flexShrink: 0, background: "rgba(52,211,153,0.04)" }}>
          <div style={{ display: "flex", gap: 4, flexShrink: 0 }}>
            {[0, 1, 2].map(i => (
              <div key={i} style={{ width: 5, height: 5, borderRadius: "50%", background: C.green, animation: `pulse 1.2s ease ${i * 0.2}s infinite` }} />
            ))}
          </div>
          <span style={{ fontSize: 11, color: C.text2, flex: 1 }}>
            {pipelineStep || (reExtracting ? `Re-extracting ${REQUIRED_PARAMS.length} parameters…` : "Processing documents…")}
          </span>
        </div>
      )}
      {!loading && !polling && params.length > 0 && (
        <div style={{ padding: "10px 18px", borderBottom: `1px solid ${C.border}`, display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, flexShrink: 0 }}>
          <div style={{ display: "flex", gap: 16 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: C.ok }} />
              <span style={{ color: C.text2 }}><strong style={{ color: C.text1 }}>{found.length}</strong> Found</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12 }}>
              <div style={{ width: 8, height: 8, borderRadius: "50%", background: C.text3 }} />
              <span style={{ color: C.text2 }}><strong style={{ color: C.text1 }}>{missing.length}</strong> Not Available</span>
            </div>
          </div>
          {/* Re-extract always visible — lets users refresh with new parameter catalog */}
          <button
            onClick={handleReExtract}
            disabled={reExtracting || polling}
            title="Re-run extraction to pick up all 27 parameters"
            style={{ padding: "4px 12px", background: "transparent", border: `1px solid ${C.greenBorder}`, borderRadius: 6, color: C.green, cursor: (reExtracting || polling) ? "default" : "pointer", fontSize: 11, fontFamily: F.sans, fontWeight: 500, opacity: (reExtracting || polling) ? 0.5 : 1, transition: "all 0.15s", display: "flex", alignItems: "center", gap: 5 }}
            onMouseEnter={e => { if (!reExtracting && !polling) e.currentTarget.style.background = C.greenSubtle; }}
            onMouseLeave={e => { e.currentTarget.style.background = "transparent"; }}>
            {reExtracting ? (
              <>
                <div style={{ width: 8, height: 8, borderRadius: "50%", background: C.green, animation: "pulse 1s ease infinite" }} />
                Re-extracting…
              </>
            ) : "↺ Re-extract"}
          </button>
        </div>
      )}

      {/* Documents bar */}
      {!loading && documents.length > 0 && (
        <div style={{ borderBottom: `1px solid ${C.border}`, flexShrink: 0 }}>
          <button
            onClick={() => setShowDocs(v => !v)}
            style={{ width: "100%", padding: "8px 18px", background: "none", border: "none", display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer", color: C.text2 }}>
            <span style={{ fontSize: 11, fontWeight: 600, color: C.text3, textTransform: "uppercase", letterSpacing: "0.08em" }}>
              {documents.length} Document{documents.length !== 1 ? "s" : ""}
              {polling && <span style={{ marginLeft: 6, color: C.warn }}>• scanning</span>}
            </span>
            <span style={{ fontSize: 10, color: C.text3 }}>{showDocs ? "▲" : "▼"}</span>
          </button>
          {showDocs && (
            <div style={{ padding: "0 14px 10px" }}>
              {documents.map((doc, i) => {
                const st = doc.processing_status;
                const isActive = st === "processing" || st === "indexed";
                const isOk     = st === "completed" || st === "pending";
                const isFailed = st === "failed";
                const statusColor = isFailed ? C.err : isOk ? C.ok : isActive ? C.warn : C.text3;
                const statusLabel = {
                  "pending":    "pending",
                  "processing": "parsing…",
                  "indexed":    "indexed",
                  "completed":  "done",
                  "failed":     "failed",
                }[st] || st || "ready";
                return (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", background: C.bg2, borderRadius: 7, marginBottom: 4, border: `1px solid ${isActive ? C.warn + "40" : C.border}` }}>
                    <span style={{ fontSize: 14, flexShrink: 0 }}>{fileIcon(doc.file_type)}</span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 11, fontWeight: 600, color: C.text1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={doc.filename}>
                        {doc.filename}
                      </div>
                      <div style={{ fontSize: 10, color: C.text3, marginTop: 1 }}>
                        {doc.page_count ? `${doc.page_count} pages · ` : ""}{doc.num_chunks ? `${doc.num_chunks} chunks` : ""}
                        {isFailed && doc.processing_error && (
                          <span style={{ color: C.err }}> · {doc.processing_error.slice(0, 60)}</span>
                        )}
                      </div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: 3, flexShrink: 0 }}>
                      <div style={{ width: 6, height: 6, borderRadius: "50%", background: statusColor, animation: isActive ? "pulse 1.2s ease infinite" : "none" }} />
                      <span style={{ fontSize: 10, color: statusColor, fontWeight: 600 }}>
                        {statusLabel}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Content */}
      <div style={{ flex: 1, overflowY: "auto", padding: "12px 14px" }}>
        {(loading || (polling && params.length === 0)) && (
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

        {!loading && groupedItems.map((item, idx) => {
          // ── Group header ──
          if (item.__groupHeader) {
            const groupParams = params.filter(p => (p.group || "extra") === item.groupId);
            const groupFound = groupParams.filter(p => p.available).length;
            return (
              <div key={`group-${item.groupId}`} style={{ display: "flex", alignItems: "center", gap: 8, margin: idx === 0 ? "4px 0 8px" : "16px 0 8px" }}>
                <span style={{ fontSize: 10, color: C.text3, fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.1em", whiteSpace: "nowrap" }}>
                  {item.groupLabel}
                </span>
                <div style={{ flex: 1, height: 1, background: C.border }} />
                <span style={{ fontSize: 10, color: groupFound > 0 ? C.ok : C.text3, fontWeight: 600, whiteSpace: "nowrap" }}>
                  {groupFound}/{groupParams.length}
                </span>
              </div>
            );
          }

          // ── Parameter row ──
          const i = item.__paramIdx;
          const r = item;
          const isExpanded = expandedIdx === i;

          return (
            <div key={i}
              onClick={() => r.available && setExpandedIdx(isExpanded ? null : i)}
              onDoubleClick={() => r.available && setPopup(r)}
              style={{ padding: "12px 14px", background: r.available ? C.bg1 : "transparent", borderRadius: 8, marginBottom: 6, border: `1px solid ${isExpanded ? C.greenBorder : C.border}`, opacity: r.available ? 1 : 0.45, transition: "border-color 0.15s, opacity 0.15s", cursor: r.available ? "pointer" : "default" }}
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
                    <div style={{ fontSize: 13, color: C.text3, fontStyle: "italic", display: "flex", alignItems: "center", gap: 6 }}>
                      {polling ? (
                        <>
                          <div style={{ width: 6, height: 6, borderRadius: "50%", background: C.warn, animation: "pulse 1.4s ease infinite", flexShrink: 0 }} />
                          Scanning documents…
                        </>
                      ) : "Not available in document"}
                    </div>
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
                    <SourceBadges sources={r.sources} isExpanded={isExpanded} />
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
        <div style={{ fontSize: 11, color: C.text3 }}>{params.length} parameters tracked</div>
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
                {popup.sources?.length > 0 && popup.sources.flatMap(s => (s.pages || []).map(pg => (
                  <span key={`${s.document}-${pg}`} title={s.document || undefined}
                    style={{ display: "inline-flex", alignItems: "center", padding: "4px 10px", background: C.greenSubtle, border: `1px solid ${C.greenBorder}`, borderRadius: 12, fontSize: 11, fontWeight: 600, color: C.green }}>
                    Pg. {pg}
                  </span>
                )))}
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
              {/* Sources breakdown */}
              {popup.sources?.length > 0 && (
                <div style={{ marginBottom: 14, display: "flex", flexDirection: "column", gap: 4 }}>
                  {popup.sources.map((src, idx) => (
                    <div key={idx} style={{ display: "flex", alignItems: "center", gap: 6, padding: "6px 10px", background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <span style={{ fontSize: 12 }}>{fileIcon(src.document?.split(".").pop())}</span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 11, fontWeight: 600, color: C.text1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={src.document}>
                          {src.document || "Unknown document"}
                        </div>
                        {src.section && (
                          <div style={{ fontSize: 10, color: C.text3, marginTop: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {src.section}
                          </div>
                        )}
                      </div>
                      <div style={{ display: "flex", gap: 3, flexShrink: 0 }}>
                        {(src.pages || []).map(pg => (
                          <span key={pg} style={{ padding: "2px 7px", background: C.greenSubtle, border: `1px solid ${C.greenBorder}`, borderRadius: 4, fontSize: 10, fontWeight: 600, color: C.green }}>
                            Pg.{pg}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {!popup.sources?.length && popup.section && (
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
