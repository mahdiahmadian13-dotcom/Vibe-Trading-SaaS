/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ["class"],
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#06070b",
        panel: "#0b0d14",
        panel2: "#11141d",
        line: "rgba(255,255,255,0.07)",
        ink: "#eef1f8",
        muted: "#8a93ab",
        brand: { DEFAULT: "#6366f1", soft: "#8b5cf6", deep: "#4f46e5" },
        pos: "#34d399",
        neg: "#f87171",
      },
      fontFamily: { sans: ["Vazirmatn", "Tahoma", "sans-serif"] },
      borderRadius: { xl2: "1.15rem" },
      keyframes: {
        "fade-up": { from: { opacity: "0", transform: "translateY(10px)" }, to: { opacity: "1", transform: "none" } },
        shimmer: { from: { backgroundPosition: "200% 0" }, to: { backgroundPosition: "-200% 0" } },
      },
      animation: {
        "fade-up": "fade-up .45s cubic-bezier(.2,.7,.3,1) both",
        shimmer: "shimmer 1.6s linear infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};
