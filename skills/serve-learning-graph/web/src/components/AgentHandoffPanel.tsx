/*
File: skills/serve-learning-graph/web/src/components/AgentHandoffPanel.tsx

Purpose:
    Render the "this step runs in the tool" hand-off when the data-plane API
    returns an `agent_required` error. The local web host never calls the LLM,
    so generation (assess probing, learn outline/chat, exam questions, scoring,
    review re-exam) is deferred to Codex / Claude Code.

Responsibilities:
    - Explain why the step cannot run in the browser
    - Surface the exact slash command to run, with a one-click copy
    - Point the learner back to where they return once the skill has persisted

What this file does NOT do:
    - Call any API or run the skill itself (the user runs it in their tool)
    - Decide WHEN to show — see ApiErrorNotice for the agent_required dispatch

Inputs: an ApiError whose code === "agent_required" (skill/command/deep_link)
Outputs: an informational card; copying the command is the only interaction
*/

import { useState } from "react";
import { Button, Card, Chip } from "@heroui/react";

import type { ApiError } from "../types";

export default function AgentHandoffPanel({ error }: { error: ApiError }) {
  const [copied, setCopied] = useState(false);
  const command = error.command ?? "";

  const onCopy = async () => {
    if (!command) return;
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard may be unavailable (insecure context); the command is visible.
      setCopied(false);
    }
  };

  return (
    <Card className="handoff-panel" role="status" aria-label="需要在工具中继续">
      <Card.Header>
        <div className="handoff-head">
          <Chip color="accent" variant="soft">需要大模型生成</Chip>
          {error.skill ? (
            <Chip color="default" variant="soft">skill: {error.skill}</Chip>
          ) : null}
        </div>
        <Card.Description>{error.message}</Card.Description>
      </Card.Header>

      <Card.Content>
        <p className="handoff-hint">
          本页是只读/作答的数据视图,不会调用大模型。请到 Codex / Claude Code 运行下面的命令完成生成,
          完成后回到本页继续。
        </p>

        {command ? (
          <div className="handoff-command">
            <code>{command}</code>
            <Button variant="outline" size="sm" onPress={onCopy}>
              {copied ? "已复制" : "复制"}
            </Button>
          </div>
        ) : null}

        {error.deep_link ? (
          <p className="handoff-return">
            完成后返回:<a href={error.deep_link}>{error.deep_link}</a>
          </p>
        ) : null}
      </Card.Content>
    </Card>
  );
}
