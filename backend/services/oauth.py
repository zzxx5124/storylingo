"""OIDC Authorization Code + PKCE boundary for Google and test providers."""
import base64
import hashlib
import hmac
from urllib.parse import urlencode, urlsplit, urlunsplit
import secrets

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken

from .. import auth, db, settings

OAUTH_TRANSACTION_TTL = 600


class OAuthError(Exception):
    code = "OAUTH_FAILED"


class OAuthNotConfigured(OAuthError):
    code = "NOT_CONFIGURED"


class OAuthProviderError(OAuthError):
    code = "PROVIDER_ERROR"


class OAuthValidationError(OAuthError):
    code = "INVALID_IDENTITY"


def _digest(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(auth.derived_secret("oauth-transaction"))
    return Fernet(key)


def encrypt_code_verifier(verifier: str) -> str:
    return _fernet().encrypt(verifier.encode("utf-8")).decode("ascii")


def decrypt_code_verifier(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeError) as error:
        raise OAuthValidationError("OAuth transaction 無效") from error


def validate_return_path(value: str | None) -> str:
    raw = value.strip() if isinstance(value, str) else "/"
    if len(raw) > 200 or any(ord(ch) < 32 for ch in raw):
        return "/"
    parsed = urlsplit(raw)
    if parsed.scheme or parsed.netloc or not parsed.path.startswith("/") or raw.startswith("//"):
        return "/"
    return urlunsplit(("", "", parsed.path or "/", parsed.query, parsed.fragment))


def result_redirect(return_path: str, result: str) -> str:
    safe = validate_return_path(return_path)
    parsed = urlsplit(safe)
    query = parsed.query
    query = f"{query}&" if query else ""
    query += urlencode({"auth": result})
    return urlunsplit(("", "", parsed.path or "/", query, parsed.fragment))


class GoogleOIDCProvider:
    provider = "google"

    @property
    def configured(self) -> bool:
        return bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET and
                    settings.GOOGLE_REDIRECT_URI and settings.GOOGLE_ISSUER)

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        if not self.configured:
            raise OAuthNotConfigured("Google OAuth 尚未配置")
        params = {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{settings.GOOGLE_AUTHORIZATION_ENDPOINT}?{urlencode(params)}"

    def exchange_code(self, *, code: str, code_verifier: str) -> dict:
        if not self.configured:
            raise OAuthNotConfigured("Google OAuth 尚未配置")
        try:
            response = httpx.post(settings.GOOGLE_TOKEN_ENDPOINT, data={
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
                "grant_type": "authorization_code",
                "code_verifier": code_verifier,
            }, timeout=10)
            response.raise_for_status()
            data = response.json()
            if not data.get("id_token"):
                raise OAuthProviderError("OIDC provider 未回傳 id_token")
            return data
        except OAuthError:
            raise
        except Exception as error:
            raise OAuthProviderError("OIDC provider token exchange failed") from error

    def validate_id_token(self, id_token: str, *, nonce_digest: str) -> dict:
        if not self.configured:
            raise OAuthNotConfigured("Google OAuth 尚未配置")
        try:
            jwks = jwt.PyJWKClient(settings.GOOGLE_JWKS_URI)
            signing_key = jwks.get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=settings.GOOGLE_CLIENT_ID,
                issuer=settings.GOOGLE_ISSUER,
                options={"require": ["iss", "sub", "aud", "exp", "iat", "nonce"]},
            )
        except OAuthError:
            raise
        except Exception as error:
            raise OAuthValidationError("OIDC ID token 驗證失敗") from error
        issuer = str(claims.get("iss") or "").rstrip("/")
        if not hmac.compare_digest(issuer, settings.GOOGLE_ISSUER.rstrip("/")):
            raise OAuthValidationError("OIDC issuer 不正確")
        if not hmac.compare_digest(_digest(str(claims.get("nonce") or "")), nonce_digest):
            raise OAuthValidationError("OIDC nonce 不正確")
        subject = str(claims.get("sub") or "")
        email = db.normalize_email(str(claims.get("email") or ""))
        if not subject or len(subject) > 300 or not email or len(email) > 200 or "@" not in email:
            raise OAuthValidationError("OIDC identity 欄位不完整")
        aud = claims.get("aud")
        if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != settings.GOOGLE_CLIENT_ID:
            raise OAuthValidationError("OIDC authorized party 不正確")
        return {
            "provider": "google",
            "issuer": issuer,
            "subject": subject,
            "email": email,
            "email_verified": claims.get("email_verified") is True,
            "name": str(claims.get("name") or "")[:100],
        }


