/*
File: skills/serve-learning-graph/web/src/components/HealthBadge.tsx

Purpose:
    Surface the host /api/health action in the GUI as a small connection /
    capability status badge in the shell nav.

Responsibilities:
    - Call getHealth once on mount and show online/offline status
    - Show how many workflow capabilities the host reports (features minus the
      workflow-host marker), with the full feature list on hover

What this file does NOT do:
    - Own route/tab state or trigger any flow
    - Write to the database or call an LLM
    - Auto-jump between flows

Inputs: GET /api/health (via api.ts getHealth)
Outputs: A compact status badge
*/

import { useEffect, useState } from "react";

import { getHealth } from "../api";
import type { HealthData } from "../types";

type Status = "loading" | "online" | "offline";

export default function HealthBadge() {
  const [health, setHealth] = useState<HealthData | null>(null);
  const [status, setStatus] = useState<Status>("loading");

  useEffect(() => {
    let cancelled = false;
    getHealth().then((res) => {
      if (cancelled) return;
      if (res.ok) {
        setHealth(res.data);
        setStatus("online");
      } else {
        setStatus("offline");
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const flowCount = health
    ? health.features.filter((f) => f !== "workflow-host").length
    : 0;

  const label =
    status === "offline"
      ? "服务未连接"
      : status === "online"
        ? `已连接 · ${flowCount} 项能力`
        : "连接中…";

  return (
    <span
      className={`host-health host-health-${status}`}
      title={health ? health.features.join(", ") : ""}
      aria-label={`Host status: ${status}`}
    >
      <span className="host-health-dot" aria-hidden="true" />
      {label}
    </span>
  );
}
