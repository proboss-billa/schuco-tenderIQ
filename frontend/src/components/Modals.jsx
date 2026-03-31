"use client";
import { useState, useEffect } from "react";
import { C, F, inputBase, fB, bB } from "@/lib/design";
import { EditIcon, TrashIcon } from "@/components/Icons";

export function ChatCtxMenu({ x, y, onRename, onDelete, onClose }) {
  useEffect(() => {
    const h = () => onClose();
    const t = setTimeout(() => document.addEventListener("click", h), 10);
    return () => { clearTimeout(t); document.removeEventListener("click", h); };
  }, [onClose]);

  return (
    <div style={{ position: "fixed", left: x, top: y, zIndex: 500, background: C.bg2, border: `1px solid ${C.border2}`, borderRadius: 10, padding: 4, minWidth: 160, boxShadow: "0 8px 30px rgba(0,0,0,0.4)", animation: "fadeUp .15s ease", fontFamily: F.sans }}>
      <button onClick={onRename}
        style={{ width: "100%", padding: "9px 12px", background: "none", border: "none", color: C.text1, cursor: "pointer", fontSize: 13, fontFamily: F.sans, display: "flex", alignItems: "center", gap: 8, borderRadius: 6 }}
        onMouseEnter={e => e.currentTarget.style.background = C.bg3}
        onMouseLeave={e => e.currentTarget.style.background = "none"}>
        <EditIcon /> Rename
      </button>
      <button onClick={onDelete}
        style={{ width: "100%", padding: "9px 12px", background: "none", border: "none", color: C.err, cursor: "pointer", fontSize: 13, fontFamily: F.sans, display: "flex", alignItems: "center", gap: 8, borderRadius: 6 }}
        onMouseEnter={e => e.currentTarget.style.background = "rgba(255,90,90,0.08)"}
        onMouseLeave={e => e.currentTarget.style.background = "none"}>
        <TrashIcon /> Delete
      </button>
    </div>
  );
}

export function RenameModal({ name, onSave, onClose }) {
  const [v, setV] = useState(name);
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 600, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: F.sans }}>
      <div style={{ background: C.bg1, borderRadius: 14, padding: 24, width: 380, border: `1px solid ${C.border2}`, boxShadow: "0 16px 48px rgba(0,0,0,0.5)", animation: "fadeUp .2s ease" }}>
        <h3 style={{ margin: "0 0 16px", fontSize: 16, fontWeight: 600, color: C.text1 }}>Rename Chat</h3>
        <input style={inputBase} value={v} onChange={e => setV(e.target.value)} autoFocus onFocus={fB} onBlur={bB}
          onKeyDown={e => e.key === "Enter" && onSave(v)} />
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", marginTop: 18 }}>
          <button onClick={onClose} style={{ padding: "9px 18px", background: "transparent", border: `1px solid ${C.border}`, borderRadius: 8, color: C.text2, cursor: "pointer", fontSize: 13, fontFamily: F.sans }}>Cancel</button>
          <button onClick={() => onSave(v)} style={{ padding: "9px 18px", background: C.green, border: "none", borderRadius: 8, color: "#111", cursor: "pointer", fontSize: 13, fontFamily: F.sans, fontWeight: 700 }}>Save</button>
        </div>
      </div>
    </div>
  );
}

export function DeleteModal({ name, onConfirm, onClose }) {
  return (
    <div style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 600, display: "flex", alignItems: "center", justifyContent: "center", fontFamily: F.sans }}>
      <div style={{ background: C.bg1, borderRadius: 14, padding: 24, width: 380, border: `1px solid ${C.border2}`, boxShadow: "0 16px 48px rgba(0,0,0,0.5)", animation: "fadeUp .2s ease" }}>
        <h3 style={{ margin: "0 0 8px", fontSize: 16, fontWeight: 600, color: C.text1 }}>Delete chat?</h3>
        <p style={{ margin: "0 0 20px", fontSize: 13, color: C.text2, lineHeight: 1.5 }}>
          <strong style={{ color: C.text1 }}>{name}</strong> will be permanently deleted.
        </p>
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button onClick={onClose} style={{ padding: "9px 18px", background: "transparent", border: `1px solid ${C.border}`, borderRadius: 8, color: C.text2, cursor: "pointer", fontSize: 13, fontFamily: F.sans }}>Cancel</button>
          <button onClick={onConfirm} style={{ padding: "9px 18px", background: C.err, border: "none", borderRadius: 8, color: "#fff", cursor: "pointer", fontSize: 13, fontFamily: F.sans, fontWeight: 700 }}>Delete</button>
        </div>
      </div>
    </div>
  );
}