class FakeOIDCProvider:
    """Deterministic no-network OIDC harness used by integration tests."""

    provider = "google"
    configured = True

    def __init__(self):
        self.claims_by_code = {}
        self.authorization_calls = []
        self.expected_code_challenge = ""

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        self.authorization_calls.append({"state": state, "nonce": nonce, "code_challenge": code_challenge})
        self.expected_code_challenge = code_challenge
        return f"https://fake-oidc.invalid/authorize?{urlencode({'state': state})}"

    def exchange_code(self, *, code: str, code_verifier: str) -> dict:
        claims = self.claims_by_code.get(code)
        if not claims:
            raise OAuthProviderError("fake code invalid")
        actual_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        if actual_challenge != self.expected_code_challenge:
            raise OAuthValidationError("fake PKCE verifier invalid")
        return {"claims": dict(claims)}

    def validate_id_token(self, id_token: str, *, nonce_digest: str) -> dict:
        raise OAuthValidationError("fake provider expects claims exchange")


_provider_override = None


def get_google_provider():
    return _provider_override or GoogleOIDCProvider()


def google_availability() -> dict:
    """Return only the safe UX signal; never expose OAuth configuration values."""
    provider = get_google_provider()
    return {"available": bool(getattr(provider, "configured", False))}


def set_provider_for_tests(provider):
    global _provider_override
    _provider_override = provider


def begin_google(*, purpose: str, account_id: int | None, return_path: str, policy_ok: bool) -> str:
    provider = get_google_provider()
    if not getattr(provider, "configured", False):
        raise OAuthNotConfigured("Google OAuth 尚未配置")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
    safe_return = validate_return_path(return_path)
    db.create_oauth_transaction(
        state_digest=_digest(state), nonce_digest=_digest(nonce),
        code_verifier_cipher=encrypt_code_verifier(verifier), provider="google",
        purpose=purpose, account_id=account_id, return_path=safe_return,
        policy_ok=policy_ok,
        expires_at=(db._dt.datetime.now() + db._dt.timedelta(seconds=OAUTH_TRANSACTION_TTL)).isoformat(timespec="seconds"),
    )
    return provider.authorization_url(state=state, nonce=nonce, code_challenge=challenge)


def complete_google(*, state: str, code: str) -> tuple[dict, dict]:
    tx = db.consume_oauth_transaction(state_digest=_digest(state or ""))
    if not tx or tx.get("provider") != "google":
        raise OAuthValidationError("OAuth state 無效或已過期")
    verifier = decrypt_code_verifier(tx["code_verifier_cipher"])
    provider = get_google_provider()
    tokens = provider.exchange_code(code=code, code_verifier=verifier)
    if tokens.get("claims") is not None:
        claims = dict(tokens["claims"])
        nonce_ok = _digest(str(claims.get("nonce") or "")) == tx["nonce_digest"]
        if not nonce_ok:
            raise OAuthValidationError("OIDC nonce 不正確")
        claims.setdefault("provider", "google")
        claims.setdefault("issuer", settings.GOOGLE_ISSUER.rstrip("/"))
        claims.setdefault("email", db.normalize_email(claims.get("email") or ""))
        claims["email_verified"] = claims.get("email_verified") is True
    else:
        claims = provider.validate_id_token(tokens.get("id_token") or "", nonce_digest=tx["nonce_digest"])
    if claims.get("issuer", "").rstrip("/") != settings.GOOGLE_ISSUER.rstrip("/"):
        raise OAuthValidationError("OIDC issuer 不正確")
    return tx, claims
