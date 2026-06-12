/*
File: skills/serve-learning-graph/web/src/types.ts

Purpose:
    Shared TypeScript contracts for the GUI workflow host: the graph JSON
    served at /graph.json and the {ok,data}|{ok,error} envelope returned by
    the /api/* endpoints.

Responsibilities:
    - Describe the SQLite-derived graph payload used by the React Flow app
    - Keep node, edge, and user-state fields explicit for reviewability
    - Provide small helper types for dependency and analogy display
    - Define the API response envelope and foundation action payloads
      (health, goals, graph) that feature modules extend

What this file does NOT do:
    - Fetch data (see api.ts)
    - Transform data into React Flow nodes (see flow.ts)
    - Define backend SQLite schema
    - Implement assess/learn/exam/review feature payloads

Inputs: JSON emitted by scripts/export_graph.py and scripts/workflow_api.py
Outputs: TypeScript types consumed by the React app and the API client
*/

export type EdgeType = "prerequisite" | "cross_domain_analogy" | string;

/* ----------------------------------------------------------------------- */
/* API envelope (matches workflow_api.py _ok / _error)                     */
/* ----------------------------------------------------------------------- */

export interface ApiError {
  code: string;
  message: string;
  /**
   * Present when code === "agent_required": the generation step must run in
   * Codex / Claude Code. `skill` is the skill to run, `command` is a copyable
   * slash command, and `deep_link` is the relative ?view=… to return to.
   */
  skill?: string;
  command?: string;
  deep_link?: string;
}

export type ApiResult<T> =
  | { ok: true; data: T }
  | { ok: false; error: ApiError };

/** Mode tabs / independent flow entries (doc/14 独立路径). */
export type WorkflowView = "graph" | "assess" | "learn" | "exam" | "review";

export interface HealthData {
  db_path: string;
  user_id: string;
  features: string[];
}

export interface GoalsData {
  db_path: string;
  user_id: string;
  goals: LearningGoal[];
}

export interface GraphData {
  graph: GraphPayload;
}

export interface LearningGoal {
  id: string;
  user_id: string;
  title: string;
  root_node: string | null;
  status: string;
  created_at: string;
}

export interface GraphSummary {
  node_count: number;
  atomic_node_count: number;
  edge_count: number;
  total_est_minutes: number;
}

export interface KnowledgeState {
  user_id: string;
  node_id: string;
  status: string;
  raw_score: number | null;
  stability: number | null;
  last_reviewed: string | null;
  next_review: string | null;
  review_count: number | null;
  updated_at: string;
}

export interface RelatedNode {
  id: string;
  node_id: string;
  title: string;
  edge_type: EdgeType;
  direction?: "incoming" | "outgoing";
  analogy_desc?: string | null;
}

export interface GraphNode {
  id: string;
  title: string;
  description: string;
  domain: string;
  concept_fingerprint: string[];
  difficulty: number | null;
  est_minutes: number | null;
  qa_set: unknown[];
  depth_level: number;
  parent_node: string | null;
  strictness_level: string;
  mastery_threshold: number | null;
  risk_note: string;
  is_atomic: boolean;
  created_at: string | null;
  state: KnowledgeState | null;
  prerequisites: RelatedNode[];
  dependents: RelatedNode[];
  analogies: RelatedNode[];
  children: GraphNode[];
  synthetic?: boolean;
  cycle_warning?: boolean;
}

export interface GraphEdge {
  id: string;
  from_node: string;
  to_node: string;
  edge_type: EdgeType;
  weight: number | null;
  analogy_desc: string | null;
  created_at: string;
  from_title: string;
  to_title: string;
}

export interface GraphPayload {
  db_path: string;
  user_id: string;
  goal: LearningGoal;
  summary: GraphSummary;
  tree: GraphNode;
  trees: GraphNode[];
  unattached_trees: GraphNode[];
  nodes: GraphNode[];
  edges: GraphEdge[];
}

