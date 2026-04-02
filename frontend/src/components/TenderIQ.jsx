"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import { C, F } from "@/lib/design";
import { api } from "@/lib/api";
import ProfileScreen from "@/components/ProfileScreen";
import AuthScreen from "@/components/AuthScreen";
import ResultsPanel from "@/components/ResultsPanel";
import { ChatCtxMenu, RenameModal, DeleteModal } from "@/components/Modals";
import {
  SchucoMark, SendIcon, UploadIcon, FileIcon, ChatIcon,
  PlusIcon, MenuIcon, CloseIcon, PanelIcon, MoreIcon,
  LogoutIcon, ChevronLeftIcon,
} from "@/components/Icons";

// ── Typing indicator ────────────────────────────────
const STAGES = [
  "Extracting text from document",
  "Identifying performance parameters",
  "Cross-referencing Schüco specs",
  "Generating analysis summary",
];

function TypingIndicator({ stage }) {
  const [dots, setDots] = useState("");
  useEffect(() => {
    const iv = setInterval(() => setDots(d => d.length >= 3 ? "" : d + "."), 400);
    return () => clearInterval(iv);
  }, []);
  return (
    <div style={{ display: "flex", gap: 12, marginBottom: 20, animation: "fadeUp .3s ease" }}>
      <div style={{ width: 32, height: 32, borderRadius: 8, overflow: "hidden", flexShrink: 0 }}><SchucoMark /></div>
      <div style={{ padding: "14px 18px", background: C.bg1, borderRadius: "14px 14px 14px 4px", border: `1px solid ${C.border}`, minWidth: 280 }}>
        <div style={{ height: 3, background: C.border, borderRadius: 2, marginBottom: 12, overflow: "hidden" }}>
          <div style={{ height: "100%", background: `linear-gradient(90deg, ${C.green}, ${C.accentHover})`, borderRadius: 2, width: `${((stage + 1) / STAGES.length) * 100}%`, transition: "width 0.6s ease" }} />
        </div>
        <div style={{ fontSize: 13, color: C.text1, fontWeight: 500, marginBottom: 4 }}>{STAGES[stage]}{dots}</div>
        <div style={{ fontSize: 11, color: C.text3 }}>Step {stage + 1} of {STAGES.length}</div>
      </div>
    </div>
  );
}

// ── Markdown-lite bold renderer ──────────────────────
function BoldText({ text }) {
  return text.split("**").map((part, j) =>
    j % 2 === 1 ? <strong key={j}>{part}</strong> : <span key={j}>{part}</span>
  );
}

