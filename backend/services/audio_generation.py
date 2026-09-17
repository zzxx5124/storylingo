"""V4 audio_generations domain：effective mode/voice、deterministic key、active pointer。

- effective_mode / effective_voice_id：章節覆寫優先於書級設定。
- generation_key：由所有會影響輸出的輸入決定性推導（JSON 正規化 + sha256）。
- 同一 key 的 queued/running/ready generation 直接重用，不重複消費 TTS。
- 只有 ready 且與章節目前文字相符的 generation 才可設為 active pointer。
- artifact 以 generation identity 儲存（traversal-safe）。
"""
import hashlib
import json
import uuid

from .. import db, settings
from .. import storage
from .. import v4_contracts as c


# 會影響逐段語速、聲線切換邊界與最終音訊的 renderer profile。
# 改變此值必須讓既有 generation key 失效，避免重用舊音訊。
RENDER_PROFILE_VERSION = "tts-render-v5"


class GenerationConflictError(ValueError):
    """同 key 已有進行中的 generation。"""


def effective_mode(book: dict, chapter: dict) -> str:
    """章節 audio_mode_override 優先；否則書級 audio_mode；預設 single。"""
    override = chapter.get("audio_mode_override")
    if override in c.AUDIO_MODES:
        return override
    mode = book.get("audio_mode") or c.AUDIO_MODE_SINGLE
    return mode if mode in c.AUDIO_MODES else c.AUDIO_MODE_SINGLE


def effective_voice_id(book: dict, chapter: dict) -> str | None:
    """章節 voice_override_id 優先；否則書級 default_voice_id。"""
    override = chapter.get("voice_override_id")
    if override:
        return override
    return book.get("default_voice_id") or None


def _stable_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_generation_key(*, book: dict, chapter: dict, mode: str, voice_id: str | None,
                           source_text_hash: str, analysis_id: int | None,
                           tts_provider: dict | None, emotion_policy: str,
                           character_registry_revision: int | None = None,
                           requested_emotion: str | None = None,
                           intensity: float | None = None,
                           tone: str | None = None,
                           speaking_style: str | None = None,
                           profile_id: str | None = None,
                           profile_version: int | None = None,
                           capability_snapshot_version: int | None = None,
                           capability_hash: str | None = None,
                           expressive_snapshot_hash: str | None = None,
                           regeneration_token: str | None = None) -> str:
    """由所有會影響輸出的輸入決定性推導 generation_key。"""
    voice_snapshot = effective_voice_id(book, chapter) if voice_id is None else voice_id
    payload = {
        "bookId": book["id"],
        "chapterKey": chapter["chapter_key"],
        "mode": mode,
        "voiceId": voice_snapshot,
        "sourceTextHash": source_text_hash,
        "analysisId": analysis_id,
        "characterRegistryRevision": character_registry_revision,
        "audioSettingsVersion": book.get("audio_settings_version") or 1,
        "renderProfileVersion": RENDER_PROFILE_VERSION,
        "ttsProviderId": tts_provider.get("id") if tts_provider else None,
        "ttsConfigVersion": tts_provider.get("config_version") if tts_provider else None,
        "adapterKey": tts_provider.get("adapter_key") if tts_provider else None,
        "emotionPolicy": emotion_policy,
        "requestedEmotion": requested_emotion,
        "intensity": intensity,
        "tone": tone,
        "speakingStyle": speaking_style,
        "profileId": profile_id,
        "profileVersion": profile_version,
        "capabilitySnapshotVersion": capability_snapshot_version,
        "capabilityHash": capability_hash or (tts_provider.get("capabilities_hash") if tts_provider else None),
        "expressiveSnapshotHash": expressive_snapshot_hash,
        "regenerationToken": regeneration_token,
    }
    return hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()


