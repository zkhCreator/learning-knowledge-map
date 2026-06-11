/*
File: skills/serve-learning-graph/web/src/components/AppShell.tsx

Purpose:
    Layout frame for the GUI workflow host: a tool-style workbench, not a
    landing page (doc/14 视觉风格).

Responsibilities:
    - Compose the summary strip, the compact mode-tab nav, and a full-width
      workspace region
    - Keep stable sizes so switching flows causes no layout jitter
    - Stay layout-only: it renders whatever workspace the shell passes in

What this file does NOT do:
    - Own route/tab state (App.tsx does)
    - Fetch data or run feature logic
    - Auto-jump between flows

Inputs: summary + nav + workspace render slots
Outputs: The workbench layout
*/

import type { ReactNode } from "react";

export interface AppShellProps {
  summary: ReactNode;
  nav: ReactNode;
  workspace: ReactNode;
}

export default function AppShell({ summary, nav, workspace }: AppShellProps) {
  return (
    <div className="host-shell">
      {summary}
      <nav className="host-nav" aria-label="Workflow navigation">
        {nav}
      </nav>
      <main className="host-workspace">{workspace}</main>
    </div>
  );
}
