export const C = {
  navy: "#002B50",
  navyDark: "#001428",
  navyDeep: "#000D1A",
  green: "#8BC53F",
  greenDark: "#6FA032",
  greenSubtle: "rgba(139,197,63,0.08)",
  greenBorder: "rgba(139,197,63,0.25)",
  greenGlow: "rgba(139,197,63,0.12)",
  accent: "#8BC53F",
  accentHover: "#9DD44F",
  text1: "#EDF1F5",
  text2: "#8A9BB0",
  text3: "#4E6178",
  bg: "#0A1520",
  bg1: "#0D1D2E",
  bg2: "#112840",
  bg3: "#16344F",
  border: "rgba(138,155,176,0.10)",
  border2: "rgba(138,155,176,0.18)",
  ok: "#00C48C",
  warn: "#FFB340",
  err: "#FF5A5A",
  white: "#fff",
};

export const F = {
  sans: "'DM Sans', system-ui, sans-serif",
  mono: "'JetBrains Mono', monospace",
};

export const inputBase = {
  width: "100%",
  padding: "11px 14px",
  background: C.bg,
  border: `1px solid ${C.border2}`,
  borderRadius: 8,
  color: C.text1,
  fontSize: 14,
  fontFamily: F.sans,
  outline: "none",
  boxSizing: "border-box",
  transition: "border-color 0.2s, box-shadow 0.2s",
};

export const lbl = {
  display: "block",
  fontSize: 12,
  fontWeight: 500,
  color: C.text2,
  marginBottom: 6,
  fontFamily: F.sans,
  letterSpacing: "0.02em",
};

export const btnG = {
  width: "100%",
  padding: "12px",
  background: C.green,
  color: "#111",
  border: "none",
  borderRadius: 8,
  fontSize: 14,
  fontWeight: 700,
  fontFamily: F.sans,
  cursor: "pointer",
  transition: "all 0.2s",
};

export const fB = (e) => {
  e.target.style.borderColor = C.green;
  e.target.style.boxShadow = `0 0 0 3px ${C.greenGlow}`;
};

export const bB = (e) => {
  e.target.style.borderColor = C.border2;
  e.target.style.boxShadow = "none";
};
