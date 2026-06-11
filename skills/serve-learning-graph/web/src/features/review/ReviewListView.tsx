/*
File: skills/serve-learning-graph/web/src/features/review/ReviewListView.tsx

Purpose:
    Review List flow for the GUI workflow host (doc/14 独立路径 → Review List).
    Shows the Ebbinghaus review queue grouped by urgency so the user can pick a
    review to run.

Responsibilities:
    - Fetch GET /api/review/queue and render the critical / overdue / today /
      future groups with counts.
    - Let the user toggle whether not-yet-due (future) reviews are shown.
    - Link each queue item to the Review Start entry (?view=review&review=<id>)
      without auto-starting — the user explicitly picks a review.

What this file does NOT do:
    - Read SQLite or call LLMs (server.js proxies to Python).
    - Auto-start a review or jump into Exam — items are manual links.
    - Own URL/tab state beyond reading its own query params.

Inputs: /api/review/queue
Outputs: A grouped, read-only review queue with per-item start links
*/

import { useCallback, useEffect, useState } from "react";
import { Button, Card, Chip, Spinner } from "@heroui/react";

import { getReviewQueue } from "../../api";
import ApiErrorNotice from "../../components/ApiErrorNotice";
import type { ApiError, ReviewGroupKey, ReviewItem, ReviewQueueData } from "../../types";

const GROUP_META: Record<ReviewGroupKey, { label: string; tone: "danger" | "warning" | "success" | "default" }> = {
  critical: { label: "关键", tone: "danger" },
  overdue: { label: "逾期", tone: "warning" },
  today: { label: "今天", tone: "success" },
  future: { label: "未来", tone: "default" },
};

const ORDER: ReviewGroupKey[] = ["critical", "overdue", "today", "future"];

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString();
}

function ReviewRow({ item }: { item: ReviewItem }) {
  const href = `?view=review&review=${encodeURIComponent(item.review_id)}&node=${encodeURIComponent(item.node_id)}`;
  return (
    <li className="review-item">
      <div className="review-item-main">
        <span className="review-item-title">{item.node_title}</span>
        <span className="review-item-meta">
          第 {item.review_round} 轮 · 计划 {formatDate(item.scheduled_at)}
          {item.strictness_level === "critical" ? " · critical" : ""}
        </span>
      </div>
      <a className="review-item-start" href={href}>
        开始复习
      </a>
    </li>
  );
}

export default function ReviewListView() {
  const [queue, setQueue] = useState<ReviewQueueData | null>(null);
  const [includeFuture, setIncludeFuture] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  const load = useCallback(async (future: boolean) => {
    setBusy(true);
    setError(null);
    const res = await getReviewQueue(future);
    setBusy(false);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    setQueue(res.data);
  }, []);

  useEffect(() => {
    load(includeFuture);
  }, [load, includeFuture]);

  return (
    <section className="review-view" aria-label="Review list flow">
      <Card className="review-card">
        <Card.Header>
          <Card.Title>Review List</Card.Title>
          <Card.Description>
            查看到期的艾宾浩斯复习队列，按关键 / 逾期 / 今天 / 未来分组。选择一项手动开始复习，不会自动开始。
          </Card.Description>
        </Card.Header>

        <Card.Content>
          <ApiErrorNotice error={error} className="review-error" />

          <div className="review-toolbar">
            <label className="review-toggle">
              <input
                type="checkbox"
                checked={includeFuture}
                onChange={(e) => setIncludeFuture(e.target.checked)}
                disabled={busy}
              />
              <span>显示未来复习</span>
            </label>
            <Button variant="outline" onPress={() => load(includeFuture)} isDisabled={busy}>
              {busy ? <Spinner size="sm" /> : "刷新"}
            </Button>
          </div>

          {queue && queue.total === 0 ? (
            <p className="review-empty">当前没有待复习的节点。</p>
          ) : null}

          {queue
            ? ORDER.map((key) => {
                const items = queue.groups[key];
                if (!items.length) return null;
                const meta = GROUP_META[key];
                return (
                  <div key={key} className="review-group">
                    <div className="review-group-head">
                      <Chip color={meta.tone} variant="soft">{meta.label}</Chip>
                      <span className="review-group-count">{items.length}</span>
                    </div>
                    <ul className="review-list">
                      {items.map((item) => (
                        <ReviewRow key={item.review_id} item={item} />
                      ))}
                    </ul>
                  </div>
                );
              })
            : null}
        </Card.Content>
      </Card>
    </section>
  );
}
