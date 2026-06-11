/*
File: skills/serve-learning-graph/web/src/main.tsx

Purpose:
    Browser entrypoint for the GUI workflow host shell.

Responsibilities:
    - Load the shared design tokens / HeroUI v3 theme via styles.css
    - Mount the React application (App shell) into #root
    - Keep bootstrap code separate from feature rendering logic

What this file does NOT do:
    - Fetch or transform graph data
    - Define UI behavior or own route state

Note (HeroUI v3 integration):
    HeroUI v3 removed the global `HeroUIProvider` that v2 required; theming is
    delivered entirely through the CSS layer (`@import "@heroui/react/styles"`
    in styles.css). So there is intentionally no provider wrapper here — the
    supported v3 + Tailwind v4 setup is CSS-first.
*/

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
