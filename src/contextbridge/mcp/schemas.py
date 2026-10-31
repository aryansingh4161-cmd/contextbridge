"""Pydantic input models: these generate the JSON Schemas the model sees. Business rules live
in the core (knowledge.py); this layer only fixes shapes and enums."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..core.checkpoints import CheckpointIn
from ..core.knowledge import EntryIn
from ..core.state import SectionIn, TaskIn

Kind = Literal["requirement", "decision", "rejected_approach", "constraint", "preference", "fact",
               "explanation", "bug", "fix", "lesson", "milestone", "risk", "open_question", "reference",
               "convention"]


class EntryInput(BaseModel):
    """One piece of project knowledge."""

    model_config = ConfigDict(extra="forbid")

    kind: Kind
    topic_key: str = Field(
        max_length=120,
        description="Stable dotted key for the QUESTION this answers, e.g. 'backend.framework', "
                    "'auth.token_storage'. A rejected_approach uses the SAME key as the decision it lost to "
                    "(rejecting localStorage for tokens -> 'auth.token_storage', not "
                    "'auth.token_storage.localstorage'). Reuse existing keys (see similar_topic_keys).")
    title: str = Field(max_length=300, description="Short statement, e.g. 'Use FastAPI for the backend'")
    body: str = Field("", max_length=20_000,
                      description="Detail. Keep exact values (ports, versions, commands, snippets) verbatim.")
    rationale: str | None = Field(None, max_length=5000,
                                  description="Why. REQUIRED for decision and rejected_approach.")
    alternatives: list[str] | None = Field(None, description="For decisions: what else was considered and why not")
    status: Literal["active", "unverified", "resolved", "rejected", "outdated"] = "active"
    provenance: Literal["user_stated", "ai_suggested", "user_confirmed", "verified", "imported"] = Field(
        description="Who it came from. Your own suggestions are ai_suggested until the user confirms.")
    confidence: Literal["confirmed", "assumed", "uncertain"] | None = None
    importance: Literal["critical", "important", "optional"] = "important"
    supersedes_id: int | None = Field(None, description="Id of the entry this replaces (it becomes superseded)")
    source_ref: str | None = Field(None, max_length=500, description="Where it came from: file, commit, doc")

    def to_core(self) -> EntryIn:
        return EntryIn(**self.model_dump())


SectionName = Literal["architecture", "stack", "implemented", "in_progress", "broken", "needs_testing",
                      "postponed", "blockers", "dependencies", "outstanding_decisions", "environment", "repo"]


class SectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(max_length=20_000, description="The full current text of this section (replaces it). "
                                                        "Empty string clears it.")
    refs: list[int] | None = Field(None, description="Ids of knowledge entries this section relies on, e.g. "
                                                     "the active backend.framework decision")

    def to_core(self) -> SectionIn:
        return SectionIn(self.content, self.refs)


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int | None = Field(None, description="Omit to create; pass to update an existing task")
    title: str | None = Field(None, max_length=2000)
    status: Literal["todo", "in_progress", "blocked", "done", "postponed"] | None = None
    priority: int | None = Field(None, ge=1, le=5, description="1 = highest")
    depends_on: list[int] | None = Field(None, description="Task ids this depends on")
    expected_outcome: str | None = Field(None, max_length=2000)
    notes: str | None = Field(None, max_length=2000)

    def to_core(self) -> TaskIn:
        return TaskIn(**self.model_dump())


class FinalUpdates(BaseModel):
    """The last checkpoint of this session, saved atomically before the package is assembled."""

    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=4000, description="What happened this session")
    where_stopped: str = Field(min_length=1, max_length=4000, description="Concrete: file/step/last result")
    next_action: str = Field(min_length=1, max_length=4000, description="The very next thing to do")
    session_label: str | None = Field(None, max_length=120)
    client: str | None = Field(None, max_length=60)
    account_label: str | None = Field(None, max_length=60)
    items: list[EntryInput] | None = Field(None, max_length=50)
    sections: dict[SectionName, SectionInput] | None = None
    tasks: list[TaskInput] | None = Field(None, max_length=100)

    def to_core(self) -> CheckpointIn:
        return CheckpointIn(summary=self.summary, where_stopped=self.where_stopped, next_action=self.next_action,
                            session_label=self.session_label, client=self.client, account_label=self.account_label,
                            items=[i.to_core() for i in self.items or []] or None,
                            sections={k: v.to_core() for k, v in (self.sections or {}).items()} or None,
                            tasks=[t.to_core() for t in self.tasks or []] or None)


class ImportEdit(BaseModel):
    """A correction to one staged import item before it is committed."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0, description="Index from the stage preview")
    kind: Kind | None = None
    topic_key: str | None = Field(None, max_length=120)
    title: str | None = Field(None, max_length=300)
    importance: Literal["critical", "important", "optional"] | None = None
    rationale: str | None = Field(None, max_length=5000)
    body: str | None = Field(None, max_length=20_000)
    replace: bool | None = Field(None, description="For a Live State section that already has content: overwrite it")
