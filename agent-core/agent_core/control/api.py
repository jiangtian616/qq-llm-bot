"""控制接口的业务路由：机器人账号、连接启停、模型配置、分层配置、运行事件和统计。

密钥只进不出：接口只接收 AppSecret / API Key 写进密钥库，返回时只给是否已设置和末 4 位。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from agent_core.control.auth import require_token
from agent_core.control.state import ControlState
from agent_core.core.bus import BusEvent
from agent_core.core.clock import start_of_local_day_ms
from agent_core.core.settings import DEFAULTS, SCOPES

DEFAULT_INTENTS = 1 << 25  # GROUP_AND_C2C_EVENT：群聊和单聊事件


def _ctl(request: Request) -> ControlState:
    return request.app.state.control


def _account_secret_key(platform: str, app_id: str) -> str:
    # 和渠道插件约定的键名，如 qq:{app_id}。
    return f"{platform}:{app_id}"


def _hint(value: str | None) -> str | None:
    return ("****" + value[-4:]) if value else None


# ---------- 请求体 ----------


class AccountCreate(BaseModel):
    app_id: str = Field(min_length=1)
    app_secret: str = Field(min_length=1)
    display_name: str = ""
    auto_connect: bool = False


class AccountUpdate(BaseModel):
    display_name: str | None = None
    app_secret: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    auto_connect: bool | None = None


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1)
    provider: str | None = None
    model: str = Field(min_length=1)
    system_prompt: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    api_key: str | None = Field(default=None, min_length=1)


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1)
    model: str | None = Field(default=None, min_length=1)
    system_prompt: str | None = None
    params: dict[str, Any] | None = None
    api_key: str | None = Field(default=None, min_length=1)


class SettingPut(BaseModel):
    scope: str = "global"
    scope_id: int = 0
    key: str
    value: Any = None
    """null 表示删除这一层的值，回到上一层或默认值。"""


# ---------- 路由 ----------


def build_router() -> APIRouter:
    r = APIRouter(dependencies=[Depends(require_token)])

    async def account_view(s: ControlState, account: Any) -> dict[str, Any]:
        secret = s.secrets.get_secret(_account_secret_key(account.platform, account.app_id))
        gateway = await s.db.accounts.gateway(account.id)
        return {
            "id": account.id,
            "platform": account.platform,
            "app_id": account.app_id,
            "display_name": account.display_name,
            "transport": account.transport,
            "intents": account.intents,
            "enabled": bool(account.enabled),
            "auto_connect": bool(await s.db.settings.resolve("connection.auto_connect", account_id=account.id)),
            "secret_set": bool(secret),
            "secret_hint": _hint(secret),
            "connection": {
                "state": s.connections.state(account.id),
                "last_error": s.connections.last_error(account.id) or (gateway.last_error if gateway else None),
                "last_ready_at": gateway.last_ready_at if gateway else None,
                "last_event_at": gateway.last_event_at if gateway else None,
            },
        }

    async def get_account(s: ControlState, account_id: int) -> Any:
        account = await s.db.accounts.get(account_id)
        if account is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "账号不存在")
        return account

    async def changed(s: ControlState, what: str) -> None:
        await s.bus.publish(BusEvent(f"{what}.changed"))

    # ---- 账号 ----

    @r.get("/accounts")
    async def list_accounts(request: Request) -> list[dict[str, Any]]:
        s = _ctl(request)
        return [await account_view(s, a) for a in await s.db.accounts.list()]

    @r.post("/accounts", status_code=status.HTTP_201_CREATED)
    async def create_account(body: AccountCreate, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        app_id = body.app_id.strip()
        try:
            account = await s.db.accounts.create(
                platform=s.platform,
                app_id=app_id,
                display_name=body.display_name.strip() or app_id,
                transport=s.transport,
                intents=DEFAULT_INTENTS,
            )
        except IntegrityError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, "这个 AppID 已经添加过了") from e
        s.secrets.set_secret(_account_secret_key(account.platform, app_id), body.app_secret.strip())
        if body.auto_connect:
            await s.db.settings.put("account", account.id, "connection.auto_connect", True)
        await s.db.events.add("info", "account", f"添加机器人账号 {account.display_name}", account_id=account.id)
        await changed(s, "accounts")
        return await account_view(s, account)

    @r.patch("/accounts/{account_id}")
    async def update_account(account_id: int, body: AccountUpdate, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        account = await get_account(s, account_id)
        fields: dict[str, Any] = {}
        if body.display_name is not None:
            fields["display_name"] = body.display_name.strip() or account.app_id
        if body.enabled is not None:
            fields["enabled"] = int(body.enabled)
        if body.app_secret is not None or body.enabled is False:
            # 换了 AppSecret 等连接参数：断开并丢掉缓存的 token，下次启动用新的。
            await s.connections.forget(account_id)
        if body.app_secret is not None:
            s.secrets.set_secret(_account_secret_key(account.platform, account.app_id), body.app_secret.strip())
        if fields:
            account = await s.db.accounts.update(account_id, **fields)
        if body.auto_connect is not None:
            await s.db.settings.put("account", account_id, "connection.auto_connect", body.auto_connect)
        await changed(s, "accounts")
        return await account_view(s, account)

    @r.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_account(account_id: int, request: Request) -> None:
        s = _ctl(request)
        account = await get_account(s, account_id)
        await s.connections.forget(account_id)
        s.secrets.delete_secret(_account_secret_key(account.platform, account.app_id))
        await s.db.accounts.delete(account_id)
        await changed(s, "accounts")

    # ---- 连接 ----

    @r.get("/accounts/{account_id}/connection")
    async def connection_state(account_id: int, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        return (await account_view(s, await get_account(s, account_id)))["connection"]

    @r.post("/accounts/{account_id}/connection/start")
    async def connection_start(account_id: int, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        account = await get_account(s, account_id)
        if not account.enabled:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "账号已停用")
        if not s.secrets.get_secret(_account_secret_key(account.platform, account.app_id)):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先填写 AppSecret")
        try:
            await s.connections.start(account_id)
        except Exception as e:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"启动连接失败：{e}") from e
        return (await account_view(s, account))["connection"]

    @r.post("/accounts/{account_id}/connection/stop")
    async def connection_stop(account_id: int, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        account = await get_account(s, account_id)
        await s.connections.stop(account_id)
        return (await account_view(s, account))["connection"]

    # ---- 模型配置 ----

    async def profile_view(s: ControlState, profile: Any, default_id: int | None) -> dict[str, Any]:
        key = s.secrets.get_secret(profile.secret_ref)
        return {
            "id": profile.id,
            "name": profile.name,
            "provider": profile.provider,
            "model": profile.model,
            "system_prompt": profile.system_prompt,
            "params": json.loads(profile.params_json or "{}"),
            "api_key_set": bool(key),
            "api_key_hint": _hint(key),
            "is_default": profile.id == default_id,
        }

    async def default_profile_id(s: ControlState) -> int | None:
        configured = await s.db.settings.resolve("provider_profile_id")
        profiles = await s.db.profiles.list()
        ids = [p.id for p in profiles]
        if configured in ids:
            return configured
        return ids[0] if ids else None

    async def get_profile(s: ControlState, profile_id: int) -> Any:
        profile = await s.db.profiles.get(profile_id)
        if profile is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "模型配置不存在")
        return profile

    @r.get("/profiles")
    async def list_profiles(request: Request) -> list[dict[str, Any]]:
        s = _ctl(request)
        default_id = await default_profile_id(s)
        return [await profile_view(s, p, default_id) for p in await s.db.profiles.list()]

    @r.post("/profiles", status_code=status.HTTP_201_CREATED)
    async def create_profile(body: ProfileCreate, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        provider = body.provider or "claude"
        if provider not in _provider_names(request):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"没有这个提供商插件：{provider}")
        profile = await s.db.profiles.create(
            name=body.name, provider=provider, model=body.model, system_prompt=body.system_prompt, params=body.params
        )
        if body.api_key:
            s.secrets.set_secret(profile.secret_ref, body.api_key.strip())
        await changed(s, "profiles")
        return await profile_view(s, profile, await default_profile_id(s))

    @r.patch("/profiles/{profile_id}")
    async def update_profile(profile_id: int, body: ProfileUpdate, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        profile = await get_profile(s, profile_id)
        if body.api_key:
            s.secrets.set_secret(profile.secret_ref, body.api_key.strip())
        fields = body.model_dump(exclude_none=True, exclude={"api_key"})
        # 即使只换了 API Key 也更新 updated_at，提供商实例池据此重建客户端。
        profile = await s.db.profiles.update(profile_id, **fields)
        await changed(s, "profiles")
        return await profile_view(s, profile, await default_profile_id(s))

    @r.delete("/profiles/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_profile(profile_id: int, request: Request) -> None:
        s = _ctl(request)
        profile = await get_profile(s, profile_id)
        if len(await s.db.profiles.list()) <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "至少要保留一个模型配置")
        await s.providers.invalidate(profile_id)
        s.secrets.delete_secret(profile.secret_ref)
        await s.db.profiles.delete(profile_id)
        await changed(s, "profiles")

    # ---- 分层配置 ----

    @r.get("/settings")
    async def get_settings(request: Request, scope: str = "global", scope_id: int = 0) -> dict[str, Any]:
        s = _ctl(request)
        if scope not in SCOPES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"scope 只能是 {', '.join(SCOPES)}")
        return {"scope": scope, "scope_id": scope_id, "values": await s.db.settings.list(scope, scope_id), "defaults": DEFAULTS}

    @r.put("/settings")
    async def put_setting(body: SettingPut, request: Request) -> dict[str, Any]:
        s = _ctl(request)
        if body.scope not in SCOPES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"scope 只能是 {', '.join(SCOPES)}")
        if body.key not in DEFAULTS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"没有这个配置项：{body.key}")
        scope_id = 0 if body.scope == "global" else body.scope_id
        if body.value is None:
            await s.db.settings.remove(body.scope, scope_id, body.key)
        else:
            await s.db.settings.put(body.scope, scope_id, body.key, body.value)
        await changed(s, "settings")
        return {"scope": body.scope, "scope_id": scope_id, "values": await s.db.settings.list(body.scope, scope_id)}

    # ---- 运行事件和统计 ----

    @r.get("/system-events")
    async def system_events(request: Request, limit: int = 50, level: str | None = None) -> list[dict[str, Any]]:
        s = _ctl(request)
        return [
            {
                "id": e.id,
                "account_id": e.account_id,
                "ts": e.ts,
                "level": e.level,
                "category": e.category,
                "message": e.message,
                "data": json.loads(e.data_json) if e.data_json else None,
            }
            for e in await s.db.events.list(min(max(limit, 1), 500), level=level)
        ]

    @r.get("/usage/today")
    async def usage_today(request: Request) -> dict[str, Any]:
        """今天（本地日期）的问答次数、token 和费用，以及全局每日预算。"""
        s = _ctl(request)
        summary = await s.db.usage.summary_since(start_of_local_day_ms())
        budget = (await s.db.settings.list("global", 0)).get("budget.daily_usd")
        return {**summary, "budget_usd": budget}

    @r.post("/maintenance/run")
    async def run_maintenance(request: Request) -> dict[str, Any]:
        """立即执行一次每日维护：清理超期数据、备份数据库。"""
        return await _ctl(request).maintenance.run_once()

    @r.get("/stats")
    async def stats(request: Request) -> dict[str, Any]:
        s = _ctl(request)
        return {"inbox": await s.db.inbox.counts(), "outbox": await s.db.outbox.counts()}

    return r


def _provider_names(request: Request) -> list[str]:
    return request.app.state.provider_names