/* ----------------------------------------------------------------------- */
/* Assess flow (Module 3) — stateless probe/answer contract.               */
/* The client carries `history` + `probe` between calls; the server keeps  */
/* no in-flight memory and only writes durable user_knowledge_state.       */
/* ----------------------------------------------------------------------- */

/** One entry recorded per answered probe (drives next-probe selection). */
export interface AssessHistoryItem {
  node_id: string;
  depth_level: number;
  score: number;
  passed: boolean;
}

/** Carried probe context; includes expected_answer so the next call can score. */
export interface AssessProbe {
  node_id: string;
  title: string;
  depth_level: number;
  strictness_level: string;
  mastery_threshold: number;
  question: string;
  expected_answer: string;
  q_num: number;
}

export interface AssessRecommendedNode {
  node_id: string;
  title: string;
  depth_level: number;
}

export interface AssessSummary {
  mastered: number;
  unknown: number;
  probes_done: number;
  total_nodes: number;
  recommended_start_node: AssessRecommendedNode | null;
}

/** Response of POST /assessment/start. */
export interface AssessStartData {
  goal_id: string;
  goal_title: string;
  self_report: number;
  total_nodes: number;
  history: AssessHistoryItem[];
  probe: AssessProbe | null;
  done: boolean;
  summary?: AssessSummary;
}

/** Response of POST /assessment/answer. */
export interface AssessAnswerData {
  done: boolean;
  history: AssessHistoryItem[];
  probe?: AssessProbe;
  summary?: AssessSummary;
}

/* ----------------------------------------------------------------------- */
/* Learn flow (Module 4) — durable session/outline/chat contract.          */
/* Unlike assess, learning state is persisted server-side (session +       */
/* chat history); the client carries only the session id between calls.    */
/* exam_ready is informational: the UI surfaces an Exam entry, never jumps. */
/* ----------------------------------------------------------------------- */

/** One section of a node's learning outline. */
export interface LearnSection {
  index: number;
  title: string;
  content?: string;
  needs_search?: boolean;
  sources?: unknown[];
  analogy?: string | null;
  analogy_source_node?: string | null;
  covered?: boolean;
}

/** Public view of the node being learned. */
export interface LearnNode {
  id: string;
  title: string;
  description: string;
  domain: string;
  depth_level: number;
  mastery_threshold: number;
  strictness_level: string;
}

export interface LearnOutline {
  id: string;
  sections: LearnSection[];
}

export interface LearnSession {
  id: string;
  covered_sections: number[];
  progress: number;
  status: string;
}

/** One persisted chat message (Socratic dialogue turn). */
export interface LearnMessage {
  role: "user" | "assistant" | string;
  content: string;
}

/** Response of POST /api/learn/<node>/prepare. */
export interface LearnPrepareData {
  node: LearnNode;
  outline: LearnOutline;
  session: LearnSession;
  history: LearnMessage[];
  progress: number;
  exam_ready: boolean;
}

/** Response of POST /api/learn/sessions/<session-id>/messages. */
export interface LearnMessageData {
  response: string;
  progress: number;
  covered: number[];
  exam_ready: boolean;
}

/* ----------------------------------------------------------------------- */
/* Exam flow (Module 5) — durable attempt/question contract.               */
/* The exam attempt, questions, and per-question scores are persisted      */
/* server-side; the client carries the exam/question ids plus the          */
/* qualitative error metadata it accumulates, which it passes to finish.   */
/* expected_answer is never sent to the browser.                           */
/* ----------------------------------------------------------------------- */

/** Public view of the node under exam. */
export interface ExamNode {
  id: string;
  title: string;
  strictness_level: string;
  mastery_threshold: number;
}

/** Client-safe question view — never includes the expected answer. */
export interface ExamQuestion {
  id: string;
  index: number;
  question: string;
  question_type: string;
  options: string[] | null;
  source_section: number | null;
  is_expansion: boolean;
}

