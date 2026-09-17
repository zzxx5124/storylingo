"""Release identity and cache policy regression coverage."""

from backend import settings


def test_shell_static_and_api_cache_contract(client):
    shell = client.get("/")
    assert shell.status_code == 200
    assert shell.headers["cache-control"] == "no-cache, must-revalidate"
    assert shell.headers["x-storylingo-release"] == settings.RELEASE_ID
    assert shell.headers["cloudflare-cdn-cache-control"] == "no-store"
    assert shell.headers["cdn-cache-control"] == "no-store"

    versioned = client.get("/app.js?v=39")
    assert versioned.status_code == 200
    assert versioned.headers["cache-control"] == "public, max-age=31536000, immutable"

    unversioned = client.get("/app.js")
    assert unversioned.status_code == 200
    assert unversioned.headers["cache-control"] == "public, max-age=0, must-revalidate"

    worker = client.get("/sw.js?v=16")
    assert worker.status_code == 200
    assert worker.headers["cache-control"] == "no-cache, must-revalidate"
    assert worker.headers["cloudflare-cdn-cache-control"] == "no-store"

    live = client.get("/api/health/live")
    assert live.status_code == 200
    assert live.headers["cache-control"] == "private, no-store"
    assert live.json()["releaseId"] == settings.RELEASE_ID


def test_release_script_protects_dirty_work_and_does_not_clear_storage(client):
    script = client.get("/release.js?v=1")
    assert script.status_code == 200
    body = script.text
    assert "cache: \"no-store\"" in body
    assert "data-unsaved='true'" in body
    assert "localStorage.clear" not in body
    assert "sessionStorage.clear" not in body
