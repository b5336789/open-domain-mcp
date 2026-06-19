"""Turn a chunk's extracted ``KnowledgeUnit.workflow`` into typed steps.

Pure logic: the cross-chunk merge and final ordering happen at query time in
the store (ordered by chunk_index, step_order). Here we only map and locally
sort one chunk's steps.
"""

from __future__ import annotations

from ..models import KnowledgeUnit
from .models import WorkflowStep


def build_workflow(knowledge: KnowledgeUnit) -> tuple[list[WorkflowStep], list[str], str]:
    wf = knowledge.workflow or {}
    name = str(wf.get("name", "")).strip()
    raw_steps = wf.get("steps", []) or []
    if not name or not raw_steps:
        return [], [], ""
    steps = [WorkflowStep(step_order=int(s["order"]), text=s["text"],
                          precondition=s.get("precondition", ""))
             for s in raw_steps]
    steps.sort(key=lambda s: s.step_order)
    prerequisites = list(wf.get("prerequisites", []) or [])
    return steps, prerequisites, name
