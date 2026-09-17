"""AI 聲線匹配（LLM + 啟發式備援）。沿用 v3 邏輯，book 用 legacy dict。"""
import json
import logging

from .. import db, settings, voices as voices_mod
from . import ai_provider as ai_provider_svc, ai_request_profile, structured_capability

VOICE_MATCH_SCHEMA_VERSION = "voice-matching-v1"
VOICE_MATCH_REPAIR_LIMIT = 8000
VOICE_MATCH_GROUP_SIZE = 8
VOICE_MATCH_COMPLETION_TOKENS = 1200

_GENDER_ZH = {
    "male": "男", "female": "女", "m": "男", "f": "女",
    "男": "男", "男性": "男", "男聲": "男", "男聲線": "男",
    "女": "女", "女性": "女", "女聲": "女", "女聲線": "女",
}


def _gender_zh(value) -> str:
    return _GENDER_ZH.get(str(value or "").strip().lower(), "未知")


def _openai_client(provider: dict):
    """建立與分析 executor 相同來源的 provider client。"""
    from openai import OpenAI

    return OpenAI(
        api_key=ai_provider_svc.decrypt_secret(provider.get("secret_ciphertext", "")),
        base_url=provider["base_url"],
        timeout=300,
        max_retries=0,
    )


def _empty_matches(unassigned: list[dict], reason: str) -> list[dict]:
    return [{"speaker": s["name"], "voice_id": "", "reason": reason} for s in unassigned]


def _voice_match_schema(speakers: list[dict], voices: list[dict]) -> dict:
    """Build a bounded strict schema from the current server-owned catalog.

    The model may choose only current speaker names and voice IDs.  The
    schema is transport validation; gender, reuse and same-chapter policy are
    still enforced below by the backend.
    """
    speaker_ids = [str(item["name"]) for item in speakers]
    voice_ids = [""] + [str(item["id"]) for item in voices]
    item = {
        "type": "object",
        "additionalProperties": False,
        "required": ["speaker", "voice_id", "alternatives", "reason"],
        "properties": {
            "speaker": {"type": "string", "enum": speaker_ids},
            "voice_id": {"type": "string", "enum": voice_ids},
            "alternatives": {
                "type": "array", "maxItems": 3,
                "items": {"type": "string", "enum": voice_ids},
            },
            "reason": {"type": "string"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["matches"],
        "properties": {
            "matches": {
                "type": "array", "minItems": len(speaker_ids), "maxItems": len(speaker_ids),
                "items": item,
            },
        },
    }


def _voice_match_response_format(capability, speakers: list[dict], voices: list[dict]):
    if capability and capability.state == "supported" and capability.supports_strict_json_schema:
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "storylingo_voice_matching_v1",
                "strict": True,
                "schema": _voice_match_schema(speakers, voices),
            },
        }, "strict_json_schema"
    return {"type": "json_object"}, "json_object"


def _parse_match_response(response) -> list[dict]:
    content = (response.choices[0].message.content or "").strip()
    data = json.loads(content)
    matches = data.get("matches", []) if isinstance(data, dict) else []
    if not isinstance(matches, list):
        raise ValueError("AI 匹配回應的 matches 不是陣列")
    return matches


def _match_failure(unassigned: list[dict], *, code: str, message: str,
                   applied_mode: str, retryable: bool = True) -> dict:
    return {
        "matches": _empty_matches(unassigned, message),
        "total": len(unassigned), "assigned": 0, "method": "ai",
        "errorCode": code, "retryable": retryable, "message": message,
        "structuredModeRequested": "best_effort",
        "structuredModeApplied": applied_mode,
    }


