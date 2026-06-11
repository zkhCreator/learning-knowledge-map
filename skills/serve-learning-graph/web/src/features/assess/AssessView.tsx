/*
File: skills/serve-learning-graph/web/src/features/assess/AssessView.tsx

Purpose:
    Goal Assess flow for the GUI workflow host (doc/14 独立路径 → Goal Assess).
    Lets the user assess what they already know about a goal so later learning
    can skip mastered content.

Responsibilities:
    - Let the user pick a goal (/api/goals) and a self-report level (1-5), then
      start an assessment
    - Show one probe question at a time, submit the free-text answer, and show
      the per-probe score/result
    - Carry the stateless in-flight history + probe context between calls
      (the server keeps no memory; only durable state is persisted)
    - Show a final summary (mastered/unknown counts + recommended start node)

What this file does NOT do:
    - Read SQLite or call LLMs (server.js proxies to Python)
    - Auto-jump into Learn/Exam/Review — the summary only *shows* a next-step
      hint; it never navigates (doc/14 独立路径)
    - Own URL/tab state beyond reading ?goal= for its initial selection

Inputs: /api/goals, startAssessment / answerProbe; optional ?goal= URL param
Outputs: An interactive assessment panel ending in a summary
*/

import { useCallback, useEffect, useState } from "react";
import { Button, Card, Chip, Spinner, TextArea } from "@heroui/react";

import { answerProbe, getGoals, startAssessment } from "../../api";
import ApiErrorNotice from "../../components/ApiErrorNotice";
import type {
  ApiError,
  AssessAnswerData,
  AssessHistoryItem,
  AssessProbe,
  AssessSummary,
  LearningGoal,
} from "../../types";

const SELF_REPORT_LEVELS: { value: number; label: string }[] = [
  { value: 1, label: "1 · 完全陌生" },
  { value: 2, label: "2 · 有点了解" },
  { value: 3, label: "3 · 学过一部分" },
  { value: 4, label: "4 · 比较熟悉" },
  { value: 5, label: "5 · 深度掌握" },
];

type Phase = "setup" | "probe" | "summary";

function readGoalFromUrl(): string {
  return new URLSearchParams(window.location.search).get("goal") ?? "";
}

function scoreChipColor(score: number): "success" | "warning" | "danger" {
  if (score >= 0.8) return "success";
  if (score >= 0.5) return "warning";
  return "danger";
}

interface LastResult {
  score: number;
  passed: boolean;
  title: string;
}

