import { StrictMode, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { createRoot } from "react-dom/client";
import { FileBarChart, FlaskConical, House, MessagesSquare, Shield, Users } from "lucide-react";
import HomePage from "@/pages/HomePage";
import SwarmPage from "@/pages/SwarmPage";
import ChatPage from "@/pages/ChatPage";
import ReportsPage from "@/pages/ReportsPage";
import DiscoveryPage from "@/pages/DiscoveryPage";
import AdminPage from "@/pages/AdminPage";
import { auth } from "@/api/client";
import "@/index.css";

function App() {
  const [view, setView] = useState(() => {
    const h = location.hash;
    return h === "#swarm" ? "swarm" : h === "#chat" ? "chat" : h === "#reports" ? "reports" : h === "#discovery" ? "discovery" : h === "#admin" ? "admin" : "home";
  });

  useEffect(() => {
    if (!auth.token) location.href = "/app/legacy.html";
  }, []);

  useEffect(() => {
    const onHash = () => {
      const h = location.hash;
      setView(h === "#swarm" ? "swarm" : h === "#chat" ? "chat" : h === "#reports" ? "reports" : h === "#discovery" ? "discovery" : h === "#admin" ? "admin" : "home");
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const goSwarm = () => { location.hash = "#swarm"; setView("swarm"); };
  const goChat = () => { location.hash = "#chat"; setView("chat"); };
  const goReports = () => { location.hash = "#reports"; setView("reports"); };
  const goDiscovery = () => { location.hash = "#discovery"; setView("discovery"); };
  const goAdmin = () => { location.hash = "#admin"; setView("admin"); };
  const goHome = () => { if (location.hash) location.hash = ""; setView("home"); };

  return view === "swarm" ? (
    <>
      <TopBar onHome={goHome} subtitle="تیم‌های هوش مصنوعی" />
      <SwarmPage />
      <BottomNav view="swarm" onHome={goHome} onSwarm={goSwarm} onChat={goChat} goReports={goReports} />
    </>
  ) : view === "chat" ? (
    <>
      <TopBar onHome={goHome} subtitle="چت با تحلیلگر" />
      <ChatPage />
      <BottomNav view="chat" onHome={goHome} onSwarm={goSwarm} onChat={goChat} goReports={goReports} />
    </>
  ) : view === "reports" ? (
    <>
      <TopBar onHome={goHome} subtitle="گزارش‌های بک‌تست" />
      <ReportsPage />
      <BottomNav view="reports" onHome={goHome} onSwarm={goSwarm} onChat={goChat} goReports={goReports} />
    </>
  ) : view === "discovery" ? (
    <>
      <TopBar onHome={goHome} subtitle="کشف استراتژی" />
      <div className="mx-auto hidden w-full max-w-6xl gap-6 px-8 pt-8 md:flex md:pb-16">
        <SideNav active="discovery" onHome={goHome} onSwarm={goSwarm} onChat={goChat} goReports={goReports} goDiscovery={goDiscovery} />
        <div className="min-w-0 flex-1"><DiscoveryPage bare /></div>
      </div>
      <div className="md:hidden">
        <DiscoveryPage />
      </div>
      <BottomNav view="home" onHome={goHome} onSwarm={goSwarm} onChat={goChat} goReports={goReports} />
    </>
  ) : view === "admin" ? (
    <AdminPage onHome={goHome} />
  ) : (
    <>
      <HomePage goChat={goChat} goReports={goReports} goDiscovery={goDiscovery} />
      <BottomNav view="home" onHome={goHome} onSwarm={goSwarm} onChat={goChat} goReports={goReports} />
    </>
  );
}

function TopBar({ onHome, subtitle }: { onHome: () => void; subtitle: string }) {
  const [isAdmin, setIsAdmin] = useState(false);
  useEffect(() => {
    if (!auth.token) return;
    // lightweight probe: admin overview returns 200 only for admins
    fetch("/api/v1/admin/overview", { headers: { Authorization: "Bearer " + auth.token } })
      .then((r) => setIsAdmin(r.ok))
      .catch(() => {});
  }, []);
  return (
    <div className="sticky top-0 z-40 border-b border-line bg-bg/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-3 md:px-8">
        <button onClick={onHome} className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand to-brand-soft text-[14px] font-bold text-white">V</div>
          <span className="text-[14px] font-extrabold">Vibe Trading</span>
        </button>
        <span className="flex items-center gap-2">
          {isAdmin && (
            <a href="#admin" onClick={(e) => { e.preventDefault(); location.hash = "#admin"; }} className="inline-flex items-center gap-1 rounded-full bg-brand/15 px-2.5 py-1 text-[11px] font-bold text-indigo-200 ring-1 ring-inset ring-brand/30 hover:bg-brand/25">
              <Shield size={12} /> ادمین
            </a>
          )}
          <span className="text-[11.5px] text-muted">{subtitle}</span>
        </span>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------------------
   SideNav — desktop sidebar (discovery lives here + drawer, never bottom dock).
--------------------------------------------------------------------------- */
const SIDE_ITEMS: Array<{ id: string; label: string; desc: string; icon: typeof House }> = [
  { id: "home", label: "خانه", desc: "نمای کلی", icon: House },
  { id: "chat", label: "چت با AI", desc: "تحلیلگر هوشمند", icon: MessagesSquare },
  { id: "swarm", label: "تیم‌های هوش مصنوعی", desc: "اجراهای چندعاملی", icon: Users },
  { id: "reports", label: "گزارش‌های بک‌تست", desc: "نتایج و PDF", icon: FileBarChart },
  { id: "discovery", label: "کشف استراتژی", desc: "زنده‌ها و مرده‌ها", icon: FlaskConical },
];

function SideNav({ active, onHome, onSwarm, onChat, goReports, goDiscovery }: {
  active: string; onHome: () => void; onSwarm: () => void; onChat: () => void; goReports: () => void; goDiscovery: () => void;
}) {
  const handlers: Record<string, () => void> = { home: onHome, chat: onChat, swarm: onSwarm, reports: goReports, discovery: goDiscovery };
  return (
    <aside className="w-60 shrink-0">
      <div className="sticky top-20 space-y-1 rounded-2xl border border-line bg-panel/60 p-2.5">
        {SIDE_ITEMS.map((t) => {
          const isActive = active === t.id;
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              onClick={handlers[t.id]}
              className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-3 text-right transition-colors ${isActive ? "bg-brand/15 text-indigo-200 ring-1 ring-inset ring-brand/40" : "text-muted hover:bg-white/5 hover:text-ink"}`}
            >
              <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${isActive ? "bg-brand/20 text-indigo-200" : "bg-white/[.04] text-muted"}`}>
                <Icon size={17} strokeWidth={isActive ? 2.4 : 2} />
              </span>
              <span className="min-w-0">
                <span className="block text-[13px] font-bold leading-5">{t.label}</span>
                <span className="block truncate text-[10.5px] leading-4 opacity-70">{t.desc}</span>
              </span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

/* ---------------------------------------------------------------------------
   BottomNav — floating dock, iOS-style.
   · Detached pill that floats above content with glass blur + soft shadow
   · Active tab: filled gradient pill that morphs between tabs (layoutId)
   · Icon lifts + label color-fades when active; subtle press scale on tap
   · Chat tab carries a small pulsing "AI alive" dot
--------------------------------------------------------------------------- */
const NAV_ITEMS: Array<{ id: string; label: string; icon: typeof House; live?: boolean }> = [
  { id: "home", label: "خانه", icon: House },
  { id: "chat", label: "چت", icon: MessagesSquare, live: true },
  { id: "swarm", label: "تیم‌ها", icon: Users },
  { id: "reports", label: "گزارش‌ها", icon: FileBarChart },
];

function BottomNav({ view, onHome, onSwarm, onChat, goReports }: {
  view: string; onHome: () => void; onSwarm: () => void; onChat: () => void; goReports: () => void;
}) {
  const handlers: Record<string, () => void> = { home: onHome, chat: onChat, swarm: onSwarm, reports: goReports };

  return (
    <div className="pointer-events-none fixed inset-x-0 bottom-0 z-40 flex justify-center pb-[max(env(safe-area-inset-bottom),0.6rem)] md:hidden">
      <nav className="pointer-events-auto flex items-center gap-1 rounded-2xl border border-white/[.07] bg-panel2/80 p-1.5 shadow-[0_12px_40px_-8px_rgba(0,0,0,.65)] backdrop-blur-2xl">
        {NAV_ITEMS.map((t) => {
          const active = view === t.id;
          const Icon = t.icon;
          return (
            <button
              key={t.id}
              onClick={handlers[t.id]}
              className="relative flex h-[46px] w-[74px] flex-col items-center justify-center gap-0.5 rounded-xl outline-none active:scale-[.94] transition-transform"
            >
              {/* morphing active pill */}
              {active && (
                <motion.span
                  layoutId="nav-pill"
                  transition={{ type: "spring", stiffness: 420, damping: 34 }}
                  className="absolute inset-0 rounded-xl bg-gradient-to-b from-brand/25 to-brand/10 ring-1 ring-inset ring-brand/40"
                />
              )}
              {/* soft glow under the active icon */}
              {active && (
                <motion.span
                  layoutId="nav-glow"
                  transition={{ type: "spring", stiffness: 420, damping: 34 }}
                  className="absolute -bottom-0.5 h-1 w-8 rounded-full bg-brand blur-[3px]"
                />
              )}
              <span className={`relative transition-colors duration-200 ${active ? "text-indigo-200" : "text-muted"}`}>
                <Icon size={19} strokeWidth={active ? 2.4 : 2} />
                {t.live && (
                  <span className="absolute -right-0.5 -top-0.5 flex h-1.5 w-1.5">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-60" />
                    <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-emerald-400" />
                  </span>
                )}
              </span>
              <span className={`relative text-[10px] font-bold tracking-tight transition-colors duration-200 ${active ? "text-indigo-200" : "text-muted"}`}>
                {t.label}
              </span>
            </button>
          );
        })}
      </nav>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
