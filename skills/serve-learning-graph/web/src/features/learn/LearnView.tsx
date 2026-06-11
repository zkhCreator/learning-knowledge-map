/*
File: skills/serve-learning-graph/web/src/features/learn/LearnView.tsx

Purpose:
    Learn Start flow for the GUI workflow host (doc/14 独立路径 → Learn Start).
    Runs a per-node Socratic learning session: prepare a node, then chat one
    turn at a time while tracking outline coverage and progress.

Responsibilities:
    - Let the user pick a node (from the goal's nodes) or paste a node id/prefix,
      then prepare it (generate/reuse outline + create/resume session).
    - Show the outline sections with a covered/uncovered marker and a progress
      bar over those sections.
    - Show the Socratic chat history and an input; each send shows the reply and
      advances progress.
    - When exam_ready, only *surface* an Exam entry/hint — never auto-navigate
      to the Exam flow (doc/14 独立路径).

What this file does NOT do:
    - Read SQLite or call LLMs (server.js proxies to Python).
    - Auto-jump into Exam/Review — the exam hint is a manual link only.
    - Own URL/tab state beyond reading ?node= for its initial selection.

Inputs: /api/graph (node picker), prepareNode / sendLearnMessage; optional
        ?node= URL param
Outputs: An interactive outline + Socratic chat panel ending at exam-readiness
*/

import { useCallback, useEffect, useRef, useState } from "react";
import { Button, Card, Chip, Spinner, TextArea } from "@heroui/react";

import { getGraph, prepareNode, sendLearnMessage } from "../../api";
import ApiErrorNotice from "../../components/ApiErrorNotice";
import type {
  ApiError,
  GraphNode,
  LearnMessage,
  LearnNode,
  LearnSection,
  LearnSession,
} from "../../types";

type Phase = "setup" | "learning";

function readNodeFromUrl(): string {
  return new URLSearchParams(window.location.search).get("node") ?? "";
}

/** Flatten the graph tree(s) into a list of atomic nodes for the picker. */
function flattenNodes(nodes: GraphNode[]): { id: string; title: string }[] {
  const out: { id: string; title: string }[] = [];
  for (const n of nodes) {
    if (n.is_atomic && !n.synthetic) out.push({ id: n.id, title: n.title });
  }
  return out;
}

