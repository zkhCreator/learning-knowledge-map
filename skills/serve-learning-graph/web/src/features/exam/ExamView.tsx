/*
File: skills/serve-learning-graph/web/src/features/exam/ExamView.tsx

Purpose:
    Exam flow for the GUI workflow host as a pure DATA PLANE (doc/16). The web
    host never calls the LLM: question generation and scoring run in the tool
    (/exam-start). Here the learner loads a tool-generated exam, answers each
    question, and saves the raw answers; scoring is handed back to the tool.

Responsibilities:
    - Setup: pick a node and request generation — which returns an
      `agent_required` hand-off pointing at /exam-start.
    - Answering: load a persisted exam by id (deep link ?exam=<id>), present one
      question at a time, and persist each raw answer (no score shown).
    - Submit: hand the scoring + finalize step back to the tool.

What this file does NOT do:
    - Generate questions, score answers, or finalize (all LLM work → tool).
    - Read SQLite or see the expected answer (it never leaves the server).
    - Auto-jump into another flow — Exam is an independent ?view=exam entry.

Inputs: optional ?exam= / ?node= URL params; getExam / recordExamAnswer;
        startExam / finishExam (which return agent_required hand-offs)
Outputs: an answer-and-save exam view that defers generation/scoring to the tool
*/

import { useCallback, useEffect, useState } from "react";
import { Button, Card, Chip, Spinner, TextArea } from "@heroui/react";

import { finishExam, getExam, getGraph, recordExamAnswer, startExam } from "../../api";
import ApiErrorNotice from "../../components/ApiErrorNotice";
import type {
  ApiError,
  ExamMnemonic,
  ExamNode,
  ExamViewData,
  ExamViewQuestion,
  GraphNode,
} from "../../types";

type Phase = "setup" | "answering";

function readParam(name: string): string {
  return new URLSearchParams(window.location.search).get(name) ?? "";
}

/** Flatten the graph tree(s) into a list of atomic nodes for the picker. */
function flattenNodes(nodes: GraphNode[]): { id: string; title: string }[] {
  const out: { id: string; title: string }[] = [];
  for (const n of nodes) {
    if (n.is_atomic && !n.synthetic) out.push({ id: n.id, title: n.title });
  }
  return out;
}

