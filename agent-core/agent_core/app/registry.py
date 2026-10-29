"""插件注册表：从 entry points 找到实现，按配置装配。core/ 不引用 plugins/，两者只在这里接上。"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points

from agent_core.app.config import PluginsConfig
from agent_core.core.ports import ChannelAdapter, InboundTransport, LLMProvider

GROUPS: dict[str, tuple[str, type]] = {
    "channel": ("qq_llm_bot.channels", ChannelAdapter),
    "transport": ("qq_llm_bot.transports", InboundTransport),
    "provider": ("qq_llm_bot.providers", LLMProvider),
}


# 随程序一起分发的插件。打包后的程序里没有分发元数据，entry_points() 查不到它们，用这张表兜底；
# 要和 pyproject.toml 的 entry points 保持一致。第三方插件仍然通过 entry points 登记。
BUILTIN_PLUGINS: dict[str, dict[str, str]] = {
    "qq_llm_bot.channels": {"qq": "agent_core.plugins.channel_qq.adapter:QQAdapter"},
    "qq_llm_bot.transports": {
        "qq.websocket": "agent_core.plugins.channel_qq.transport_ws:WebSocketTransport",
        "qq.webhook": "agent_core.plugins.channel_qq.transport_webhook:WebhookTransport",
    },
    "qq_llm_bot.providers": {
        "claude": "agent_core.plugins.provider_claude.provider:ClaudeProvider",
        "deepseek": "agent_core.plugins.provider_deepseek.provider:DeepSeekProvider",
    },
}


def _discover(group: str) -> list[EntryPoint]:
    found = list(entry_points(group=group))
    names = {ep.name for ep in found}
    found += [
        EntryPoint(name=name, value=value, group=group)
        for name, value in BUILTIN_PLUGINS.get(group, {}).items()
        if name not in names
    ]
    return found


class PluginNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class Assembly:
    channel: type[ChannelAdapter]
    transport: type[InboundTransport]
    provider: type[LLMProvider]

    def describe(self) -> dict[str, str]:
        return {
            kind: f"{cls.__module__}.{cls.__qualname__}"
            for kind, cls in (("channel", self.channel), ("transport", self.transport), ("provider", self.provider))
        }


class PluginRegistry:
    def __init__(self, loader: Callable[[str], Iterable[EntryPoint]] | None = None) -> None:
        self._loader = loader or _discover

    def available(self, kind: str) -> list[str]:
        group, _ = GROUPS[kind]
        return sorted(ep.name for ep in self._loader(group))

    def load(self, kind: str, name: str) -> type:
        group, base = GROUPS[kind]
        for ep in self._loader(group):
            if ep.name == name:
                cls = ep.load()
                if not (isinstance(cls, type) and issubclass(cls, base)):
                    raise TypeError(f"插件 {group}:{name} 不是 {base.__name__} 的实现")
                return cls
        raise PluginNotFoundError(f"找不到插件 {group}:{name}，可用的有：{', '.join(self.available(kind)) or '无'}")

    def assemble(self, cfg: PluginsConfig) -> Assembly:
        # 接入方式按「渠道.接入方式」登记，不同渠道可以各有自己的 websocket 实现。
        return Assembly(
            channel=self.load("channel", cfg.channel),
            transport=self.load("transport", f"{cfg.channel}.{cfg.transport}"),
            provider=self.load("provider", cfg.provider),
        )
