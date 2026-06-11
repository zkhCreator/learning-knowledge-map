/*
File: skills/serve-learning-graph/web/src/components/ApiErrorNotice.tsx

Purpose:
    Single dispatch point for rendering an ApiError in a feature view. An
    `agent_required` error becomes the AgentHandoffPanel (defer to the tool);
    any other error stays a plain inline alert.

Responsibilities:
    - Render nothing when there is no error
    - Route agent_required to AgentHandoffPanel
    - Render every other error as the view's existing inline alert style

What this file does NOT do:
    - Own error state (views keep their own ApiError | null)
    - Make API calls or know about specific flows

Inputs: error (ApiError | null) and the className for the plain-alert variant
Outputs: the matching error UI, or null
*/

import type { ApiError } from "../types";
import AgentHandoffPanel from "./AgentHandoffPanel";

export default function ApiErrorNotice({
  error,
  className,
}: {
  error: ApiError | null;
  className?: string;
}) {
  if (!error) return null;
  if (error.code === "agent_required") {
    return <AgentHandoffPanel error={error} />;
  }
  return (
    <p className={className} role="alert">
      {error.message}
    </p>
  );
}
