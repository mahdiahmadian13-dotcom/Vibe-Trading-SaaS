import { motion, type HTMLMotionProps } from "framer-motion";
import { forwardRef } from "react";
import { cn } from "@/lib/utils";

type Variant = "primary" | "ghost" | "outline";
type Size = "sm" | "md" | "lg";

const variants: Record<Variant, string> = {
  primary:
    "bg-gradient-to-l from-brand-deep via-brand to-brand-soft text-white shadow-[0_8px_24px_-6px_rgba(99,102,241,.5)] hover:shadow-[0_12px_32px_-6px_rgba(99,102,241,.65)]",
  ghost: "text-muted hover:text-ink hover:bg-white/5",
  outline: "border border-line bg-white/[.03] text-ink hover:bg-white/[.06] hover:border-brand/40",
};

const sizes: Record<Size, string> = {
  sm: "h-9 px-4 text-[13px] rounded-lg",
  md: "h-11 px-6 text-sm rounded-xl",
  lg: "h-12 px-8 text-[15px] rounded-xl",
};

export interface ButtonProps extends HTMLMotionProps<"button"> {
  variant?: Variant;
  size?: Size;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = "primary", size = "md", ...props }, ref) => (
    <motion.button
      ref={ref}
      whileHover={{ y: -1 }}
      whileTap={{ scale: 0.97 }}
      transition={{ type: "spring", stiffness: 400, damping: 24 }}
      className={cn(
        "inline-flex select-none items-center justify-center gap-2 font-semibold outline-none transition-colors duration-200 focus-visible:ring-2 focus-visible:ring-brand/60 disabled:pointer-events-none disabled:opacity-50",
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    />
  )
);
Button.displayName = "Button";