// ── Main App ─────────────────────────────────────────
export default function TenderIQ() {
  const [screen, setScreen] = useState("auth");
  const [token, setToken] = useState(null);
  const [user, setUser] = useState(null);
  const [sideOpen, setSideOpen] = useState(true);
  const [showResults, setShowResults] = useState(false);
  const [mobResults, setMobResults] = useState(false);
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState([]);
  const [isMob, setIsMob] = useState(false);
  const [isTyping, setIsTyping] = useState(false);
  const [typingStage, setTypingStage] = useState(0);
  const [isDragging, setIsDragging] = useState(false);
  const [panelPos, setPanelPos] = useState(null); // {x, y} — null = default position
  const [panelWidth, setPanelWidth] = useState(400);
  const [panelHeight, setPanelHeight] = useState(null); // null = full height
  const panelDragRef = useRef(null);
  const resizeState = useRef(null);
  const [ctxMenu, setCtxMenu] = useState(null);
  const [renameModal, setRenameModal] = useState(null);
  const [deleteModal, setDeleteModal] = useState(null);
  const [currentProjectId, setCurrentProjectId] = useState(null);
  const [currentProjectName, setCurrentProjectName] = useState("");
  const [chats, setChats] = useState([]);
  const [chatCounts, setChatCounts] = useState({});
  const fileRef = useRef(null);
  const chatEnd = useRef(null);
  const dragState = useRef(null);

  // Auto-login with default credentials
  useEffect(() => {
    api.login("abc@sooru.ai", "12345678")
      .then(data => {
        setToken(data.access_token);
        setScreen("main");
        api.listProjects(data.access_token).then(projects => {
          if (Array.isArray(projects) && projects.length > 0) {
            setChats(projects);
            const latest = projects[0];
            setCurrentProjectId(latest.id);
            setCurrentProjectName(latest.name);
            api.getChatHistory(data.access_token, latest.id).then(msgs => { if (Array.isArray(msgs)) setMsgs(msgs); });
          }
        });
      })
      .catch(() => {}); // fall through to login screen if it fails
  }, []);

  // Responsive
  useEffect(() => {
    const c = () => setIsMob(window.innerWidth < 768);
    c(); window.addEventListener("resize", c);
    return () => window.removeEventListener("resize", c);
  }, []);

  // Auto-scroll
  useEffect(() => { chatEnd.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs, isTyping]);

  // Track per-project chat counts
  useEffect(() => {
    if (currentProjectId) {
      const userMsgs = msgs.filter(m => m.role === "user").length;
      if (userMsgs > 0) setChatCounts(prev => ({ ...prev, [currentProjectId]: userMsgs }));
    }
  }, [msgs, currentProjectId]);

  // Panel drag handlers
  const onPanelMouseDown = (e) => {
    e.preventDefault();
    const panel = panelDragRef.current;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    dragState.current = { startX: e.clientX - rect.left, startY: e.clientY - rect.top };
    const onMove = (ev) => {
      const x = Math.max(0, Math.min(window.innerWidth - rect.width, ev.clientX - dragState.current.startX));
      const y = Math.max(0, Math.min(window.innerHeight - 60, ev.clientY - dragState.current.startY));
      setPanelPos({ x, y });
    };
    const onUp = () => { document.removeEventListener("mousemove", onMove); document.removeEventListener("mouseup", onUp); };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  const onResizeMouseDown = (e, dir) => {
    e.preventDefault();
    e.stopPropagation();
    const panel = panelDragRef.current;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    const startX = e.clientX, startY = e.clientY;
    const startW = rect.width, startH = rect.height;
    const startLeft = rect.left, startTop = rect.top;
    const onMove = (ev) => {
      const dx = ev.clientX - startX, dy = ev.clientY - startY;
      if (dir.includes("e")) setPanelWidth(w => Math.max(300, Math.min(900, startW + dx)));
      if (dir.includes("w")) { setPanelWidth(Math.max(300, Math.min(900, startW - dx))); setPanelPos(p => ({ x: Math.max(0, startLeft + dx), y: p ? p.y : startTop })); }
      if (dir.includes("s")) setPanelHeight(Math.max(200, Math.min(window.innerHeight - 20, startH + dy)));
      if (dir.includes("n")) { setPanelHeight(Math.max(200, Math.min(window.innerHeight - 20, startH - dy))); setPanelPos(p => ({ x: p ? p.x : startLeft, y: Math.max(0, startTop + dy) })); }
    };
    const onUp = () => { document.removeEventListener("mousemove", onMove); document.removeEventListener("mouseup", onUp); };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp);
  };

  // Project history is loaded on login, no need to reload on mount

  // ── Run the 4-stage typing animation ──
  const runTypingAnimation = (onDone) => {
    setIsTyping(true);
    setTypingStage(0);
    let s = 0;
    const iv = setInterval(async () => {
      s++;
      if (s >= STAGES.length) {
        clearInterval(iv);
        await onDone();
        setIsTyping(false);
      } else {
        setTypingStage(s);
      }
    }, 900);
    return iv;
  };

  // ── Send handler ──
  const handleSend = async () => {
    if (!input.trim() && files.length === 0) return;
    const userText = input.trim();
    const uploadedFiles = [...files];
    setInput("");
    setFiles([]);

    // Build user message
    const userMsg = uploadedFiles.length > 0
      ? { role: "user", type: "file", content: uploadedFiles.map(f => f.name).join(", "), text: userText || "Analyze this tender document" }
      : { role: "user", type: "text", content: userText };

    const newMsgs = [...msgs, userMsg];
    setMsgs(newMsgs);

    if (uploadedFiles.length > 0) {
      // Upload + process flow
      const projectName = userText || uploadedFiles[0].name.replace(/\.[^/.]+$/, "");
      runTypingAnimation(async () => {
        try {
          const created = await api.createProject(token, projectName, "", uploadedFiles);
          const pid = created.project_id;
          await api.processProject(token, pid);
          setCurrentProjectId(pid);
          setCurrentProjectName(projectName);
          setShowResults(true);
          const assistantMsg = {
            role: "assistant", type: "analysis",
            content: `I've analyzed **${projectName}** and extracted the key parameters. You can see the structured results in the side panel.\n\nWhat would you like to dive deeper into?`,
          };
          setMsgs([...newMsgs, assistantMsg]);
          // Add to sidebar
          setChats(prev => [{ id: pid, title: projectName, date: "Today" }, ...prev]);
        } catch (e) {
          setMsgs([...newMsgs, { role: "assistant", type: "text", content: `Error during analysis: ${e.message}` }]);
        }
      });
    } else if (currentProjectId) {
      // Chat with existing project
      try {
        const res = await api.query(token, currentProjectId, userText);
        const sources = (res.sources || []).filter(s => s.page || s.section);
        setMsgs([...newMsgs, {
          role: "assistant", type: "text",
          content: res.answer || res.response || "No response.",
          sources: sources.map(s => `${s.document}${s.page ? ` · Page ${s.page}` : ""}${s.section ? ` · ${s.section}` : ""}`),
        }]);
      } catch (e) {
        setMsgs([...newMsgs, { role: "assistant", type: "text", content: `Query failed: ${e.message}` }]);
      }
    } else {
      // No project loaded — guide user
      setMsgs([...newMsgs, { role: "assistant", type: "text", content: "Please upload a tender document first so I can analyse it and answer your questions." }]);
    }
  };

  const openChat = (chat) => {
    setCurrentProjectId(chat.id);
    setCurrentProjectName(chat.title);
    setMsgs([
      { role: "user", type: "file", content: `${chat.title}.pdf`, text: "Load this analysis" },
      { role: "assistant", type: "analysis", content: `Loaded **${chat.title}**. The extracted parameters are ready in the side panel. What would you like to know?` },
    ]);
    setShowResults(true);
    if (isMob) setSideOpen(false);
  };

  const handleCtx = (e, chat) => { e.preventDefault(); e.stopPropagation(); setCtxMenu({ x: e.clientX, y: e.clientY, chat }); };

  const newAnalysis = () => {
    setMsgs([]);
    setShowResults(false);
    setFiles([]);
    setIsTyping(false);
    setCurrentProjectId(null);
    setCurrentProjectName("");
  };

  // ── Auth screen ──
  const handleLogin = (accessToken) => {
    setToken(accessToken);
    setScreen("main");
    // Load project history after login
    api.listProjects(accessToken).then(projects => {
      if (Array.isArray(projects) && projects.length > 0) {
        setChats(projects.map(p => ({
          id: p.project_id || p.id,
          title: p.project_name || p.name || "Untitled",
          date: p.created_at ? new Date(p.created_at).toLocaleDateString() : "",
        })));
      }
    });
  };

  if (screen === "auth") return <AuthScreen onLogin={handleLogin} />;

  // ── Profile screen ──
  if (screen === "profile") return (
    <div style={{ minHeight: "100vh", background: C.bg }}>
      <ProfileScreen user={user} onBack={() => setScreen("main")} />
    </div>
  );

  const hasContent = msgs.length > 0 || isTyping;

  const SIDE_W = 258;
  const SIDE_MINI = 64;
  const sidebarW = sideOpen ? SIDE_W : SIDE_MINI;
  const logout = () => { setToken(null); setScreen("auth"); setChats([]); newAnalysis(); };

  const iconBtn = (onClick, title, children, danger = false) => (
    <button onClick={onClick} title={title}
      style={{ width: 40, height: 40, borderRadius: 10, background: "transparent", border: "none", color: danger ? C.text3 : C.text2, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", transition: "all 0.15s", fontFamily: F.sans }}
      onMouseEnter={e => { e.currentTarget.style.background = danger ? "rgba(255,90,90,0.1)" : C.bg2; e.currentTarget.style.color = danger ? C.err : C.text1; }}
      onMouseLeave={e => { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = danger ? C.text3 : C.text2; }}>
      {children}
    </button>
  );

  return (
    <div style={{ height: "100vh", background: C.bg, fontFamily: F.sans, overflow: "hidden" }}>

      {/* ── Floating Sidebar ── */}
      <div style={{
        position: "fixed", left: 10, top: 10, bottom: 10,
        width: sidebarW,
        background: C.navyDark,
        borderRadius: 18,
        border: `1px solid ${C.border}`,
        boxShadow: "0 8px 40px rgba(0,0,0,0.55)",
        display: "flex", flexDirection: "column",
        overflow: "hidden",
        transition: "width 0.25s cubic-bezier(0.4,0,0.2,1)",
        zIndex: 100,
      }}>

        {/* ── Header: TenderIQ + Partnership ── */}
        <div style={{ padding: sideOpen ? "14px 12px 4px" : "12px 0 10px", borderBottom: `1px solid ${C.border}`, flexShrink: 0 }}>
          {sideOpen ? (
            <>
              {/* Row 1: TenderIQ logo + name + collapse */}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <img src="/teiq.png" alt="TenderIQ" style={{ height: 26, width: "auto", objectFit: "contain" }} />
                  <span style={{ fontSize: 15, fontWeight: 700, color: C.text1, whiteSpace: "nowrap", letterSpacing: "-0.02em" }}>TenderIQ</span>
                </div>
                <button onClick={() => setSideOpen(false)}
                  style={{ background: "none", border: "none", color: C.text3, cursor: "pointer", padding: 4, borderRadius: 6, display: "flex", flexShrink: 0, transition: "color 0.15s" }}
                  onMouseEnter={e => e.currentTarget.style.color = C.text1}
                  onMouseLeave={e => e.currentTarget.style.color = C.text3}>
                  <ChevronLeftIcon />
                </button>
              </div>
              {/* Row 2: Schüco × Sooru */}
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: -14 }}>
                <img src="/schu.png" alt="Schüco" style={{ height: 40, width: "auto", objectFit: "contain" }} />
                <span style={{ color: "rgba(255,255,255,0.5)", fontSize: 12, fontWeight: 300 }}>×</span>
                <img src="/suru.png" alt="Sooru" style={{ height: 10, width: "auto", objectFit: "contain" }} />
                <span style={{ color: C.text2, fontSize: 10, fontWeight: 600, whiteSpace: "nowrap" }}>Sooru.AI</span>
              </div>
            </>
          ) : (
            /* Collapsed: teiq logo + expand button (inline) */
            <div style={{ display: "flex", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 4 }}>
              <img src="/teiq.png" alt="TenderIQ" style={{ height: 22, width: "auto", objectFit: "contain", flexShrink: 0 }} />
              <button onClick={() => setSideOpen(true)} title="Expand"
                style={{ background: "none", border: `1px solid ${C.border}`, borderRadius: 5, color: C.text3, cursor: "pointer", padding: "1px 4px", display: "flex", alignItems: "center", justifyContent: "center", transition: "all 0.15s", flexShrink: 0 }}
                onMouseEnter={e => { e.currentTarget.style.color = C.text1; e.currentTarget.style.borderColor = C.text2; }}
                onMouseLeave={e => { e.currentTarget.style.color = C.text3; e.currentTarget.style.borderColor = C.border; }}>
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
              </button>
            </div>
          )}
        </div>

        {/* ── New Analysis ── */}
        <div style={{ padding: sideOpen ? "10px 10px 6px" : "10px 0 6px", display: "flex", justifyContent: "center", flexShrink: 0 }}>
          {sideOpen ? (
            <button onClick={newAnalysis}
              style={{ width: "100%", padding: "9px 14px", background: C.greenSubtle, border: `1px solid ${C.greenBorder}`, borderRadius: 9, color: C.green, cursor: "pointer", fontSize: 13, fontWeight: 700, fontFamily: F.sans, display: "flex", alignItems: "center", gap: 8, transition: "all 0.15s" }}
              onMouseEnter={e => e.currentTarget.style.background = C.greenGlow}
              onMouseLeave={e => e.currentTarget.style.background = C.greenSubtle}>
              <PlusIcon /> New Analysis
            </button>
          ) : (
            iconBtn(newAnalysis, "New Analysis", <PlusIcon />)
          )}
        </div>

        {/* ── Chats ── */}
        <div style={{ flex: 1, overflowY: "auto", padding: sideOpen ? "4px 8px" : "4px 0" }}>
          {sideOpen ? (
            <>
              <div style={{ fontSize: 10, fontWeight: 600, color: C.text3, padding: "6px 8px 5px", letterSpacing: "0.1em", textTransform: "uppercase" }}>Recent</div>
              {chats.length === 0 && <div style={{ padding: "10px 8px", fontSize: 12, color: C.text3 }}>No analyses yet</div>}
              {chats.map(chat => (
                <div key={chat.id} style={{ position: "relative", marginBottom: 1 }}>
                  <button onClick={() => openChat(chat)} onContextMenu={e => handleCtx(e, chat)}
                    style={{ width: "100%", padding: "8px 30px 8px 10px", background: currentProjectId === chat.id ? C.bg2 : "transparent", border: "none", borderRadius: 7, color: currentProjectId === chat.id ? C.text1 : C.text2, cursor: "pointer", textAlign: "left", fontFamily: F.sans, fontSize: 13, display: "flex", alignItems: "center", gap: 8, transition: "all 0.1s" }}
                    onMouseEnter={e => { if (currentProjectId !== chat.id) { e.currentTarget.style.background = C.bg2; e.currentTarget.style.color = C.text1; } }}
                    onMouseLeave={e => { if (currentProjectId !== chat.id) { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = C.text2; } }}>
                    <div style={{ position: "relative", flexShrink: 0 }}>
                      <ChatIcon />
                    </div>
                    <div style={{ flex: 1, overflow: "hidden" }}>
                      <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{chat.title}</div>
                      <div style={{ fontSize: 10, color: C.text3, marginTop: 1 }}>{chat.date}</div>
                    </div>
                  </button>
                  <button onClick={e => handleCtx(e, chat)}
                    style={{ position: "absolute", right: 4, top: "50%", transform: "translateY(-50%)", background: "none", border: "none", color: C.text3, cursor: "pointer", padding: 2, opacity: 0.4, transition: "opacity 0.15s" }}
                    onMouseEnter={e => e.currentTarget.style.opacity = 1}
                    onMouseLeave={e => e.currentTarget.style.opacity = 0.4}>
                    <MoreIcon />
                  </button>
                </div>
              ))}
            </>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 2, paddingTop: 4 }}>
              {chats.length === 0 && (
                <div style={{ color: C.text3, fontSize: 10, textAlign: "center", padding: "8px 0" }}>—</div>
              )}
              {chats.map(chat => (
                <div key={chat.id} style={{ position: "relative" }}>
                  <button onClick={() => openChat(chat)} title={chat.title}
                    style={{ width: 40, height: 40, borderRadius: 10, background: currentProjectId === chat.id ? C.bg2 : "transparent", border: currentProjectId === chat.id ? `1px solid ${C.border}` : "none", color: currentProjectId === chat.id ? C.green : C.text3, cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", transition: "all 0.15s" }}
                    onMouseEnter={e => { if (currentProjectId !== chat.id) { e.currentTarget.style.background = C.bg2; e.currentTarget.style.color = C.text2; } }}
                    onMouseLeave={e => { if (currentProjectId !== chat.id) { e.currentTarget.style.background = "transparent"; e.currentTarget.style.color = C.text3; } }}>
                    <ChatIcon />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── Sign Out ── */}
        <div style={{ padding: sideOpen ? "8px 10px" : "8px 0", borderTop: `1px solid ${C.border}`, display: "flex", justifyContent: "center", flexShrink: 0 }}>
          {sideOpen ? (
            <button onClick={logout}
              style={{ width: "100%", padding: "8px 12px", background: "transparent", border: `1px solid ${C.border}`, borderRadius: 8, color: C.text3, cursor: "pointer", fontSize: 12, fontFamily: F.sans, fontWeight: 500, display: "flex", alignItems: "center", gap: 8, transition: "all 0.15s" }}
              onMouseEnter={e => { e.currentTarget.style.color = C.err; e.currentTarget.style.borderColor = C.err; e.currentTarget.style.background = "rgba(255,90,90,0.06)"; }}
              onMouseLeave={e => { e.currentTarget.style.color = C.text3; e.currentTarget.style.borderColor = C.border; e.currentTarget.style.background = "transparent"; }}>
              <LogoutIcon /> Sign Out
            </button>
          ) : (
            iconBtn(logout, "Sign Out", <LogoutIcon />, true)
          )}
        </div>
      </div>

      {/* Mobile overlay */}
      {isMob && sideOpen && <div onClick={() => setSideOpen(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 99 }} />}

      {/* ── Main area ── */}
      <div style={{ marginLeft: isMob ? 0 : sidebarW + 20, height: "100vh", display: "flex", flexDirection: "column", overflow: "hidden", transition: "margin-left 0.25s cubic-bezier(0.4,0,0.2,1)" }}>
        {/* Topbar */}
        <div style={{ height: 50, display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 16px", borderBottom: `1px solid ${C.border}`, flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span style={{ fontSize: 14, fontWeight: 500, color: C.text1 }}>{currentProjectName || (hasContent ? "Analysis" : "New Analysis")}</span>
          </div>
          {currentProjectId && (
            <button onClick={() => isMob ? setMobResults(true) : setShowResults(true)}
              style={{ padding: "5px 12px", background: showResults ? C.green : C.greenSubtle, border: `1px solid ${C.greenBorder}`, borderRadius: 6, color: showResults ? "#111" : C.green, cursor: "pointer", fontSize: 12, fontFamily: F.sans, fontWeight: 600, display: "flex", alignItems: "center", gap: 5, transition: "all 0.15s" }}>
              <PanelIcon /> Results
            </button>
          )}
        </div>

        <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
          {/* Chat column */}
          <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
            {/* Messages */}
            <div style={{ flex: 1, overflowY: "auto", padding: isMob ? "16px" : "24px" }}>
              {!hasContent && (
                <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", textAlign: "center", padding: 24, animation: "fadeUp 0.5s ease" }}>
                  <div style={{ width: 72, height: 72, borderRadius: 16, background: C.greenSubtle, display: "flex", alignItems: "center", justifyContent: "center", marginBottom: 24, border: `1px solid ${C.greenBorder}` }}>
                    <SchucoMark />
                  </div>
                  <h1 style={{ margin: "0 0 8px", fontSize: 22, fontWeight: 700, color: C.text1 }}>Welcome to TenderIQ</h1>
                  <p style={{ margin: "0 0 28px", fontSize: 14, color: C.text2, maxWidth: 400, lineHeight: 1.6 }}>
                    Upload a tender document or BoQ to automatically extract wind loads, water resistance, system specs, and compliance data.
                  </p>

                  {/* Drop zone */}
                  <div
                    onClick={() => fileRef.current?.click()}
                    onDragOver={e => { e.preventDefault(); setIsDragging(true); }}
                    onDragLeave={() => setIsDragging(false)}
                    onDrop={e => {
                      e.preventDefault();
                      setIsDragging(false);
                      const dropped = Array.from(e.dataTransfer.files).filter(f =>
                        /\.(pdf|docx?|xlsx?)$/i.test(f.name)
                      );
                      if (dropped.length) setFiles(dropped);
                    }}
                    style={{
                      width: "100%", maxWidth: 480, padding: "36px 24px",
                      border: `2px dashed ${isDragging ? C.green : C.border}`,
                      borderRadius: 14, cursor: "pointer", marginBottom: 20,
                      background: isDragging ? C.greenSubtle : C.bg1,
                      transition: "all 0.2s",
                    }}
                    onMouseEnter={e => { if (!isDragging) e.currentTarget.style.borderColor = C.greenBorder; }}
                    onMouseLeave={e => { if (!isDragging) e.currentTarget.style.borderColor = C.border; }}
                  >
                    <div style={{ color: C.green, marginBottom: 14, display: "flex", justifyContent: "center" }}><UploadIcon /></div>
                    <div style={{ fontSize: 15, fontWeight: 600, color: C.text1, marginBottom: 6 }}>
                      {isDragging ? "Drop to upload" : "Drag & drop your file here"}
                    </div>
                    <div style={{ fontSize: 12, color: C.text3, marginBottom: 16 }}>or click to browse</div>
                    <div style={{ display: "flex", justifyContent: "center", gap: 8, flexWrap: "wrap" }}>
                      {["PDF", "DOCX", "XLSX"].map(ext => (
                        <span key={ext} style={{ padding: "3px 10px", background: C.bg, border: `1px solid ${C.border}`, borderRadius: 20, fontSize: 11, color: C.text3, fontWeight: 600 }}>{ext}</span>
                      ))}
                    </div>
                  </div>

                  {/* Selected files preview */}
                  {files.length > 0 && (
                    <div style={{ width: "100%", maxWidth: 480, marginBottom: 16 }}>
                      {files.map((f, i) => (
                        <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "9px 14px", background: C.bg1, borderRadius: 8, border: `1px solid ${C.greenBorder}`, marginBottom: 6, fontSize: 13, color: C.text1 }}>
                          <FileIcon />
                          <span style={{ flex: 1, textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
                          <button onClick={e => { e.stopPropagation(); setFiles(files.filter((_, j) => j !== i)); }} style={{ background: "none", border: "none", color: C.text3, cursor: "pointer", display: "flex" }}><CloseIcon /></button>
                        </div>
                      ))}
                      <button onClick={handleSend}
                        style={{ width: "100%", padding: "11px 0", background: C.green, border: "none", borderRadius: 8, color: "#111", fontWeight: 700, fontSize: 14, fontFamily: F.sans, cursor: "pointer", marginTop: 4 }}>
                        Analyse Document
                      </button>
                    </div>
                  )}

                  <div style={{ display: "flex", gap: 20, marginTop: 8 }}>
                    {[
                      { icon: <FileIcon />, t: "Auto-Extract", d: "Wind, water, thermal data" },
                      { icon: <ChatIcon />, t: "Ask Questions", d: "Chat about your tender" },
                    ].map((item, i) => (
                      <div key={i} style={{ padding: "14px 18px", background: C.bg1, borderRadius: 10, border: `1px solid ${C.border}`, textAlign: "center", minWidth: 130 }}>
                        <div style={{ color: C.green, marginBottom: 8 }}>{item.icon}</div>
                        <div style={{ fontSize: 12, fontWeight: 600, color: C.text1, marginBottom: 3 }}>{item.t}</div>
                        <div style={{ fontSize: 11, color: C.text3 }}>{item.d}</div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {msgs.map((m, i) => (
                <div key={i} style={{ display: "flex", gap: 12, marginBottom: 18, justifyContent: m.role === "user" ? "flex-end" : "flex-start", animation: "fadeUp 0.3s ease" }}>
                  {m.role === "assistant" && (
                    <div style={{ width: 32, height: 32, borderRadius: 8, overflow: "hidden", flexShrink: 0 }}><SchucoMark /></div>
                  )}
                  <div style={{ maxWidth: "75%", padding: "12px 16px", background: m.role === "user" ? `linear-gradient(135deg, ${C.green}, ${C.greenDark})` : C.bg1, borderRadius: m.role === "user" ? "16px 16px 4px 16px" : "16px 16px 16px 4px", border: m.role === "assistant" ? `1px solid ${C.border}` : "none" }}>
                    {m.type === "file" && (
                      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 10px", background: "rgba(0,0,0,0.15)", borderRadius: 6, marginBottom: 8, fontSize: 12 }}>
                        <FileIcon /><span style={{ color: m.role === "user" ? "#111" : C.text1 }}>{m.content}</span>
                      </div>
                    )}
                    <div style={{ fontSize: 14, lineHeight: 1.65, color: m.role === "user" ? "#111" : C.text1, whiteSpace: "pre-wrap", fontWeight: m.role === "user" ? 500 : 400 }}>
                      <BoldText text={m.text || m.content} />
                    </div>
                    {m.sources?.length > 0 && (
                      <div style={{ marginTop: 10, padding: "8px 10px", background: "rgba(0,0,0,0.15)", borderRadius: 6, fontSize: 11, color: C.text3, borderTop: `1px solid rgba(255,255,255,0.06)` }}>
                        <div style={{ fontWeight: 600, marginBottom: 5, color: C.text2, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.05em" }}>Sources</div>
                        {m.sources.map((s, j) => (
                          <div key={j} style={{ display: "flex", alignItems: "flex-start", gap: 5, marginBottom: 3 }}>
                            <span style={{ color: C.green, fontWeight: 700, flexShrink: 0 }}>·</span>
                            <span>{s}</span>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ))}

              {isTyping && <TypingIndicator stage={typingStage} />}
              <div ref={chatEnd} />
            </div>

            {/* Input bar */}
            <div style={{ padding: isMob ? "10px 12px 14px" : "10px 24px 18px", borderTop: hasContent ? `1px solid ${C.border}` : "none", flexShrink: 0 }}>
              {files.length > 0 && (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8 }}>
                  {files.map((f, i) => (
                    <div key={i} style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 10px", background: C.bg1, borderRadius: 6, fontSize: 12, color: C.text2, border: `1px solid ${C.border}` }}>
                      <FileIcon /> {f.name}
                      <button onClick={() => setFiles(files.filter((_, j) => j !== i))} style={{ background: "none", border: "none", color: C.text3, cursor: "pointer", padding: 2, display: "flex" }}><CloseIcon /></button>
                    </div>
                  ))}
                </div>
              )}
              <div style={{ display: "flex", alignItems: "flex-end", gap: 8, background: C.bg1, borderRadius: 12, border: `1px solid ${C.border}`, padding: "5px 8px 5px 5px" }}>
                <button onClick={() => fileRef.current?.click()}
                  style={{ padding: 8, background: "none", border: "none", color: C.text3, cursor: "pointer", borderRadius: 6, flexShrink: 0 }}
                  onMouseEnter={e => e.currentTarget.style.color = C.green}
                  onMouseLeave={e => e.currentTarget.style.color = C.text3}>
                  <UploadIcon />
                </button>
                <input ref={fileRef} type="file" multiple accept=".pdf,.xlsx,.xls,.docx,.doc" style={{ display: "none" }}
                  onChange={e => { setFiles(Array.from(e.target.files)); e.target.value = ""; }} />
                <textarea value={input} onChange={e => setInput(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(); } }}
                  placeholder={files.length > 0 ? "Add a project name or notes (optional)…" : currentProjectId ? "Ask a question about this tender…" : "Upload a tender document or ask a question…"}
                  rows={1}
                  style={{ flex: 1, padding: "8px 4px", background: "transparent", border: "none", color: C.text1, fontSize: 14, fontFamily: F.sans, outline: "none", resize: "none", lineHeight: 1.4, minHeight: 22, maxHeight: 120 }} />
                <button onClick={handleSend}
                  style={{ padding: 8, background: (input.trim() || files.length > 0) ? C.green : "transparent", border: "none", borderRadius: 8, cursor: "pointer", color: (input.trim() || files.length > 0) ? "#111" : C.text3, transition: "all 0.2s", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <SendIcon />
                </button>
              </div>
              <div style={{ textAlign: "center", marginTop: 7, fontSize: 10, color: C.text3 }}>
                TenderIQ extracts data from uploaded documents. Always verify against original tender specifications.
              </div>
            </div>
          </div>

          {/* Results panel — desktop (draggable + resizable floating) */}
          {showResults && !isMob && (() => {
            const H = 6, C2 = 10; // handle thickness, corner size
            const hl = (cursor, style, dir) => (
              <div key={dir} onMouseDown={e => onResizeMouseDown(e, dir)}
                style={{ position: "absolute", cursor, zIndex: 10, ...style }}
                onMouseEnter={e => e.currentTarget.style.background = "rgba(100,220,100,0.18)"}
                onMouseLeave={e => e.currentTarget.style.background = "transparent"} />
            );
            return (
              <div ref={panelDragRef}
                style={{
                  position: "fixed",
                  top: panelPos ? panelPos.y : 10,
                  right: panelPos ? "auto" : 10,
                  left: panelPos ? panelPos.x : "auto",
                  width: panelWidth,
                  height: panelHeight ? panelHeight : "calc(100vh - 20px)",
                  borderRadius: 18,
                  overflow: "hidden",
                  boxShadow: "0 8px 40px rgba(0,0,0,0.55)",
                  border: `1px solid ${C.border}`,
                  zIndex: 150,
                  animation: "slideIn 0.25s ease",
                  display: "flex",
                  flexDirection: "column",
                }}>
                {/* Edge handles */}
                {hl("ew-resize", { left: 0, top: C2, bottom: C2, width: H }, "w")}
                {hl("ew-resize", { right: 0, top: C2, bottom: C2, width: H }, "e")}
                {hl("ns-resize", { top: 0, left: C2, right: C2, height: H }, "n")}
                {hl("ns-resize", { bottom: 0, left: C2, right: C2, height: H }, "s")}
                {/* Corner handles */}
                {hl("nw-resize", { top: 0, left: 0, width: C2, height: C2 }, "nw")}
                {hl("ne-resize", { top: 0, right: 0, width: C2, height: C2 }, "ne")}
                {hl("sw-resize", { bottom: 0, left: 0, width: C2, height: C2 }, "sw")}
                {hl("se-resize", { bottom: 0, right: 0, width: C2, height: C2 }, "se")}
                {/* Drag handle */}
                <div onMouseDown={onPanelMouseDown}
                  style={{ height: 28, background: C.navyDark, display: "flex", alignItems: "center", justifyContent: "center", cursor: "grab", flexShrink: 0, borderBottom: `1px solid ${C.border}` }}>
                  <div style={{ width: 36, height: 4, borderRadius: 2, background: C.border }} />
                </div>
                <div style={{ flex: 1, overflow: "hidden" }}>
                  <ResultsPanel token={token} projectId={currentProjectId} projectName={currentProjectName}
                    onClose={() => { setShowResults(false); setPanelPos(null); setPanelWidth(400); setPanelHeight(null); }} isMobile={false} />
                </div>
              </div>
            );
          })()}
        </div>
      </div>

      {/* Results panel — mobile bottom sheet */}
      {isMob && mobResults && <>
        <div onClick={() => setMobResults(false)} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.6)", zIndex: 200 }} />
        <div style={{ position: "fixed", bottom: 0, left: 0, right: 0, height: "85vh", zIndex: 201, borderRadius: "16px 16px 0 0", overflow: "hidden", animation: "slideUp 0.3s ease" }}>
          <div style={{ width: 36, height: 4, borderRadius: 2, background: C.text3, margin: "10px auto", opacity: 0.4 }} />
          <ResultsPanel token={token} projectId={currentProjectId} projectName={currentProjectName} onClose={() => setMobResults(false)} isMobile={true} />
        </div>
      </>}

      {/* Context menu & modals */}
      {ctxMenu && (
        <ChatCtxMenu x={ctxMenu.x} y={ctxMenu.y}
          onRename={() => { setRenameModal(ctxMenu.chat); setCtxMenu(null); }}
          onDelete={() => { setDeleteModal(ctxMenu.chat); setCtxMenu(null); }}
          onClose={() => setCtxMenu(null)} />
      )}
      {renameModal && (
        <RenameModal name={renameModal.title}
          onSave={n => { setChats(chats.map(c => c.id === renameModal.id ? { ...c, title: n } : c)); setRenameModal(null); }}
          onClose={() => setRenameModal(null)} />
      )}
      {deleteModal && (
        <DeleteModal name={deleteModal.title}
          onConfirm={() => { setChats(chats.filter(c => c.id !== deleteModal.id)); if (currentProjectId === deleteModal.id) newAnalysis(); setDeleteModal(null); }}
          onClose={() => setDeleteModal(null)} />
      )}

      {/* ── Analysing overlay ── */}
      {isTyping && msgs.some(m => m.type === "file") && (
        <div style={{ position: "fixed", inset: 0, background: "rgba(10,14,20,0.85)", zIndex: 500, display: "flex", alignItems: "center", justifyContent: "center", backdropFilter: "blur(4px)", animation: "fadeUp 0.3s ease" }}>
          <div style={{ background: C.bg1, borderRadius: 20, padding: "44px 52px", border: `1px solid ${C.border}`, textAlign: "center", maxWidth: 380, width: "90%" }}>
            <div style={{ fontSize: 22, fontWeight: 700, color: C.text1, marginBottom: 6, letterSpacing: "-0.02em" }}>Analysing</div>
            <div style={{ fontSize: 13, color: C.text3, marginBottom: 32 }}>Reading your document&hellip;</div>
            {/* Scanner line */}
            <div style={{ height: 3, background: C.bg2, borderRadius: 3, overflow: "hidden", position: "relative" }}>
              <div style={{ position: "absolute", top: 0, left: 0, height: "100%", width: "40%", borderRadius: 3, background: `linear-gradient(90deg, transparent, ${C.green}, transparent)`, animation: "scanner 1.6s ease-in-out infinite" }} />
            </div>
            <style>{`@keyframes scanner { 0% { left: -40%; } 100% { left: 140%; } }`}</style>
          </div>
        </div>
      )}
    </div>
  );
}