def create_or_reuse_generation(*, book: dict, chapter: dict, source_text_hash: str,
                               mode: str | None = None, voice_id: str | None = None,
                               analysis_id: int | None = None, tts_provider: dict | None = None,
                               emotion_policy: str = c.EMOTION_POLICY_BEST_EFFORT,
                               requested_by: int | None = None,
                               expressive_snapshot: dict | None = None,
                               force_new: bool = False) -> dict:
    """建立或重用 generation。回傳 (record, created: bool)。"""
    if emotion_policy not in c.EMOTION_POLICIES:
        raise ValueError("不支援的 emotion policy")
    mode = mode or effective_mode(book, chapter)
    voice_id = effective_voice_id(book, chapter) if voice_id is None else voice_id
    registry_meta = db.query_one("SELECT revision FROM character_registry_meta WHERE book_id=?", (book["id"],))
    character_registry_revision = int(registry_meta["revision"]) if registry_meta else 0
    key_args = dict(
        book=book, chapter=chapter, mode=mode, voice_id=voice_id,
        source_text_hash=source_text_hash, analysis_id=analysis_id,
        tts_provider=tts_provider, emotion_policy=emotion_policy,
        character_registry_revision=character_registry_revision,
        requested_emotion=(expressive_snapshot or {}).get("requestedEmotion"),
        intensity=(expressive_snapshot or {}).get("intensity"),
        tone=(expressive_snapshot or {}).get("tone"),
        speaking_style=(expressive_snapshot or {}).get("speakingStyle"),
        profile_id=(expressive_snapshot or {}).get("profileId"),
        profile_version=(expressive_snapshot or {}).get("profileVersion"),
        capability_snapshot_version=(expressive_snapshot or {}).get("capabilitySnapshotVersion"),
        capability_hash=(expressive_snapshot or {}).get("capabilityHash"),
        expressive_snapshot_hash=(expressive_snapshot or {}).get("snapshotHash"),
    )
    base_key = compute_generation_key(**key_args)
    # 進行中的 generation 永遠重用，避免連點建立重複工作。
    existing = db.get_audio_generation_by_key(base_key)
    if existing and existing["status"] in ("queued", "running"):
        return existing, False
    if existing and existing["status"] == "ready" and not force_new:
        return existing, False
    # force_new 僅代表作者明確要求重生成；以一次性 token 建立新的 generation，
    # 不影響普通呼叫的 deterministic reuse，也不會讓 in-flight job 分叉。
    # failed generation 不能以相同 unique key 靜默阻塞下一次重試；
    # 明確重生成與 failed retry 都建立一次性 identity，ready/queued/running
    # 仍維持上方的 deterministic reuse 行為。
    needs_new_identity = force_new or bool(existing and existing["status"] == "failed")
    key = compute_generation_key(
        **key_args,
        regeneration_token=uuid.uuid4().hex if needs_new_identity else None,
    )
    generation_id = db.create_audio_generation({
        "book_id": book["id"], "chapter_id": chapter["id"], "mode": mode,
        "source_text_hash": source_text_hash, "analysis_id": analysis_id,
        "character_registry_revision": character_registry_revision,
        "voice_snapshot_json": _stable_json({"voiceId": voice_id}),
        "tts_profile_snapshot_json": _stable_json(expressive_snapshot or {}),
        "generation_key": key, "status": "queued",
        "tts_provider_id": tts_provider.get("id") if tts_provider else None,
        "provider_config_version": tts_provider.get("config_version") if tts_provider else None,
        "adapter_key": tts_provider.get("adapter_key") if tts_provider else None,
        "emotion_policy": emotion_policy, "requested_by": requested_by,
    })
    return db.get_audio_generation(generation_id), True


def resolve_active_generation(book: dict, chapter: dict) -> dict | None:
    """回傳章節 active pointer 指向的 generation（僅當仍 ready、文字相符且 mode 相符）。"""
    aid = chapter.get("active_audio_generation_id")
    if not aid:
        return None
    gen = db.get_audio_generation(aid)
    if not gen or gen["status"] != "ready":
        return None
    if gen["chapter_id"] != chapter["id"] or gen["source_text_hash"] != chapter["text_hash"]:
        return None
    if gen["mode"] != effective_mode(book, chapter):
        return None
    meta = db.query_one("SELECT revision FROM character_registry_meta WHERE book_id=?", (book["id"],))
    if meta and gen.get("character_registry_revision") is not None \
            and int(gen["character_registry_revision"]) != int(meta["revision"]):
        return None
    return gen


