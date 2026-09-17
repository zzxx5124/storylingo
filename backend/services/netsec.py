"""共用遠端 URL／SSRF 安全驗證 helpers（V4 審核過的安全檢查）。

供 TTS provider 與 AI provider 共用，避免各自維護一套私有位址判斷。
"""
import ipaddress
import socket
from urllib.parse import urlparse

from .. import settings


def is_private_host(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
        return address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError("provider 網域無法解析") from error
    return any(is_private_host(info[4][0]) for info in infos)


def validate_http_url(value: str, *, label: str = "provider URL") -> str:
    """驗證 provider base URL：合法 HTTP(S)、無帳密、production 強制 HTTPS 與禁止私有位址。"""
    value = (value or "").strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.username or parsed.password:
        raise ValueError(f"{label} 必須是合法的 HTTP(S) URL，且不可包含帳密")
    if settings.APP_ENV == "production" and parsed.scheme != "https":
        raise ValueError(f"production {label} 必須使用 HTTPS")
    if settings.APP_ENV == "production" and is_private_host(parsed.hostname or ""):
        raise ValueError(f"production {label} 不可指向本機或內部網路")
    return value
