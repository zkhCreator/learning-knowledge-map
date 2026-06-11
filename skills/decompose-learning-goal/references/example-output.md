# Example Output

## Human Summary

Target: Learn Kubernetes service networking.

Recommended order:

1. ClusterIP service model
2. EndpointSlice mapping
3. kube-proxy routing behavior

Review: Approved after adding EndpointSlice mapping as an explicit bridge between the
Service virtual IP model and kube-proxy routing behavior.

Persistence: Saved through `scripts/persist_result.py` to `learning.db`.
Report the actual `db_path`, `goal_id`, and `import_id` returned by the script.

## JSON

```json
{
  "target": "Learn Kubernetes service networking",
  "assumptions": ["Learner already understands basic TCP/IP and containers."],
  "sources": [],
  "nodes": [
    {
      "title": "Kubernetes service networking",
      "description": "Understand how Kubernetes Services provide stable access to changing Pod backends.",
      "domain": "Kubernetes",
      "concept_fingerprint": ["stable abstraction", "dynamic membership"],
      "difficulty": 3,
      "est_minutes": 0,
      "strictness_level": "standard",
      "mastery_threshold": 0.8,
      "risk_note": "",
      "is_atomic": false,
      "parent_title": null,
      "qa_draft": []
    },
    {
      "title": "ClusterIP service model",
      "description": "Explain why a Service has a stable virtual IP while Pods behind it can change.",
      "domain": "Kubernetes",
      "concept_fingerprint": ["stable abstraction", "indirection"],
      "difficulty": 2,
      "est_minutes": 12,
      "strictness_level": "standard",
      "mastery_threshold": 0.8,
      "risk_note": "",
      "is_atomic": true,
      "parent_title": "Kubernetes service networking",
      "qa_draft": [
        "What problem does ClusterIP solve for clients?",
        "Why can Pods change without clients changing their target address?",
        "What is the difference between a Service IP and a Pod IP?"
      ]
    },
    {
      "title": "EndpointSlice mapping",
      "description": "Explain how EndpointSlices represent the current backend endpoints selected for a Service.",
      "domain": "Kubernetes",
      "concept_fingerprint": ["backend registry", "dynamic membership"],
      "difficulty": 3,
      "est_minutes": 15,
      "strictness_level": "critical",
      "mastery_threshold": 0.95,
      "risk_note": "Without this bridge, learners often confuse a stable Service address with the mutable Pod backends.",
      "is_atomic": true,
      "parent_title": "Kubernetes service networking",
      "qa_draft": [
        "What information does an EndpointSlice store for a Service?",
        "How does EndpointSlice membership change when Pods are created or removed?",
        "Why is EndpointSlice mapping separate from the Service's stable ClusterIP?"
      ]
    },
    {
      "title": "kube-proxy routing behavior",
      "description": "Explain how kube-proxy consumes Service and EndpointSlice state to program node-level routing behavior.",
      "domain": "Kubernetes",
      "concept_fingerprint": ["node-local routing", "control-plane watch"],
      "difficulty": 3,
      "est_minutes": 15,
      "strictness_level": "critical",
      "mastery_threshold": 0.95,
      "risk_note": "This is the core data-path model needed for Service debugging.",
      "is_atomic": true,
      "parent_title": "Kubernetes service networking",
      "qa_draft": [
        "Which resources does kube-proxy watch to update Service routing?",
        "Why does kube-proxy need EndpointSlice information?",
        "What role does kube-proxy play on each node when traffic targets a Service?"
      ]
    }
  ],
  "edges": [
    {
      "from_title": "ClusterIP service model",
      "to_title": "EndpointSlice mapping",
      "edge_type": "prerequisite",
      "weight": 1.0,
      "analogy_desc": null
    },
    {
      "from_title": "EndpointSlice mapping",
      "to_title": "kube-proxy routing behavior",
      "edge_type": "prerequisite",
      "weight": 1.0,
      "analogy_desc": null
    }
  ],
  "review": {
    "status": "approved",
    "provider": "codex",
    "local_checks": [
      {
        "parent_title": "Kubernetes service networking",
        "status": "approved",
        "provider": "codex",
        "issues": [],
        "suggestions_applied": ["Added EndpointSlice mapping as an explicit bridge node."]
      }
    ],
    "global_check": {
      "status": "approved",
      "provider": "codex",
      "issues": [],
      "suggestions_applied": []
    },
    "issues": [],
    "suggestions_applied": ["Added EndpointSlice mapping as an explicit bridge node."]
  },
  "unresolved_questions": []
}
```
