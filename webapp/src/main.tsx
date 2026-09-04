import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import HomePage from "@/pages/HomePage";
import "@/index.css";

const goLegacy = (hash: string) => (location.href = `/app/legacy.html${hash}`);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <HomePage goChat={() => goLegacy("")} />
  </StrictMode>
);
