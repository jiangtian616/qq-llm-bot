"""端口：核心服务对外部世界的抽象。具体实现放在 plugins/ 和 infra/，core/ 不引用它们。"""

from agent_core.core.ports.channel import ChannelAdapter, ChannelContext
from agent_core.core.ports.middleware import Middleware, NextHandler
from agent_core.core.ports.provider import CompactResult, LLMProvider, ProviderCapabilities, ProviderContext
from agent_core.core.ports.repository import Repository
from agent_core.core.ports.secrets import SecretReader
from agent_core.core.ports.tool import Tool
from agent_core.core.ports.transport import InboundTransport, ResumeState, TransportContext

__all__ = [
    "ChannelAdapter",
    "ChannelContext",
    "CompactResult",
    "InboundTransport",
    "LLMProvider",
    "Middleware",
    "NextHandler",
    "ProviderCapabilities",
    "ProviderContext",
    "Repository",
    "ResumeState",
    "SecretReader",
    "Tool",
    "TransportContext",
]
