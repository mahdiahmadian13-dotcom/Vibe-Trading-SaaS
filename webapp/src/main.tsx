import { StrictMode, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { createRoot } from "react-dom/client";
import HomePage from "@/pages/HomePage";
import SwarmPage from "@/pages/SwarmPage";
import ChatPage from "@/pages/ChatPage";
import { auth } from "@/api/client";
import "@/index.css";

const goLegacy = () => (location.href = "/app/legacy.html#reports");

function App() {
  const [view, setView] = useState(() => {
    const h = location.hash;
    return h === "#swarm" ? "swarm" : h === "#chat" ? "chat" : "home";
  });

  useEffect(() => {
    if (!auth.token) location.href = "/app/legacy.html";
  }, []);

  useEffect(() => {
    const onHash = () => {
      const h = location.hash;
      setView(h === "#swarm" ? "swarm" : h === "#chat" ? "chat" : "home");
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const goSwarm = () => { location.hash = "#swarm"; setView("swarm"); };
  const goChat = () => { location.hash = "#chat"; setView("chat"); };
  const goHome = () => { location.hash = ""; setView("home"); };

  return view === "swarm" ? (
    <>
      <TopBar onHome={goHome} />
      <SwarmPage />
      <BottomNav view="swarm" onHome={goHome} onSwarm={goSwarm} onChat={goChat} goLegacy={goLegacy} />
    </>
  ) : view === "chat" ? (
    <>
      <TopBar onHome={goHome} />
      <ChatPage />
      <BottomNav view="chat" onHome={goHome} onSwarm={goSwarm} onChat={goChat} goLegacy={goLegacy} />
    </>
  ) : (
    <>
      <HomePage goChat={goChat} />
      <BottomNav view="home" onHome={goHome} onSwarm={goSwarm} onChat={goChat} goLegacy={goLegacy} />
    </>
  );
}

function TopBar({ onHome }: { onHome: () => void }) {
  return (
    <div className="sticky top-0 z-40 border-b border-line bg-bg/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-3 md:px-8">
        <button onClick={onHome} className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand to-brand-soft text-[14px] font-bold text-white">V</div>
          <span className="text-[14px] font-extrabold">Vibe Trading</span>
        </button>
        <span className="text-[11.5px] text-muted">تیم‌های هوش مصنوعی</span>
      </div>
    </div>
  );
}

function BottomNav({ view, onHome, onSwarm, onChat, goLegacy }: {
  view: string; onHome: () => void; onSwarm: () => void; onChat: () => void; goLegacy: () => void;
}) {
  const tabs = [
    { id: "home", label: "خانه", active: view === "home", onClick: onHome },
    { id: "chat", label: "چت با AI", active: view === "chat", onClick: onChat },
    { id: "swarm", label: "تیم‌ها", active: view === "swarm", onClick: onSwarm },
  ];
  return (
    <nav className="fixed inset-x-0 bottom-0 z-40 border-t border-line bg-panel/85 backdrop-blur-xl md:hidden" style={{ paddingBottom: "env(safe-area-inset-bottom)" }}>
      <div className="mx-auto grid max-w-md grid-cols-3">
        {tabs.map((t) => (
          <button key={t.id} onClick={t.onClick} className={`relative flex flex-col items-center gap-1 py-2.5 text-[10.5px] font-semibold ${t.active ? "text-indigo-300" : "text-muted"}`}>
            {t.active && <motion.span layoutId="nav-pill" className="absolute -top-px h-0.5 w-12 rounded-full bg-gradient-to-l from-brand to-brand-soft" />}
            <span className="text-[16px]">{t.id === "home" ? "🏠" : t.id === "chat" ? "💬" : "🤖"}</span>
            {t.label}
          </button>
        ))}
      </div>
    </nav>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
