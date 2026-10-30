"""出站代理：模型接口和 QQ 各自决定走不走代理，代理本身只有全局一份（network.* 配置项）。"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from urllib.parse import urlsplit


class ProxyMode(enum.StrEnum):
    NONE = "none"
    """直连，也不读系统代理和 HTTP_PROXY 等环境变量。"""
    SYSTEM = "system"
    """跟随系统代理：系统设置（Windows 注册表、macOS 网络设置）和 HTTP_PROXY / HTTPS_PROXY 等环境变量。"""
    CUSTOM = "custom"
    """用 network.proxy_url 指定的代理。"""


# httpx 只支持这几种，aiohttp 那边也都能用。socks5h 表示由代理解析域名。
PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")


@dataclass(frozen=True, slots=True)
class Proxy:
    mode: ProxyMode = ProxyMode.NONE
    url: str | None = None
    """mode 为 custom 时的代理地址，已经过 normalize_proxy_url 校验。"""

    @property
    def is_socks(self) -> bool:
        return self.mode == ProxyMode.CUSTOM and bool(self.url) and self.url.startswith("socks")


DIRECT = Proxy()


def normalize_proxy_url(url: str) -> str:
    """校验代理地址，返回去掉首尾空白和末尾斜杠的写法；不合法时抛 ValueError，说明给用户看。

    不支持带用户名和密码的代理：密码只能存系统密钥库，不能跟着配置项写进数据库。
    """
    url = url.strip().rstrip("/")
    if "://" not in url:
        raise ValueError("代理地址要带协议，比如 http://127.0.0.1:7890 或 socks5://127.0.0.1:7891")
    parts = urlsplit(url)
    if parts.scheme not in PROXY_SCHEMES:
        raise ValueError(f"不支持 {parts.scheme}:// 代理，只支持 {'、'.join(s + '://' for s in PROXY_SCHEMES)}")
    if parts.username or parts.password:
        raise ValueError("暂不支持带用户名和密码的代理")
    if not parts.hostname:
        raise ValueError("代理地址缺少主机名")
    try:
        port = parts.port
    except ValueError as e:
        raise ValueError("代理端口不对") from e
    if port is None:
        raise ValueError("代理地址缺少端口，比如 http://127.0.0.1:7890")
    if parts.path or parts.query or parts.fragment:
        raise ValueError("代理地址只写协议、主机和端口，比如 http://127.0.0.1:7890")
    return url
