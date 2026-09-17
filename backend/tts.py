"""Edge TTS 逐語者生成 + ffmpeg 合併為章節音檔。"""
import asyncio
import inspect
import json
import os
import shutil
import subprocess
import tempfile
import uuid

from . import db, storage
from . import voices as voices_mod
from . import replacements
from . import settings
from .services import tts_provider
from .services import tts_partials
from .services.tts_text import split_text_for_synthesis

MAX_CONCURRENT = 4
_FFMPEG_CONCURRENCY = 6  # loudnorm 等每段 ffmpeg 的並行數（純 CPU，獨立檔案）
TTS_SYNTHESIS_UNIT_MAX_CHARS = 3000
TTS_UNIT_VERSION = "tts-unit-v1"
# Provider 的自然語速由 provider 自己決定；不要用字數估算去拉伸每一段，
# 否則短句會被放慢、長句會被加速，造成聽感與原始音檔不一致。
SPEED_FACTOR_MIN = 0.85
SPEED_FACTOR_MAX = 1.30
SPEED_NORMALIZATION_THRESHOLD = 0.05
BOUNDARY_FADE_IN_SECONDS = 0.015
BOUNDARY_FADE_OUT_SECONDS = 0.025
INTER_SEGMENT_SILENCE_SECONDS = 0.08

_NARRATOR_LABELS = frozenset(("旁白", "narrator", "speaker:narrator"))
_UNRESOLVED_LABELS = frozenset(("unknown", "未解析語者", "unresolved", "speaker:unresolved"))


def _tts_text(book: dict, text: str, is_en: bool = False) -> str:
    """套用發音校正：book.settings.ttsCorrect 開啟時，非英文朗讀文字先做文字替換。"""
    if is_en:
        return text
    if book.get("settings", {}).get("ttsCorrect"):
        text = replacements.apply(text)
    # 片段只含省略號、沒有中文字 → 無可朗讀內容，Edge 會無聲/出錯；改成「嗯……」
    if not any("\u4e00" <= ch <= "\u9fff" for ch in text) and "\u2026" in text:
        return "嗯……"
    return text


def _speakable(text: str) -> bool:
    """是否有可朗讀的字元（中文或 ASCII 字母/數字）。純標點/符號（不含中文字）回 False，
    這類片段跳過 TTS，避免合成器把「。」「～」等當內容產生異常/無聲。"""
    if not text or not text.strip():
        return False
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            return True
        if ch.isascii() and ch.isalnum():
            return True
    return False


def _narrator_voice(book: dict) -> str | None:
    """回傳產品資料中的 canonical narrator voice，不使用 legacy Edge fallback。"""
    voices = book.get("voices") or {}
    for key in ("旁白", "speaker:narrator", "narrator"):
        if voices.get(key):
            return voices[key]
    if settings.effective_category(book) == settings.CAT_EN and voices.get("_english"):
        return voices["_english"]
    return None


def _voice_for(book: dict, speaker: str | None, speaker_id: str | None = None) -> str | None:
    """以與作者 UI 相同的產品 mapping 解析聲線。

    narrator、unknown 與 unresolved 都回到已設定的旁白聲線；若沒有旁白
    mapping，回傳 None 讓 preflight 明確拒絕，不再送出淘汰的 Edge voice ID。
    """
    voices = book.get("voices") or {}
    if speaker_id in ("speaker:narrator", "speaker:unresolved") \
            or speaker in _NARRATOR_LABELS or speaker in _UNRESOLVED_LABELS \
            or not speaker:
        return _narrator_voice(book)
    v = voices.get(speaker)
    if v:
        return v
    if settings.effective_category(book) == settings.CAT_EN:
        # 純英文書：未指定角色沿用產品設定的英文教學聲線。
        return _english_voice_for(book)
    return _narrator_voice(book)


def _english_voice_for(book: dict) -> str | None:
    v = book.get("voices", {}).get("_english")
    return v or None