def _voice_match_prompt(speakers: list[dict], voices: list[dict], prefs: dict,
                        used: dict[str, int], *, targeted: bool = False) -> str:
    def label(vid):
        p = prefs.get(vid, {})
        return "保留（不匹配）" if p.get("exclude") else ("可重用" if p.get("reuse") else "僅一次")

    voice_list = "\n".join([
        f'- {v["id"]} | {v["name"]} | {v["gender"]} | {v["region"]} | {label(v["id"])} | 已用{used.get(v["id"], 0)}次'
        for v in voices
    ])
    spk_list = "\n".join([
        f'- {s["name"]} | 性別:{s["gender"]} | 年齡:{s["age"]} | 出現:{s["count"]}次'
        for s in speakers
    ])
    task = "只補以下尚未成功匹配的角色" if targeted else "為以下每一位角色匹配一個聲線"
    return f"""你是有聲書聲色導演。請{task}，並**遵守語音的可用次數與性別資訊**：
- 「僅一次」的語音：一個語音**只能配給一個角色**（若已標「已用≥1」就不可再選）。
- 「可重用」的語音：可配給多個角色，但同一章出現的不同角色仍不得共用同一語音。
- 保留（不匹配）的語音已排除，不會出現在清單。
- 角色性別若有明確值，不能匹配到相反性別的語音；不確定時寧可留空，不要猜測。
- 只能使用【可用語音】中的完整 voice_id，不得改寫、截短或自行產生 voice_id。
- **清單中的每一位角色都必須輸出且只能輸出一次，不得省略、合併或只挑高頻角色。**
- 若沒有安全可用的聲線，仍輸出該角色並將 voice_id 設為空字串。

【可用語音】(id | 名稱 | 性別 | 區域 | 可用性 | 已用次數)
{voice_list}

【本次角色（名字 | 性別 | 年齡 | 出現次數）】
{spk_list}

【輸出格式】只輸出 JSON 物件，禁止 Markdown、說明文字或額外 wrapper；matches 必須恰好包含上述 {len(speakers)} 位角色：
{{"matches": [{{"speaker": "角色名", "voice_id": "口音id或空字串", "alternatives": ["最多3個備選voice_id"], "reason": "簡短理由"}}]}}
"""


def _request_voice_match_group(client, profile, model: str, capability,
                               speakers: list[dict], voices: list[dict], prefs: dict,
                               used: dict[str, int], *, targeted: bool = False):
    response_format, applied_mode = _voice_match_response_format(capability, speakers, voices)
    prompt = _voice_match_prompt(speakers, voices, prefs, used, targeted=targeted)
    messages = [
        {"role": "system", "content": "你是聲線導演，只輸出符合指定格式的 JSON 物件，不要輸出 Markdown 或額外 wrapper。"},
        {"role": "user", "content": prompt},
    ]
    request = profile.request_kwargs(
        model=model,
        messages=messages,
        completion_tokens=VOICE_MATCH_COMPLETION_TOKENS,
        response_format=response_format,
    )
    resp = client.chat.completions.create(**request)
    repair_count = 0
    try:
        return _parse_match_response(resp), applied_mode, repair_count, False
    except (json.JSONDecodeError, ValueError):
        raw_content = (resp.choices[0].message.content or "").strip()
        repair_messages = [
            {"role": "system", "content": "你只負責修復 JSON 語法與既有欄位形狀，不得新增、刪除或改寫任何匹配語意；只輸出指定 JSON 物件。"},
            {"role": "user", "content": (
                "將下列 bounded AI 匹配回應修復成合法 JSON。保留原有 matches 項目與欄位值，"
                "不得建立新 speaker 或 voice_id，不得輸出 Markdown 或額外 wrapper。\n"
                f"原始回應：\n{raw_content[:VOICE_MATCH_REPAIR_LIMIT]}"
            )},
        ]
        repair_request = profile.request_kwargs(
            model=model,
            messages=repair_messages,
            completion_tokens=VOICE_MATCH_COMPLETION_TOKENS,
            response_format=response_format,
        )
        repair_count = 1
        repaired = client.chat.completions.create(**repair_request)
        try:
            return _parse_match_response(repaired), applied_mode, repair_count, False
        except (json.JSONDecodeError, ValueError):
            return None, applied_mode, repair_count, True


def _speaker_chapters(book: dict) -> dict[str, set[str]]:
    """建立語者出現章節索引，供同章聲線衝突檢查使用。"""
    result: dict[str, set[str]] = {}
    raw = book.get("speakerChapters") or {}
    if not isinstance(raw, dict):
        return result
    for chapter_key, members in raw.items():
        if isinstance(members, dict):
            names = members.keys()
        elif isinstance(members, (list, tuple, set)):
            names = members
        else:
            continue
        chapter = str(chapter_key)
        for name in names:
            result.setdefault(str(name), set()).add(chapter)
    return result


def _same_chapter(a: str, b: str, chapters_by_speaker: dict[str, set[str]]) -> bool:
    return bool(chapters_by_speaker.get(a, set()) & chapters_by_speaker.get(b, set()))


