"""Backend-owned source segmentation and annotation boundary for analysis v4.

This module deliberately does not call an AI provider.  It creates stable source
spans and accepts only annotations that reference those spans; canonical text is
always sliced from the normalized source.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass

from .analysis_version_policy import CANONICAL_WRITE_VERSION, require_artifact


SCHEMA_VERSION = CANONICAL_WRITE_VERSION
NORMALIZATION_VERSION = "unicode-nfc-v1"
SEGMENTATION_VERSION = "paragraph-quote-sentence-v2"
MAX_SEGMENTS_PER_GROUP = 50
_OPEN_TO_CLOSE = {"「": "」", "『": "』", "“": "”", '"': '"'}
_CLOSES = set(_OPEN_TO_CLOSE.values())
_FORMATTING = frozenset(" \t\r\n\u00a0\u200b\u2028\u2029")
_TERMINAL_PUNCTUATION = frozenset("。！？!?；;：:、，,．.…")
_SENTENCE_PUNCTUATION = frozenset("。！？!?；;.…")
_LINE_BREAK = r"(?:\r\n|[\r\n\u2028\u2029])"
_PARAGRAPH_BREAK = re.compile(
    rf"{_LINE_BREAK}[ \t\u00a0\u200b]*{_LINE_BREAK}"
    rf"(?:[ \t\u00a0\u200b]*{_LINE_BREAK})*"
)
_CHARACTER_ID = re.compile(r"^char_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_GENERIC_SPEAKER_SURFACES = frozenset({
    "unknown", "speaker", "generic", "narrator", "說話者", "語者", "角色", "某人", "有人",
    "未解析語者", "待辨識語者", "未知", "旁白",
})
_SPEAKER_GENDERS = frozenset(("男", "女", "未知"))
_SPEAKER_AGES = frozenset(("兒童", "少年", "青年", "中年", "老年", "未知"))
_VOCAB_FIELDS = ("zh", "en", "spelling", "example", "level")


class SourceFaithfulnessError(ValueError):
    """The source span or annotation cannot be proven safe."""


@dataclass(frozen=True)
class SourceSpan:
    segment_id: str
    type: str
    source_start: int
    source_end: int
    span_hash: str
    text: str

    def as_dict(self) -> dict:
        return {
            "segment_id": self.segment_id,
            "type": self.type,
            "source_start": self.source_start,
            "source_end": self.source_end,
            "span_hash": self.span_hash,
            "text": self.text,
        }


def normalize_source(source: str) -> str:
    if not isinstance(source, str):
        raise SourceFaithfulnessError("source 必須是文字")
    return unicodedata.normalize("NFC", source)


def source_hash(source: str) -> str:
    return hashlib.sha256(normalize_source(source).encode("utf-8")).hexdigest()


def _span_id(source_digest: str, start: int, end: int, kind: str) -> str:
    seed = f"{SEGMENTATION_VERSION}:{source_digest}:{start}:{end}:{kind}"
    return "seg_" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def _make_span(source: str, start: int, end: int, kind: str, digest: str) -> SourceSpan:
    if end <= start:
        raise SourceFaithfulnessError("zero-length source span")
    text = source[start:end]
    if not text or text.isspace():
        raise SourceFaithfulnessError("formatting-only text cannot be a spoken span")
    span_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return SourceSpan(_span_id(digest, start, end, kind), kind, start, end, span_digest, text)


def _non_formatting_ranges(source: str) -> list[tuple[int, int]]:
    """取得段落範圍，不把一般空格當成正文間隔。

    舊實作把所有格式字元（包含 ASCII 空格）視為硬邊界，導致
    ``The sky is blue`` 被拆成每個單字一個朗讀片段。現在只有真正的空白行
    才是段落邊界；空格與單行換行會保留在來源範圍內，再於句子邊界切分。
    """
    ranges = []
    start = 0
    for separator in _PARAGRAPH_BREAK.finditer(source):
        if start < separator.start() and any(char not in _FORMATTING for char in source[start:separator.start()]):
            ranges.append((start, separator.start()))
        start = separator.end()
    if start < len(source) and any(char not in _FORMATTING for char in source[start:]):
        ranges.append((start, len(source)))
    return ranges


def _append_narration(spans: list[SourceSpan], source: str, start: int, end: int, digest: str) -> None:
    """加入來源擁有的旁白單位，並在保守的句末切分。"""
    cursor = start
    index = start
    while index < end:
        char = source[index]
        if char in _SENTENCE_PUNCTUATION:
            next_char = source[index + 1] if index + 1 < end else ""
            # 省略號或類似小數點的連續句點，不在前面的句點切開；
            # 最後一個終止標點仍會結束句子。
            if char == "." and next_char == ".":
                index += 1
                continue
            boundary = index + 1
            while boundary < end and source[boundary] in _SENTENCE_PUNCTUATION:
                boundary += 1
            following = source[boundary] if boundary < end else ""
            if not following or following in _FORMATTING or char != ".":
                left = cursor
                while left < boundary and source[left] in _FORMATTING:
                    left += 1
                right = boundary
                while right > left and source[right - 1] in _FORMATTING:
                    right -= 1
                if left < right:
                    spans.append(_make_span(source, left, right, "narration", digest))
                cursor = boundary
                index = boundary
                continue
        index += 1
    left = cursor
    while left < end and source[left] in _FORMATTING:
        left += 1
    right = end
    while right > left and source[right - 1] in _FORMATTING:
        right -= 1
    if left < right:
        spans.append(_make_span(source, left, right, "narration", digest))


def _split_paragraph(source: str, start: int, end: int, digest: str) -> list[SourceSpan]:
    spans = []
    cursor = start
    quote_start = None
    expected_close = None
    index = start
    while index < end:
        char = source[index]
        if quote_start is None and char in _OPEN_TO_CLOSE:
            _append_narration(spans, source, cursor, index, digest)
            # Advance the narration cursor even when the quote is unpaired.
            # Otherwise the final unpaired-quote branch would emit the same
            # prefix a second time, creating duplicate source spans.
            cursor = index
            quote_start = index
            expected_close = _OPEN_TO_CLOSE[char]
        elif quote_start is not None and char == expected_close:
            # 緊跟在對話引號外的終止標點屬於同一個朗讀單位，避免產生
            # 只有「。」或「！」的孤立旁白 span；source ownership 仍由
            # backend slice 保證，沒有忽略任何正文文字。
            dialogue_end = index + 1
            while dialogue_end < end and source[dialogue_end] in _TERMINAL_PUNCTUATION:
                dialogue_end += 1
            spans.append(_make_span(source, quote_start, dialogue_end, "dialogue", digest))
            cursor = dialogue_end
            quote_start = None
            expected_close = None
            index = dialogue_end - 1
        index += 1
    if quote_start is not None:
        # Unpaired quotation is retained as a source-owned dialogue block.
        _append_narration(spans, source, cursor, quote_start, digest)
        spans.append(_make_span(source, quote_start, end, "dialogue", digest))
        cursor = end
    _append_narration(spans, source, cursor, end, digest)
    return spans


def segment_source(source: str) -> dict:
    """Build deterministic, source-owned spans and explicit formatting gaps."""
    normalized = normalize_source(source)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    spans: list[SourceSpan] = []
    for start, end in _non_formatting_ranges(normalized):
        spans.extend(_split_paragraph(normalized, start, end, digest))
    validate_spans(normalized, [span.as_dict() for span in spans])
    covered = {(span.source_start, span.source_end) for span in spans}
    ignored = []
    cursor = 0
    for start, end in sorted(covered):
        if cursor < start:
            gap = normalized[cursor:start]
            if any(char not in _FORMATTING for char in gap):
                raise SourceFaithfulnessError("正文 gap 不可忽略")
            ignored.append({"source_start": cursor, "source_end": start, "kind": "ignored_gap"})
        cursor = end
    if cursor < len(normalized):
        gap = normalized[cursor:]
        if any(char not in _FORMATTING for char in gap):
            raise SourceFaithfulnessError("正文 gap 不可忽略")
        ignored.append({"source_start": cursor, "source_end": len(normalized), "kind": "ignored_gap"})
    return {
        "schemaVersion": SCHEMA_VERSION,
        "sourceHash": digest,
        "sourceLength": len(normalized),
        "normalizationVersion": NORMALIZATION_VERSION,
        "segmentationVersion": SEGMENTATION_VERSION,
        "segments": [span.as_dict() for span in spans],
        "ignoredGaps": ignored,
    }


def validate_spans(source: str, spans: list[dict]) -> None:
    normalized = normalize_source(source)
    ordered = sorted(spans, key=lambda item: (item.get("source_start", -1), item.get("source_end", -1)))
    if not ordered:
        if any(char not in _FORMATTING for char in normalized):
            raise SourceFaithfulnessError("正文未被任何 source span 覆蓋")
        return
    previous_end = 0
    seen_ids = set()
    seen_ranges = set()
    for span in ordered:
        start, end = span.get("source_start"), span.get("source_end")
        if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end > len(normalized) or end <= start:
            raise SourceFaithfulnessError("invalid source span")
        if span.get("segment_id") in seen_ids or (start, end) in seen_ranges:
            raise SourceFaithfulnessError("duplicate source span")
        if start < previous_end:
            raise SourceFaithfulnessError("overlapping source spans")
        text = normalized[start:end]
        if not text or text.isspace():
            raise SourceFaithfulnessError("formatting-only span")
        if "text" in span and span["text"] != text:
            raise SourceFaithfulnessError("canonical text is not a source slice")
        expected_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if span.get("span_hash") and span["span_hash"] != expected_hash:
            raise SourceFaithfulnessError("span hash mismatch")
        seen_ids.add(span.get("segment_id"))
        seen_ranges.add((start, end))
        previous_end = end
    leading = normalized[:ordered[0]["source_start"]]
    trailing = normalized[ordered[-1]["source_end"]:]
    if any(char not in _FORMATTING for char in leading + trailing):
        raise SourceFaithfulnessError("non-formatting source boundary gap")
    for start, end in zip(ordered, ordered[1:]):
        gap = normalized[start["source_end"]:end["source_start"]]
        if any(char not in _FORMATTING for char in gap):
            raise SourceFaithfulnessError("non-formatting source gap")


def validate_child_span(parent: dict, child: dict) -> None:
    parent_start, parent_end = parent["source_start"], parent["source_end"]
    start, end = child.get("relative_start"), child.get("relative_end")
    if not isinstance(start, int) or not isinstance(end, int) or end <= start:
        raise SourceFaithfulnessError("invalid child span")
    if start < 0 or end > parent_end - parent_start:
        raise SourceFaithfulnessError("child span outside parent")


def build_annotation_request(segmentation: dict, *, context: str = "") -> dict:
    """Return an annotation-only request shape; no canonical text is accepted."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "segmentationVersion": segmentation["segmentationVersion"],
        "sourceHash": segmentation["sourceHash"],
        "context": context,
        "segments": [
            {"segment_id": span["segment_id"], "type": span["type"]}
            for span in segmentation.get("segments", [])
        ],
    }


