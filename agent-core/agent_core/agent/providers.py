"""模型提供商实例池：按模型配置缓存，配置、API Key 或代理改了就重建。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_core.core.network import Proxy
from agent_core.core.ports import LLMProvider

ProviderFactory = Callable[[Any, Proxy], LLMProvider]
"""参数是 provider_profiles 的一行和调模型接口用的代理。"""


class ProviderPool:
    def __init__(self, factory: ProviderFactory) -> None:
        self._factory = factory
        self._cache: dict[int, tuple[tuple[int, Proxy], LLMProvider]] = {}

    async def get(self, profile: Any, proxy: Proxy) -> LLMProvider:
        key = (profile.updated_at, proxy)
        cached = self._cache.get(profile.id)
        if cached is not None and cached[0] == key:
            return cached[1]
        if cached is not None:
            await cached[1].aclose()
        provider = self._factory(profile, proxy)
        self._cache[profile.id] = (key, provider)
        return provider

    async def invalidate(self, profile_id: int) -> None:
        cached = self._cache.pop(profile_id, None)
        if cached is not None:
            await cached[1].aclose()

    async def close_all(self) -> None:
        for _, provider in self._cache.values():
            await provider.aclose()
        self._cache.clear()