def ai_match(book: dict) -> dict:
    """為「未指定」的角色自動匹配 TTS 語者。

    語音池依書籍有效語言選擇（en → en 聲線；其餘 → zh 聲線），不再硬編碼 zh。
    回傳 {"matches": [...], "total": 未指定角色數, "assigned": 實際指派數}，
    供前端顯示「已自動匹配 X / Y 個角色」；0 指派時不得呈現為成功。
    """
    spk_info = book.get("speakerInfo", {})
    voices_cfg = book.get("voices", {})
    prefs = book.get("voicePrefs", {})

    assigned = {}
    unassigned = []
    for name, info in spk_info.items():
        if name == "_english":
            continue
        cur = voices_cfg.get(name)
        if cur and cur != "":
            assigned[name] = cur
        else:
            unassigned.append({
                "name": name,
                "gender": _gender_zh(info.get("gender", "未知")),
                "age": info.get("age", "未知"),
                "count": info.get("count", 0),
            })

    all_voices = [
        {"id": row["voice_id"], "name": row["name"], "lang": row["lang"],
         "gender": _gender_zh(row["gender"]), "region": row.get("region") or ""}
        for row in db.list_tts_provider_voices()
    ]
    book_lang = "en" if settings.effective_category(book) == settings.CAT_EN else "zh"
    lang_voices = [v for v in all_voices if v["lang"] == book_lang and v["id"] != voices_mod.ENGLISH_VOICE_DEFAULT]
    used: dict[str, int] = {}
    for vid in assigned.values():
        used[vid] = used.get(vid, 0) + 1
    chapters_by_speaker = _speaker_chapters(book)

    if not unassigned:
        return {"matches": [], "total": 0, "assigned": 0, "message": "所有角色皆已指定語者"}
    if not lang_voices:
        return {"matches": [], "total": len(unassigned), "assigned": 0,
                "message": f"目前朗讀服務沒有支援此語言（{book_lang}）的聲線，無法自動匹配"}

    provider = db.get_default_ai_provider()
    if not provider:
        return {"matches": _empty_matches(unassigned, "尚未設定可用的 AI provider"),
                "total": len(unassigned), "assigned": 0,
                "method": "unavailable", "message": "尚未設定可用的 AI provider，無法進行 AI 匹配"}

    def _pool():
        out = []
        for v in lang_voices:
            p = prefs.get(v["id"], {})
            if p.get("exclude"):
                continue
            out.append(v)
        return out

    def _label(vid):
        p = prefs.get(vid, {})
        if p.get("exclude"):
            return "保留（不匹配）"
        return "可重用" if p.get("reuse") else "僅一次"

    applied_mode = "json_object"
    try:
        client = _openai_client(provider)
        model = provider.get("model") or settings.AI_MODEL

        avail = _pool()
        main_unassigned = [s for s in unassigned if s["count"] > 0]
        capability = structured_capability.load(provider)
        profile = ai_request_profile.resolve(provider, model)
        repair_count = 0
        targeted_completion_count = 0
        completion_failed = False
        final_by_speaker = {}
        speaker_by_name = {s["name"]: s for s in unassigned}
        voice_by_id = {v["id"]: v for v in avail}

        def apply_matches(llm_matches, allowed_names):
            for mm in llm_matches or []:
                if not isinstance(mm, dict):
                    continue
                speaker = str(mm.get("speaker") or "").strip()
                if speaker not in allowed_names or speaker in final_by_speaker:
                    continue
                proposed = [str(mm.get("voice_id") or "").strip()]
                alternatives = mm.get("alternatives")
                if isinstance(alternatives, list):
                    proposed.extend(str(item or "").strip() for item in alternatives[:3])
                reason = str(mm.get("reason") or "")[:200]
                speaker_gender = speaker_by_name[speaker]["gender"]
                vid = ""
                rejected_reason = "AI 回傳的聲線不在可用目錄"
                for candidate_id in proposed:
                    candidate = voice_by_id.get(candidate_id)
                    if not candidate:
                        continue
                    p = prefs.get(candidate_id, {})
                    if used.get(candidate_id, 0) > 0 and not p.get("reuse"):
                        rejected_reason = "僅一次聲線已被其他角色使用"
                        continue
                    if any(candidate_id == other_vid and _same_chapter(speaker, other_speaker, chapters_by_speaker)
                           for other_speaker, other_vid in assigned.items()):
                        rejected_reason = "同章角色不可共用聲線"
                        continue
                    if speaker_gender != "未知" and candidate["gender"] != "未知" and speaker_gender != candidate["gender"]:
                        rejected_reason = "性別不一致，保留未指定"
                        continue
                    vid = candidate_id
                    break
                if not vid:
                    reason = rejected_reason
                if vid:
                    used[vid] = used.get(vid, 0) + 1
                    assigned[speaker] = vid
                final_by_speaker[speaker] = {"speaker": speaker, "voice_id": vid, "reason": reason}

        if main_unassigned and avail:
            for offset in range(0, len(main_unassigned), VOICE_MATCH_GROUP_SIZE):
                batch = main_unassigned[offset:offset + VOICE_MATCH_GROUP_SIZE]
                allowed_names = {s["name"] for s in batch}
                llm_matches, mode, repairs, invalid = _request_voice_match_group(
                    client, profile, model, capability, batch, avail, prefs, used,
                )
                applied_mode = mode
                repair_count += repairs
                if invalid:
                    return _match_failure(
                        unassigned,
                        code="ai_match_invalid_response",
                        message="AI 語者匹配回應格式無效，請稍後再試",
                        applied_mode=applied_mode,
                    )
                apply_matches(llm_matches, allowed_names)

                missing = [s for s in batch if not final_by_speaker.get(s["name"], {}).get("voice_id")]
                if missing:
                    targeted_completion_count += 1
                    completion_matches, mode, repairs, invalid = _request_voice_match_group(
                        client, profile, model, capability, missing, avail, prefs, used,
                        targeted=True,
                    )
                    applied_mode = mode
                    repair_count += repairs
                    if invalid:
                        completion_failed = True
                        continue
                    # A targeted request may only replace blank results for its
                    # own missing speakers; existing valid assignments remain immutable.
                    for speaker in {s["name"] for s in missing}:
                        final_by_speaker.pop(speaker, None)
                    apply_matches(completion_matches, {s["name"] for s in missing})

            cnt_by = {s["name"]: s["count"] for s in unassigned}
            matches = [final_by_speaker.get(s["name"], {
                "speaker": s["name"], "voice_id": "", "reason": "AI 未提供匹配"
            }) for s in sorted(unassigned, key=lambda item: -cnt_by.get(item["name"], 0))]
        else:
            matches = _empty_matches(unassigned, "沒有可供 AI 匹配的主要角色或聲線")
    except Exception as e:
        logging.getLogger("voice").warning("AI voice matching failed; no heuristic assignment: %s", type(e).__name__)
        return _match_failure(
            unassigned,
            code="ai_match_provider_error",
            message="AI 語者匹配服務暫時無法使用，請稍後重試",
            applied_mode=locals().get("applied_mode", "json_object"),
        )

    matches = [mm for mm in matches if mm.get("speaker")]
    return {"matches": matches, "total": len(unassigned),
            "assigned": sum(1 for mm in matches if mm.get("voice_id")),
            "method": "ai", "structuredModeRequested": "best_effort",
            "structuredModeApplied": applied_mode, "repairCount": repair_count,
            "targetedCompletionCount": targeted_completion_count,
            "targetedCompletionFailed": completion_failed}