def set_active_generation(book: dict, chapter: dict, generation_id: int | None) -> None:
    """更新 chapters.active_audio_generation_id；None 表示清除。"""
    db.update_chapter(book["id"], chapter["seq"], {"active_audio_generation_id": generation_id})


def mark_generation_ready_and_activate(*, book: dict, chapter: dict, generation_id: int,
                                       audio_path: str, timing_path: str,
                                       job_id: int | None = None, claim_token: str | None = None) -> dict:
    """標記 ready，並以 generation id 防止舊結果覆蓋較新 active output。"""
    gen = db.get_audio_generation(generation_id)
    if not gen:
        raise GenerationConflictError("找不到 generation")
    if gen["chapter_id"] != chapter["id"]:
        raise GenerationConflictError("generation 不屬於此章節")
    if job_id is not None:
        job = db.get_generation_job(job_id)
        if not job or job.get("status") != "running" or job.get("worker_claim_token") != claim_token:
            raise GenerationConflictError("generation job 已失去目前 worker ownership")
        if job.get("chapter_id") == chapter.get("id") and job.get("source_text_hash") \
                and job["source_text_hash"] != chapter.get("text_hash"):
            raise GenerationConflictError("source revision 已變更，generation 必須重建")
        if job.get("cancel_requested"):
            raise GenerationConflictError("generation job 已要求取消")
    current = db.get_chapter(chapter["book_id"], chapter["seq"]) if "book_id" in chapter else None
    if current is None:
        current = db.get_chapter(book["id"], chapter["seq"])
    if current and current["text_hash"] != gen["source_text_hash"]:
        db.mark_generation_failed(generation_id, "source revision 已變更，generation 必須重建")
        raise GenerationConflictError("source revision 已變更，generation 必須重建")
    # Keep the ready transition and active-pointer comparison in one short
    # SQLite transaction.  A slower older generation may finish after a
    # newer valid generation; it must remain historical and cannot win the
    # pointer race merely because it committed last.
    source_conflict = False
    with db.atomic() as con:
        current_row = con.execute(
            "SELECT * FROM chapters WHERE book_id=? AND seq=?", (book["id"], chapter["seq"])).fetchone()
        if not current_row or current_row["text_hash"] != gen["source_text_hash"]:
            con.execute(
                "UPDATE audio_generations SET status='failed', error=?, finished_at=? WHERE id=?",
                ("source revision 已變更，generation 必須重建", db.ts(), generation_id),
            )
            source_conflict = True
        else:
            meta = con.execute(
                "SELECT revision FROM character_registry_meta WHERE book_id=?", (book["id"],)).fetchone()
            if meta and gen.get("character_registry_revision") is not None \
                    and int(gen["character_registry_revision"]) != int(meta["revision"]):
                raise GenerationConflictError("character registry revision 已變更，generation 必須重建")
            con.execute(
                "UPDATE audio_generations SET status='ready', audio_path=?, timing_path=?, finished_at=?, error='' "
                "WHERE id=? AND status IN ('queued','running','ready')",
                (audio_path, timing_path, db.ts(), generation_id),
            )
            active_id = current_row["active_audio_generation_id"]
            active = con.execute(
                "SELECT id, created_at, status, source_text_hash, mode FROM audio_generations WHERE id=?",
                (active_id,)).fetchone() if active_id else None
            current_matches = gen["mode"] == effective_mode(book, chapter)
            active_is_newer = bool(active and active["status"] == "ready" and (
                int(active["id"]) > int(generation_id)
                or (active["created_at"] or "") > (gen["created_at"] or "")
            ))
            if current_matches and not active_is_newer:
                con.execute(
                    "UPDATE chapters SET active_audio_generation_id=?, updated_at=? WHERE id=?",
                    (generation_id, db.ts(), current_row["id"]),
                )
    if source_conflict:
        raise GenerationConflictError("source revision 已變更，generation 必須重建")
    gen = db.get_audio_generation(generation_id)
    return gen
