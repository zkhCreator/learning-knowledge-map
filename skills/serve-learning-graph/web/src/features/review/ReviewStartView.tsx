/*
File: skills/serve-learning-graph/web/src/features/review/ReviewStartView.tsx

Purpose:
    Review Start flow for the GUI workflow host (doc/14 独立路径 → Review Start).
    Shows the review context (historical errors + mnemonic priming) for a chosen
    review, then runs a re-exam that REUSES the Exam primitives, and finalizes
    through the review-specific completion endpoint.

Responsibilities:
    - Load the review context via POST /api/review/start (review id from ?review=).
    - Prime the learner with past error-notebook entries and any mnemonic anchors
      before the re-exam; the user explicitly confirms to start.
    - Run the re-exam by reusing the Exam API (startExam + answerExamQuestion),
      accumulating per-question error metadata.
    - Finalize via POST /api/review/<review-id>/finish, which completes the old
      review and reschedules — independent of the normal Exam finish.

What this file does NOT do:
    - Read SQLite or call LLMs (server.js proxies to Python).
    - Auto-start the re-exam — the user confirms after seeing the context.
    - Reimplement scoring/scheduling — it reuses the Exam + review services.

Inputs: ?review= / ?node= URL params; startReview / startExam /
        answerExamQuestion / finishReview
Outputs: A primed re-exam ending in a review result summary
*/

import { useCallback, useEffect, useState } from "react";
import { Button, Card, Chip, Spinner, TextArea } from "@heroui/react";

import { answerExamQuestion, finishReview, startExam, startReview } from "../../api";
import ApiErrorNotice from "../../components/ApiErrorNotice";
import type {
  ApiError,
  ExamAnswerData,
  ExamQuestion,
  ExamQuestionMeta,
  ReviewFinishData,
  ReviewStartData,
} from "../../types";

type Phase = "loading" | "context" | "examining" | "summary";

function scoreTone(score: number): "success" | "warning" | "danger" {
  if (score >= 0.8) return "success";
  if (score >= 0.5) return "warning";
  return "danger";
}

