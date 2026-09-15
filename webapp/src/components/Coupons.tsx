import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import { FlaskConical, Timer, Users, Zap } from "lucide-react";
import { api } from "@/api/client";

/* --------------------------------------------------------------------------- */
/*  Coupon wallet — free-tier metering UI (fleet 001 policy)                     */
/*  · backtest: 1/day (Tehran midnight, NO accumulation) + permanent credit     */
/*  · swarm: 1/week (Monday 00:00 Tehran)                                       */
/* --------------------------------------------------------------------------- */

export type CouponWallet = {
  backtest: {
    active: number;
    expires_at: string | null;
    next_refill_at: string;
    welcome_left: number;
    daily: boolean;
  };
  swarm: { active: number; next_refill_at: string };
  plan: string;
  metered: boolean;
};

export const getCoupons = () => api<CouponWallet>("/api/v1/coupons");

/* Live countdown to a future ISO timestamp (Tehran wall-clock display). */
function useCountdown(target: string | null | undefined) {
  const [txt, setTxt] = useState("--:--:--");
  useEffect(() => {
    if (!target) return;
    const tick = () => {
      const ms = new Date(target).getTime() - Date.now();
      if (ms <= 0) { setTxt("۰۰:۰۰:۰۰"); return; }
      const h = Math.floor(ms / 3_600_000);
      const m = Math.floor((ms % 3_600_000) / 60_000);
      const s = Math.floor((ms % 60_000) / 1000);
      setTxt(`${fa(h)}:${fa(m)}:${fa(s)}`);
    };
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [target]);
  return txt;
}

const fa = (n: number) => String(n).padStart(2, "۰").replace(/\d/g, (d) => "۰۱۲۳۴۵۶۷۸۹"[+d]);

/* --------------------------------------------------------------------------- */

export function CouponCards({ compact = false }: { compact?: boolean }) {
  const [w, setW] = useState<CouponWallet | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    getCoupons().then(setW).catch((e) => setErr(e.message));
    const t = setInterval(() => getCoupons().then(setW).catch(() => {}), 30_000);
    return () => clearInterval(t);
  }, []);

  if (err) return null;
  if (!w) {
    return (
      <div className="grid grid-cols-2 gap-3">
        <div className="h-28 animate-pulse rounded-2xl border border-line bg-panel/50" />
        <div className="h-28 animate-pulse rounded-2xl border border-line bg-panel/50" />
      </div>
    );
  }

  // Paid plans aren't metered by coupons — show usage-free state
  if (!w.metered) {
    return (
      <div className="flex items-center gap-3 rounded-2xl border border-pos/25 bg-pos/[.06] px-4 py-3 text-[12.5px] font-bold text-pos">
        <Zap size={16} /> پلن شما سقف روزانه دارد و نیازی به کوپن ندارد — آزادانه استفاده کن.
      </div>
    );
  }

  return (
    <div className={`grid grid-cols-2 gap-3 ${compact ? "" : "md:gap-4"}`}>
      <BacktestCard w={w} compact={compact} />
      <SwarmCard w={w} compact={compact} />
    </div>
  );
}

