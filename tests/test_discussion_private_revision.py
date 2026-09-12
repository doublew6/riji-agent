"""Exercise private edit commands through normalized, verified ingress."""

from types import SimpleNamespace
import re

import pytest

from riji_agent.mentors.ingress import DiscussionIngress
from riji_agent.mentors.legacy_route import DISCUSSION_COMMANDS
from riji_agent.mentors.models import Envelope, MentorError
from test_ai_discussion_journal import _handoff, journal, system  # noqa: F401


def entry(system, journal):
    handoffs, selected, _, binding = _handoff(system, journal)
    service = system[0]
    ingress = DiscussionIngress(service, SimpleNamespace(wake=lambda: None), handoffs.history, handoffs)

    def send(text, event, *, group=False):
        conversation = service.get(selected.conversation_id, binding.principal_id)
        message = Envelope(delivery_id=event, message_id=event, external_user_id="open-host",
            subject=system[5].account.subject, external_chat_id=conversation.room_id if group else binding.external_chat_id,
            chat_type="group" if group else "p2p", text=text)
        return ingress.receive(binding.application_id, message)
    return send, selected


def confirmation_id(text):
    return re.search(r"/确认转交 ([a-zA-Z0-9_-]+)", text).group(1)


def test_private_edit_and_date_commands_require_the_new_confirmation(system, journal):
    send, selected = entry(system, journal)
    send("/接收转交 " + selected.id, "preview")
    edited = send("/修改转交 " + selected.id + " | A revised AI result, still only a suggestion.", "edit")
    revised = confirmation_id(edited["text"])
    assert revised != selected.id and "/修改转交" in DISCUSSION_COMMANDS
    dated = send("/转交日期 " + revised + " 2026-08-01", "date")
    final_id = confirmation_id(dated["text"])
    assert final_id != revised and "/转交日期" in DISCUSSION_COMMANDS
    with pytest.raises(MentorError, match="handoff_preview_superseded"):
        send("/确认转交 " + selected.id, "old-confirm")
    assert not list(journal[0].glob("daily/*.md"))
    result = send("/确认转交 " + final_id, "valid-confirm")
    assert "保存日记" in result["text"]
    text = (journal[0] / "daily" / "2026-08-01.md").read_text()
    assert "AI 导师讨论结果" in text and "A revised AI result" in text
    assert send("/确认转交 " + final_id, "valid-confirm")["status"] == "duplicate"
    assert text == (journal[0] / "daily" / "2026-08-01.md").read_text()


def test_group_cannot_use_private_revision_command(system, journal):
    send, selected = entry(system, journal)
    with pytest.raises(MentorError, match="verified_riji_private_chat_required"):
        send("/修改转交 " + selected.id + " | Group edit must fail.", "group-edit", group=True)
    assert not list(journal[0].glob("daily/*.md"))


def test_invalid_date_does_not_create_a_preview_or_write(system, journal):
    send, selected = entry(system, journal)
    with pytest.raises(MentorError, match="handoff_date_invalid"):
        send("/转交日期 " + selected.id + " 2026-99-99", "invalid-date")
    assert not list(journal[0].glob("daily/*.md"))
