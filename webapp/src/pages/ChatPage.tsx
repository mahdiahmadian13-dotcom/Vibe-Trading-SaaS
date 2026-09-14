import { useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { MessageSquare, Plus, Send, Square, History, ChevronRight, Pencil, Check, X } from "lucide-react";
import {
  listSessions, createSession, getMessages, sendMessage, cancelRun,
  waitForNewAnswer, sid, renameSession,
  type ChatMessage, type SessionRow,
} from "@/api/chat";
import { faNum } from "@/lib/utils";
import { Button } from "@/components/ui/Button";
import { Card, EmptyState, Skeleton } from "@/components/ui/primitives";
import { CouponBlocker, CouponCards, isCouponError } from "@/components/Coupons";

type Bubble = { role: "user" | "bot"; text: string };

export default function ChatPage() {
  const [sessions, setSessions] = useState<SessionRow[] | null>(null);
  const [current, setCurrent] = useState<string | null>(null);
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [loadErr, setLoadErr] = useState("");
  const [couponOut, setCouponOut] = useState(false);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameVal, setRenameVal] = useState("");
  const stopRef = useRef<{ cancelled: boolean }>({ cancelled: false });
  const logRef = useRef<HTMLDivElement>(null);

  const scrollDown = () => requestAnimationFrame(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  });

  useEffect(() => {
    listSessions()
      .then((list) => setSessions(Array.isArray(list) ? list : []))
      .catch((e) => { setLoadErr(e.message); setSessions([]); });
  }, []);

  useEffect(() => { scrollDown(); }, [bubbles]);

  /* ------------------------------ sessions ------------------------------- */

  /* ------------------------------ rename ---------------------------------- */

  const startRename = (s: SessionRow) => {
    setRenamingId(sid(s));
    setRenameVal(s.title || s.name || "");
  };

  const doRename = async () => {
    const id = renamingId;
    if (!id) return;
    const title = renameVal.trim();
    if (!title) return;
    try {
      await renameSession(id, title);
      setSessions((prev) => (prev || []).map((s) => (sid(s) === id ? { ...s, title } : s)));
      setRenamingId(null);
    } catch (e) {
      setBubbles((b) => [...(b || []), { role: "bot", text: "❌ تغییر نام ناموفق: " + (e as Error).message }]);
      setRenamingId(null);
    }
  };

  const openSession = async (id: string) => {
    setCurrent(id);
    setHistoryOpen(false);
    setBubbles(null!);
    try {
      const msgs = await getMessages(id);
      const list = Array.isArray(msgs) ? msgs : [];
      setBubbles(list.map((m) => ({
        role: m.role === "user" || m.role === "human" ? "user" : "bot",
        text: m.content || "",
      })));
    } catch {
      setBubbles([]);
    }
  };

  const newChat = async () => {
    try {
      const s = await createSession();
      const id = s.session_id || (s as unknown as { id?: string }).id;
      if (!id) throw new Error("شناسه سشن دریافت نشد");
      setSessions((prev) => [{ session_id: id, title: "چت جدید" }, ...(prev || [])]);
      setCurrent(id);
      setBubbles([]);
      setHistoryOpen(false);
    } catch (e) {
      setBubbles([{ role: "bot", text: "❌ خطا در ساخت گفتگو: " + (e as Error).message }]);
    }
  };

  /* -------------------------------- send --------------------------------- */

  const doSend = async (raw?: string) => {
    const text = (raw ?? input).trim();
    if (!text || busy) return;
    let idOrNull = current;
    if (!idOrNull) {
      try {
        const s = await createSession();
        const newId = s.session_id || (s as unknown as { id?: string }).id;
        if (!newId) throw new Error("شناسه سشن دریافت نشد");
        setSessions((prev) => [{ session_id: newId, title: "چت جدید" }, ...(prev || [])]);
        setCurrent(newId);
        idOrNull = newId;
      } catch (e) {
        setBubbles((b) => [...(b || []), { role: "bot", text: "❌ " + (e as Error).message }]);
        return;
      }
    }
    const id: string = idOrNull;

    setInput("");
    setBubbles((b) => [...(b || []), { role: "user", text }]);
    setBusy(true);
    setElapsed(0);
    stopRef.current = { cancelled: false };

    // Elapsed timer (feels alive during the long wait)
    const t0 = setInterval(() => setElapsed((e) => e + 1), 1000);

    try {
      // Snapshot BEFORE sending — the fix for the "previous answer shown again" bug
      const pre = await getMessages(id);
      const preCount = Array.isArray(pre) ? pre.length : 0;

      await sendMessage(id, text);

      // Auto-title: name the session from the first message (best-effort)
      setSessions((prev) => {
        const me = (prev || []).find((s) => sid(s) === id);
        const t = me?.title || me?.name || "";
        const isDefault = !t || t === "چت جدید" || t.startsWith("گفتگوی ");
        if (isDefault) {
          const auto = text.length > 34 ? text.slice(0, 34).trim() + "…" : text;
          renameSession(id, auto).catch(() => {});
          return (prev || []).map((s) => (sid(s) === id ? { ...s, title: auto } : s));
        }
        return prev;
      });

      const answer = await waitForNewAnswer(id, preCount, {
        maxWait: 240,
        signal: stopRef.current,
        onTick: () => {},
      });

      if (stopRef.current.cancelled) {
        try { await cancelRun(id); } catch { /* ignore */ }
        setBubbles((b) => [...(b || []), { role: "bot", text: "⏹ متوقف شد. هر وقت خواستی ادامه بده." }]);
      } else {
        setBubbles((b) => [...(b || []), {
          role: "bot",
          text: answer || "⏰ پردازش طولانی شد — چند لحظه دیگر پیام بده یا از «📈 گزارش‌ها» چک کن.",
        }]);
      }
    } catch (e) {
      const err = e as Error & { status?: number };
      if (isCouponError(e)) {
        setCouponOut(true);
        setBubbles((b) => [...(b || []), { role: "user", text }]);
        return;
      }
      let msg = "❌ خطا: " + err.message;
      if (err.status === 429) msg = "⏳ " + err.message + "\nچند لحظه صبر کن و دوباره بفرست.";
      else if (err.status === 409 || err.message.includes("already has a run")) {
        msg = "⏳ پاسخ قبلی هنوز در حال تولید است — منتظر می‌مانم…";
        const answer = await waitForNewAnswer(id, 0, { maxWait: 180, signal: stopRef.current });
        msg = answer || "⏰ پردازش قبلی طولانی شد. بعداً چک کن.";
      }
      setBubbles((b) => [...(b || []), { role: "bot", text: msg }]);
    } finally {
      clearInterval(t0);
      setBusy(false);
    }
  };

  /* -------------------------------- view ---------------------------------- */

  if (couponOut) {
    return (
      <div className="mx-auto flex h-[calc(100dvh-150px)] w-full max-w-3xl flex-col items-center justify-center px-4 pt-4 md:h-[calc(100dvh-170px)] md:px-8 md:pt-6">
        <CouponBlocker kind="backtest" onRetry={() => { setCouponOut(false); }} />
        <div className="mt-4 w-full max-w-md"><CouponCards compact /></div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex h-[calc(100dvh-150px)] w-full max-w-3xl flex-col px-4 pt-4 md:h-[calc(100dvh-170px)] md:px-8 md:pt-6">
      {/* header */}
      <div className="mb-3 flex items-center justify-between">
        <div>
          <h1 className="flex items-center gap-2 text-[18px] font-extrabold tracking-tight md:text-[21px]">
            <MessageSquare size={19} className="text-brand" /> چت با تحلیلگر AI
          </h1>
          <p className="mt-0.5 text-[11.5px] text-muted md:text-[12.5px]">
            استراتژی‌ات را با زبان خودت توصیف کن — بک‌تست، تحلیل، ایده
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setHistoryOpen((v) => !v)}
            className="flex h-9 items-center gap-1.5 rounded-xl border border-line bg-white/[.03] px-3 text-[12px] font-semibold text-muted transition-colors hover:text-ink"
          >
            <History size={14} /> گفتگوها
          </button>
          <button
            onClick={newChat}
            className="flex h-9 items-center gap-1.5 rounded-xl bg-gradient-to-l from-brand to-brand-soft px-3 text-[12px] font-bold text-white shadow-lg shadow-brand/20 active:scale-95"
          >
            <Plus size={14} /> جدید
          </button>
        </div>
      </div>

      {/* session history drawer */}
      <AnimatePresence>
        {historyOpen && (
          <motion.div
            initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <Card className="mb-3 max-h-52 overflow-y-auto p-2">
              {(sessions || []).length === 0 && (
                <div className="p-4 text-center text-[12px] text-muted">هنوز گفتگویی نداری</div>
              )}
              {(sessions || []).map((s) => {
                const id = sid(s);
                const isRenaming = renamingId === id;
                return isRenaming ? (
                  <div key={id} className="flex items-center gap-2 rounded-lg bg-white/[.04] px-3 py-2">
                    <input
                      autoFocus
                      value={renameVal}
                      onChange={(e) => setRenameVal(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") doRename();
                        if (e.key === "Escape") setRenamingId(null);
                      }}
                      maxLength={80}
                      placeholder="نام جدید گفتگو…"
                      className="h-8 flex-1 rounded-lg border border-line bg-black/30 px-2.5 text-[12.5px] outline-none focus:border-brand/50"
                    />
                    <button onClick={doRename} aria-label="ثبت نام"
                      className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand/20 text-indigo-200 transition-colors hover:bg-brand/30">
                      <Check size={13} />
                    </button>
                    <button onClick={() => setRenamingId(null)} aria-label="انصراف"
                      className="flex h-7 w-7 items-center justify-center rounded-lg border border-line text-muted transition-colors hover:text-ink">
                      <X size={13} />
                    </button>
                  </div>
                ) : (
                  <div
                    key={id}
                    className={`group flex w-full items-center justify-between gap-2 rounded-lg px-3.5 py-2.5 text-right text-[12.5px] transition-colors ${current === id ? "bg-brand/10 font-bold text-indigo-200" : "hover:bg-white/[.04]"}`}
                  >
                    <button onClick={() => openSession(id)} className="flex min-w-0 flex-1 items-center gap-2">
                      <ChevronRight size={13} className="shrink-0 text-muted" />
                      <span className="truncate">{s.title || s.name || "گفتگو"}</span>
                    </button>
                    <span className="flex shrink-0 items-center gap-2">
                      <span className="text-[10.5px] text-muted/70">{s.created_at ? new Date(s.created_at).toLocaleDateString("fa-IR") : ""}</span>
                      <button
                        onClick={() => startRename(s)}
                        aria-label="تغییر نام گفتگو"
                        title="تغییر نام"
                        className="flex h-6 w-6 items-center justify-center rounded-md text-muted/60 opacity-0 transition-all hover:bg-white/[.06] hover:text-ink group-hover:opacity-100"
                      >
                        <Pencil size={12} />
                      </button>
                    </span>
                  </div>
                );
              })}
            </Card>
          </motion.div>
        )}
      </AnimatePresence>

      {/* message log */}
      <Card className="flex min-h-0 flex-1 flex-col overflow-hidden p-0">
        <div ref={logRef} className="flex-1 space-y-3 overflow-y-auto p-4 md:p-5">
          {sessions === null ? (
            <div className="space-y-3">
              <Skeleton className="h-12 w-2/3" />
              <Skeleton className="ml-auto h-10 w-1/2" />
              <Skeleton className="h-14 w-3/4" />
            </div>
          ) : bubbles === null ? (
            <div className="flex h-full items-center justify-center"><Skeleton className="h-24 w-3/4" /></div>
          ) : bubbles.length === 0 ? (
            <EmptyState
              icon="💬"
              title="شروع یک تحلیل جدید"
              desc="مثلاً بنویس: «یه بک‌تست روی سبک RTM چهار ساعته بیت‌کوین می‌خوام»"
            />
          ) : (
            bubbles.map((b, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}
                className={`flex ${b.role === "user" ? "justify-start" : "justify-end"}`}
              >
                <div
                  className={`max-w-[85%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-[13px] leading-7 md:text-[13.5px] ${
                    b.role === "user"
                      ? "rounded-br-md bg-gradient-to-l from-brand to-brand-soft text-white"
                      : "rounded-bl-md border border-line bg-white/[.04]"
                  }`}
                >
                  {b.text}
                </div>
              </motion.div>
            ))
          )}

          {/* typing indicator */}
          {busy && (
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex justify-end">
              <div className="flex items-center gap-2.5 rounded-2xl rounded-bl-md border border-line bg-white/[.04] px-4 py-3">
                <div className="flex gap-1">
                  {[0, 1, 2].map((i) => (
                    <motion.span
                      key={i}
                      className="h-1.5 w-1.5 rounded-full bg-indigo-300"
                      animate={{ opacity: [0.3, 1, 0.3] }}
                      transition={{ duration: 1.2, repeat: Infinity, delay: i * 0.18 }}
                    />
                  ))}
                </div>
                <span className="text-[11.5px] text-muted">
                  تیم AI در حال تحلیل… {faNum(Math.floor(elapsed / 60))}:{String(elapsed % 60).padStart(2, "0")}
                </span>
              </div>
            </motion.div>
          )}
        </div>

        {/* composer */}
        <div className="border-t border-line bg-white/[.02] p-3 md:p-4">
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); doSend(); }
              }}
              rows={1}
              placeholder="پیامت را بنویس…"
              className="max-h-32 min-h-[44px] flex-1 resize-none rounded-xl border border-line bg-white/[.04] px-4 py-3 text-[14px] outline-none transition-colors placeholder:text-muted/60 focus:border-brand/50"
            />
            {busy ? (
              <button
                onClick={() => { stopRef.current.cancelled = true; }}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-neg/40 bg-neg/10 text-neg active:scale-95"
                aria-label="توقف"
              >
                <Square size={16} />
              </button>
            ) : (
              <button
                onClick={() => doSend()}
                disabled={!input.trim()}
                className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-l from-brand to-brand-soft text-white shadow-lg shadow-brand/25 transition-all active:scale-95 disabled:opacity-40"
                aria-label="ارسال"
              >
                <Send size={17} className="-scale-x-100" />
              </button>
            )}
          </div>
        </div>
      </Card>

      {loadErr && (
        <div className="mt-2 text-center text-[11.5px] text-neg">خطا در دریافت گفتگوها: {loadErr}</div>
      )}
      {sessions !== null && sessions.length > 0 && !current && bubbles === null && (
        <div className="mt-2 text-center">
          <Button variant="ghost" size="sm" onClick={() => setHistoryOpen(true)}>
            یکی از گفتگوهای قبلی را باز کن
          </Button>
        </div>
      )}
    </div>
  );
}
