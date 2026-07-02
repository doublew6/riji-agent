"""Optional voice reply generation for IM transports."""

from riji_agent.voice.models import VoiceAttachment
from riji_agent.voice.service import (
    MacOSSayVoiceReplyService,
    MeloTTSVoiceReplyService,
    VoxCPMVoiceReplyService,
    VoiceReplyService,
)

__all__ = [
    "MacOSSayVoiceReplyService",
    "MeloTTSVoiceReplyService",
    "VoxCPMVoiceReplyService",
    "VoiceAttachment",
    "VoiceReplyService",
]
