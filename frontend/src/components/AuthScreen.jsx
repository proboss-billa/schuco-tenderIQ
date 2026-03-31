"use client";
import { useState, useEffect, useRef } from "react";
import { C, F, inputBase, lbl, btnG, fB, bB } from "@/lib/design";
import { api } from "@/lib/api";
import {
  SchucoFull, BackIcon, EyeIcon, EyeOffIcon, CheckIcon, MailIcon,
} from "@/components/Icons";

export default function AuthScreen({ onLogin }) {
  const [view, setView] = useState("login");
  const [showPw, setShowPw] = useState(false);
  const [form, setForm] = useState({ email: "", pw: "", first: "", last: "", cc: "+91", phone: "", pw2: "" });
  const [otpValues, setOtpValues] = useState(["", "", "", "", "", ""]);
  const [otpTimer, setOtpTimer] = useState(30);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const otpRefs = useRef([]);

  useEffect(() => {
    if (view === "otp" && otpTimer > 0) {
      const t = setTimeout(() => setOtpTimer(otpTimer - 1), 1000);
      return () => clearTimeout(t);
    }
  }, [view, otpTimer]);

  const handleOtpChange = (idx, val) => {
    if (val.length > 1) return;
    const nv = [...otpValues]; nv[idx] = val; setOtpValues(nv);
    if (val && idx < 5) otpRefs.current[idx + 1]?.focus();
  };
  const handleOtpKey = (idx, e) => {
    if (e.key === "Backspace" && !otpValues[idx] && idx > 0) otpRefs.current[idx - 1]?.focus();
  };

  const handleLogin = async () => {
    setError(""); setLoading(true);
    try {
      const data = await api.login(form.email, form.pw);
      onLogin(data.access_token);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleSignup = async () => {
    setError(""); setLoading(true);
    try {
      await api.signup(form.email, form.pw);
      setView("login");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const f = (k) => (e) => setForm({ ...form, [k]: e.target.value });

  return (
    <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", background: `radial-gradient(ellipse at 30% 20%, ${C.bg2} 0%, ${C.bg} 50%, ${C.navyDeep} 100%)`, fontFamily: F.sans, padding: 20 }}>
      <div style={{ position: "fixed", inset: 0, opacity: 0.02, backgroundImage: `linear-gradient(${C.green} 1px, transparent 1px), linear-gradient(90deg, ${C.green} 1px, transparent 1px)`, backgroundSize: "50px 50px", pointerEvents: "none" }} />
      <div style={{ width: "100%", maxWidth: 420, position: "relative", zIndex: 1, animation: "fadeUp 0.5s ease" }}>
        <div style={{ textAlign: "center", marginBottom: 32 }}>
          <div style={{ display: "inline-flex", justifyContent: "center", marginBottom: 16 }}><SchucoFull h={28} /></div>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8, marginBottom: 6 }}>
            <div style={{ height: 1, width: 32, background: C.border2 }} />
            <span style={{ fontSize: 22, fontWeight: 700, color: C.text1, letterSpacing: "-0.03em" }}>TenderIQ</span>
            <div style={{ height: 1, width: 32, background: C.border2 }} />
          </div>
          <div style={{ fontSize: 10, color: C.text3, letterSpacing: "0.18em", textTransform: "uppercase", fontWeight: 500 }}>Smart Tender Analysis</div>
        </div>

        <div style={{ background: C.bg1, borderRadius: 16, padding: "30px 28px", border: `1px solid ${C.border}`, boxShadow: "0 24px 64px rgba(0,0,0,0.4)" }}>

          {/* ── LOGIN ── */}
          {view === "login" && <>
            <h2 style={{ margin: "0 0 4px", fontSize: 19, fontWeight: 600, color: C.text1 }}>Welcome back</h2>
            <p style={{ margin: "0 0 26px", fontSize: 13, color: C.text2 }}>Sign in to continue analyzing tenders</p>
            <div style={{ marginBottom: 16 }}>
              <label style={lbl}>Email</label>
              <input style={inputBase} type="email" placeholder="you@company.com" value={form.email} onChange={f("email")} onFocus={fB} onBlur={bB} />
            </div>
            <div style={{ marginBottom: 8 }}>
              <label style={lbl}>Password</label>
              <div style={{ position: "relative" }}>
                <input style={{ ...inputBase, paddingRight: 40 }} type={showPw ? "text" : "password"} placeholder="Enter password" value={form.pw} onChange={f("pw")} onFocus={fB} onBlur={bB}
                  onKeyDown={e => e.key === "Enter" && handleLogin()} />
                <button onClick={() => setShowPw(!showPw)} style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", color: C.text3, cursor: "pointer", padding: 4 }}>
                  {showPw ? <EyeOffIcon /> : <EyeIcon />}
                </button>
              </div>
            </div>
            <div style={{ textAlign: "right", marginBottom: 22 }}>
              <button onClick={() => { setError(""); setView("forgot"); }} style={{ background: "none", border: "none", color: C.green, fontSize: 12, cursor: "pointer", fontFamily: F.sans, fontWeight: 600 }}>Forgot password?</button>
            </div>
            {error && <div style={{ marginBottom: 14, padding: "9px 12px", background: "rgba(255,90,90,0.08)", border: `1px solid rgba(255,90,90,0.2)`, borderRadius: 6, color: C.err, fontSize: 13 }}>{error}</div>}
            <button style={{ ...btnG, opacity: loading ? 0.7 : 1 }} onClick={handleLogin} disabled={loading}
              onMouseEnter={e => { if (!loading) e.target.style.background = C.accentHover; }}
              onMouseLeave={e => e.target.style.background = C.green}>
              {loading ? "Signing in…" : "Sign In"}
            </button>
            <p style={{ margin: "18px 0 0", textAlign: "center", fontSize: 13, color: C.text2 }}>
              Don't have an account?{" "}
              <button onClick={() => { setError(""); setView("register"); }} style={{ background: "none", border: "none", color: C.green, cursor: "pointer", fontFamily: F.sans, fontWeight: 700, fontSize: 13 }}>Sign up</button>
            </p>
          </>}

          {/* ── REGISTER ── */}
          {view === "register" && <>
            <h2 style={{ margin: "0 0 4px", fontSize: 19, fontWeight: 600, color: C.text1 }}>Create account</h2>
            <p style={{ margin: "0 0 22px", fontSize: 13, color: C.text2 }}>Start analyzing tender documents with AI</p>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
              <div><label style={lbl}>First Name</label><input style={inputBase} placeholder="John" value={form.first} onChange={f("first")} onFocus={fB} onBlur={bB} /></div>
              <div><label style={lbl}>Last Name</label><input style={inputBase} placeholder="Doe" value={form.last} onChange={f("last")} onFocus={fB} onBlur={bB} /></div>
            </div>
            <div style={{ marginBottom: 14 }}>
              <label style={lbl}>Email</label>
              <input style={inputBase} type="email" placeholder="you@company.com" value={form.email} onChange={f("email")} onFocus={fB} onBlur={bB} />
            </div>
            <div style={{ marginBottom: 14 }}>
              <label style={lbl}>Phone Number</label>
              <div style={{ display: "grid", gridTemplateColumns: "85px 1fr", gap: 8 }}>
                <select style={{ ...inputBase, cursor: "pointer", textAlign: "center", padding: "11px 6px" }} value={form.cc} onChange={f("cc")}>
                  {["+91", "+1", "+44", "+49", "+33", "+971", "+65", "+61"].map(c => <option key={c} value={c}>{c}</option>)}
                </select>
                <input style={inputBase} type="tel" placeholder="98765 43210" value={form.phone} onChange={f("phone")} onFocus={fB} onBlur={bB} />
              </div>
            </div>
            <div style={{ marginBottom: 14 }}>
              <label style={lbl}>Password</label>
              <div style={{ position: "relative" }}>
                <input style={{ ...inputBase, paddingRight: 40 }} type={showPw ? "text" : "password"} placeholder="Min 8 characters" value={form.pw} onChange={f("pw")} onFocus={fB} onBlur={bB} />
                <button onClick={() => setShowPw(!showPw)} style={{ position: "absolute", right: 10, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", color: C.text3, cursor: "pointer", padding: 4 }}>
                  {showPw ? <EyeOffIcon /> : <EyeIcon />}
                </button>
              </div>
            </div>
            <div style={{ marginBottom: 22 }}>
              <label style={lbl}>Confirm Password</label>
              <input style={inputBase} type="password" placeholder="Re-enter password" value={form.pw2} onChange={f("pw2")} onFocus={fB} onBlur={bB} />
            </div>
            {error && <div style={{ marginBottom: 14, padding: "9px 12px", background: "rgba(255,90,90,0.08)", border: `1px solid rgba(255,90,90,0.2)`, borderRadius: 6, color: C.err, fontSize: 13 }}>{error}</div>}
            <button style={{ ...btnG, opacity: loading ? 0.7 : 1 }} onClick={handleSignup} disabled={loading}
              onMouseEnter={e => { if (!loading) e.target.style.background = C.accentHover; }}
              onMouseLeave={e => e.target.style.background = C.green}>
              {loading ? "Creating…" : "Create Account"}
            </button>
            <p style={{ margin: "18px 0 0", textAlign: "center", fontSize: 13, color: C.text2 }}>
              Already have an account?{" "}
              <button onClick={() => { setError(""); setView("login"); }} style={{ background: "none", border: "none", color: C.green, cursor: "pointer", fontFamily: F.sans, fontWeight: 700, fontSize: 13 }}>Sign in</button>
            </p>
          </>}

          {/* ── FORGOT PASSWORD ── */}
          {view === "forgot" && <>
            <button onClick={() => setView("login")} style={{ background: "none", border: "none", color: C.text2, cursor: "pointer", display: "flex", alignItems: "center", gap: 4, padding: 0, marginBottom: 18, fontFamily: F.sans, fontSize: 12 }}>
              <BackIcon /> Back to login
            </button>
            <div style={{ display: "flex", justifyContent: "center", marginBottom: 20 }}>
              <div style={{ width: 52, height: 52, borderRadius: "50%", background: C.greenSubtle, border: `2px solid ${C.greenBorder}`, display: "flex", alignItems: "center", justifyContent: "center", color: C.green }}>
                <MailIcon />
              </div>
            </div>
            <h2 style={{ margin: "0 0 4px", fontSize: 19, fontWeight: 600, color: C.text1, textAlign: "center" }}>Reset password</h2>
            <p style={{ margin: "0 0 24px", fontSize: 13, color: C.text2, textAlign: "center" }}>We'll send a 6-digit OTP to your email</p>
            <div style={{ marginBottom: 22 }}>
              <label style={lbl}>Email</label>
              <input style={inputBase} type="email" placeholder="you@company.com" value={form.email} onChange={f("email")} onFocus={fB} onBlur={bB} />
            </div>
            <button style={btnG} onClick={() => { setView("otp"); setOtpTimer(30); setOtpValues(["", "", "", "", "", ""]); }}
              onMouseEnter={e => e.target.style.background = C.accentHover} onMouseLeave={e => e.target.style.background = C.green}>
              Send OTP
            </button>
          </>}

          {/* ── OTP ── */}
          {view === "otp" && <>
            <button onClick={() => setView("forgot")} style={{ background: "none", border: "none", color: C.text2, cursor: "pointer", display: "flex", alignItems: "center", gap: 4, padding: 0, marginBottom: 18, fontFamily: F.sans, fontSize: 12 }}>
              <BackIcon /> Back
            </button>
            <h2 style={{ margin: "0 0 4px", fontSize: 19, fontWeight: 600, color: C.text1, textAlign: "center" }}>Enter OTP</h2>
            <p style={{ margin: "0 0 28px", fontSize: 13, color: C.text2, textAlign: "center" }}>
              Sent to <strong style={{ color: C.text1 }}>{form.email || "your email"}</strong>
            </p>
            <div style={{ display: "flex", gap: 8, justifyContent: "center", marginBottom: 24 }}>
              {otpValues.map((v, i) => (
                <input key={i} ref={el => { otpRefs.current[i] = el; }} value={v}
                  onChange={e => handleOtpChange(i, e.target.value)} onKeyDown={e => handleOtpKey(i, e)} maxLength={1}
                  style={{ width: 46, height: 52, textAlign: "center", fontSize: 20, fontWeight: 700, fontFamily: F.mono, background: C.bg, border: `1.5px solid ${v ? C.green : C.border2}`, borderRadius: 10, color: C.text1, outline: "none", boxShadow: v ? `0 0 0 3px ${C.greenGlow}` : "none", transition: "all 0.2s" }}
                  onFocus={e => { e.target.style.borderColor = C.green; e.target.style.boxShadow = `0 0 0 3px ${C.greenGlow}`; }}
                  onBlur={e => { if (!v) { e.target.style.borderColor = C.border2; e.target.style.boxShadow = "none"; } }}
                />
              ))}
            </div>
            <button style={btnG} onClick={() => setView("newpass")}
              onMouseEnter={e => e.target.style.background = C.accentHover} onMouseLeave={e => e.target.style.background = C.green}>
              Verify OTP
            </button>
            <p style={{ margin: "16px 0 0", textAlign: "center", fontSize: 12, color: C.text3 }}>
              {otpTimer > 0
                ? <>Resend in <strong style={{ color: C.text2 }}>{otpTimer}s</strong></>
                : <button onClick={() => { setOtpTimer(30); setOtpValues(["", "", "", "", "", ""]); }} style={{ background: "none", border: "none", color: C.green, cursor: "pointer", fontFamily: F.sans, fontWeight: 600, fontSize: 12 }}>Resend OTP</button>}
            </p>
          </>}

          {/* ── NEW PASSWORD ── */}
          {view === "newpass" && <>
            <div style={{ display: "flex", justifyContent: "center", marginBottom: 20 }}>
              <div style={{ width: 52, height: 52, borderRadius: "50%", background: C.greenSubtle, border: `2px solid ${C.greenBorder}`, display: "flex", alignItems: "center", justifyContent: "center", color: C.green }}>
                <CheckIcon />
              </div>
            </div>
            <h2 style={{ margin: "0 0 4px", fontSize: 19, fontWeight: 600, color: C.text1, textAlign: "center" }}>Set new password</h2>
            <p style={{ margin: "0 0 24px", fontSize: 13, color: C.text2, textAlign: "center" }}>Choose a strong password for your account</p>
            <div style={{ marginBottom: 14 }}>
              <label style={lbl}>New Password</label>
              <input style={inputBase} type="password" placeholder="Min 8 characters" onFocus={fB} onBlur={bB} />
            </div>
            <div style={{ marginBottom: 22 }}>
              <label style={lbl}>Confirm Password</label>
              <input style={inputBase} type="password" placeholder="Re-enter password" onFocus={fB} onBlur={bB} />
            </div>
            <button style={btnG} onClick={() => setView("login")}
              onMouseEnter={e => e.target.style.background = C.accentHover} onMouseLeave={e => e.target.style.background = C.green}>
              Reset Password
            </button>
          </>}
        </div>
      </div>
    </div>
  );
}