function BacktestCard({ w, compact }: { w: CouponWallet; compact?: boolean }) {
  const bt = w.backtest;
  const cd = useCountdown(bt.next_refill_at);
  const has = bt.active > 0;
  return (
    <div
      className={`relative overflow-hidden rounded-2xl border p-4 transition-colors ${
        has ? "border-brand/30 bg-gradient-to-bl from-brand/[.10] via-panel/70 to-panel/50" : "border-amber/30 bg-amber/[.04]"
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className={`flex h-9 w-9 items-center justify-center rounded-xl ${has ? "bg-brand/20 text-indigo-200" : "bg-amber/15 text-amber-200"}`}>
            <FlaskConical size={17} />
          </span>
          <div>
            <div className="text-[13px] font-extrabold">کوپن بک‌تست</div>
            <div className="text-[10.5px] text-muted">تحلیل + بک‌تست با چت AI</div>
          </div>
        </div>
        {has ? (
          <div className="flex -space-x-1.5 space-x-reverse">
            {Array.from({ length: Math.min(bt.active, 5) }).map((_, i) => (
              <span key={i} className="h-2.5 w-2.5 rounded-full bg-gradient-to-br from-brand to-brand-soft ring-2 ring-panel" />
            ))}
          </div>
        ) : null}
      </div>

      <div className="mt-3 flex items-end justify-between">
        <div className={`text-[26px] font-black leading-8 ${has ? "text-indigo-100" : "text-amber-200"}`}>{fa(bt.active)}</div>
        <div className="text-left">
          {has ? (
            <div className="flex flex-col items-end gap-1">
              {bt.welcome_left > 0 && (
                <span className="rounded-full bg-brand/15 px-2 py-0.5 text-[10px] font-bold text-indigo-200">اعتبار دائمی: {fa(bt.welcome_left)}</span>
              )}
              {bt.daily && (
                <span className="rounded-full bg-white/5 px-2 py-0.5 text-[10px] font-bold text-muted">کوپن امروز فعال</span>
              )}
            </div>
          ) : (
            <span className="flex items-center gap-1 text-[11px] font-bold text-amber-200">
              <Timer size={12} /> شارژ بعدی
            </span>
          )}
        </div>
      </div>

      {!has && (
        <div className="mt-2 flex items-center justify-between rounded-xl bg-amber/10 px-3 py-1.5">
          <span className="text-[10.5px] text-amber-200/90">نیمه‌شب (تهران)</span>
          <span className="font-mono text-[13px] font-bold text-amber-100" dir="ltr">{cd}</span>
        </div>
      )}
      {compact && has && <div className="mt-1 text-[10px] text-muted">هر روز ۱ کوپن · نیمه‌شب شارژ</div>}
    </div>
  );
}

function SwarmCard({ w, compact }: { w: CouponWallet; compact?: boolean }) {
  const sw = w.swarm;
  const cd = useCountdown(sw.next_refill_at);
  const has = sw.active > 0;
  return (
    <div
      className={`relative overflow-hidden rounded-2xl border p-4 ${
        has ? "border-emerald-400/25 bg-gradient-to-bl from-emerald-400/[.09] via-panel/70 to-panel/50" : "border-amber/30 bg-amber/[.04]"
      }`}
    >
      <div className="flex items-start justify-between">
        <div className="flex items-center gap-2">
          <span className={`flex h-9 w-9 items-center justify-center rounded-xl ${has ? "bg-emerald-400/15 text-emerald-200" : "bg-amber/15 text-amber-200"}`}>
            <Users size={17} />
          </span>
          <div>
            <div className="text-[13px] font-extrabold">کوپن سوارم</div>
            <div className="text-[10.5px] text-muted">تیم چندعاملی · هفتگی</div>
          </div>
        </div>
        {has && <span className="h-2.5 w-2.5 rounded-full bg-emerald-400 ring-2 ring-panel" />}
      </div>

      <div className="mt-3 flex items-end justify-between">
        <div className={`text-[26px] font-black leading-8 ${has ? "text-emerald-100" : "text-amber-200"}`}>{fa(sw.active)}</div>
        {!has && (
          <span className="flex items-center gap-1 text-[11px] font-bold text-amber-200">
            <Timer size={12} /> شارژ بعدی
          </span>
        )}
      </div>

      {!has && (
        <div className="mt-2 flex items-center justify-between rounded-xl bg-amber/10 px-3 py-1.5">
          <span className="text-[10.5px] text-amber-200/90">دوشنبه ۰۰:۰۰ (تهران)</span>
          <span className="font-mono text-[13px] font-bold text-amber-100" dir="ltr">{cd}</span>
        </div>
      )}
      {compact && has && <div className="mt-1 text-[10px] text-muted">۱ کوپن در هفته · دوشنبه شارژ</div>}
    </div>
  );
}

/* --------------------------------------------------------------------------- */
/*  Out-of-coupons blocker — shown when the API says 429 COUPON                 */
/* --------------------------------------------------------------------------- */

export function CouponBlocker({ kind, onRetry }: { kind: "backtest" | "swarm"; onRetry?: () => void }) {
  const [w, setW] = useState<CouponWallet | null>(null);
  useEffect(() => { getCoupons().then(setW).catch(() => {}); }, []);
  const target = w ? (kind === "swarm" ? w.swarm.next_refill_at : w.backtest.next_refill_at) : null;
  const cd = useCountdown(target);
  const isSwarm = kind === "swarm";

  return (
    <motion.div
      initial={{ opacity: 0, y: 10, scale: 0.98 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      className="mx-auto max-w-md overflow-hidden rounded-2xl border border-amber/30 bg-gradient-to-b from-amber/[.07] to-panel/60 p-6 text-center shadow-xl shadow-amber/5"
    >
      <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-amber/15 ring-1 ring-inset ring-amber/25">
        <Timer size={26} className="text-amber-300" />
      </div>
      <h3 className="mt-4 text-[15.5px] font-extrabold">
        {isSwarm ? "کوپن سوارم هفتگی شما به پایان رسید" : "کوپن بک‌تست شما به پایان رسید"}
      </h3>
      <p className="mt-2 text-[12.5px] leading-6 text-muted">
        {isSwarm
          ? "هر هفته ۱ کوپن سوارم دریافت می‌کنی. کوپن بعدی دوشنبه ساعت ۰۰:۰۰ (تهران) شارژ می‌شود."
          : "هر روز ۱ کوپن بک‌تست دریافت می‌کنی. کوپن بعدی نیمه‌شب (تهران) شارژ می‌شود."}
      </p>
      <div className="mt-4 flex items-center justify-center gap-3 rounded-xl border border-amber/25 bg-amber/10 px-4 py-3">
        <span className="text-[11px] text-amber-200/90">شارژ کوپن جدید در</span>
        <span className="font-mono text-[20px] font-black tracking-wide text-amber-100" dir="ltr">{cd}</span>
      </div>
      <p className="mt-3 text-[11.5px] text-muted">
        برای مصرف بی‌سقف می‌توانی پلن خود را ارتقا دهی.
      </p>
      {onRetry && (
        <button onClick={onRetry} className="mt-4 rounded-xl border border-line bg-white/[.04] px-4 py-2 text-[12px] font-bold text-muted transition-colors hover:text-ink">
          تلاش مجدد
        </button>
      )}
    </motion.div>
  );
}

/* Detect coupon-exhaustion from API errors (429 + the marker text). */
export function isCouponError(e: unknown): boolean {
  const err = e as Error & { status?: number };
  return err?.status === 429 && /کوپن/.test(err.message || "");
}
