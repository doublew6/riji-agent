"""Source for an in-place lifecycle observer on the original Hermes adapter."""

LIFECYCLE_EVENTS = (
    "im.chat.member.user.added_v1",
    "im.chat.member.user.deleted_v1",
    "im.chat.member.user.withdrawn_v1",
    "im.chat.updated_v1",
    "im.chat.member.bot.deleted_v1",
)
METHOD_BEGIN = "    # BEGIN riji-agent Feishu lifecycle transport"
METHOD_END = "    # END riji-agent Feishu lifecycle transport"
REGISTER_BEGIN = "            # BEGIN riji-agent Feishu lifecycle registrations"
REGISTER_END = "            # END riji-agent Feishu lifecycle registrations"
BUILD_ANCHOR = "    def _build_event_handler(self) -> Any:\n"
ORIGINAL_BOT_REGISTRATION = (
    "            .register_p2_im_chat_member_bot_deleted_v1(self._on_bot_removed_from_chat)"
)


def registration_block() -> str:
    return "\n".join((
        REGISTER_BEGIN,
        "            .register_p2_im_chat_member_user_added_v1(self._riji_on_lifecycle_event)",
        "            .register_p2_im_chat_member_user_deleted_v1(self._riji_on_lifecycle_event)",
        "            .register_p2_im_chat_member_user_withdrawn_v1(self._riji_on_lifecycle_event)",
        "            .register_p2_im_chat_updated_v1(self._riji_on_lifecycle_event)",
        "            .register_p2_im_chat_member_bot_deleted_v1(",
        "                lambda data: self._riji_on_lifecycle_event(data, self._on_bot_removed_from_chat)",
        "            )",
        REGISTER_END,
    ))


def lifecycle_block() -> str:
    events = repr(LIFECYCLE_EVENTS)
    return '''    # BEGIN riji-agent Feishu lifecycle transport
    def _riji_on_lifecycle_event(self, data, original=None):
        # The existing callback still runs once, including on transport failure.
        try:
            self._riji_forward_lifecycle(data)
        finally:
            if original is not None:
                return original(data)

    def _riji_forward_lifecycle(self, data):
        # This is a bounded invalidation request, never an agent/model request.
        if not os.getenv("RIJI_AGENT_URL") or not os.getenv("HERMES_SHARED_SECRET"):
            return
        if self._app_id != os.getenv("FEISHU_APP_ID"):
            return
        try:
            import json as _riji_json
            import httpx as _riji_httpx
            from urllib.parse import urlsplit as _riji_urlsplit
            from lark_oapi import JSON as _riji_codec

            url = _riji_urlsplit(os.environ["RIJI_AGENT_URL"])
            if (url.scheme != "http" or url.hostname != "127.0.0.1" or url.port != 8765
                    or url.path != "/hermes/messages" or url.username is not None
                    or url.password is not None or url.query or url.fragment):
                return
            raw = data if isinstance(data, dict) else _riji_json.loads(_riji_codec.marshal(data))
            if (not isinstance(raw, dict) or raw.get("schema") != "2.0"
                    or not isinstance(raw.get("header"), dict)
                    or not isinstance(raw.get("event"), dict)
                    or raw["header"].get("app_id") != self._app_id
                    or raw["header"].get("event_type") not in ''' + events + '''
                    or not isinstance(raw["event"].get("chat_id"), str)
                    or not 1 <= len(raw["event"]["chat_id"]) <= 300
                    or len(_riji_json.dumps(raw).encode()) > 100000):
                return
            with _riji_httpx.Client(timeout=1.0, trust_env=False, follow_redirects=False) as client:
                response = client.post("http://127.0.0.1:8765/api/mentors/v1/host-lifecycle",
                    headers={"X-Hermes-Secret": os.environ["HERMES_SHARED_SECRET"]},
                    json={"raw_event": raw})
            if not 200 <= response.status_code < 300:
                logger.warning("riji-agent lifecycle invalidation unconfirmed")
        except Exception:
            # A lost event is not evidence of continuous membership observation.
            logger.warning("riji-agent lifecycle invalidation unavailable")
    # END riji-agent Feishu lifecycle transport'''