def _provider_concurrency(provider: dict | None) -> int:
    """Use provider-declared capacity; unknown providers stay serial by default."""
    if not provider or not provider.get("enabled"):
        return MAX_CONCURRENT
    configured = provider.get("max_concurrency")
    try:
        configured_limit = max(1, min(MAX_CONCURRENT, int(configured))) if configured is not None else None
    except (TypeError, ValueError):
        configured_limit = 1
    try:
        from .services import tts_adapter
        capabilities = tts_adapter.load_capabilities(provider)
        value = (capabilities.get("limits") or {}).get("maxConcurrent")
        capability_limit = max(1, min(MAX_CONCURRENT, int(value))) if value is not None else 1
        return min(configured_limit or capability_limit, capability_limit)
    except (TypeError, ValueError, AttributeError):
        return configured_limit or 1


def _expand_synthesis_jobs(jobs: list[tuple]) -> list[tuple]:
    """Split large units while preserving source order and canonical segment index."""
    expanded = []
    for name, text, voice, seg_idx, is_en, expression, segment_id in jobs:
        chunks = split_text_for_synthesis(text, TTS_SYNTHESIS_UNIT_MAX_CHARS)
        if len(chunks) <= 1:
            expanded.append((name, text, voice, seg_idx, is_en, expression, segment_id))
            continue
        stem, extension = os.path.splitext(name)
        for part_index, chunk in enumerate(chunks):
            expanded.append((
                f"{stem}-part-{part_index:04d}{extension}", chunk, voice, seg_idx,
                is_en, expression, f"{segment_id}::part-{part_index}",
            ))
    return expanded


async def _synth(text: str, voice: str, out: str, sem: asyncio.Semaphore, expression=None, segment_id=None,
                 language="zh-TW", provider: dict | None = None):
    async with sem:
        if not text.strip():
            return
        provider = provider or (db.get_tts_provider(settings.TTS_PROVIDER_ID) if settings.TTS_PROVIDER_ID else db.get_active_tts_provider())
        if provider and provider.get("enabled"):
            if provider.get("adapter_key") == "cosyvoice_http":
                from .services import tts_provider_v1
                expression = expression or {}
                request = {"request_id": f"segment-{uuid.uuid4().hex}", "text": text,
                           "voice_id": voice, "language": language,
                           "emotion": expression.get("emotion", "neutral"),
                           "intensity": expression.get("intensity", 0.5), "tone": expression.get("tone"),
                           "speaking_style": expression.get("speaking_style"), "segment_id": segment_id,
                           "mode": expression.get("mode", "best_effort")}
                return await asyncio.to_thread(tts_provider_v1.synthesize_to_file, provider, request, out)
            await asyncio.to_thread(tts_provider.synthesize_to_file, provider, text, voice, out)
            return
        if False:
            raise RuntimeError("目前尚未設定正式遠端 TTS provider")
        raise RuntimeError("目前沒有可用的 F5 語者或啟用中的遠端 TTS provider")


def _probe_duration(path: str) -> float:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


# 目標時長模型：d = BASE + A*字數 + B*標點數。
# 目標以固定中文字元速率為主，避免大 BASE 讓短句被拉慢、過高 per_char 讓長句被加速。
# 這組值依目前遠端中文 provider 的實測分布校準至約 4.5 字/秒。
# 中文：dur ≈ 0.04 + 0.22*chars + 0.02*punct
# 英文：dur ≈ 0.2 + 0.06*chars（英文標點項忽略）
_ZH = dict(base=0.04, per_char=0.22, per_punct=0.02)
_EN = dict(base=0.2, per_char=0.06, per_punct=0.0)
_F5 = dict(base=0.1, per_char=0.14, per_punct=0.0)
_PUNCT_RE = None  # 惰性初始化


