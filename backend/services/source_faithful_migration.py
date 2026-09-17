"""Incremental, non-destructive v3 → v4 source-faithful rollout helpers."""

from __future__ import annotations

import json
import os

from .. import db, settings, storage
from . import source_faithful


def dry_run_chapter(bid: str, seq: int) -> dict:
    """Plan deterministic v4 segmentation without changing DB or artifacts."""
    book = db.get_book_row(bid)
    if not book:
        raise ValueError("找不到 book")
    chapter = db.get_chapter(book["id"], seq)
    if not chapter:
        raise ValueError("找不到 chapter")
    record = db.get_ready_chapter_analysis(chapter["id"], chapter["text_hash"])
    segmentation = source_faithful.segment_source(chapter.get("text") or "")
    return {
        "bid": bid, "chapter": seq, "sourceHash": chapter["text_hash"],
        "existingReady": bool(record),
        "existingSchemaVersion": (record or {}).get("schema_version"),
        "segmentationVersion": segmentation["segmentationVersion"],
        "segmentCount": len(segmentation["segments"]),
        "wouldWrite": False,
        "wouldChangeActivePointer": False,
    }


def write_shadow_artifact(analysis_id: int, artifact: dict, source: str) -> str:
    """Write a validated v4 shadow file; never changes the active DB pointer."""
    record = db.get_chapter_analysis(analysis_id)
    if not record:
        raise ValueError("找不到 analysis")
    source_faithful.validate_v4_artifact(artifact, source)
    book = db.get_book_by_rowid(record["book_id"])
    if not book:
        raise ValueError("找不到 book")
    base = storage.analysis_artifact_path(book["bid"], analysis_id)
    relative = base + ".v4-shadow.json"
    absolute = os.path.join(settings.ROOT_DIR, relative)
    os.makedirs(os.path.dirname(absolute), exist_ok=True)
    temporary = absolute + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(artifact, handle, ensure_ascii=False, indent=2)
    os.replace(temporary, absolute)
    return relative


def promote_shadow_artifact(analysis_id: int, shadow_path: str, source: str) -> None:
    """Promote only an existing validated shadow after a current-source gate."""
    record = db.get_chapter_analysis(analysis_id)
    if not record:
        raise ValueError("找不到 analysis")
    absolute = os.path.join(settings.ROOT_DIR, shadow_path)
    with open(absolute, encoding="utf-8") as handle:
        artifact = json.load(handle)
    source_faithful.validate_v4_artifact(artifact, source)
    chapter = next((item for item in db.list_chapters(record["book_id"])
                    if item["id"] == record["chapter_id"]), None)
    if not chapter or chapter["text_hash"] != record["source_text_hash"]:
        raise ValueError("source hash changed; shadow remains unpromoted")
    db.update_chapter_analysis(analysis_id, {
        "schema_version": 4, "status": "ready", "artifact_path": shadow_path,
        "finished_at": db.ts(), "error": "",
    })


def rollback_to_v3(analysis_id: int, v3_artifact_path: str) -> None:
    """Restore a previous v3 pointer without deleting the v4 shadow."""
    record = db.get_chapter_analysis(analysis_id)
    if not record:
        raise ValueError("找不到 analysis")
    if not os.path.exists(os.path.join(settings.ROOT_DIR, v3_artifact_path)):
        raise ValueError("v3 artifact 不存在")
    db.update_chapter_analysis(analysis_id, {
        "schema_version": 3, "status": "ready", "artifact_path": v3_artifact_path,
        "finished_at": db.ts(), "error": "",
    })
