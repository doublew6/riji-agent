"""Optional graph adapter. Checkpoints carry identifiers and cursors, never text."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable, TypedDict

from riji_agent.mentors.models import Artifact, Conversation, Execution, MentorError
from riji_agent.mentors.planner import next_stage
from riji_agent.mentors.store import MentorStore


class Cursor(TypedDict):
    conversation_id: str
    run_id: str
    input_revision: int
    cancel_epoch: int
    lease_generation: int
    stage: str
    progressed: bool


class LangGraphDriver:
    def __init__(self, store: MentorStore, checkpoint_path: Path) -> None:
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver
        except ImportError:
            raise MentorError("mentor_dependencies_required") from None
        self.store = store
        self.path = Path(checkpoint_path)
        if self.path.is_symlink():
            raise MentorError("checkpoint_path_invalid")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(self.path, check_same_thread=False)
        self.saver = SqliteSaver(self.connection)
        self.saver.setup()
        self.connection.execute("PRAGMA secure_delete=ON")
        self.path.chmod(0o600)

    def __call__(self, conversation: Conversation, execution: Execution,
                 execute: Callable[[Conversation, Execution], bool]) -> bool:
        from langgraph.graph import END, START, StateGraph
        from langsmith import tracing_context

        def select(cursor: Cursor) -> dict:
            current = self.store.read("conversation", cursor["conversation_id"], Conversation)
            stage = next_stage(current, self.store.list("artifact", current.id, Artifact))
            return {"stage": stage.kind if stage else "finish"}

        def perform(cursor: Cursor) -> dict:
            return {"progressed": execute(conversation, execution)}

        graph = StateGraph(Cursor)
        graph.add_node("select", select)
        stages = ("opinion", "comparison", "debate", "synthesis", "followup", "finish")
        for stage in stages:
            graph.add_node(stage, perform)
            graph.add_edge(stage, END)
        graph.add_edge(START, "select")
        graph.add_conditional_edges("select", lambda cursor: cursor["stage"], {stage: stage for stage in stages})
        cursor: Cursor = {**execution.model_dump(), "stage": "", "progressed": False}
        config = {"configurable": {"thread_id": conversation.id + "/" + conversation.run_id}, "callbacks": []}
        with tracing_context(enabled=False):
            result = graph.compile(checkpointer=self.saver).invoke(cursor, config=config)
        current = self.store.read("conversation", conversation.id, Conversation)
        if current is None or current.status == "deleted":
            self.forget(conversation.id)
        return bool(result["progressed"])

    def forget(self, conversation_id: str) -> None:
        rows = self.connection.execute("SELECT DISTINCT thread_id FROM checkpoints WHERE thread_id LIKE ?",
                                       (conversation_id + "/%",)).fetchall()
        for row in rows:
            self.saver.delete_thread(row[0])

    def close(self) -> None:
        self.connection.close()
