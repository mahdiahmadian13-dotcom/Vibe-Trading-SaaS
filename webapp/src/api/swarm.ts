import { api } from "@/api/client";

/* ------------------------------ engine types ------------------------------ */

export type SwarmVariable = {
  name: string;
  description: string;
  required: boolean;
};

export type SwarmPreset = {
  name: string;
  title: string;
  description: string;
  agent_count: number;
  variables: SwarmVariable[];
};

export type SwarmTaskStatus = {
  agent_name?: string;
  status?: string;
};

export type SwarmRunStatus = {
  id?: string;
  status?: string;
  preset_name?: string;
  tasks?: SwarmTaskStatus[];
  final_report?: string;
  error?: string;
};

export type SwarmRunRow = {
  id: string;
  preset_name?: string;
  status?: string;
  created_at?: string;
};

export const getSwarmPresets = () => api<SwarmPreset[]>("/api/v1/vibe/swarm/presets");
export const createSwarmRun = (preset_name: string, user_vars: Record<string, string>) =>
  api<{ id: string }>("/api/v1/vibe/swarm/runs", {
    method: "POST",
    body: JSON.stringify({ preset_name, user_vars }),
  });
export const getSwarmRun = (id: string) => api<SwarmRunStatus>(`/api/v1/vibe/swarm/runs/${id}`);
export const listSwarmRuns = () => api<SwarmRunRow[]>(`/api/v1/vibe/swarm/runs`);

export async function downloadSwarmPdf(id: string) {
  const { authDownload } = await import("@/api/client");
  await authDownload(`/api/v1/vibe/swarm/runs/${id}/pdf?_=${Date.now()}`, `swarm_${id.slice(0, 16)}.pdf`);
}

/* ------------------------ Persian form knowledge base ---------------------- */
/* Mirrors bot/app/main.py (VAR_FA, VAR_SUGGESTIONS, SYMBOL_ALIASES) so the web
   form behaves exactly like the Telegram bot form.                            */

export const CRYPTO_PRESETS = new Set(["crypto_research_lab", "crypto_trading_desk"]);

export const VAR_FA: Record<string, [string, string]> = {
  target: ["🎯 دارایی هدف", "چه ارز یا سهمی را تحلیل کنیم؟ یکی را انتخاب کن یا بنویس (مثلاً BTC یا تسلا)"],
  timeframe: ["⏱ بازه زمانی", "تحلیل برای چه بازه‌ای باشد؟"],
  market: ["🌍 بازار هدف", "کدام بازار را می‌خواهی تحلیل کنیم؟"],
  goal: ["🎓 هدف تحقیق", "تمرکز اصلی تحلیل چه باشد؟"],
  horizon: ["📅 افق سرمایه‌گذاری", "چقدر قصد داری نگه داری کنی؟"],
  risk_profile: ["⚖️ پروفایل ریسک", "سطح ریسک قابل تحمل شما؟"],
  risk_tolerance: ["⚖️ تحمل ریسک", "چقدر ریسک را می‌پذیری؟"],
  view: ["📈 دیدگاه بازار", "دیدگاه فعلی شما نسبت به بازار؟"],
  target_variable: ["🎯 متغیر پیش‌بینی", "مدل چه چیزی را پیش‌بینی کند؟"],
  factor_type: ["🧮 نوع فاکتور", "کدام دسته فاکتور بررسی شود؟"],
  fund_type: ["💼 نوع صندوق", "چه نوع صندوقی تحلیل شود؟"],
  sector: ["🏭 صنعت", "کدام صنعت بررسی شود؟ (خالی = همه صنایع)"],
  commodity: ["🛢 کالا", "کدام کالا تحلیل شود؟ (نفت، طلا، مس...)"],
  crisis: ["🌍 سناریوی بحران", "چه سناریوی بحرانی شبیه‌سازی شود؟ مثلاً: جنگ تجاری، بسته شدن تنگه هرمز"],
  portfolio: ["📊 پرتفوی", "پرتفوی مورد نظر را توصیف کن (مثلاً ترکیب ارزش-رشد)"],
  company: ["🏢 شرکت", "نام یا نماد شرکت (مثلاً تسلا، NVDA)"],
  review_period: ["🔁 بازه بازنگری", "بازنگری با چه بازه‌ای انجام شود؟"],
  strategy_type: ["📈 نوع استراتژی", "کدام سبک استراتژی؟"],
  event_type: ["📢 نوع رویداد", "کدام رویدادها بررسی شوند؟ (ادغام، گزارش مالی...)"],
};

