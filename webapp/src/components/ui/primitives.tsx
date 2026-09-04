import { motion } from "framer-motion";
import { cn } from "@/lib/utils";

export function Card({ className, children }: { className?: string; children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45, ease: [0.2, 0.7, 0.3, 1] }}
      className={cn(
        "rounded-xl2 border border-line bg-panel/70 backdrop-blur-xl",
        "shadow-[0_8px_30px_-12px_rgba(0,0,0,.5)]",
        className
      )}
    >
      {children}
    </motion.div>
  );
}

export function StatCard({ label, value, tone, sub }: {
  label: string; value: string; tone?: "pos" | "neg" | "brand"; sub?: string;
}) {
  return (
    <Card className="card-hover p-5">
      <div className="text-[12.5px] font-semibold text-muted">{label}</div>
      <div className={cn(
        "mt-2 text-[22px] font-bold tracking-tight",
        tone === "pos" && "text-pos",
        tone === "neg" && "text-neg",
        tone === "brand" && "text-gradient"
      )}>
        {value}
      </div>
      {sub && <div className="mt-1 text-[11.5px] text-muted/80">{sub}</div>}
    </Card>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-4 w-full", className)} />;
}

export function CardSkeleton() {
  return (
    <div className="rounded-xl2 border border-line bg-panel/50 p-5">
      <div className="flex items-center justify-between">
        <Skeleton className="h-3 w-24" />
        <Skeleton className="h-5 w-14 rounded-full" />
      </div>
      <Skeleton className="mt-4 h-4 w-4/5" />
      <Skeleton className="mt-2 h-4 w-2/5" />
      <div className="mt-5 flex gap-4">
        <Skeleton className="h-3.5 w-16" />
        <Skeleton className="h-3.5 w-16" />
      </div>
    </div>
  );
}

export function EmptyState({ icon, title, desc, action }: {
  icon: string; title: string; desc: string; action?: React.ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.4 }}
      className="flex flex-col items-center justify-center rounded-xl2 border border-dashed border-line bg-panel/30 px-6 py-16 text-center"
    >
      <div className="mb-4 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-brand/25 to-brand-soft/15 text-3xl shadow-[inset_0_1px_0_rgba(255,255,255,.08)]">
        {icon}
      </div>
      <div className="text-[15px] font-bold">{title}</div>
      <div className="mt-2 max-w-sm text-[13px] leading-7 text-muted">{desc}</div>
      {action && <div className="mt-6">{action}</div>}
    </motion.div>
  );
}
