/*
File: skills/serve-learning-graph/web/src/App.tsx

Purpose:
    Top-level GUI workflow host shell. Owns the active view/route state and
    renders the matching feature in the workspace region.

Responsibilities:
    - Map the active WorkflowView to/from the ?view= URL entry
    - Render the AppShell (summary strip + mode tabs + workspace)
    - Switch between the graph view and the not-yet-built flow placeholders
    - Keep each flow an independent entry: no implicit cross-flow auto-jumps

What this file does NOT do:
    - Implement assess/learn/exam/review feature logic (later modules)
    - Read SQLite or call LLMs
    - Transform graph data (see flow.ts) or fetch the graph (see GraphView)

Inputs: ?view= URL param, GraphPayload surfaced by the graph view
Outputs: The rendered workbench for the active flow
*/

import { useCallback, useEffect, useState } from "react";

import AppShell from "./components/AppShell";
import ModeTabs from "./components/ModeTabs";
import SummaryStrip from "./components/SummaryStrip";
import HealthBadge from "./components/HealthBadge";
import GraphView from "./features/graph/GraphView";
import AssessView from "./features/assess/AssessView";
import LearnView from "./features/learn/LearnView";
import ExamView from "./features/exam/ExamView";
import ReviewView from "./features/review/ReviewView";
import type { GraphPayload, WorkflowView } from "./types";

const VIEWS: WorkflowView[] = ["graph", "assess", "learn", "exam", "review"];

function readViewFromUrl(): WorkflowView {
  const param = new URLSearchParams(window.location.search).get("view");
  return VIEWS.includes(param as WorkflowView) ? (param as WorkflowView) : "graph";
}

function writeViewToUrl(view: WorkflowView): void {
  const url = new URL(window.location.href);
  url.searchParams.set("view", view);
  window.history.replaceState(null, "", url);
}

export default function App() {
  const [view, setView] = useState<WorkflowView>(() => readViewFromUrl());
  const [payload, setPayload] = useState<GraphPayload | null>(null);

  // Keep the view in sync with back/forward navigation.
  useEffect(() => {
    const onPopState = () => setView(readViewFromUrl());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const changeView = useCallback((next: WorkflowView) => {
    setView(next);
    writeViewToUrl(next);
  }, []);

  const onPayload = useCallback((next: GraphPayload | null) => {
    setPayload(next);
  }, []);

  let workspace;
  if (view === "graph") {
    workspace = <GraphView onPayload={onPayload} />;
  } else if (view === "assess") {
    workspace = <AssessView />;
  } else if (view === "learn") {
    workspace = <LearnView />;
  } else if (view === "exam") {
    workspace = <ExamView />;
  } else {
    workspace = <ReviewView />;
  }

  return (
    <AppShell
      summary={<SummaryStrip payload={payload} />}
      nav={
        <>
          <ModeTabs view={view} onChange={changeView} />
          <HealthBadge />
        </>
      }
      workspace={workspace}
    />
  );
}
