"""P2 — Admin Operations（pagination / job history cleanup / audit / author applications）regression。"""
from fastapi.testclient import TestClient

from backend import db
from backend.main import app
from backend.tests.conftest import new_admin, new_author, new_user, upload_book


def _mk_job(author, status, job_type="audio_single"):
    """建立一本書 + 第一章，並產生指定狀態的 job + generation（job 可刪、generation 保留）。"""
    book = upload_book(author)
    row = db.get_book_row(book["id"])
    ch = db.list_chapters(row["id"])[0]
    gid = db.create_audio_generation({
        "book_id": row["id"], "chapter_id": ch["id"], "mode": "single", "source_text_hash": ch["text_hash"],
        "generation_key": f"k-{db.ts()}-{job_type}-{status}-{gid_n()}", "status": "queued",
    })
    jid = db.create_analysis_job(job_type, book_id=row["id"], chapter_id=ch["id"], analysis_id=None,
                                 provider={}, requested_by=1, payload={})
    db.update_generation_job(jid, status)
    return jid, gid


_gid_counter = {"n": 0}


def gid_n():
    _gid_counter["n"] += 1
    return _gid_counter["n"]


def test_jobs_pagination_and_cleanup_only_terminal():
    adm = new_admin()
    author = new_author("任務作者")
    j_success, g_success = _mk_job(author, "success")
    j_failed, g_failed = _mk_job(author, "failed")
    j_running, g_running = _mk_job(author, "running")

    r = adm.get("/api/admin/jobs", params={"page": 1, "page_size": 2})
    body = r.json()
    assert body["total"] >= 3
    assert len(body["items"]) == 2
    assert body["total_pages"] >= 2

    # 單筆清除：success → 可刪（job row 消失，audio_generation 保留）
    ok = adm.delete(f"/api/admin/jobs/{j_success}")
    assert ok.status_code == 200, ok.text
    assert db.get_generation_job(j_success) is None
    assert db.get_audio_generation(g_success) is not None  # 產物不刪

    # running → 不可刪
    deny = adm.delete(f"/api/admin/jobs/{j_running}")
    assert deny.status_code == 409
    assert db.get_generation_job(j_running) is not None

    # 批次清除 failed → 只刪 failed
    batch = adm.post("/api/admin/jobs/clear", json={"scope": "failed"})
    assert batch.status_code == 200, batch.text
    assert db.get_generation_job(j_failed) is None
    assert db.get_audio_generation(g_failed) is not None

    # 非法 scope → 400
    assert adm.post("/api/admin/jobs/clear", json={"scope": "running"}).status_code == 400
    assert adm.post("/api/admin/jobs/clear", json={"scope": "pending"}).status_code == 400


def test_audit_logs_pagination_filter_and_no_delete():
    adm = new_admin()
    admin_row = db.get_user_by_name(adm.username)
    db.add_audit_log(admin_row["id"], "create_tts_provider", "tts_provider", 1)
    db.add_audit_log(admin_row["id"], "set_user_status", "user", 1, {"status": "disabled"})
    r = adm.get("/api/admin/audit-logs", params={"page": 1, "page_size": 2})
    body = r.json()
    assert body["total"] >= 2
    assert len(body["items"]) == 2
    assert body["total_pages"] >= 1
    # 動作篩選
    ra = adm.get("/api/admin/audit-logs", params={"action": "set_user_status"})
    assert all("set_user_status" in (it.get("action") or "") for it in ra.json()["items"])
    # 日期範圍（當天）
    today = db.ts()[:10]
    rd = adm.get("/api/admin/audit-logs", params={"date_from": today, "date_to": today})
    assert rd.json()["total"] >= 2
    # 稽核紀錄不提供刪除（無 DELETE endpoint）
    assert adm.request("DELETE", "/api/admin/audit-logs").status_code == 405


def test_author_applications_pagination_and_status_filter():
    adm = new_admin()
    a = new_user("申請者A")
    a.post("/api/authors/apply", json={"penName": "筆名A", "bio": "想寫書", "rightsConfirmed": True})
    b = new_user("申請者B")
    b.post("/api/authors/apply", json={"penName": "筆名B", "bio": "也想寫書", "rightsConfirmed": True})
    r = adm.get("/api/admin/author-applications", params={"status": "pending"})
    body = r.json()
    assert body["total"] >= 2
    assert all(it["status"] == "pending" for it in body["items"])
    # 核准一筆後，approved filter 可見
    app_id = body["items"][0]["id"]
    assert adm.post(f"/api/admin/author-applications/{app_id}/approve").status_code == 200
    ra = adm.get("/api/admin/author-applications", params={"status": "approved"})
    assert any(it["id"] == app_id for it in ra.json()["items"])
    rp = adm.get("/api/admin/author-applications", params={"status": "pending", "page": 1, "page_size": 1})
    assert len(rp.json()["items"]) == 1
    assert rp.json()["total"] >= 1