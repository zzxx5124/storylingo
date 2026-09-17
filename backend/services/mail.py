"""Authentication mail boundary.

The token service owns token lifecycle; this module owns rendering and delivery.
Local/test delivery is deterministic and queryable. Production delivery uses a
secret-free-at-rest-in-app Microsoft Graph adapter when explicitly configured.
"""
from dataclasses import dataclass
import html as html_lib
import json
import logging
import os
import threading
from urllib.parse import parse_qs, quote, urlsplit

import httpx

from .. import settings
from .netsec import is_private_host

_log = logging.getLogger("auth.mail")
_EXTERNAL_BACKENDS = {"microsoft_graph", "microsoft365", "m365"}
_TEST_ONLY_HOST_SUFFIXES = (".test", ".invalid", ".localhost", ".local")


def _external_backend() -> bool:
    return (settings.AUTH_MAIL_BACKEND or "console").strip().lower() in _EXTERNAL_BACKENDS


def _safe_origin(value: str):
    """Return a normalized public HTTPS origin/path, or None for unsafe config."""
    try:
        parsed = urlsplit((value or "").strip().rstrip("/"))
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme.lower() != "https" or not parsed.netloc or not host:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    if host == "localhost" or host.endswith(_TEST_ONLY_HOST_SUFFIXES):
        return None
    try:
        if is_private_host(host):
            return None
    except (OSError, ValueError):
        # An externally delivered auth link must not depend on an unresolved or
        # otherwise unverifiable host being treated as public.
        return None
    return parsed.scheme.lower(), host, port or 443, parsed.path.rstrip("/")


def auth_base_url_is_safe() -> bool:
    """External auth mail requires an explicit public HTTPS application URL."""
    return _safe_origin(settings.AUTH_BASE_URL) is not None


def is_auth_link_safe(link: str, *, purpose: str) -> bool:
    """Validate a token link before any external mail request is attempted."""
    if not _external_backend():
        return True
    base = _safe_origin(settings.AUTH_BASE_URL)
    try:
        parsed = urlsplit(link or "")
    except ValueError:
        return False
    if base is None:
        return False
    try:
        link_port = parsed.port
    except ValueError:
        return False
    host = (parsed.hostname or "").rstrip(".").lower()
    if (parsed.scheme.lower(), host, link_port or 443) != base[:3]:
        return False
    if parsed.username or parsed.password or parsed.fragment:
        return False
    expected_path = {
        "email_verification": "/api/auth/verify-email",
        "password_reset": "/api/auth/reset-password",
    }.get(purpose)
    if not expected_path or parsed.path.rstrip("/") != f"{base[3]}{expected_path}":
        return False
    query = parse_qs(parsed.query, keep_blank_values=True)
    return set(query) == {"token"} and len(query["token"]) == 1 and bool(query["token"][0])


@dataclass(frozen=True)
class MailDeliveryResult:
    ok: bool
    code: str = "SENT"


def render_message(*, to: str, purpose: str, link: str) -> dict:
    """Build bounded Traditional Chinese auth mail content."""
    if purpose == "email_verification":
        subject = "驗證你的語閱 StoryLingo 電子郵件"
        intro = "請點擊以下連結完成電子郵件驗證："
        expiry = "此連結 24 小時內有效。"
    elif purpose == "password_reset":
        subject = "重設你的語閱 StoryLingo 密碼"
        intro = "請點擊以下連結設定新的登入密碼："
        expiry = "此連結 1 小時內有效。若不是你提出的請求，請忽略這封信。"
    else:
        subject = "語閱 StoryLingo 帳號通知"
        intro = "請依照以下連結完成帳號操作："
        expiry = "請儘快完成操作。"
    text = f"{intro}\n{link}\n\n{expiry}\n\n語閱 StoryLingo"
    html_link = html_lib.escape(link, quote=True)
    html = (
        f"<p>{intro}</p><p><a href=\"{html_link}\">完成操作</a></p>"
        f"<p>{expiry}</p><p>語閱 StoryLingo</p>"
    )
    return {"to": to, "purpose": purpose, "subject": subject, "text": text, "html": html, "link": link}