def _count_punct(text: str) -> int:
    global _PUNCT_RE
    if _PUNCT_RE is None:
        import re as _re
        # 中文全形標點 + 常用半形標點
        _PUNCT_RE = _re.compile(r"[\u3001\u3002\uff0c\uff0e\uff01\uff1f\uff1b\uff1a"
                                r"\u2014\u2026\u201c\u201d\u300a\u300b"
                                r"\.\,!?;:(\)\-]")
    return len(_PUNCT_RE.findall(text))


def _expected_duration(text: str, is_en: bool, is_f5: bool = False) -> float:
    """依「BASE + 字數×每字 + 標點×每標點」估算目標時長。
    F5 用自己校準的模型，避免被 Edge 的 BASE 開銷拖慢。"""
    n = float(len(text.strip()))
    p = float(_count_punct(text))
    if is_f5:
        m = _F5
    else:
        m = _EN if is_en else _ZH
    return m["base"] + n * m["per_char"] + p * m["per_punct"]


def _match_duration(path: str, target: float) -> float:
    """以保守倍率把 provider 時長拉回共同語速帶，回傳實際新時長。

    ``target`` 只用於有限的 pacing normalization：倍率限制在 0.85–1.30，
    偏差小於 5% 不處理。這不改變朗讀文字，也不使用估算值取代 timing；
    timing 仍以處理後 WAV 的實際時長為準。
    """
    current = _probe_duration(path)
    if current <= 0 or target <= 0:
        return current
    factor = _bounded_speed_factor(current, target)
    if abs(factor - 1.0) < SPEED_NORMALIZATION_THRESHOLD:
        return current
    tmp = path + ".rate.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", path, "-af", f"atempo={factor:.4f}",
             "-c:a", "pcm_s16le", tmp],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, check=True,
        )
        os.replace(tmp, path)
        return current / factor
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return current


def _bounded_speed_factor(current: float, target: float) -> float:
    """回傳受保守範圍限制的 atempo 倍率，避免不同片段忽快忽慢。"""
    if current <= 0 or target <= 0:
        return 1.0
    return max(SPEED_FACTOR_MIN, min(SPEED_FACTOR_MAX, current / target))


def _normalize_loudness(mp3_path: str, wav_path: str):
    """把片段轉成 44.1k PCM WAV，統一音量（EBU R128 loudnorm）並裁掉開頭靜音。
    用 WAV 當中間格式可避免 mp3 逐段 encoder delay 造成邊界偏移、最後只編碼一次；
    裁開頭靜音讓每段語音在邊界處立即開始，字幕高亮才跟得上實際聲音。"""
    tmp = wav_path + ".tmp.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path,
             "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,"
                    "silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.1",
             "-ar", "44100", "-c:a", "pcm_s16le", tmp],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, check=True,
        )
    except Exception:
        # loudnorm 失敗仍要能合併：退而求其次做純轉換
        subprocess.run(
            ["ffmpeg", "-y", "-i", mp3_path, "-ar", "44100", "-c:a", "pcm_s16le", tmp],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, check=True,
        )
    os.replace(tmp, wav_path)


def _apply_boundary_fade(path: str, duration: float, fade_in: bool, fade_out: bool) -> None:
    """只在聲線切換邊界做極短淡入淡出，不重疊兩段語音。"""
    filters = []
    if fade_in:
        filters.append(f"afade=t=in:st=0:d={BOUNDARY_FADE_IN_SECONDS:.3f}")
    if fade_out and duration > BOUNDARY_FADE_OUT_SECONDS:
        start = max(0.0, duration - BOUNDARY_FADE_OUT_SECONDS)
        filters.append(f"afade=t=out:st={start:.3f}:d={BOUNDARY_FADE_OUT_SECONDS:.3f}")
    if not filters:
        return
    tmp = path + ".boundary.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-af", ",".join(filters), "-c:a", "pcm_s16le", tmp],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, check=True,
    )
    os.replace(tmp, path)


def _write_inter_segment_silence(path: str) -> None:
    """建立固定長度的無聲 WAV，供句子間加入短暫自然停頓。"""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
         "-t", f"{INTER_SEGMENT_SILENCE_SECONDS:.3f}", "-c:a", "pcm_s16le", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, check=True,
    )


