"""模型提供商实例池：按模型配置缓存，配置或 API Key 改了就重建。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agent_core.core.ports import LLMProvider

ProviderFactory = Callable[[Any], LLMProvider]
"""参数是 provider_profiles 的一行。"""


class ProviderPool:
    def __init__(self, factory: ProviderFactory) -> None:
        self._factory = factory
        self._cache: dict[int, tuple[int, LLMProvider]] = {}

    async def get(self, profile: Any) -> LLMProvider:
        cached = self._cache.get(profile.id)
        if cached is not None and cached[0] == profile.updated_at:
            return cached[1]
        if cached is not None:
            await cached[1].aclose()
        provider = self._factory(profile)
        self._cache[profile.id] = (profile.updated_at, provider)
        return provider

    async def invalidate(self, profile_id: int) -> None:
        cached = self._cache.pop(profile_id, None)
        if cached is not None:
            await cached[1].aclose()

    async def close_all(self) -> None:
        for _, provider in self._cache.values():
            await provider.aclose()
        self._cache.clear()
