import { StrictMode, useEffect, useState } from "react";
import { motion } from "framer-motion";
import { createRoot } from "react-dom/client";
import { House, MessagesSquare, Users } from "lucide-react";
import HomePage from "@/pages/HomePage";
import SwarmPage from "@/pages/SwarmPage";
import ChatPage from "@/pages/ChatPage";
import { auth } from "@/api/client";
import "@/index.css";

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
  const goHome = () => { if (location.hash) location.hash = ""; setView("home"); };

  return view === "swarm" ? (
    <>
      <TopBar onHome={goHome} subtitle="تیم‌های هوش مصنوعی" />
      <SwarmPage />
      <BottomNav view="swarm" onHome={goHome} onSwarm={goSwarm} onChat={goChat} />
    </>
  ) : view === "chat" ? (
    <>
      <TopBar onHome={goHome} subtitle="چت با تحلیلگر" />
      <ChatPage />
      <BottomNav view="chat" onHome={goHome} onSwarm={goSwarm} onChat={goChat} />
    </>
  ) : (
    <>
      <HomePage goChat={goChat} />
      <BottomNav view="home" onHome={goHome} onSwarm={goSwarm} onChat={goChat} />
    </>
  );
}

function TopBar({ onHome, subtitle }: { onHome: () => void; subtitle: string }) {
  return (
    <div className="sticky top-0 z-40 border-b border-line bg-bg/80 backdrop-blur-xl">
      <div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-3 md:px-8">
        <button onClick={onHome} className="flex items-center gap-2.5">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-gradient-to-br from-brand to-brand-soft text-[14px] font-bold text-white">V</div>
          <span className="text-[14px] font-extrabold">Vibe Trading</span>
        </button>
        <span className="text-[11.5px] text-muted">{subtitle}</span>
      </div>
    </div>
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
];

function BottomNav({ view, onHome, onSwarm, onChat }: {
  view: string; onHome: () => void; onSwarm: () => void; onChat: () => void;
}) {
  const handlers: Record<string, () => void> = { home: onHome, chat: onChat, swarm: onSwarm };

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
