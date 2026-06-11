/*
File: skills/serve-learning-graph/web/src/api.ts

Purpose:
    Typed fetch client for the local GUI workflow host's /api/* endpoints.

Responsibilities:
    - Wrap fetch and normalize every response into the {ok,data}|{ok,error}
      envelope returned by scripts/workflow_api.py
    - Expose the foundation read actions (health, goals, graph) as typed calls
    - Give feature modules (assess/learn/exam/review) one extension point via
      the shared apiGet/apiPost helpers, instead of re-implementing fetch

What this file does NOT do:
    - Access SQLite or call Python directly (server.js proxies to Python)
    - Implement assess/learn/exam/review business calls
    - Transform graph data (see flow.ts) or hold UI state
    - Auto-jump between flows; callers pass explicit ids

Inputs: HTTP requests to the local Node server
Outputs: Typed ApiResult<T> envelopes
*/

import type {
  ApiResult,
  AssessAnswerData,
  AssessHistoryItem,
  AssessProbe,
  AssessStartData,
  ExamAnswerData,
  ExamFinishData,
  ExamQuestionMeta,
  ExamRecordData,
  ExamStartData,
  ExamViewData,
  GoalsData,
  GraphData,
  HealthData,
  LearnMessageData,
  LearnPrepareData,
  ReviewFinishData,
  ReviewQueueData,
  ReviewStartData,
} from "./types";

/**
 * Run a request and coerce any outcome into an ApiResult envelope.
 * Network/parse failures surface as {ok:false} so callers never throw.
 */
async function request<T>(
  path: string,
  init?: RequestInit
): Promise<ApiResult<T>> {
  try {
    const response = await fetch(path, init);
    const body = (await response.json()) as unknown;

    if (
      body &&
      typeof body === "object" &&
      "ok" in body &&
      typeof (body as { ok: unknown }).ok === "boolean"
    ) {
      return body as ApiResult<T>;
    }

    return {
      ok: false,
      error: {
        code: "invalid_envelope",
        message: `Unexpected response shape from ${path}`,
      },
    };
  } catch (err: unknown) {
    return {
      ok: false,
      error: {
        code: "network_error",
        message: err instanceof Error ? err.message : String(err),
      },
    };
  }
}

function withQuery(path: string, params?: Record<string, string | undefined>): string {
  if (!params) return path;
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value != null && value !== "") search.set(key, value);
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

/** GET an /api/* endpoint with optional query params. */
export function apiGet<T>(
  path: string,
  params?: Record<string, string | undefined>
): Promise<ApiResult<T>> {
  return request<T>(withQuery(path, params));
}

/** POST a JSON body to an /api/* endpoint. Reserved for feature write flows. */
export function apiPost<T>(path: string, body: unknown): Promise<ApiResult<T>> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
}

/* ----------------------------------------------------------------------- */
/* Foundation read actions                                                 */
/* ----------------------------------------------------------------------- */

export function getHealth(user = "default"): Promise<ApiResult<HealthData>> {
  return apiGet<HealthData>("/api/health", { user });
}

export function getGoals(user = "default"): Promise<ApiResult<GoalsData>> {
  return apiGet<GoalsData>("/api/goals", { user });
}

export function getGraph(
  goal?: string,
  user = "default"
): Promise<ApiResult<GraphData>> {
  return apiGet<GraphData>("/api/graph", { goal, user });
}

/* ----------------------------------------------------------------------- */
/* Assess flow write actions (Module 3)                                    */
/* Each call carries the in-flight history/probe; the server is stateless. */
/* ----------------------------------------------------------------------- */

/** Begin an assessment for a goal: returns the first probe (or a done summary). */
export function startAssessment(
  goal: string,
  selfReport: number,
  user = "default"
): Promise<ApiResult<AssessStartData>> {
  return apiPost<AssessStartData>(
    `/api/goals/${encodeURIComponent(goal)}/assessment/start`,
    { user, self_report: selfReport }
  );
}

/** Submit an answer to the carried probe and get the next probe or summary. */
export function answerProbe(
  goal: string,
  args: {
    selfReport: number;
    history: AssessHistoryItem[];
    probe: AssessProbe;
    userAnswer: string;
    user?: string;
  }
): Promise<ApiResult<AssessAnswerData>> {
  return apiPost<AssessAnswerData>(
    `/api/goals/${encodeURIComponent(goal)}/assessment/answer`,
    {
      user: args.user ?? "default",
      self_report: args.selfReport,
      history: args.history,
      probe: args.probe,
      user_answer: args.userAnswer,
    }
  );
}

/* ----------------------------------------------------------------------- */
/* Learn flow write actions (Module 4)                                     */
/* The session is persisted server-side; the client carries only its id.   */
/* ----------------------------------------------------------------------- */