export default function ExamView() {
  const [pickList, setPickList] = useState<{ id: string; title: string }[]>([]);
  const [selectedNode, setSelectedNode] = useState<string>("");
  const [pastedNode, setPastedNode] = useState<string>("");

  const [phase, setPhase] = useState<Phase>("setup");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const [examId, setExamId] = useState<string>(() => readParam("exam"));
  const [examNode, setExamNode] = useState<ExamNode | null>(null);
  const [questions, setQuestions] = useState<ExamViewQuestion[]>([]);
  const [index, setIndex] = useState(0);
  const [draft, setDraft] = useState("");
  const [finished, setFinished] = useState(false);
  const [mnemonic, setMnemonic] = useState<ExamMnemonic | null>(null);

  // Load the goal's atomic nodes for the picker.
  useEffect(() => {
    let cancelled = false;
    getGraph().then((res) => {
      if (cancelled) return;
      if (res.ok) {
        const list = flattenNodes(res.data.graph.nodes ?? []);
        setPickList(list);
        const fromUrl = readParam("node");
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

  const loadExam = useCallback(async (id: string) => {
    setBusy(true);
    setError(null);
    const res = await getExam(id);
    setBusy(false);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    applyExam(res.data);
  }, []);

  function applyExam(data: ExamViewData) {
    setExamId(data.exam_id);
    setExamNode(data.node);
    setQuestions(data.questions);
    setFinished(data.finished);
    setMnemonic(data.mnemonic ?? null);
    // Resume at the first unanswered question.
    const firstUnanswered = data.questions.findIndex((q) => !q.user_answer);
    const start = firstUnanswered === -1 ? 0 : firstUnanswered;
    setIndex(start);
    setDraft(data.questions[start]?.user_answer ?? "");
    setPhase("answering");
  }

  // Deep link ?exam=<id>: load the persisted exam straight into answering.
  useEffect(() => {
    const fromUrl = readParam("exam");
    if (fromUrl) loadExam(fromUrl);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const nodeArg = pastedNode.trim() || selectedNode;

  // Generation lives in the tool: this surfaces the /exam-start hand-off.
  const onGenerate = useCallback(async () => {
    if (!nodeArg) return;
    setBusy(true);
    setError(null);
    const res = await startExam(nodeArg);
    setBusy(false);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    // Data plane never reaches here (start always defers), but stay safe.
    void res;
  }, [nodeArg]);

  const current = questions[index] ?? null;

  const onSave = useCallback(async () => {
    if (!current) return;
    setBusy(true);
    setError(null);
    const res = await recordExamAnswer(examId, current.id, draft.trim());
    setBusy(false);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    setQuestions((prev) =>
      prev.map((q) => (q.id === current.id ? { ...q, user_answer: res.data.user_answer } : q))
    );
  }, [current, examId, draft]);

  const onNav = useCallback(
    (next: number) => {
      setIndex(next);
      setDraft(questions[next]?.user_answer ?? "");
      setError(null);
    },
    [questions]
  );

  // Scoring + finalize run in the tool: this surfaces the hand-off.
  const onSubmitForScoring = useCallback(async () => {
    setBusy(true);
    setError(null);
    const res = await finishExam(examId, {});
    setBusy(false);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    void res;
  }, [examId]);

  const onReset = useCallback(() => {
    setPhase("setup");
    setExamId("");
    setExamNode(null);
    setQuestions([]);
    setIndex(0);
    setDraft("");
    setFinished(false);
    setMnemonic(null);
    setError(null);
  }, []);

  const answeredCount = questions.filter((q) => q.user_answer).length;
  const allAnswered = questions.length > 0 && answeredCount === questions.length;
  const isLast = index >= questions.length - 1;

  return (
    <section className="exam-view" aria-label="Exam flow">
      <Card className="exam-card">
        <Card.Header>
          <Card.Title>Exam</Card.Title>
          <Card.Description>
            本页用于加载已生成的考卷、作答并保存。出题与评分由工具完成(/exam-start),
            完成后回到本页继续。这是独立入口,不会自动跳转到其他流程。
          </Card.Description>
        </Card.Header>

        <Card.Content>
          <ApiErrorNotice error={error} className="exam-error" />

          {phase === "setup" ? (
            <div className="exam-setup">
              <label className="exam-field">
                <span>选择知识点</span>
                <select
                  className="exam-select"
                  value={selectedNode}
                  onChange={(e) => {
                    setSelectedNode(e.target.value);
                    setPastedNode("");
                  }}
                  disabled={busy || pickList.length === 0}
                >
                  {pickList.length === 0 ? <option value="">没有可考试的知识点</option> : null}
                  {pickList.map((n) => (
                    <option key={n.id} value={n.id}>
                      {n.title}
                    </option>
                  ))}
                </select>
              </label>

              <label className="exam-field">
                <span>或粘贴 Node ID / 前缀</span>
                <input
                  className="exam-input"
                  value={pastedNode}
                  onChange={(e) => setPastedNode(e.target.value)}
                  placeholder="例如 a1b2c3d4"
                  disabled={busy}
                />
              </label>

              <label className="exam-field">
                <span>或粘贴已生成的 Exam ID</span>
                <input
                  className="exam-input"
                  value={examId}
                  onChange={(e) => setExamId(e.target.value)}
                  placeholder="工具生成考卷后会给出深链"
                  disabled={busy}
                />
              </label>

              <div className="exam-actions">
                <Button variant="primary" onPress={onGenerate} isDisabled={busy || !nodeArg}>
                  {busy ? <Spinner size="sm" /> : "在工具中生成考卷"}
                </Button>
                <Button
                  variant="outline"
                  onPress={() => examId && loadExam(examId)}
                  isDisabled={busy || !examId}
                >
                  加载考卷
                </Button>
              </div>
            </div>
          ) : null}

          {phase === "answering" && examNode && current ? (
            <div className="exam-run">
              {mnemonic?.prompt ? (
                <details className="exam-mnemonic">
                  <summary>🧠 考前回忆 — 先在脑中回忆，再开始作答</summary>
                  <p className="exam-mnemonic-prompt">{mnemonic.prompt}</p>
                  {mnemonic.display ? (
                    <details className="exam-mnemonic-display">
                      <summary>看锚点参考</summary>
                      <p>{mnemonic.display}</p>
                    </details>
                  ) : null}
                </details>
              ) : null}
              <div className="exam-node-head">
                <span className="exam-node-title">{examNode.title}</span>
                <span className="exam-node-count">
                  第 {index + 1}/{questions.length} 题 · 已保存 {answeredCount}/{questions.length}
                </span>
              </div>

              <div className="exam-question">
                <div className="exam-question-tags">
                  <Chip color="default" variant="soft">{current.question_type}</Chip>
                  {current.is_expansion ? <Chip color="accent" variant="soft">扩展</Chip> : null}
                  {current.user_answer ? <Chip color="success" variant="soft">已保存</Chip> : null}
                </div>
                <p className="exam-question-text">{current.question}</p>
                {current.options ? (
                  <ul className="exam-options">
                    {current.options.map((opt, i) => (
                      <li key={i}>{opt}</li>
                    ))}
                  </ul>
                ) : null}
              </div>

              <TextArea
                aria-label="Your answer"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                placeholder="输入你的答案…"
                rows={4}
                disabled={busy || finished}
              />

              <div className="exam-actions">
                <Button variant="primary" onPress={onSave} isDisabled={busy || finished}>
                  {busy ? <Spinner size="sm" /> : "保存答案"}
                </Button>
                <Button variant="outline" onPress={() => onNav(index - 1)} isDisabled={busy || index === 0}>
                  上一题
                </Button>
                <Button variant="outline" onPress={() => onNav(index + 1)} isDisabled={busy || isLast}>
                  下一题
                </Button>
              </div>

              <div className="exam-actions">
                <Button
                  variant="primary"
                  onPress={onSubmitForScoring}
                  isDisabled={busy || !allAnswered}
                >
                  {busy ? <Spinner size="sm" /> : "提交评分(去工具)"}
                </Button>
                <Button variant="outline" onPress={onReset} isDisabled={busy}>
                  换一个考卷
                </Button>
              </div>
              {!allAnswered ? (
                <p className="exam-hint">保存全部题目的答案后即可提交评分。</p>
              ) : null}
            </div>
          ) : null}
        </Card.Content>
      </Card>
    </section>
  );
}