const TARGET_CRYPTO = ["BTC", "ETH", "SOL", "XRP", "DOGE", "BNB", "ADA", "TON", "TRX", "AVAX", "LINK", "DOT"];
const TARGET_MULTI = ["BTC", "ETH", "TSLA", "AAPL", "NVDA", "MSFT", "GOOGL", "AMZN", "META", "طلا", "نفت", "نقره", "EUR/USD", "GBP/USD", "USD/JPY", "NFLX", "AMD"];

export const VAR_SUGGESTIONS: Record<string, string[]> = {
  timeframe: ["کوتاه‌مدت ۱-۴ هفته", "میان‌مدت ۱-۳ ماه", "بلندمدت ۳-۱۲ ماه"],
  market: ["کریپتو", "سهام آمریکا", "سهام چین", "بازار جهانی چند-دارایی"],
  goal: ["چشم‌انداز ماه آینده", "کشف فرصت‌های کم‌ارزش", "تحلیل ریسک پرتفوی", "انتخاب سهم ماهانه"],
  horizon: ["۱ ماه", "۳ ماه", "۶ ماه", "۱ سال"],
  risk_profile: ["محافظه‌کار", "متعادل", "تهاجمی"],
  risk_tolerance: ["محافظه‌کار", "متعادل", "تهاجمی"],
  view: ["صعودی", "نزولی", "خنثی", "نوسانی"],
  target_variable: ["بازده", "جهت حرکت", "نوسان"],
  factor_type: ["ارزش", "مومنتوم", "کیفیت", "رشد"],
  fund_type: ["سهامی", "درآمد ثابت", "مختلط", "شاخصی"],
  sector: ["بانک", "انرژی", "نیمه‌هادی", "مصرفی"],
};

const SYMBOL_ALIASES: Record<string, string> = {
  "طلا": "GC=F", "طلات": "GC=F", "gold": "GC=F",
  "نقره": "SI=F", "silver": "SI=F",
  "نفت": "CL=F", "نفت خام": "CL=F", "oil": "CL=F", "wti": "CL=F",
  "گاز": "NG=F", "natural gas": "NG=F",
  "مس": "HG=F", "copper": "HG=F",
  "بیت‌کوین": "BTC", "بیت کوین": "BTC", "bitcoin": "BTC",
  "اتریوم": "ETH", "ethereum": "ETH",
  "تسلا": "TSLA", "tesla": "TSLA",
  "اپل": "AAPL.US", "apple": "AAPL.US",
  "انویدیا": "NVDA", "nvidia": "NVDA",
  "مایکروسافت": "MSFT", "microsoft": "MSFT",
  "گوگل": "GOOGL", "آلفابت": "GOOGL", "google": "GOOGL",
  "آمازون": "AMZN", "amazon": "AMZN",
  "متا": "META", "فیس‌بوک": "META", "facebook": "META",
  "دلار": "EUR/USD", "یورو دلار": "EUR/USD",
  "شاخص نزدک": "QQQ", "نزدک": "QQQ",
  "شاخص داوجونز": "DIA", "داوجونز": "DIA",
  "اس‌اند‌پی": "SPY", "شاخص اس‌اند‌پی": "SPY",
};

export function resolveSymbol(text: string): string | null {
  const t = text.trim().toLowerCase();
  for (const [k, v] of Object.entries(SYMBOL_ALIASES)) {
    if (t === k || (t.length >= 3 && k.includes(t))) return v;
  }
  return null;
}

export function fuzzySuggest(text: string, pool: string[]): string[] {
  const t = text.trim().toLowerCase();
  if (!t) return [];
  const starts = pool.filter((s) => s.toLowerCase().startsWith(t));
  const contains = pool.filter((s) => s.toLowerCase().includes(t) && !starts.includes(s));
  return [...starts, ...contains].slice(0, 6);
}

export function targetPool(presetName: string): string[] {
  return CRYPTO_PRESETS.has(presetName) ? TARGET_CRYPTO : TARGET_MULTI;
}

export function suggestionsFor(varName: string, presetName: string): string[] {
  if (varName === "target") return targetPool(presetName);
  return VAR_SUGGESTIONS[varName] || [];
}

export function varTitle(varName: string, engineDesc: string, required: boolean): { title: string; desc: string } {
  const [t, d] = VAR_FA[varName] || [`❓ ${varName}`, ""];
  const desc = d || engineDesc || "یکی از گزینه‌ها را انتخاب کن یا مقدارش را بنویس.";
  return { title: t, desc: required ? desc : `${desc}\n⭕ این مورد اختیاری است — می‌توانی ردش کنی.` };
}

export const TASK_ICON: Record<string, string> = {
  completed: "✅", in_progress: "🔄", blocked: "⚠️", failed: "❌",
};
