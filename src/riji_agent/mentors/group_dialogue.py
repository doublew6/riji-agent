"""Explicit group intents over an owned persistent problem, without model routing."""

from __future__ import annotations

import re
from dataclasses import dataclass

from riji_agent.mentors.models import Command, Conversation, MentorError


PERSONA_NAMES = {
    "gentle_reviewer": "温柔回顾者",
    "blunt_coach": "直率教练",
    "future_self": "未来的我",
    "wang_yangming": "王阳明",
}
HOST_NAMES = {"日记导师", "Riji", "Riji 主持人", "日记导师主持人"}
CONTROL_INTENTS = {
    "停止讨论": "stop", "停止": "stop", "先停止": "stop",
    "先总结": "summarize", "先总结到这里": "summarize",
    "继续上次": "continue", "继续这个问题": "continue",
    "重新分析": "reanalyze", "根据现在的情况重新分析": "reanalyze",
    "重新分析，不沿用旧结论": "reanalyze",
    "归档这个问题": "archive", "恢复这个问题": "restore",
    "把这次结果记到日记里": "save", "把这次讨论记到日记里": "save",
    "把这次结果记下来": "save", "保存讨论结果": "save",
}
ACKNOWLEDGEMENTS = {"收到", "好的", "好", "谢谢", "明白了", "有道理", "同意"}
GROUP_HELP = (
    "这里围绕当前问题持续沟通，普通补充由日记导师接话。\n"
    "请几位导师分别给我参考；请大家辩论一下；请四位重新讨论。\n"
    "继续上次；重新分析；先总结；停止讨论；归档这个问题；恢复这个问题。\n"
    "把这次结果记到日记里：转到本人日记导师私聊预览确认。"
)


@dataclass(frozen=True)
class GroupIntent:
    kind: str
    text: str = ""
    actor: str = "host"
    mode: str = "reference"
    personas: tuple[str, ...] = ()
    reanalyze: bool = False
    fresh_run: bool = False


def normalized_control(text: str) -> str:
    return text.strip().rstrip("。.!！?？ ")


def is_stop_intent(text: str) -> bool:
    return CONTROL_INTENTS.get(normalized_control(text)) == "stop"


def _mentioned_actors(names: tuple[str, ...]) -> tuple[str, ...] | None:
    actors = []
    for name in names:
        name = name.strip()
        clean = name.strip().removeprefix("日记导师·").removesuffix("导师")
        if name in HOST_NAMES or clean in HOST_NAMES:
            continue
        matches = [actor for actor, label in PERSONA_NAMES.items() if clean == label]
        if len(matches) != 1:
            return None
        if matches[0] not in actors:
            actors.append(matches[0])
    return tuple(actors)


def _roundtable_intent(text: str) -> GroupIntent | None:
    fresh = text.endswith(("，不沿用上次结论", "，不沿用旧结论"))
    clean = re.sub(r"，不沿用(?:上次|旧)结论$", "", text)
    match = re.fullmatch(
        r"(?:请|让)(大家|四位(?:导师)?|几位导师|各位(?:导师)?|导师们|他们)"
        r"(?:就这个分歧)?(?P<fresh>重新)?(?:分别)?(?:给我)?"
        r"(?P<mode>参考|讨论|辩论)(?:一下)?(?:[：:]\s*(?P<content>.+))?", clean,
    )
    if not match:
        return _selected_roundtable(clean, fresh)
    mode = "debate" if match["mode"] in {"讨论", "辩论"} else "reference"
    return GroupIntent("start_run", text=match["content"] or "", mode=mode,
                       personas=tuple(PERSONA_NAMES) if match.group(1).startswith("四位") else (),
                       reanalyze=fresh, fresh_run=bool(match["fresh"]) or fresh)


def _selected_roundtable(text: str, fresh: bool) -> GroupIntent | None:
    match = re.fullmatch(r"请(?P<names>.+?)(?P<fresh>重新)?(?:分别)?(?:给我)?"
                         r"(?P<mode>参考|讨论|辩论)(?:一下)?(?:[：:]\s*(?P<content>.+))?", text)
    if match is None:
        return None
    names = tuple(re.split(r"[、,，]|和|与", match["names"]))
    actors = _mentioned_actors(names)
    if actors is None or not 2 <= len(actors) <= 4:
        return None
    mode = "debate" if match["mode"] in {"讨论", "辩论"} else "reference"
    return GroupIntent("start_run", text=match["content"] or "", mode=mode,
                       personas=actors, reanalyze=fresh, fresh_run=bool(match["fresh"]) or fresh)


def _named_followup(text: str) -> GroupIntent | None:
    expressions = {actor: r"@?(?:日记导师·)?" + re.escape(name) + r"(?:导师)?[，,:：\s]+(.+)"
                   for actor, name in PERSONA_NAMES.items()}
    for actor, pattern in expressions.items():
        match = re.fullmatch(pattern, text)
        if match:
            content = match.group(1)
            if any(re.fullmatch(other, content) for other in expressions.values()):
                return GroupIntent("clarify")
            return GroupIntent("followup", text=content, actor=actor)
    return None


