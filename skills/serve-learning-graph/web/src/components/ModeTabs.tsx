/*
File: skills/serve-learning-graph/web/src/components/ModeTabs.tsx

Purpose:
    Graph / Assess / Learn / Exam / Review tab switcher for the workflow host.

Responsibilities:
    - Render a compact HeroUI Tabs control for the five workflow views
    - Report the selected view up to the shell, which owns route state
    - Stay a pure switcher: it neither auto-jumps between flows nor triggers
      any flow logic; each tab is an independent entry (doc/14 独立路径)

What this file does NOT do:
    - Own URL state (App.tsx maps view <-> ?view= and calls onChange)
    - Fetch data or run feature logic
    - Imply ordering or completion between flows

Inputs: current WorkflowView + onChange callback
Outputs: A controlled tab strip
*/

import { Tabs } from "@heroui/react";
import type { WorkflowView } from "../types";

const VIEWS: { key: WorkflowView; label: string }[] = [
  { key: "graph", label: "Graph" },
  { key: "assess", label: "Assess" },
  { key: "learn", label: "Learn" },
  { key: "exam", label: "Exam" },
  { key: "review", label: "Review" },
];

export interface ModeTabsProps {
  view: WorkflowView;
  onChange: (view: WorkflowView) => void;
}

export default function ModeTabs({ view, onChange }: ModeTabsProps) {
  return (
    <Tabs
      aria-label="Workflow views"
      selectedKey={view}
      onSelectionChange={(key) => onChange(key as WorkflowView)}
    >
      <Tabs.List>
        {VIEWS.map((item) => (
          <Tabs.Tab key={item.key} id={item.key}>
            {item.label}
          </Tabs.Tab>
        ))}
      </Tabs.List>
    </Tabs>
  );
}
