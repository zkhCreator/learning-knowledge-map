/*
File: skills/serve-learning-graph/web/src/features/review/ReviewView.tsx

Purpose:
    Entry router for the Review tab. Keeps the list and the start flow as
    independent ?review= entries within the single ?view=review tab.

Responsibilities:
    - With no ?review= param, show the review queue (ReviewListView).
    - With ?review=<id>, show the Review Start flow for that review.

What this file does NOT do:
    - Fetch data or own tab state (App owns ?view=); it only reads ?review=.
    - Auto-navigate between list and start — the user follows explicit links.

Inputs: ?review= URL param
Outputs: The list or the start flow for the Review tab
*/

import ReviewListView from "./ReviewListView";
import ReviewStartView from "./ReviewStartView";

export default function ReviewView() {
  const reviewId = new URLSearchParams(window.location.search).get("review");
  return reviewId ? <ReviewStartView reviewId={reviewId} /> : <ReviewListView />;
}