export default function AssessView() {
  const [goals, setGoals] = useState<LearningGoal[]>([]);
  const [goalsError, setGoalsError] = useState<string | null>(null);
  const [selectedGoal, setSelectedGoal] = useState<string>("");
  const [selfReport, setSelfReport] = useState<number>(3);

  const [phase, setPhase] = useState<Phase>("setup");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const [history, setHistory] = useState<AssessHistoryItem[]>([]);
  const [probe, setProbe] = useState<AssessProbe | null>(null);
  const [answer, setAnswer] = useState("");
  const [lastResult, setLastResult] = useState<LastResult | null>(null);
  const [summary, setSummary] = useState<AssessSummary | null>(null);
  const [goalTitle, setGoalTitle] = useState<string>("");

  // Load goals for the picker.
  useEffect(() => {
    let cancelled = false;
    getGoals().then((result) => {
      if (cancelled) return;
      if (result.ok) {
        setGoals(result.data.goals);
        const fromUrl = readGoalFromUrl();
        const match = result.data.goals.find(
          (g) => g.id === fromUrl || g.id.startsWith(fromUrl)
        );
        setSelectedGoal(match ? match.id : result.data.goals[0]?.id ?? "");
      } else {
        setGoalsError(result.error.message);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const onStart = useCallback(async () => {
    if (!selectedGoal) return;
    setBusy(true);
    setError(null);
    setLastResult(null);
    const result = await startAssessment(selectedGoal, selfReport);
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    const data = result.data;
    setGoalTitle(data.goal_title);
    setHistory(data.history);
    if (data.done) {
      setSummary(data.summary ?? null);
      setPhase("summary");
      return;
    }
    setProbe(data.probe);
    setAnswer("");
    setPhase("probe");
  }, [selectedGoal, selfReport]);

  const applyAnswerResult = useCallback(
    (data: AssessAnswerData, answeredProbe: AssessProbe, answeredScore: AssessHistoryItem | undefined) => {
      setHistory(data.history);
      if (answeredScore) {
        setLastResult({
          score: answeredScore.score,
          passed: answeredScore.passed,
          title: answeredProbe.title,
        });
      }
      if (data.done) {
        setSummary(data.summary ?? null);
        setProbe(null);
        setPhase("summary");
        return;
      }
      setProbe(data.probe ?? null);
      setAnswer("");
    },
    []
  );

  const onSubmitAnswer = useCallback(async () => {
    if (!probe) return;
    setBusy(true);
    setError(null);
    const answeredProbe = probe;
    const result = await answerProbe(selectedGoal, {
      selfReport,
      history,
      probe: answeredProbe,
      userAnswer: answer,
    });
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return;
    }
    // The newest history entry is this probe's score.
    const newest = result.data.history[result.data.history.length - 1];
    applyAnswerResult(result.data, answeredProbe, newest);
  }, [probe, selectedGoal, selfReport, history, answer, applyAnswerResult]);

  const onRestart = useCallback(() => {
    setPhase("setup");
    setHistory([]);
    setProbe(null);
    setAnswer("");
    setLastResult(null);
    setSummary(null);
    setError(null);
  }, []);

  if (goalsError) {
    return (
      <section className="assess-view">
        <Card className="assess-card">
          <Card.Header>
            <Card.Title>Goal Assess</Card.Title>
          </Card.Header>
          <Card.Content>
            <p className="assess-error">Failed to load goals: {goalsError}</p>
          </Card.Content>
        </Card>
      </section>
    );
  }

  return (
    <section className="assess-view" aria-label="Goal assess flow">
      <Card className="assess-card">
        <Card.Header>
          <Card.Title>Goal Assess</Card.Title>
          <Card.Description>
            初始评估会探测几道题，判断你已掌握的范围，让后续学习跳过已知内容。不会自动开始学习。
          </Card.Description>
        </Card.Header>

        <Card.Content>
          <ApiErrorNotice error={error} className="assess-error" />

          {phase === "setup" ? (
            <div className="assess-setup">
              <label className="assess-field">
                <span>目标</span>
                <select
                  className="assess-select"
                  value={selectedGoal}
                  onChange={(e) => setSelectedGoal(e.target.value)}
                  disabled={busy || goals.length === 0}
                >
                  {goals.length === 0 ? <option value="">没有可评估的目标</option> : null}
                  {goals.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.title}
                    </option>
                  ))}
                </select>
              </label>

              <div className="assess-field">
                <span>你对这个领域的整体了解程度？</span>
                <div className="assess-segments" role="group" aria-label="Self report level">
                  {SELF_REPORT_LEVELS.map((level) => (
                    <Button
                      key={level.value}
                      size="sm"
                      variant={selfReport === level.value ? "primary" : "outline"}
                      onPress={() => setSelfReport(level.value)}
                      isDisabled={busy}
                    >
                      {level.label}
                    </Button>
                  ))}
                </div>
              </div>

              <div className="assess-actions">
                <Button
                  variant="primary"
                  onPress={onStart}
                  isDisabled={busy || !selectedGoal}
                >
                  {busy ? <Spinner size="sm" /> : "开始评估"}
                </Button>
              </div>
            </div>
          ) : null}

          {phase === "probe" && probe ? (
            <div className="assess-probe">
              {lastResult ? (
                <div className="assess-last-result">
                  <Chip size="sm" color={scoreChipColor(lastResult.score)} variant="soft">
                    {lastResult.title} · {Math.round(lastResult.score * 100)}%
                    {lastResult.passed ? " · 已掌握" : ""}
                  </Chip>
                </div>
              ) : null}

              <div className="assess-probe-head">
                <span className="assess-probe-num">第 {probe.q_num} 题</span>
                <span className="assess-probe-node">{probe.title}</span>
              </div>
              <p className="assess-question">{probe.question}</p>

              <TextArea
                aria-label="Your answer"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                placeholder="简短作答即可，系统会自动评分。"
                rows={5}
                disabled={busy}
              />

              <div className="assess-actions">
                <Button variant="primary" onPress={onSubmitAnswer} isDisabled={busy}>
                  {busy ? <Spinner size="sm" /> : "提交答案"}
                </Button>
              </div>
            </div>
          ) : null}

          {phase === "summary" && summary ? (
            <div className="assess-summary">
              <div className="assess-summary-stats">
                <Chip color="success" variant="soft">已掌握 {summary.mastered}</Chip>
                <Chip color="warning" variant="soft">待学习 {summary.unknown}</Chip>
                <Chip color="default" variant="soft">探测 {summary.probes_done} 题</Chip>
                <Chip color="default" variant="soft">共 {summary.total_nodes} 个知识点</Chip>
              </div>

              {summary.recommended_start_node ? (
                <p className="assess-next-hint">
                  建议从「{summary.recommended_start_node.title}」开始学习。
                  <small>
                    {" "}
                    可在 Learn 视图手动开始 — 评估不会自动进入学习。
                  </small>
                </p>
              ) : (
                <p className="assess-next-hint">该目标的知识点都已掌握，无需进一步学习。</p>
              )}

              <div className="assess-actions">
                <Button variant="outline" onPress={onRestart}>
                  重新评估
                </Button>
              </div>
            </div>
          ) : null}
        </Card.Content>
      </Card>
    </section>
  );
}