def _heuristic_match(speakers, lang_voices, used, prefs=None):
    prefs = prefs or {}
    speakers_sorted = sorted(speakers, key=lambda s: -s["count"])
    available = {v["id"]: v for v in lang_voices}
    matches = []
    male_voices = [v for v in available.values() if v["gender"] == "男"]
    female_voices = [v for v in available.values() if v["gender"] == "女"]
    all_voices = male_voices + female_voices
    total_use = {vid: used.get(vid, 0) for vid in available}
    run_used = set()

    def _pick(cands):
        unused = [v for v in cands if total_use.get(v["id"], 0) == 0]
        if unused:
            pref = [v for v in unused if v["region"] in ("TW", "CN")] or unused
            return min(pref, key=lambda x: total_use.get(x["id"], 0))
        avail = [v for v in cands if prefs.get(v["id"], {}).get("reuse") or v["id"] not in run_used]
        if avail:
            return min(avail, key=lambda x: total_use.get(x["id"], 0))
        return min(cands, key=lambda x: total_use.get(x["id"], 0))

    for spk in speakers_sorted:
        gender = spk.get("gender", "未知")
        if gender == "男":
            pool = male_voices
        elif gender == "女":
            pool = female_voices
        else:
            pool = all_voices
        if not pool:
            matches.append({"speaker": spk["name"], "voice_id": "", "reason": "無可用語者"})
            continue
        v = _pick(pool)
        total_use[v["id"]] = total_use.get(v["id"], 0) + 1
        run_used.add(v["id"])
        matches.append({"speaker": spk["name"], "voice_id": v["id"],
                        "reason": f"啟發式：{v['name']}（{v['gender']}/{v['region']}），用{total_use[v['id']]}次"})
    return matches
