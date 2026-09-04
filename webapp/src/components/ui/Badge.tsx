import { cn } from "@/lib/utils";

export function Badge({ tone, children }: {
  tone: "success" | "running" | "failed" | "bt" | "chat"; children: React.ReactNode;
}) {
  const map = {
    success: "bg-pos/10 text-pos border-pos/20",
    running: "bg-amber-400/10 text-amber-300 border-amber-400/20",
    failed: "bg-neg/10 text-neg border-neg/20",
    bt: "bg-brand/15 text-indigo-300 border-brand/25",
    chat: "bg-white/5 text-muted border-line",
  } as const;
  return (
    <span className={cn(
      "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold leading-none",
      map[tone]
    )}>
      {children}
    </span>
  );
}

export function StatusDot({ status }: { status?: string }) {
  const color =
    status === "success" ? "bg-pos" :
    status === "running" ? "bg-amber-400" : "bg-neg";
  return (
    <span className="relative flex h-2 w-2">
      <span className={cn("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", color)} />
      <span className={cn("relative inline-flex h-2 w-2 rounded-full", color)} />
    </span>
  );
}