class MailAdapter:
    def send(self, *, to: str, purpose: str, link: str) -> MailDeliveryResult:
        raise NotImplementedError


class FakeMailAdapter(MailAdapter):
    """Deterministic, queryable, no-network mail harness for tests."""

    def __init__(self):
        self.messages = []
        self._lock = threading.Lock()

    def send(self, *, to: str, purpose: str, link: str) -> MailDeliveryResult:
        message = render_message(to=to, purpose=purpose, link=link)
        with self._lock:
            self.messages.append({**message, "messageId": f"fake-{len(self.messages) + 1:04d}"})
        return MailDeliveryResult(True)

    def clear(self):
        with self._lock:
            self.messages.clear()

    def latest(self, *, purpose: str | None = None, to: str | None = None):
        with self._lock:
            rows = list(self.messages)
        if purpose is not None:
            rows = [row for row in rows if row["purpose"] == purpose]
        if to is not None:
            rows = [row for row in rows if row["to"] == to]
        return rows[-1] if rows else None


class LocalMailAdapter(FakeMailAdapter):
    """Explicit local outbox alias; still has no network side effect."""


class ConsoleMailAdapter(MailAdapter):
    def send(self, *, to: str, purpose: str, link: str) -> MailDeliveryResult:
        # Never place a raw secret/token/link in application logs.
        _log.info("development mail queued: purpose=%s recipient=%s", purpose, to)
        return MailDeliveryResult(True)


class FileMailAdapter(MailAdapter):
    def __init__(self, path: str):
        self.path = path

    def send(self, *, to: str, purpose: str, link: str) -> MailDeliveryResult:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps({
                "to": to, "purpose": purpose, "configured": True,
            }, ensure_ascii=False) + "\n")
        return MailDeliveryResult(True)


class NotConfiguredMailAdapter(MailAdapter):
    def send(self, *, to: str, purpose: str, link: str) -> MailDeliveryResult:
        return MailDeliveryResult(False, "NOT_CONFIGURED")