def group_segments(segmentation: dict, *, max_chars: int = 6000,
                   max_segments: int = MAX_SEGMENTS_PER_GROUP) -> list[dict]:
    """Group whole deterministic spans without splitting or duplicating them."""
    if max_chars <= 0:
        raise SourceFaithfulnessError("max_chars 必須為正數")
    if max_segments <= 0:
        raise SourceFaithfulnessError("max_segments 必須為正數")
    groups: list[dict] = []
    current: list[dict] = []
    current_chars = 0
    for span in segmentation.get("segments", []):
        size = span["source_end"] - span["source_start"]
        if current and (current_chars + size > max_chars or len(current) >= max_segments):
            groups.append({"chunk_index": len(groups), "segments": current})
            current = []
            current_chars = 0
        current.append(span)
        current_chars += size
    if current:
        groups.append({"chunk_index": len(groups), "segments": current})
    return groups


def apply_annotations(segmentation: dict, annotations: list[dict], *, source: str) -> dict:
    normalized = normalize_source(source)
    if source_hash(normalized) != segmentation.get("sourceHash"):
        raise SourceFaithfulnessError("source changed after segmentation")
    spans = {span["segment_id"]: span for span in segmentation.get("segments", [])}
    annotation_ids = [item.get("segment_id") for item in annotations]
    if len(annotation_ids) != len(set(annotation_ids)):
        raise SourceFaithfulnessError("duplicate segment annotation")
    if set(annotation_ids) != set(spans):
        raise SourceFaithfulnessError("missing or unexpected segment annotation")
    result = []
    characters: dict[str, dict] = {}
    learning_vocab = []
    learning_keys = set()
    for annotation in annotations:
        segment_id = annotation.get("segment_id")
        if segment_id not in spans:
            raise SourceFaithfulnessError("invalid segment_id")
        if "text" in annotation:
            raise SourceFaithfulnessError("AI cannot provide canonical text")
        if annotation.get("type") not in ("narration", "dialogue"):
            raise SourceFaithfulnessError("invalid annotation type")
        span = spans[segment_id]
        item = dict(span)
        item.update({
            key: value
            for key, value in annotation.items()
            if key not in {"segment_id", "type", "vocab"}
        })
        # Segment type is source-owned.  The model may annotate it for
        # transport/schema purposes, but it must never be able to turn a
        # deterministic dialogue span into narration (or vice versa).
        item["type"] = span["type"]
        if item.get("type") == "narration":
            item.setdefault("speaker_id", "speaker:narrator")
        else:
            item.setdefault("speaker_id", "speaker:unresolved")
            if item["speaker_id"] == "speaker:unresolved":
                surface = item.get("speaker_surface") or item.get("speaker") or item.get("speaker_candidate")
                if isinstance(surface, str) and surface.strip():
                    surface = surface.strip()
                    if surface.casefold() not in _GENERIC_SPEAKER_SURFACES:
                        provisional_id = "char_" + hashlib.sha256(
                            surface.casefold().encode("utf-8")
                        ).hexdigest()[:20]
                        item["speaker_id"] = provisional_id
                        item["speaker_surface"] = surface
        item["text"] = normalized[span["source_start"]:span["source_end"]]
        speaker_id = item.get("speaker_id")
        if speaker_id not in ("speaker:narrator", "speaker:unresolved", None):
            surface = item.get("speaker_surface") or item.get("speaker") or item.get("speaker_candidate") or speaker_id
            gender = str(item.get("speaker_gender") or "未知").strip()
            age = str(item.get("speaker_age") or "未知").strip()
            if gender not in _SPEAKER_GENDERS:
                gender = "未知"
            if age not in _SPEAKER_AGES:
                age = "未知"
            character = characters.setdefault(speaker_id, {
                "character_id": speaker_id,
                "canonical_name": str(surface),
                "aliases": [], "gender": gender, "age_group": age,
            })
            if character.get("gender") == "未知" and gender != "未知":
                character["gender"] = gender
            if character.get("age_group") == "未知" and age != "未知":
                character["age_group"] = age
        vocab = annotation.get("vocab")
        if vocab is not None:
            if not isinstance(vocab, dict) or any(
                    not isinstance(vocab.get(key), str) or not vocab[key].strip()
                    for key in _VOCAB_FIELDS):
                raise SourceFaithfulnessError("invalid vocabulary annotation")
            vocab_item = {"segment_id": segment_id}
            vocab_item.update({key: vocab[key].strip() for key in _VOCAB_FIELDS})
            vocab_key = (vocab_item["zh"].casefold(), vocab_item["en"].casefold())
            if vocab_key not in learning_keys:
                learning_keys.add(vocab_key)
                learning_vocab.append(vocab_item)
        result.append(item)
    artifact = {
        "schemaVersion": SCHEMA_VERSION,
        "sourceTextHash": segmentation["sourceHash"],
        "sourceLength": len(normalized),
        "normalizationVersion": segmentation["normalizationVersion"],
        "segmentationVersion": segmentation["segmentationVersion"],
        "segments": result,
        "characters": list(characters.values()),
        "ignoredGaps": segmentation.get("ignoredGaps", []),
    }
    if learning_vocab:
        artifact["learning"] = {"vocab": learning_vocab}
    return artifact