def _build_jobs(book: dict, analysis: dict):
    """回傳 [(out_name, text, voice, seg_idx, is_en, expression, segment_id)]"""
    en_voice = _english_voice_for(book)
    is_en_book = settings.effective_category(book) == settings.CAT_EN
    jobs: list[tuple] = []
    n = 0
    for seg_idx, seg in enumerate(analysis.get("segments", [])):
        emotion = seg.get("emotion") if isinstance(seg.get("emotion"), dict) else {}
        expression = {"emotion": emotion.get("label") or "neutral", "intensity": emotion.get("intensity", 0.5),
                      "tone": seg.get("tone"), "speaking_style": seg.get("speaking_style"),
                      "mode": seg.get("emotionPolicy", "best_effort")}
        segment_id = seg.get("segment_id") or seg.get("id") or f"segment-{seg_idx}"
        if seg.get("type") == "vocab":
            speaker = seg.get("speaker", "旁白")
            zh_voice = _voice_for(book, speaker, seg.get("speaker_id"))
            for u in seg.get("utterances", []):
                is_en = u.get("lang") == "en"
                v = en_voice if is_en else zh_voice
                actual = _tts_text(book, u.get("text", ""), is_en)
                if not _speakable(actual):
                    continue
                jobs.append((f"{n:05d}.mp3", actual, v, seg_idx, is_en, expression, segment_id))
                n += 1
        elif seg.get("type") == "bilingual":
            # 雙語段：一段中文＋一段英文，都落在同一個 seg_idx（字幕對齊）
            speaker = seg.get("speaker", "旁白")
            zh_voice = _voice_for(book, speaker, seg.get("speaker_id"))
            zh = _tts_text(book, seg.get("zh", ""))
            en = _tts_text(book, seg.get("en", ""), True)
            if _speakable(zh):
                jobs.append((f"{n:05d}.mp3", zh, zh_voice, seg_idx, False, expression, segment_id))
                n += 1
            if _speakable(en):
                jobs.append((f"{n:05d}.mp3", en, en_voice, seg_idx, True, expression, segment_id))
                n += 1
        else:
            # 一律以語者聲線為優先（single 模式由 pipeline 注入「旁白」= defaultVoiceId；
            # multi 模式為各語者綁定）。未指定時才依書籍語言 fallback
            # （en → _english 聲線；zh → 預設旁白聲線）。
            v = _voice_for(book, seg.get("speaker", "旁白"), seg.get("speaker_id"))
            is_en = settings.effective_category(book) == settings.CAT_EN
            actual = _tts_text(book, seg.get("text", ""), is_en)
            if not _speakable(actual):
                continue
            jobs.append((f"{n:05d}.mp3", actual, v, seg_idx, is_en, expression, segment_id))
            n += 1
    return jobs


def required_voice_ids(book: dict, analysis: dict) -> set[str]:
    """回傳本次 worker 實際會使用的聲線 ID。

    生成 API 的 preflight 必須使用同一條解析路徑，包含未綁定語者的
    語言 fallback；否則未知 fallback 會繞過 preflight，直到 worker 才失敗。
    """
    return {job[2] for job in _build_jobs(book, analysis)}


def required_voice_usage(book: dict, analysis: dict) -> dict:
    """回傳 voice → 受影響 segment/role，供 preflight 產生可操作錯誤。"""
    usage = {}
    for index, segment in enumerate(analysis.get("segments", [])):
        speaker = segment.get("speaker")
        speaker_id = segment.get("speaker_id")
        voice = _voice_for(book, speaker, speaker_id)
        if speaker_id == "speaker:narrator" or speaker in _NARRATOR_LABELS or not speaker:
            role = "narrator"
        elif speaker_id == "speaker:unresolved" or speaker in _UNRESOLVED_LABELS:
            role = "unresolved fallback"
        else:
            role = "character"
        key = voice or ""
        entry = usage.setdefault(key, {"segment_count": 0, "roles": set(), "indexes": []})
        entry["segment_count"] += 1
        entry["roles"].add(role)
        entry["indexes"].append(index)
    return usage


