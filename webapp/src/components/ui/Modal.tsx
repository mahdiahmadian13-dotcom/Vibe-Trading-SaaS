import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { X } from "lucide-react";
import { Button } from "./Button";

export function Modal({ open, onClose, children }: {
  open: boolean; onClose: () => void; children: React.ReactNode;
}) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
          transition={{ duration: 0.22 }}
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-md"
          onClick={onClose}
        >
          <motion.div
            initial={{ opacity: 0, y: 24, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.98 }}
            transition={{ duration: 0.3, ease: [0.2, 0.8, 0.25, 1] }}
            className="relative max-h-[88vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-line bg-panel2 p-7 shadow-[0_40px_100px_-20px_rgba(0,0,0,.8)]"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={onClose}
              className="absolute left-5 top-5 rounded-lg border border-line bg-white/5 p-2 text-muted transition-colors hover:bg-white/10 hover:text-ink"
              aria-label="بستن"
            >
              <X size={16} />
            </button>
            {children}
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

export function Toast({ msg, tone }: { msg: string; tone: "ok" | "err" }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 12 }}
      className={cnToast(tone)}
    >
      {tone === "ok" ? "✅ " : "⚠️ "}{msg}
    </motion.div>
  );
}

function cnToast(tone: "ok" | "err") {
  return [
    "fixed bottom-6 left-1/2 z-[60] -translate-x-1/2 rounded-xl border px-5 py-3 text-[13px] font-semibold shadow-2xl backdrop-blur-xl",
    tone === "ok" ? "border-pos/25 bg-pos/10 text-pos" : "border-neg/25 bg-neg/10 text-neg",
  ].join(" ");
}

export { Button };