def validate_v4_artifact(artifact: dict, source: str) -> None:
    """Validate a source-owned v4 artifact immediately before ready save."""
    if not isinstance(artifact, dict) or artifact.get("schemaVersion") != SCHEMA_VERSION:
        raise SourceFaithfulnessError("不是 schemaVersion 4 artifact")
    normalized = normalize_source(source)
    if source_hash(normalized) != artifact.get("sourceTextHash"):
        raise SourceFaithfulnessError("v4 artifact source hash 不一致")
    segments = artifact.get("segments")
    if not isinstance(segments, list) or not segments:
        raise SourceFaithfulnessError("v4 artifact 缺少 segments")
    validate_spans(normalized, segments)
    ids = set()
    for segment in segments:
        segment_id = segment.get("segment_id")
        if not segment_id or segment_id in ids:
            raise SourceFaithfulnessError("v4 segment_id 重複或缺失")
        ids.add(segment_id)
        if segment.get("type") not in ("narration", "dialogue"):
            raise SourceFaithfulnessError("v4 segment type 不合法")
        if not segment.get("speaker_id"):
            raise SourceFaithfulnessError("v4 segment 缺少 speaker_id")
        speaker_id = segment["speaker_id"]
        if speaker_id not in ("speaker:narrator", "speaker:unresolved") and not _CHARACTER_ID.fullmatch(str(speaker_id)):
            raise SourceFaithfulnessError("v4 speaker_id namespace 不合法")
        if segment.get("text") != normalized[segment["source_start"]:segment["source_end"]]:
            raise SourceFaithfulnessError("v4 canonical text 必須來自 source slice")
    learning = artifact.get("learning")
    if learning is not None:
        if not isinstance(learning, dict) or not isinstance(learning.get("vocab", []), list):
            raise SourceFaithfulnessError("v4 learning vocab shape 不合法")
        segment_ids = {segment.get("segment_id") for segment in segments}
        seen_vocab = set()
        for item in learning["vocab"]:
            if not isinstance(item, dict) or item.get("segment_id") not in segment_ids:
                raise SourceFaithfulnessError("v4 learning vocab segment reference 不合法")
            if set(item) - {"segment_id", *_VOCAB_FIELDS}:
                raise SourceFaithfulnessError("v4 learning vocab 欄位不合法")
            if any(not isinstance(item.get(key), str) or not item[key].strip() for key in _VOCAB_FIELDS):
                raise SourceFaithfulnessError("v4 learning vocab 欄位缺失")
            key = (item["zh"].casefold(), item["en"].casefold())
            if key in seen_vocab:
                raise SourceFaithfulnessError("v4 learning vocab 重複")
            seen_vocab.add(key)


def read_compatible_artifact(artifact: dict) -> dict:
    """Read v3/v4 artifacts without rewriting the stored ready artifact."""
    try:
        _, mode = require_artifact(artifact)
    except ValueError as exc:
        raise SourceFaithfulnessError(str(exc)) from exc
    if mode not in ("compatibility", "native"):
        raise SourceFaithfulnessError("unsupported analysis artifact version")
    return artifact
