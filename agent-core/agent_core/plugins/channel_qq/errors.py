from __future__ import annotations


class QQApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        http_status: int | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.trace_id = trace_id

    def __str__(self) -> str:
        return f"{self.args[0]} (code={self.code}, http={self.http_status}, trace_id={self.trace_id})"


class QQAuthError(QQApiError):
    """换取 access_token 失败。11298 表示出口 IP 不在白名单，提示用户去控制台检查。"""


class MediaUnavailableError(Exception):
    """要发的图片取不到或不能用（链接打不开、太大、格式不对），重试也没用。"""


class FatalGatewayError(Exception):
    """重连也没用的连接错误，如机器人被封禁、没有 intents 权限。"""
