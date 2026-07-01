"""Optional voice reply generation for IM transports."""

from riji_agent.voice.models import VoiceAttachment
from riji_agent.voice.service import MacOSSayVoiceReplyService, VoiceReplyService

__all__ = ["MacOSSayVoiceReplyService", "VoiceAttachment", "VoiceReplyService"]