export default function LearnView() {
  const [pickList, setPickList] = useState<{ id: string; title: string }[]>([]);
  const [selectedNode, setSelectedNode] = useState<string>("");
  const [pastedNode, setPastedNode] = useState<string>("");

  const [phase, setPhase] = useState<Phase>("setup");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const [node, setNode] = useState<LearnNode | null>(null);
  const [sections, setSections] = useState<LearnSection[]>([]);
  const [session, setSession] = useState<LearnSession | null>(null);
  const [covered, setCovered] = useState<number[]>([]);
  const [progress, setProgress] = useState(0);
  const [examReady, setExamReady] = useState(false);

  const [messages, setMessages] = useState<LearnMessage[]>([]);
  const [draft, setDraft] = useState("");

  const logRef = useRef<HTMLDivElement | null>(null);

  // Load the goal's atomic nodes for the picker.
  useEffect(() => {
    let cancelled = false;
    getGraph().then((result) => {
      if (cancelled) return;
      if (result.ok) {
        const list = flattenNodes(result.data.graph.nodes ?? []);
        setPickList(list);
        const fromUrl = readNodeFromUrl();
        if (fromUrl) {
          const match = list.find((n) => n.id === fromUrl || n.id.startsWith(fromUrl));
          setSelectedNode(match ? match.id : "");
          setPastedNode(match ? "" : fromUrl);
        } else {
          setSelectedNode(list[0]?.id ?? "");
        }
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // Keep the chat scrolled to the newest message without resizing the panel.
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [messages]);

  const nodeArg = pastedNode.trim() || selectedNode;

  const onPrepare = useCallback(async () => {
    if (!nodeArg) return;
    setBusy(true);
    setError(null);
    const result = await prepareNode(nodeArg);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    const data = result.data;
    setNode(data.node);
    setSections(data.outline.sections);
    setSession(data.session);
    setCovered(data.session.covered_sections);
    setProgress(data.progress);
    setExamReady(data.exam_ready);
    setMessages(data.history);
    setDraft("");
    setPhase("learning");
  }, [nodeArg]);

  const onSend = useCallback(async () => {
    if (!session) return;
    const text = draft.trim();
    if (!text) return;
    setBusy(true);
    setError(null);
    // Optimistically show the user's turn; the reply is appended on success.
    setMessages((prev) => [...prev, { role: "user", content: text }]);
    setDraft("");
    const result = await sendLearnMessage(session.id, text);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    const data = result.data;
    setMessages((prev) => [...prev, { role: "assistant", content: data.response }]);
    setCovered(data.covered);
    setProgress(data.progress);
    setExamReady(data.exam_ready);
  }, [session, draft]);

  const onReset = useCallback(() => {
    setPhase("setup");
    setNode(null);
    setSections([]);
    setSession(null);
    setCovered([]);
    setProgress(0);
    setExamReady(false);
    setMessages([]);
    setDraft("");
    setError(null);
  }, []);

  const pct = Math.round(progress * 100);

  return (
    <section className="learn-view" aria-label="Learn start flow">
      <Card className="learn-card">
        <Card.Header>
          <Card.Title>Learn Start</Card.Title>
          <Card.Description>
            针对单个知识点进行苏格拉底式学习：生成或复用大纲，逐轮对话并跟踪覆盖进度。达到考试条件时只显示考试入口，不会自动开始考试。
          </Card.Description>
        </Card.Header>

        <Card.Content>
          <ApiErrorNotice error={error} className="learn-error" />

          {phase === "setup" ? (
            <div className="learn-setup">
              <label className="learn-field">
                <span>选择知识点</span>
                <select
                  className="learn-select"
                  value={selectedNode}
                  onChange={(e) => {
                    setSelectedNode(e.target.value);
                    setPastedNode("");
                  }}
                  disabled={busy || pickList.length === 0}
                >
                  {pickList.length === 0 ? <option value="">没有可学习的知识点</option> : null}
                  {pickList.map((n) => (
                    <option key={n.id} value={n.id}>
                      {n.title}
                    </option>
                  ))}
                </select>
              </label>

              <label className="learn-field">
                <span>或粘贴 Node ID / 前缀</span>
                <input
                  className="learn-input"
                  value={pastedNode}
                  onChange={(e) => setPastedNode(e.target.value)}
                  placeholder="例如 a1b2c3d4"
                  disabled={busy}
                />
              </label>

              <div className="learn-actions">
                <Button variant="primary" onPress={onPrepare} isDisabled={busy || !nodeArg}>
                  {busy ? <Spinner size="sm" /> : "开始学习"}
                </Button>
              </div>
            </div>
          ) : null}

          {phase === "learning" && node ? (
            <div className="learn-session">
              <div className="learn-node-head">
                <span className="learn-node-title">{node.title}</span>
                {node.domain ? <span className="learn-node-domain">{node.domain}</span> : null}
              </div>

              <div className="learn-progress" aria-label="Learning progress">
                <div className="learn-progress-track">
                  <div
                    className={`learn-progress-fill${examReady ? " is-ready" : ""}`}
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="learn-progress-label">
                  {pct}% · {covered.length}/{sections.length} 节
                </span>
              </div>

              <ul className="learn-outline">
                {sections.map((s) => {
                  const done = covered.includes(s.index);
                  return (
                    <li key={s.index} className={`learn-outline-item${done ? " is-covered" : ""}`}>
                      <span className="learn-outline-mark">{done ? "✓" : "○"}</span>
                      <span className="learn-outline-title">
                        §{s.index} {s.title}
                      </span>
                    </li>
                  );
                })}
              </ul>

              {examReady ? (
                <div className="learn-exam-hint" role="status">
                  <Chip color="success" variant="soft">学习进度已达标</Chip>
                  <p>
                    你已覆盖大纲主要内容。可前往
                    <a className="learn-exam-link" href={`?view=exam&node=${encodeURIComponent(node.id)}`}>
                      Exam 视图
                    </a>
                    手动开始考试 — 学习不会自动进入考试。
                  </p>
                </div>
              ) : null}

              <div className="learn-chat">
                <div className="learn-chat-log" ref={logRef}>
                  {messages.length === 0 ? (
                    <p className="learn-chat-empty">向助手提问或回答，开始苏格拉底式对话。</p>
                  ) : (
                    messages.map((m, i) => (
                      <div key={i} className={`learn-msg learn-msg-${m.role === "user" ? "user" : "assistant"}`}>
                        <span className="learn-msg-role">{m.role === "user" ? "你" : "助手"}</span>
                        <span className="learn-msg-text">{m.content}</span>
                      </div>
                    ))
                  )}
                </div>

                <TextArea
                  aria-label="Your message"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="输入你的回答或问题…"
                  rows={3}
                  disabled={busy}
                />

                <div className="learn-actions">
                  <Button variant="primary" onPress={onSend} isDisabled={busy || !draft.trim()}>
                    {busy ? <Spinner size="sm" /> : "发送"}
                  </Button>
                  <Button variant="outline" onPress={onReset} isDisabled={busy}>
                    换一个知识点
                  </Button>
                </div>
              </div>
            </div>
          ) : null}
        </Card.Content>
      </Card>
    </section>
  );
}
