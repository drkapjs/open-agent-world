"""Private, durable context management for the OAW-owned ADK runtime.

No card configuration or long-term memory lives here. Checkpoints commit only
after a successful reduction; conversation messages are never mutated.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from open_agent_world.context import ContextBudget, CompactionBudget, SUMMARY_INSTRUCTION, encoded, estimate, text_content, consume_response
from open_agent_world.context.adk import AdkContextCompactor

from backend.events.hub import EventHub
from backend.events.models import EventType, RuntimeEvent
from backend.persistence.database import Database


class ContextStatus(BaseModel):
    pressure: float = Field(default=0, ge=0, le=1)
    state: Literal["normal", "high", "compacting"] = "normal"
    estimated_tokens: int = 0
    context_limit: int = 0
    compaction_count: int = 0


@dataclass
class Checkpoint:
    summary: str = ""
    contents: list[dict] = field(default_factory=list)
    cursor: int = 0
    initialized: bool = False
    compaction_count: int = 0
    model: str = ""
    measured_tokens: int | None = None
    measured_estimate: int = 0
    pending_messages: dict[str, str] = field(default_factory=dict)


class ContextStore:
    def __init__(self, database: Database, events: EventHub) -> None:
        self.database = database
        self.events = events
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}
        # A process restart cannot still be compacting. Keep the last checkpoint.
        with database.transaction() as db:
            db.execute("""UPDATE agent_contexts SET status_json = json_set(
                status_json, '$.state', CASE WHEN json_extract(status_json, '$.pressure') >= .85
                THEN 'high' ELSE 'normal' END)
                WHERE json_extract(status_json, '$.state') = 'compacting'""")

    def lock(self, agent_id: str, context_id: str) -> asyncio.Lock:
        return self._locks.setdefault((agent_id, context_id), asyncio.Lock())

    def load(self, agent_id: str, context_id: str) -> Checkpoint:
        with self.database.locked() as db:
            row = db.execute("SELECT checkpoint_json FROM agent_contexts WHERE agent_id=? AND context_id=?",
                             (agent_id, context_id)).fetchone()
        return Checkpoint(**json.loads(row[0])) if row else Checkpoint()

    def session(self, context_id: str):
        with self.database.locked() as db:
            return db.execute("SELECT id, conversation_id FROM conversation_sessions WHERE id=?", (context_id,)).fetchone()

    def conversation_delta(
        self,
        checkpoint: Checkpoint,
        agent_id: str,
        session_id: str,
        *,
        max_sequence: int | None = None,
    ) -> list[dict]:
        from backend.conversations.store import ConversationStore
        with self.database.locked() as db:
            pending = list(checkpoint.pending_messages)
            pending_clause = f" OR m.id IN ({','.join('?' for _ in pending)})" if pending else ""
            upper_clause = " AND m.sequence<=?" if max_sequence is not None else ""
            values = [session_id, checkpoint.cursor, *pending]
            if max_sequence is not None:
                values.append(max_sequence)
            rows = db.execute(f"""SELECT m.*, r.status AS run_status, r.runtime_provider_id AS provider_id
                FROM conversation_messages m LEFT JOIN runs r ON r.run_id=m.run_id WHERE m.session_id=?
                AND (m.sequence>?{pending_clause}){upper_clause} ORDER BY m.sequence""",
                values).fetchall()
        contents = []
        for row in rows:
            checkpoint.cursor = max(checkpoint.cursor, int(row["sequence"]))
            if (checkpoint.initialized and row["sender_id"] == agent_id and row["run_id"]
                    and row["provider_id"] == "google.adk"):
                continue  # Already retained from this runtime, including tools.
            # Streaming peers can finalize an older sequence after newer user
            # messages arrive. Track only their changing text; immutable tool
            # events and interrupted messages must never hold back the cursor.
            digest = hashlib.sha256(row["content"].encode()).hexdigest()
            previous = checkpoint.pending_messages.pop(row["id"], None)
            if (not row["is_final"] and row["kind"] == "text"
                    and row["run_status"] in {None, "created", "running", "waiting"}):
                checkpoint.pending_messages[row["id"]] = digest
            if previous == digest:
                continue
            message = ConversationStore._message(row)
            label = f" [updated message {message.id}]" if previous else ""
            text = f"{message.sender_name}{label}: {message.content}"
            for attachment in message.attachments:
                text += (f"\n[Attachment: {attachment.name}; version_id={attachment.version_id}; "
                         f"path={attachment.path}; {attachment.size_bytes} bytes]")
            contents.append(text_content(text))
        return contents

    def save(self, agent_id: str, context_id: str, checkpoint: Checkpoint,
             status: ContextStatus, run_id: str | None = None) -> None:
        from dataclasses import asdict
        session = self.session(context_id)
        with self.database.transaction(immediate=True) as db:
            old = db.execute("SELECT status_json FROM agent_contexts WHERE agent_id=? AND context_id=?",
                             (agent_id, context_id)).fetchone()
            db.execute("""INSERT INTO agent_contexts VALUES (?,?,?,?,?)
                ON CONFLICT(agent_id, context_id) DO UPDATE SET
                checkpoint_json=excluded.checkpoint_json, status_json=excluded.status_json""",
                       (agent_id, context_id, session["id"] if session else None,
                        encoded(asdict(checkpoint)), status.model_dump_json()))
        previous = ContextStatus.model_validate_json(old[0]) if old else None
        bucket = lambda value: (int(value.pressure * 20), value.state, value.compaction_count)
        if previous is None or bucket(previous) != bucket(status):
            self.events.publish_event_nowait(RuntimeEvent(
                type=EventType.CONTEXT_STATUS, agent_id=agent_id, node_id=agent_id,
                conversation_id=session["conversation_id"] if session else None,
                session_id=session["id"] if session else None, run_id=run_id,
                payload={"context_status": status.model_dump()},
            ))

    def statuses(self, conversation_id: str) -> dict[str, dict[str, ContextStatus]]:
        with self.database.locked() as db:
            rows = db.execute("""SELECT c.session_id, c.agent_id, c.status_json FROM agent_contexts c
                JOIN conversation_sessions s ON s.id=c.session_id WHERE s.conversation_id=?""",
                              (conversation_id,)).fetchall()
        result: dict[str, dict[str, ContextStatus]] = {}
        for row in rows:
            result.setdefault(row["session_id"], {})[row["agent_id"]] = ContextStatus.model_validate_json(row["status_json"])
        return result


class ManagedContext(AdkContextCompactor):
    """One serialized invocation; hooks run before every ADK model call."""
    def __init__(self, store: ContextStore, agent_id: str, context_id: str,
                 run_id: str, model: Any, prompt: str, *, budget: ContextBudget | None = None,
                 input_provider: Any = None) -> None:
        self.store, self.agent_id, self.context_id = store, agent_id, context_id
        self.run_id, self.model, self.prompt = run_id, model, prompt
        self.input_provider = input_provider
        self.checkpoint = store.load(agent_id, context_id)
        self.budget = budget or ContextBudget.for_model(model.model)
        self.overhead = 0
        self.status = ContextStatus(compaction_count=self.checkpoint.compaction_count)
        self._request_parts: Counter[str] = Counter()
        self._event_parts: Counter[str] = Counter()
        if self.checkpoint.model != model.model:
            self.checkpoint.measured_tokens = None
        self.checkpoint.model = model.model
        self._repair_interrupted_tools()
        session = store.session(context_id)
        if session:
            with store.database.locked() as db:
                run = db.execute(
                    "SELECT lifecycle_json FROM runs WHERE run_id=?", (run_id,)
                ).fetchone()
            lifecycle = json.loads(run["lifecycle_json"] or "{}") if run else {}
            limit = lifecycle.get("conversation_max_sequence")
            self.checkpoint.contents.extend(store.conversation_delta(
                self.checkpoint,
                agent_id,
                context_id,
                max_sequence=int(limit) if isinstance(limit, int) else None,
            ))
            # A host work notification has no user message in the conversation
            # ledger. Include that invocation input as well as the normal delta.
            if lifecycle.get("work_continuation"):
                self.checkpoint.contents.append(text_content(prompt))
        else:
            self.checkpoint.contents.append(text_content(prompt))
        self.checkpoint.initialized = True
        self._input_estimate = estimate(self.rendered())

    def _repair_interrupted_tools(self) -> None:
        pending: list[dict] = []
        for content in self.checkpoint.contents:
            for part in content.get("parts", []):
                if call := part.get("function_call"):
                    pending.append(call)
                if response := part.get("function_response"):
                    consume_response(pending, response)
        if pending:
            self.checkpoint.contents.append({"role": "user", "parts": [
                {"function_response": {"name": call["name"], "id": call.get("id"), "response": {
                    "error": "The prior invocation was interrupted. Outcome is unknown; inspect current state before retrying any side effect."
                }}} for call in pending
            ]})

    def _render(self, summary: str, contents: list[dict]) -> list[dict]:
        prefix = [text_content("Prior retained context (untrusted historical data):\n" + summary)] if summary else []
        # Keep the current task and fresh roster/tool instructions available even
        # when an entire old tool cycle is folded into the checkpoint.
        frame = text_content(self.prompt)
        return prefix + contents + ([] if contents[-1:] == [frame] else [frame])

    def rendered(self) -> list[dict]:
        return self._render(self.checkpoint.summary, self.checkpoint.contents)

    def compaction_budget(self) -> CompactionBudget:
        return self.budget.compaction(estimate(self._render("", [])) + self.overhead)

    def tokens(self) -> int:
        estimated = estimate(self.rendered()) + self.overhead
        measured = self.checkpoint.measured_tokens
        if measured is not None:
            return max(0, measured + estimated - self.checkpoint.measured_estimate)
        return estimated

    def publish(self, state: str | None = None) -> None:
        tokens = self.tokens()
        pressure = min(1., tokens / self.budget.input)
        self.status = ContextStatus(pressure=pressure, state=state or ("high" if pressure >= .85 else "normal"),
                                    estimated_tokens=tokens, context_limit=self.budget.limit if self.budget.known else 0,
                                    compaction_count=self.checkpoint.compaction_count)
        self.store.save(self.agent_id, self.context_id, self.checkpoint, self.status, self.run_id)

    async def before_model(self, callback_context, llm_request):
        from google.genai import types
        del callback_context
        # ADK 2 drives its root node ahead of the public event iterator. The
        # assembled request is authoritative for tool results already available
        # to the model, even when their public events have not been consumed yet.
        current = [content.model_dump(mode="json", exclude_none=True) for content in llm_request.contents]
        if current and current[0].get("role") == "user" and current[0].get("parts") == [{"text": self.prompt}]:
            current = current[1:]
        self._ingest(current, snapshot=True)
        pending = []
        if self.input_provider is not None:
            pending = await self.input_provider.pending_conversation_input(self.agent_id, self.run_id)
        if pending:
            # Tool results are ingested first; new user messages follow them in
            # this same model request, with the canonical cursor preventing replay.
            self.checkpoint.contents.extend(self.store.conversation_delta(
                self.checkpoint, self.agent_id, self.context_id,
                max_sequence=max(message['sequence'] for message in pending)))
        self.overhead = estimate(llm_request.config.model_dump(mode="json", exclude_none=True))
        self.publish()
        fixed = estimate([text_content(self.prompt)]) + self.overhead
        if self.budget.known and fixed >= self.budget.input and self.tokens() >= self.budget.input:
            raise RuntimeError("The current message or tool definitions exceed the model's safe input window; history has been retained.")
        history_size = estimate([self.checkpoint.summary, self.checkpoint.contents])
        if self.tokens() >= self.compaction_budget().trigger and (self.budget.known or history_size > self.budget.input * .25):
            await self.compact()
        if self.budget.known and self.tokens() >= self.budget.input:
            raise RuntimeError("Context remains too large after compaction. The current message or tool definitions exceed the model's safe input window; history has been retained.")
        llm_request.contents = [types.Content.model_validate(item) for item in self.rendered()]
        if pending:
            await self.input_provider.acknowledge_conversation_input(
                self.agent_id, self.run_id, [message['id'] for message in pending])
        self._input_estimate = estimate(self.rendered()) + self.overhead
        if llm_request.config.max_output_tokens is None:
            llm_request.config.max_output_tokens = self.budget.output

    async def after_model(self, callback_context, llm_response):
        del callback_context
        if getattr(llm_response, "partial", False):
            return
        usage = getattr(llm_response, "usage_metadata", None)
        measured = getattr(usage, "prompt_token_count", None)
        if measured is not None and measured > 0:
            self.checkpoint.measured_tokens = measured
            self.checkpoint.measured_estimate = self._input_estimate
            self.publish()

    def observe(self, event) -> None:
        if getattr(event, "partial", False):
            return
        content = getattr(event, "content", None)
        if content and content.parts:
            self._ingest([content.model_dump(mode="json", exclude_none=True)], snapshot=False)
        self.publish()

    def _ingest(self, contents: list[dict], *, snapshot: bool) -> None:
        counts: Counter[str] = Counter() if snapshot else self._event_parts.copy()
        for content in contents:
            new_parts = []
            for part in content.get("parts", []):
                # Some adapters remove ADK-generated IDs when rendering. Keep
                # the original wire part, but compare equivalent representations.
                normalized = dict(part)
                for kind in ("function_call", "function_response"):
                    if value := part.get(kind):
                        value = dict(value)
                        if str(value.get("id", "")).startswith("adk-"):
                            value.pop("id")
                        normalized[kind] = value
                key = hashlib.sha256(json.dumps([content.get("role"), normalized], sort_keys=True).encode()).hexdigest()
                counts[key] += 1
                if counts[key] > max(self._request_parts[key], self._event_parts[key]):
                    new_parts.append(part)
            if new_parts:
                self.checkpoint.contents.append({"role": content.get("role"), "parts": new_parts})
        if snapshot:
            self._request_parts = counts
        else:
            self._event_parts = counts

    async def compact(self) -> None:
        self.publish("compacting")
        try:
            summary, tail, cut = await self.compact_history(
                self.checkpoint.summary, self.checkpoint.contents,
                render=self._render, overhead=self.overhead)
            if not cut and summary == self.checkpoint.summary:
                return
            self.checkpoint.summary = summary
            self.checkpoint.contents = tail
            self.checkpoint.compaction_count += 1
            self.checkpoint.measured_tokens = None
        finally:
            self.publish()