export default function ReviewStartView({ reviewId }: { reviewId: string }) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const [context, setContext] = useState<ReviewStartData | null>(null);

  const [examId, setExamId] = useState<string>("");
  const [questions, setQuestions] = useState<ExamQuestion[]>([]);
  const [index, setIndex] = useState(0);
  const [draft, setDraft] = useState("");
  const [result, setResult] = useState<ExamAnswerData | null>(null);
  const [meta, setMeta] = useState<Record<string, ExamQuestionMeta>>({});

  const [summary, setSummary] = useState<ReviewFinishData | null>(null);

  // Load the review context on mount.
  useEffect(() => {
    let cancelled = false;
    startReview({ reviewId }).then((res) => {
      if (cancelled) return;
      if (!res.ok) {
        setError(res.error);
        setPhase("context");
        return;
      }
      setContext(res.data);
      setPhase("context");
    });
    return () => {
      cancelled = true;
    };
  }, [reviewId]);

  const onStartExam = useCallback(async () => {
    if (!context) return;
    setBusy(true);
    setError(null);
    const res = await startExam(context.node.id);
    setBusy(false);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    setExamId(res.data.exam_id);
    setQuestions(res.data.questions);
    setIndex(0);
    setDraft("");
    setResult(null);
    setMeta({});
    setPhase("examining");
  }, [context]);

  const current = questions[index] ?? null;

  const onSubmitAnswer = useCallback(async () => {
    if (!current) return;
    setBusy(true);
    setError(null);
    const res = await answerExamQuestion(examId, current.id, draft.trim());
    setBusy(false);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    setResult(res.data);
    setMeta((prev) => ({
      ...prev,
      [current.id]: {
        error_type: res.data.error_type,
        explanation: res.data.explanation,
        related_concepts: res.data.related_concepts,
      },
    }));
  }, [current, examId, draft]);

  const onNext = useCallback(() => {
    setIndex((i) => i + 1);
    setDraft("");
    setResult(null);
  }, []);

  const onFinish = useCallback(async () => {
    setBusy(true);
    setError(null);
    const res = await finishReview(reviewId, examId, meta);
    setBusy(false);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    setSummary(res.data);
    setPhase("summary");
  }, [reviewId, examId, meta]);

  const isLast = index >= questions.length - 1;

  return (
    <section className="review-view" aria-label="Review start flow">
      <Card className="review-card">
        <Card.Header>
          <Card.Title>Review Start</Card.Title>
          <Card.Description>
            先回顾历史错题与助记线索，再进行复习考试。复习复用考试流程，但完成后会更新本次复习并安排下一次。
          </Card.Description>
        </Card.Header>

        <Card.Content>
          <p className="review-back">
            <a href="?view=review">← 返回复习队列</a>
          </p>

          <ApiErrorNotice error={error} className="review-error" />

          {phase === "loading" ? <Spinner size="sm" /> : null}

          {phase === "context" && context ? (
            <div className="review-context">
              <div className="review-node-head">
                <span className="review-node-title">{context.node.title}</span>
                <span className="review-node-meta">
                  第 {context.review_round} 轮 · {context.node.strictness_level}
                </span>
              </div>

              <div className="review-section">
                <h3>历史错题（{context.errors.length}）</h3>
                {context.errors.length === 0 ? (
                  <p className="review-muted">此节点没有历史错题记录。</p>
                ) : (
                  <ul className="review-errors">
                    {context.errors.slice(0, 10).map((e, i) => (
                      <li key={i} className="review-error-item">
                        <span className="review-error-q">{e.question}</span>
                        <span className="review-error-tags">
                          {e.error_type ? <Chip color="warning" variant="soft">{e.error_type}</Chip> : null}
                        </span>
                        {e.explanation ? <span className="review-error-exp">{e.explanation}</span> : null}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {context.mnemonic ? (
                <div className="review-section">
                  <h3>助记回忆</h3>
                  <p className="review-mnemonic-prompt">{context.mnemonic.prompt}</p>
                  <pre className="review-mnemonic-display">{context.mnemonic.display}</pre>
                </div>
              ) : null}

              <div className="review-actions">
                <Button variant="primary" onPress={onStartExam} isDisabled={busy}>
                  {busy ? <Spinner size="sm" /> : "开始复习考试"}
                </Button>
              </div>
            </div>
          ) : null}

          {phase === "examining" && current ? (
            <div className="review-run">
              <div className="review-node-head">
                <span className="review-node-title">{context?.node.title}</span>
                <span className="review-node-meta">
                  第 {index + 1}/{questions.length} 题
                </span>
              </div>

              <div className="review-question">
                <div className="review-question-tags">
                  <Chip color="default" variant="soft">{current.question_type}</Chip>
                  {current.is_expansion ? <Chip color="accent" variant="soft">扩展</Chip> : null}
                </div>
                <p className="review-question-text">{current.question}</p>
                {current.options ? (
                  <ul className="review-options">
                    {current.options.map((opt, i) => (
                      <li key={i}>{opt}</li>
                    ))}
                  </ul>
                ) : null}
              </div>

              {result ? (
                <div className="review-feedback" role="status">
                  <Chip color={scoreTone(result.score)} variant="soft">
                    得分 {Math.round(result.score * 100)}%
                  </Chip>
                  {result.explanation ? <p className="review-error-exp">{result.explanation}</p> : null}
                </div>
              ) : (
                <TextArea
                  aria-label="Your answer"
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder="输入你的答案…"
                  rows={4}
                  disabled={busy}
                />
              )}

              <div className="review-actions">
                {result ? (
                  isLast ? (
                    <Button variant="primary" onPress={onFinish} isDisabled={busy}>
                      {busy ? <Spinner size="sm" /> : "完成复习"}
                    </Button>
                  ) : (
                    <Button variant="primary" onPress={onNext} isDisabled={busy}>
                      下一题
                    </Button>
                  )
                ) : (
                  <Button variant="primary" onPress={onSubmitAnswer} isDisabled={busy}>
                    {busy ? <Spinner size="sm" /> : "提交答案"}
                  </Button>
                )}
              </div>
            </div>
          ) : null}

          {phase === "summary" && summary ? (
            <div className="review-summary" role="status">
              <div className="review-summary-head">
                <Chip color={summary.passed ? "success" : "danger"} variant="soft">
                  {summary.passed ? "通过" : "未通过"}
                </Chip>
                <span className="review-summary-score">
                  总分 {Math.round(summary.total_score * 100)}%
                </span>
              </div>
              {summary.next_review_days != null ? (
                <p className="review-muted">下一次复习将在约 {summary.next_review_days} 天后。</p>
              ) : null}
              <div className="review-actions">
                <Button variant="outline" onPress={() => { window.location.href = "?view=review"; }} isDisabled={busy}>
                  返回复习队列
                </Button>
              </div>
            </div>
          ) : null}
        </Card.Content>
      </Card>
    </section>
  );
}