class MicrosoftGraphMailAdapter(MailAdapter):
    """Microsoft 365 delivery through Graph app-only OAuth2."""

    def configured(self) -> bool:
        return all((
            settings.AUTH_MAIL_M365_TENANT_ID,
            settings.AUTH_MAIL_M365_CLIENT_ID,
            settings.AUTH_MAIL_M365_CLIENT_SECRET,
            settings.AUTH_MAIL_M365_MAILBOX,
            settings.AUTH_MAIL_FROM,
            settings.AUTH_MAIL_M365_GRAPH_BASE_URL,
        )) and auth_base_url_is_safe()

    def _token(self) -> str:
        if not self.configured():
            raise RuntimeError("NOT_CONFIGURED")
        token_url = (
            f"https://login.microsoftonline.com/"
            f"{quote(settings.AUTH_MAIL_M365_TENANT_ID, safe='')}/oauth2/v2.0/token"
        )
        response = httpx.post(
            token_url,
            data={
                "client_id": settings.AUTH_MAIL_M365_CLIENT_ID,
                "client_secret": settings.AUTH_MAIL_M365_CLIENT_SECRET,
                "scope": "https://graph.microsoft.com/.default",
                "grant_type": "client_credentials",
            },
            timeout=settings.AUTH_MAIL_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            raise RuntimeError("AUTH_FAILED")
        token = (response.json() or {}).get("access_token") or ""
        if not token:
            raise RuntimeError("AUTH_FAILED")
        return token

    def send(self, *, to: str, purpose: str, link: str) -> MailDeliveryResult:
        if not self.configured():
            return MailDeliveryResult(False, "NOT_CONFIGURED")
        if not is_auth_link_safe(link, purpose=purpose):
            return MailDeliveryResult(False, "INVALID_CONFIGURATION")
        message = render_message(to=to, purpose=purpose, link=link)
        try:
            access_token = self._token()
            mailbox = quote(settings.AUTH_MAIL_M365_MAILBOX, safe="@.")
            response = httpx.post(
                f"{settings.AUTH_MAIL_M365_GRAPH_BASE_URL}/users/{mailbox}/sendMail",
                headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                json={
                    "message": {
                        "subject": message["subject"],
                        "body": {"contentType": "HTML", "content": message["html"]},
                        "toRecipients": [{"emailAddress": {"address": message["to"]}}],
                        "from": {"emailAddress": {"address": settings.AUTH_MAIL_FROM,
                                                   "name": settings.AUTH_MAIL_FROM_NAME}},
                    },
                    "saveToSentItems": False,
                },
                timeout=settings.AUTH_MAIL_TIMEOUT_SECONDS,
            )
        except RuntimeError as error:
            code = str(error)
            return MailDeliveryResult(False, code if code in {"AUTH_FAILED", "NOT_CONFIGURED"} else "PROVIDER_ERROR")
        except httpx.TimeoutException:
            return MailDeliveryResult(False, "TIMEOUT")
        except httpx.HTTPError:
            return MailDeliveryResult(False, "NETWORK_TRANSIENT")
        except (ValueError, KeyError, TypeError):
            return MailDeliveryResult(False, "PROVIDER_ERROR")
        if response.status_code == 202:
            return MailDeliveryResult(True)
        if response.status_code == 429:
            return MailDeliveryResult(False, "RATE_LIMITED")
        if 400 <= response.status_code < 500:
            return MailDeliveryResult(False, "PROVIDER_REJECTED")
        return MailDeliveryResult(False, "PROVIDER_ERROR")


def configuration_status() -> dict:
    backend = (settings.AUTH_MAIL_BACKEND or "console").strip().lower()
    configured = backend in _EXTERNAL_BACKENDS and MicrosoftGraphMailAdapter().configured()
    return {
        "backend": backend,
        "configured": bool(configured),
        "fromAddress": settings.AUTH_MAIL_FROM,
        "fromName": settings.AUTH_MAIL_FROM_NAME,
        "testMode": backend in {"fake", "local"},
    }


_adapter = None
_fake_adapter = None


def get_mail_adapter() -> MailAdapter:
    global _adapter, _fake_adapter
    backend = (settings.AUTH_MAIL_BACKEND or "console").strip().lower()
    if settings.APP_ENV == "production" and backend not in {"microsoft_graph", "microsoft365", "m365"}:
        _adapter = NotConfiguredMailAdapter()
        return _adapter
    if backend == "fake":
        if _fake_adapter is None:
            _fake_adapter = FakeMailAdapter()
        return _fake_adapter
    if backend == "local":
        if _fake_adapter is None:
            _fake_adapter = LocalMailAdapter()
        return _fake_adapter
    if _adapter is not None:
        return _adapter
    if backend == "console":
        _adapter = ConsoleMailAdapter()
    elif backend == "file":
        _adapter = FileMailAdapter(os.path.join(settings.ROOT_DIR, "logs", "mail.outbox.jsonl"))
    elif backend in {"microsoft_graph", "microsoft365", "m365"}:
        provider = MicrosoftGraphMailAdapter()
        _adapter = provider if settings.APP_ENV != "production" or provider.configured() else NotConfiguredMailAdapter()
    else:
        _adapter = NotConfiguredMailAdapter()
    return _adapter


def get_fake_mail_adapter() -> FakeMailAdapter:
    global _fake_adapter
    if _fake_adapter is None:
        _fake_adapter = FakeMailAdapter()
    return _fake_adapter


def get_local_mail_adapter() -> LocalMailAdapter:
    global _fake_adapter
    if not isinstance(_fake_adapter, LocalMailAdapter):
        _fake_adapter = LocalMailAdapter()
    return _fake_adapter


def reset_mail_adapter():
    global _adapter, _fake_adapter
    _adapter = None
    _fake_adapter = None