/** Pre-exam recall cue (docs/23 4b); shape matches ReviewMnemonic. */
export interface ExamMnemonic {
  strategy: string | null;
  prompt: string;
  display: string;
}

/** Response of POST /api/exams/start. */
export interface ExamStartData {
  exam_id: string;
  node: ExamNode;
  questions: ExamQuestion[];
  total: number;
  /** Pre-exam recall cue; null without a cognitive profile / anchors. */
  mnemonic?: ExamMnemonic | null;
}

/** A question in the data-plane view, echoing any already-recorded answer. */
export interface ExamViewQuestion extends ExamQuestion {
  user_answer: string | null;
  score: number | null;
}

/** Response of GET /api/exams/<exam-id> — a tool-generated exam for answering. */
export interface ExamViewData {
  exam_id: string;
  node: ExamNode;
  questions: ExamViewQuestion[];
  total: number;
  finished: boolean;
  /** Pre-exam recall cue; null without a cognitive profile / anchors. */
  mnemonic?: ExamMnemonic | null;
}

/** Response of POST /api/exams/<exam-id>/questions/<question-id>/record. */
export interface ExamRecordData {
  exam_id: string;
  question_id: string;
  user_answer: string;
  recorded: boolean;
}

/** Response of POST /api/exams/<exam-id>/questions/<question-id>/answer. */
export interface ExamAnswerData {
  question_id: string;
  score: number;
  error_type: string | null;
  explanation: string;
  related_concepts: string[];
}

/** Per-question qualitative error metadata carried into finish. */
export interface ExamQuestionMeta {
  error_type?: string | null;
  explanation?: string;
  related_concepts?: string[];
}

/** Response of POST /api/exams/<exam-id>/finish. */
export interface ExamFinishData {
  total_score: number;
  passed: boolean;
  threshold: number;
  interval_days: number | null;
  next_review: string | null;
  weak_sections: string[];
}

/* ----------------------------------------------------------------------- */
/* Review flow (Module 6/7) — durable review_schedule contract.            */
/* The queue and review records are persisted server-side; the client      */
/* carries only review/node/exam ids. Review reuses the Exam primitives for */
/* the re-exam but keeps its own entry, context, and completion logic.      */
/* ----------------------------------------------------------------------- */

/** One pending review, joined with its node, as shown in the queue. */
export interface ReviewItem {
  review_id: string;
  node_id: string;
  node_title: string;
  strictness_level: string;
  mastery_threshold: number | null;
  scheduled_at: string | null;
  review_round: number;
  status: string;
}

export type ReviewGroupKey = "critical" | "overdue" | "today" | "future";

/** Response of GET /api/review/queue. Each review is in exactly one group. */
export interface ReviewQueueData {
  groups: Record<ReviewGroupKey, ReviewItem[]>;
  counts: Record<ReviewGroupKey, number>;
  total: number;
}

/** A historical error-notebook entry shown to prime the learner before re-exam. */
export interface ReviewErrorEntry {
  question: string;
  error_type: string | null;
  user_answer: string;
  correct_answer: string;
  explanation: string;
  source_section_title: string;
}

/** Mnemonic retrieval context for review priming (null when unavailable). */
export interface ReviewMnemonic {
  strategy: string | null;
  prompt: string;
  display: string;
}

/** Public view of the node under review. */
export interface ReviewNode {
  id: string;
  title: string;
  description: string;
  strictness_level: string;
  mastery_threshold: number;
}

/** Response of POST /api/review/start — context, not yet a re-exam. */
export interface ReviewStartData {
  review_id: string | null;
  node: ReviewNode;
  review_round: number;
  scheduled_at: string | null;
  errors: ReviewErrorEntry[];
  mnemonic: ReviewMnemonic | null;
}

/** Response of POST /api/review/<review-id>/finish. */
export interface ReviewFinishData {
  review_id: string | null;
  node_id: string;
  passed: boolean;
  total_score: number;
  threshold: number | null;
  next_review_days: number | null;
  weak_sections: string[];
}
