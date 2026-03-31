"use client";
import { useState } from "react";
import { C, F, inputBase, lbl } from "@/lib/design";
import { COUNTRY_CODES } from "@/lib/countryCodes";
import { BackIcon, CheckIcon } from "@/components/Icons";

export default function ProfileScreen({ user, onBack }) {
  const [p, setP] = useState({
    first: "",
    last: "",
    email: user?.email || "",
    cc: "+49",
    phone: "",
  });
  const [saved, setSaved] = useState(false);
  const [changePw, setChangePw] = useState(false);
  const [pw, setPw] = useState({ current: "", next: "", confirm: "" });

  const save = () => { setSaved(true); setTimeout(() => setSaved(false), 2000); };
  const f = (k) => (e) => setP({ ...p, [k]: e.target.value });

  const fB = (e) => { e.target.style.borderColor = C.green; e.target.style.boxShadow = `0 0 0 3px rgba(139,197,63,0.12)`; };
  const bB = (e) => { e.target.style.borderColor = C.border2; e.target.style.boxShadow = "none"; };

  return (
    <div style={{ maxWidth: 560, margin: "0 auto", padding: "36px 24px", fontFamily: F.sans, animation: "fadeUp .3s ease" }}>
      <button onClick={onBack} style={{ background: "none", border: "none", color: C.text2, cursor: "pointer", display: "flex", alignItems: "center", gap: 4, padding: 0, marginBottom: 24, fontFamily: F.sans, fontSize: 13 }}>
        <BackIcon /> Back
      </button>
      <h2 style={{ margin: "0 0 4px", fontSize: 22, fontWeight: 700, color: C.text1 }}>Profile Settings</h2>
      <p style={{ margin: "0 0 28px", fontSize: 13, color: C.text2 }}>Manage your account information</p>

      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 28 }}>
        <div style={{ width: 60, height: 60, borderRadius: "50%", background: `linear-gradient(135deg, ${C.green}, ${C.navy})`, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 20, fontWeight: 700, color: "#fff", flexShrink: 0 }}>
          {p.email ? p.email[0].toUpperCase() : "?"}
        </div>
        <div>
          <div style={{ fontSize: 16, fontWeight: 600, color: C.text1 }}>{p.email}</div>
        </div>
      </div>

      {/* Personal info */}
      <div style={{ background: C.bg1, borderRadius: 12, padding: 22, border: `1px solid ${C.border}`, marginBottom: 16 }}>
        <h3 style={{ margin: "0 0 18px", fontSize: 13, fontWeight: 600, color: C.text1, textTransform: "uppercase", letterSpacing: "0.05em" }}>Personal Information</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
          <div><label style={lbl}>First Name</label><input style={inputBase} value={p.first} onChange={f("first")} onFocus={fB} onBlur={bB} /></div>
          <div><label style={lbl}>Last Name</label><input style={inputBase} value={p.last} onChange={f("last")} onFocus={fB} onBlur={bB} /></div>
        </div>
        <div style={{ marginBottom: 14 }}>
          <label style={lbl}>Email</label>
          <input style={inputBase} type="email" value={p.email} onChange={f("email")} onFocus={fB} onBlur={bB} />
        </div>
        <div>
          <label style={lbl}>Phone Number</label>
          <div style={{ display: "grid", gridTemplateColumns: "160px 1fr", gap: 8 }}>
            <select style={{ ...inputBase, cursor: "pointer", padding: "11px 8px" }} value={p.cc} onChange={f("cc")}>
              {COUNTRY_CODES.map((c, i) => (
                <option key={i} value={c.code}>{c.code} {c.country}</option>
              ))}
            </select>
            <input style={inputBase} type="tel"
              placeholder={COUNTRY_CODES.find(c => c.code === p.cc)?.placeholder || "000 000 0000"}
              value={p.phone} onChange={f("phone")} onFocus={fB} onBlur={bB} />
          </div>
        </div>
      </div>

      {/* Security */}
      <div style={{ background: C.bg1, borderRadius: 12, padding: 22, border: `1px solid ${C.border}`, marginBottom: 16 }}>
        <h3 style={{ margin: "0 0 18px", fontSize: 13, fontWeight: 600, color: C.text1, textTransform: "uppercase", letterSpacing: "0.05em" }}>Security</h3>
        {!changePw ? (
          <button onClick={() => setChangePw(true)}
            style={{ padding: "10px 18px", background: "transparent", border: `1px solid ${C.greenBorder}`, borderRadius: 8, color: C.green, cursor: "pointer", fontSize: 13, fontFamily: F.sans, fontWeight: 600 }}
            onMouseEnter={e => e.target.style.background = C.greenSubtle} onMouseLeave={e => e.target.style.background = "transparent"}>
            Change Password
          </button>
        ) : (
          <div style={{ animation: "fadeUp .2s ease" }}>
            <div style={{ marginBottom: 14 }}><label style={lbl}>Current Password</label><input style={inputBase} type="password" placeholder="Enter current password" value={pw.current} onChange={e => setPw({ ...pw, current: e.target.value })} onFocus={fB} onBlur={bB} /></div>
            <div style={{ marginBottom: 14 }}><label style={lbl}>New Password</label><input style={inputBase} type="password" placeholder="Min 8 characters" value={pw.next} onChange={e => setPw({ ...pw, next: e.target.value })} onFocus={fB} onBlur={bB} /></div>
            <div style={{ marginBottom: 16 }}><label style={lbl}>Confirm New Password</label><input style={inputBase} type="password" placeholder="Re-enter" value={pw.confirm} onChange={e => setPw({ ...pw, confirm: e.target.value })} onFocus={fB} onBlur={bB} /></div>
            <div style={{ display: "flex", gap: 10 }}>
              <button onClick={() => setChangePw(false)} style={{ padding: "9px 18px", background: "transparent", border: `1px solid ${C.border}`, borderRadius: 8, color: C.text2, cursor: "pointer", fontSize: 13, fontFamily: F.sans }}>Cancel</button>
              <button onClick={() => { setChangePw(false); save(); }} style={{ padding: "9px 18px", background: C.green, border: "none", borderRadius: 8, color: "#111", cursor: "pointer", fontSize: 13, fontFamily: F.sans, fontWeight: 700 }}>Update Password</button>
            </div>
          </div>
        )}
      </div>

      <div style={{ display: "flex", gap: 10, justifyContent: "flex-end", alignItems: "center" }}>
        <div style={{ display: "flex", gap: 10 }}>
          <button onClick={onBack} style={{ padding: "10px 20px", background: "transparent", border: `1px solid ${C.border}`, borderRadius: 8, color: C.text2, cursor: "pointer", fontSize: 13, fontFamily: F.sans }}>Cancel</button>
          <button onClick={save} style={{ padding: "10px 20px", background: C.green, border: "none", borderRadius: 8, color: "#111", cursor: "pointer", fontSize: 13, fontFamily: F.sans, fontWeight: 700, display: "flex", alignItems: "center", gap: 5 }}>
            {saved && <CheckIcon />} {saved ? "Saved!" : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