def parse_group_intent(text: str, mentioned_names: tuple[str, ...] = ()) -> GroupIntent:
    clean = normalized_control(text)
    mentions = _mentioned_actors(mentioned_names)
    if mentions is None or len(mentions) > 1:
        return GroupIntent("clarify")
    if clean in {"帮助", "/帮助", "/讨论帮助"}:
        return GroupIntent("help")
    if clean in CONTROL_INTENTS:
        return GroupIntent(CONTROL_INTENTS[clean], text=clean)
    if clean in ACKNOWLEDGEMENTS or not clean:
        return GroupIntent("ack")
    intent = _roundtable_intent(clean)
    if intent is not None:
        return intent if not mentions else GroupIntent("clarify")
    if clean.startswith(("/", "更正：", "更正:", "纠正：", "纠正:")):
        return GroupIntent("clarify")
    named = _named_followup(clean)
    if named is not None:
        if named.kind == "clarify" or (mentions and named.actor != mentions[0]):
            return GroupIntent("clarify")
        return named
    if mentions:
        return GroupIntent("followup", text=clean, actor=mentions[0])
    return GroupIntent("supplement", text=clean)


class GroupDialogue:
    def __init__(self, service, history, handoffs=None) -> None:
        self.service, self.history, self.handoffs = service, history, handoffs

    def receive(self, conversation: Conversation, operation_id: str, text: str,
                *, mentioned_names: tuple[str, ...] = (), group_binding=None) -> dict:
        if conversation.kind != "roundtable":
            raise MentorError("roundtable_required")
        intent = parse_group_intent(text, mentioned_names)
        self.service.check_input_scope(conversation, intent.kind, group_binding)
        if intent.kind not in {"stop", "archive", "help", "save"}:
            self.service.policy._check_audience(conversation)
        if intent.kind == "help":
            return {"conversation_id": conversation.id, "text": GROUP_HELP}
        if intent.kind == "clarify":
            return {"conversation_id": conversation.id, "text": (
                "请明确指定一位导师，或说“请大家辩论一下”。"
                "更正已有情况时，请在问题记录中选择要更正的原话。")}
        if intent.kind == "ack":
            return {"conversation_id": conversation.id, "text": "已收到；不会因此开启全桌讨论或保存日记。"}
        if intent.kind == "save":
            return self.save(conversation, operation_id)
        if intent.actor != "host" and intent.actor not in conversation.personas:
            raise MentorError("actor_not_allowed")
        return self._apply(conversation, operation_id, intent, group_binding)

    def _apply(self, conversation: Conversation, operation_id: str, intent: GroupIntent, group_binding=None) -> dict:
        kind = intent.kind
        if (conversation.source_scope == "group_only" and kind == "supplement"
                and conversation.run_kind != "followup" and conversation.status in {"queued", "running", "waiting_user", "delivering"}):
            kind = "followup"
        unchanged_speakers = (not intent.personas or
                              set(intent.personas) == set(conversation.run_personas or conversation.personas))
        if (kind == "start_run" and intent.mode == "debate" and not intent.fresh_run
                and not intent.reanalyze and not intent.text and unchanged_speakers
                and conversation.mode == "reference" and conversation.status == "completed"
                and conversation.run_kind != "followup" and not conversation.debate_started):
            kind = "debate"
        command = Command(id=operation_id, principal_id=conversation.owner_id,
            conversation_id=conversation.id, expected_revision=conversation.input_revision,
            kind=kind, text=intent.text, actor=intent.actor, mode=intent.mode,
            personas=intent.personas, reanalyze=intent.reanalyze)
        receipt = self.service.apply(command, group_binding=group_binding)
        reply = "已收到，由日记导师接话。"
        if kind == "start_run":
            reply = "已在原问题开启一次" + ("辩论" if intent.mode == "debate" else "参考") + "，将按本次上限进行。"
        elif kind == "debate":
            reply = "在本次参考的基础上继续辩论，沿用本场讨论的剩余额度。"
        elif intent.actor != "host":
            reply = "已请" + PERSONA_NAMES[intent.actor] + "回应。"
        elif kind in {"stop", "summarize", "archive", "restore", "continue", "reanalyze"}:
            reply = {
                "stop": "已停止本次讨论，已有记录保留。",
                "summarize": "正在根据已完成内容收束。",
                "archive": "已归档这个问题，仍可回看。",
                "restore": "已恢复这个问题；请说明目前的情况。",
                "continue": "由日记导师接着上次的情况继续。",
                "reanalyze": "由日记导师根据当前事实重新分析，不沿用旧 AI 结论。",
            }[kind]
        if kind in {"start_run", "debate"}:
            reply += self._run_description(conversation)
        return {"text": reply, **receipt.model_dump()}

    def _run_description(self, previous: Conversation) -> str:
        current = self.service.get(previous.id, previous.owner_id)
        budget = self.service.budgets.status(current)["current"]
        actors = "、".join(PERSONA_NAMES[item] for item in current.run_personas or current.personas)
        rounds = f"，最多 {current.rounds} 轮辩论" if current.mode == "debate" else "，本次仅参考"
        return (f"\n本次发言：{actors}{rounds}；背景版本 {current.summary_version}。"
                f"模型请求已用 {budget['requests']}/{budget['request_limit']}。")

    def save(self, conversation: Conversation, operation_id: str) -> dict:
        if self.handoffs is None:
            raise MentorError("handoff_unavailable")
        view = self.history.read(conversation.id, conversation.owner_id)
        selected = [item["id"] for item in view["artifacts"]
                    if item["kind"] in {"synthesis", "comparison", "followup"}
                    and "unavailable" not in item and not item.get("superseded")][-1:]
        if not selected:
            raise MentorError("discussion_result_unavailable")
        handoff = self.handoffs.create(conversation.id, conversation.owner_id,
                                      tuple(selected), operation_id=operation_id)
        return {"conversation_id": conversation.id, "handoff_id": handoff.id,
                "text": "请到本人日记导师私聊查看预览，再确认保存：\n/接收转交 " + handoff.id}