/** Generate/reuse the node's outline and create/resume its learning session. */
export function prepareNode(
  node: string,
  args?: { userDomains?: string[]; user?: string }
): Promise<ApiResult<LearnPrepareData>> {
  return apiPost<LearnPrepareData>(
    `/api/learn/${encodeURIComponent(node)}/prepare`,
    {
      user: args?.user ?? "default",
      ...(args?.userDomains ? { user_domains: args.userDomains } : {}),
    }
  );
}

/** Send one Socratic turn and get the reply with updated progress/coverage. */
export function sendLearnMessage(
  sessionId: string,
  message: string,
  user = "default"
): Promise<ApiResult<LearnMessageData>> {
  return apiPost<LearnMessageData>(
    `/api/learn/sessions/${encodeURIComponent(sessionId)}/messages`,
    { user, message }
  );
}

/* ----------------------------------------------------------------------- */
/* Exam flow write actions (Module 5)                                      */
/* The attempt/questions/scores persist server-side; the client carries    */
/* the ids and the qualitative error metadata it passes to finish.         */
/* ----------------------------------------------------------------------- */

/**
 * Ask to create an exam attempt for a node. In the data-plane host this returns
 * an `agent_required` error: question generation runs in the tool (/exam-start).
 */
export function startExam(
  node: string,
  user = "default"
): Promise<ApiResult<ExamStartData>> {
  return apiPost<ExamStartData>("/api/exams/start", { user, node });
}

/** Load a tool-generated exam for display + answering (no expected_answer). */
export function getExam(
  examId: string,
  user = "default"
): Promise<ApiResult<ExamViewData>> {
  return apiPost<ExamViewData>(
    `/api/exams/${encodeURIComponent(examId)}`,
    { user }
  );
}

/** Persist one raw answer (no scoring); scoring is deferred to the tool. */
export function recordExamAnswer(
  examId: string,
  questionId: string,
  userAnswer: string,
  user = "default"
): Promise<ApiResult<ExamRecordData>> {
  return apiPost<ExamRecordData>(
    `/api/exams/${encodeURIComponent(examId)}/questions/${encodeURIComponent(questionId)}/record`,
    { user, user_answer: userAnswer }
  );
}

/** Score one answer against the DB-authoritative question. */
export function answerExamQuestion(
  examId: string,
  questionId: string,
  userAnswer: string,
  user = "default"
): Promise<ApiResult<ExamAnswerData>> {
  return apiPost<ExamAnswerData>(
    `/api/exams/${encodeURIComponent(examId)}/questions/${encodeURIComponent(questionId)}/answer`,
    { user, user_answer: userAnswer }
  );
}

/** Finalize the exam: update state, write the error notebook, schedule review. */
export function finishExam(
  examId: string,
  questionMeta: Record<string, ExamQuestionMeta>,
  user = "default"
): Promise<ApiResult<ExamFinishData>> {
  return apiPost<ExamFinishData>(
    `/api/exams/${encodeURIComponent(examId)}/finish`,
    { user, question_meta: questionMeta }
  );
}

/* ----------------------------------------------------------------------- */
/* Review flow read actions (Module 6)                                     */
/* ----------------------------------------------------------------------- */

/** Fetch the grouped Ebbinghaus review queue (critical/overdue/today/future). */
export function getReviewQueue(
  includeFuture = true,
  user = "default"
): Promise<ApiResult<ReviewQueueData>> {
  return apiGet<ReviewQueueData>("/api/review/queue", {
    user,
    include_future: includeFuture ? "true" : "false",
  });
}

/* ----------------------------------------------------------------------- */
/* Review flow write actions (Module 7)                                    */
/* Entry/context/completion are review-specific; the re-exam in between     */
/* reuses the Exam primitives (startExam / answerExamQuestion).             */
/* ----------------------------------------------------------------------- */

/** Assemble the review context (errors + mnemonic) for a review or node. */
export function startReview(
  args: { reviewId?: string; node?: string; user?: string }
): Promise<ApiResult<ReviewStartData>> {
  return apiPost<ReviewStartData>("/api/review/start", {
    user: args.user ?? "default",
    ...(args.reviewId ? { review_id: args.reviewId } : {}),
    ...(args.node ? { node: args.node } : {}),
  });
}

/** Finalize a review: delegate the exam result, complete + reschedule. */
export function finishReview(
  reviewId: string,
  examId: string,
  questionMeta: Record<string, ExamQuestionMeta>,
  user = "default"
): Promise<ApiResult<ReviewFinishData>> {
  return apiPost<ReviewFinishData>(
    `/api/review/${encodeURIComponent(reviewId)}/finish`,
    { user, exam_id: examId, question_meta: questionMeta }
  );
}