def format_missing_voice_error(book: dict, analysis: dict, catalog: set[str]) -> str | None:
    usage = required_voice_usage(book, analysis)
    missing = [voice for voice in usage if voice not in catalog]
    if not missing:
        return None
    details = []
    for voice in sorted(missing, key=lambda value: str(value)):
        entry = usage[voice]
        label = voice or "未設定的旁白聲線"
        roles = "/".join(sorted(entry["roles"]))
        details.append(f"{label}（{roles}，影響{entry['segment_count']}段）")
    return "選定的聲線不在目前朗讀服務的語者清單中：" + ", ".join(details) \
        + "。請重新選擇聲線，或請管理員重新測試 TTS 服務以更新語者清單"


def generate_chapter_audio(book: dict, chapter: dict, analysis: dict, on_progress=None,
                           final_audio_path: str = None, final_timing_path: str = None,
                           generation_metadata: list | None = None, provider: dict | None = None):
    seq = chapter["seq"]
    tmpdir = tempfile.mkdtemp(prefix="novel_tts_")
    if final_audio_path is None:
        final_audio_path = storage.chapter_audio_path(book["id"], seq)
    if final_timing_path is None:
        final_timing_path = storage.chapter_timing_path(book["id"], seq)
    final = final_audio_path
    try:
        jobs = _expand_synthesis_jobs(_build_jobs(book, analysis))
        if not jobs:
            raise RuntimeError("本章無可朗讀內容")

        # 聲線目錄驗證：啟用中的遠端 provider 只接受其 catalog 內的聲音。
        # 避免把 Edge fallback id（en-US-JennyNeural / zh-CN-XiaoxiaoNeural）送給
        # 遠端 provider 造成 HTTP 400；缺聲線時以明確錯誤失敗，而非靜默產出壞音訊。
        active_provider = provider or db.get_active_tts_provider()
        if active_provider and active_provider.get("enabled"):
            catalog = {row["voice_id"] for row in db.list_tts_provider_voices(active_provider["id"])}
            error = format_missing_voice_error(book, analysis, catalog)
            if error:
                raise RuntimeError(error)

        # Respect provider-declared capacity. Unknown/legacy providers remain
        # serial so the platform never assumes a remote model is parallel-safe.
        sem = asyncio.Semaphore(_provider_concurrency(active_provider))

        async def _run_all():
            completed = 0

            async def _run_one(job):
                nonlocal completed
                name, text, voice, _seg_idx, is_en, expression, segment_id = job
                output = os.path.join(tmpdir, name)
                language = "en-US" if is_en else "zh-TW"
                cache_key = tts_partials.cache_key(
                    source_text_hash=chapter.get("text_hash", ""), segment_id=segment_id,
                    text=text, voice_id=voice, language=language, expression=expression,
                    provider=active_provider, unit_version=TTS_UNIT_VERSION,
                )
                cache_hit = await asyncio.to_thread(tts_partials.copy_if_valid, cache_key, output)
                result = None
                if not cache_hit:
                    # Preserve compatibility with older test/provider shims
                    # while the canonical implementation receives the bound
                    # provider snapshot.
                    synth_params = inspect.signature(_synth).parameters
                    if "provider" in synth_params:
                        result = await _synth(
                            text, voice, output, sem, expression, segment_id, language,
                            provider=active_provider,
                        )
                    else:
                        result = await _synth(text, voice, output, sem, expression, segment_id, language)
                    await asyncio.to_thread(tts_partials.publish, cache_key, output)
                completed += 1
                if on_progress:
                    on_progress(completed, len(jobs))
                if cache_hit and generation_metadata is not None:
                    generation_metadata.append({"cacheHit": True, "segmentId": segment_id})
                return result

            tasks = [_run_one(job) for job in jobs]
            results = await asyncio.gather(*tasks)
            if generation_metadata is not None:
                generation_metadata.extend(item for item in results if isinstance(item, dict))

        asyncio.run(_run_all())

        # 統一所有片段的音量與取樣率，並轉成無損 WAV（避免 mp3 逐段 encoder delay 造成字幕偏移）
        # 每段彼此獨立，並行處理可明顯縮短整體生成時間。
        def _norm(job):
            name, _t, _v, _i, _en, _expr, _sid = job
            mp3 = os.path.join(tmpdir, name)
            wav = os.path.join(tmpdir, name[:-4] + ".wav")
            _normalize_loudness(mp3, wav)
            try:
                os.remove(mp3)
            except OSError:
                pass

        if len(jobs) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=_FFMPEG_CONCURRENCY) as ex:
                list(ex.map(_norm, jobs))
        else:
            _norm(jobs[0])

        # 保留 provider 正規化後的自然語速；timing 以實際 WAV 時長累計，
        # 不再用字數估算值做 atempo 拉伸，避免短句被放慢、長句被加速。
        sec_count = len(analysis.get("segments", []))
        acc = [0.0] * sec_count

        def _speed(job):
            name, text, voice, seg_idx, is_en, _expr, _sid = job
            wav = os.path.join(tmpdir, name[:-4] + ".wav")
            # timing 只採用處理後 WAV 的實際時長；不要再以字數估算值
            # 呼叫 _match_duration，避免短句被放慢、長句被加速。
            return seg_idx, _probe_duration(wav)

        if len(jobs) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=_FFMPEG_CONCURRENCY) as ex:
                for done, (seg_idx, rate) in enumerate(ex.map(_speed, jobs), 1):
                    acc[seg_idx] += rate
                    if on_progress:
                        on_progress(done, len(jobs))
        else:
            seg_idx, rate = _speed(jobs[0])
            acc[seg_idx] += rate
            if on_progress:
                on_progress(1, 1)

        # 不同聲線直接硬切會造成突兀感；只對 voice-change 邊界做極短淡入淡出，
        # 不重疊音訊，也不在同一聲線的連續片段加入人為停頓。
        for index, (name, _text, voice, _seg_idx, _en, _expr, _sid) in enumerate(jobs):
            previous_voice = jobs[index - 1][2] if index else None
            next_voice = jobs[index + 1][2] if index + 1 < len(jobs) else None
            if voice == previous_voice and voice == next_voice:
                continue
            wav = os.path.join(tmpdir, name[:-4] + ".wav")
            _apply_boundary_fade(
                wav, acc[_seg_idx],
                fade_in=bool(index and voice != previous_voice),
                fade_out=bool(index + 1 < len(jobs) and voice != next_voice),
            )

        # 合併（WAV 無損拼接 → 最後只編碼一次 mp3）
        listfile = os.path.join(tmpdir, "list.txt")
        silence = os.path.join(tmpdir, "inter-segment-silence.wav")
        if len(jobs) > 1:
            _write_inter_segment_silence(silence)
        with open(listfile, "w", encoding="utf-8") as f:
            for index, (name, _t, _v, seg_idx, _en, _expr, _sid) in enumerate(jobs):
                f.write(f"file '{name[:-4]}.wav'\n")
                if index + 1 < len(jobs):
                    f.write("file 'inter-segment-silence.wav'\n")
                    acc[seg_idx] += INTER_SEGMENT_SILENCE_SECONDS
        os.makedirs(os.path.dirname(final), exist_ok=True)
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "list.txt",
             "-c:a", "libmp3lame", "-q:a", "4", final],
            cwd=tmpdir, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=1800, check=True,
        )

        # timing.json
        timing = {"segments": [{"dur": round(d, 3)} for d in acc]}
        with open(final_timing_path, "w", encoding="utf-8") as f:
            json.dump(timing, f, ensure_ascii=False)

        return final
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
