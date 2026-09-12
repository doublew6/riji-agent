"""Run opt-in synthetic model checks; semantic ratings remain a separate review."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile

from riji_agent.config import load_settings
from riji_agent.mentors.delivery import OutboxDispatcher
from riji_agent.mentors.generation import ModelGeneration
from riji_agent.mentors.identity import IdentityService
from riji_agent.mentors.local_channel import LocalChannel
from riji_agent.mentors.models import Account, Application, Artifact, Command, Conversation, Envelope
from riji_agent.mentors.policy import DiscussionPolicy
from riji_agent.mentors.ports import NoSources
from riji_agent.mentors.service import DiscussionService
from riji_agent.mentors.store import MentorStore
from riji_agent.mentors.worker import DiscussionWorker
from riji_agent.models.registry import build_model_provider
from riji_agent.personas.registry import PersonaRegistry


def evaluate(case, provider):
    with tempfile.TemporaryDirectory(prefix="riji-mentor-eval-") as temporary:
        store = MentorStore(Path(temporary) / "synthetic.sqlite3")
        identity = IdentityService(store, PersonaRegistry())
        principal = identity.register_principal(Account(platform="local", tenant="synthetic", subject="synthetic"), "synthetic")
        applications = {actor: identity.register_application(Application(platform="local", tenant="synthetic",
            external_id=actor, persona_id=actor, role="host" if actor == "host" else "mentor")) for actor in ("host", *identity.personas.ids())}
        actor = case.get("persona", "host")
        _, _, binding = identity.resolve(applications[actor].id, Envelope(delivery_id="synthetic", message_id="synthetic",
            external_user_id="synthetic", subject="synthetic", external_chat_id="synthetic-private", chat_type="p2p", text=""))
        service = DiscussionService(store, identity, DiscussionPolicy(store, NoSources(), LocalChannel()))
        worker = DiscussionWorker(service, ModelGeneration(provider, identity.personas))
        dispatcher = OutboxDispatcher(service)
        conversation = service.create(binding, case["question"], personas=tuple(case.get("personas", [actor])), mode=case.get("mode", "private"), rounds=1)
        if conversation.kind == "roundtable":
            preview = service.share_preview(conversation.id, principal.id)
            service.apply(Command(id="share", principal_id=principal.id, conversation_id=conversation.id,
                expected_revision=1, kind="share", preview_hash=preview["preview_hash"]))
        for _ in range(30):
            progressed = worker.run_one(conversation.id)
            progressed = dispatcher.dispatch_one(conversation.id) or progressed
            if not progressed:
                break
        current = service.get(conversation.id, principal.id)
        with store.transaction() as db:
            blocked = store.lookup(db, "blocked", conversation.id)
        artifacts = store.list("artifact", conversation.id, Artifact)
        return {"id": case["id"], "status": current.status, "blocked": blocked,
            "requests": service.budgets.status(current)["total_requests"],
            "question": case["question"], "rubric": case["rubric"], "semantic_review": "pending",
            "outputs": [{"id": item.id, "actor": item.actor, "kind": item.kind, "text": item.text,
                         "responds_to": item.responds_to, "stance_change": item.stance_change,
                         "debate_needed": item.debate_needed,
                         "uncertainties": item.uncertainties, "next_steps": item.next_steps}
                        for item in artifacts if item.kind != "user"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Authorize sending repository synthetic cases to the configured model.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    if not args.live:
        parser.exit(2, "Use --live to send synthetic cases to the configured model.\n")
    cases = json.loads((Path(__file__).resolve().parent.parent / "evals/mentors/cases.json").read_text())["cases"]
    if args.case:
        cases = [case for case in cases if case["id"] in args.case]
    provider = build_model_provider(load_settings())
    report = {"data_class": "synthetic", "quality_pass": None, "cases": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    for case in cases:
        result = evaluate(case, provider)
        report["cases"].append(result)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        args.output.chmod(0o600)
        print(case["id"] + ": " + result["status"], flush=True)


if __name__ == "__main__":
    main()
