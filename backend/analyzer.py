"""AI 逐章分析：語者拆分 + 清洗 + 依語言分類出單字/雙語/純英文。支援 OpenAI 與 NVIDIA NIM。"""
import hashlib
import json
import logging
import re
import threading
import time
from collections import Counter

from openai import OpenAI

from . import settings
from . import voices as voices_mod
from .services import ai_provider as ai_provider_svc
from .services import analysis as analysis_svc
from .services import source_faithful
from .services import structured_output
from .services import structured_capability
from .services import ai_request_profile
from .services import usage_metrics

_log = logging.getLogger("analyzer")

# Increment whenever the analysis instructions change in a way that can alter
# the returned segments. This is part of the partial-result cache identity.
ANALYSIS_PROMPT_VERSION = "v2-speaker-7-vocab-bounded-per-group"

BASE_PROMPT = """你是小說的編輯與英語學習教材設計師。使用者給你一章小說原文，你要輸出結構化 JSON，供之後「角色聲線朗讀」使用。

【任務1：清洗】
- 移除作者碎念、PS、括號備註、重複標題等非正文內容。
- 保留「……」「…」等語氣符號。

【任務2：語者拆分】
- 將章節切成連續段落(segment)，每段 1~3 句，方便逐句朗讀與螢幕顯示。
- 每段類型：
  * narration：敘述/旁白（包含動作、心理、場景描寫）
  * dialogue：角色台詞（原文用「」括起的說話內容），speaker 填角色名，text 不含「」符號
- dialogue 無法確定說話者時，仍保留為 dialogue，speaker 留空；後續 canonical normalize 會標記為 speaker:unresolved。
- 不要用子字串合併不同表面名稱；只有明確可證實的 alias 才可放在同一 character identity，否則保留為不同名稱或留待 unresolved。
- 若提示中提供「本書已知說話者」與「上一章結尾內容」，請優先沿用已知角色名，並參考上一章的對話續以判斷本章開頭的說話者。

【任務3：歸因證據與情緒】
- dialogue 必須保留 attribution：method（explicit/continuation/contextual/unresolved）、confidence（0.0~1.0）、evidence_text（若有）。沒有可靠 speaker 時不可改成 narration。
- 每個 narration/dialogue segment 都可提供 emotion（label、intensity 0.0~1.0）、tone、speaking_style；情緒是 segment 級，不是角色級。

【任務3：角色清單】
- speakers 列出本章所有角色（含「旁白」），每個給 gender（男/女/未知），以及 age（從原文線索合理推測的年齡帶，只能填：兒童/少年/青年/中年/老年/未知，無法判斷就填「未知」），供使用者挑選聲線時參考。"""

ZH_TASK = """
【注意：本書為純中文小說】
- 不要輸出任何英文單字教學段（vocab）。
- segments 只輸出 narration / dialogue 兩種型別。"""

EN_TASK = """
【注意：本書為純英文文章】
- 原文可能是中文或英文：若該段原文是中文，請忠實翻譯成自然流暢、符合語境的英文（保有原意、語氣與情節）；若已是英文則直接沿用原文。
- narration / dialogue 的 text 一律輸出英文（中文段落翻譯後即為英文）。
- 旁白（敘述）的 speaker 請統一使用「旁白」。
- 角色語者依原文拆分，對話 text 不含括號符號；翻譯後每個語者仍維持各自的台詞。
- 不要輸出中文，也不要輸出英文單字教學段（vocab）。
- segments 只輸出 narration / dialogue 兩種型別。"""

VOCAB_TASK = """
【任務4：英文單字教學（vocab 段）— 核心功能，必須執行】
【來源對齊硬性規則】
- narration / dialogue 的 text 必須逐字複製章節原文中連續出現的文字，包含原有標點與語氣符號；不得改寫、摘要、翻譯、補寫或省略內容。
- 只能用原文實際存在的連續片段建立 narration / dialogue；若無法安全切段，保留更大的原文片段，不要自行改寫。
- vocab 段的教學 utterances 可以產生新內容，但不得把 narration / dialogue 改成教學文字。
- 每章挑選 2~3 個 vocab（至少 2 個）。只要章節超過 100 字，就必須挑滿 2~3 個；除非整章真的找不到任何能對應簡單英文的詞，才可以少於 2 個。
- 若本章較長（會分段處理），每一段都可各挑 2~3 個，章節字數越多可越密集。
- 2~3 個 vocab 要分散放在原文「不同位置」，不要集中在開頭，也不要重複選同一詞。
- 挑選標準：該章原文中真正出現過的詞，且對應的英文是常見簡單單字，例如：書→book、水→water、跑→run、朋友→friend、開心→happy。
- {LEVEL_DIRECTION}
- 每個 vocab 的 en 必須不同，避免重複。
- 絕對不要挑：人名、地名、專有名詞、文言詞、超難詞、不確定單字。
- 把教學段放在原文出現該詞的位置附近。
- 教學段結構（utterances，依序朗讀，lang 交替）：
  1. zh：中文引導，例如「『書』的英文是——」
  2. en：英文單字，例如 "book."
  3. en：拼寫示範，例如 "B - O - O - K."
  4. zh：中文講解+引導例句，例如「意思是書本。聽例句：」
  5. en：極簡單英文例句，例如 "This is a book."
  6. zh：中文翻譯，例如「就是『這是一本書』。」
- vocab 物件：{zh: 中文詞, en: 英文單字, spelling: 拼寫（B-O-O-K）, example: 例句 + 中文翻譯, level: 見難度指示}。
- 重要：vocab 段必須加在 segments 中（type="vocab"），穿插在對應位置，不要只列在 speakers。"""

BILINGUAL_TASK = """
【任務5：雙語對譯 — 必須執行】
- 每個 narration 與 dialogue 段都要附上英文翻譯，型別一律改用 bilingual：
  {"type": "bilingual", "speaker": "角色名", "zh": "中文原文", "en": "英文翻譯"}
- zh 保留中文原文（dialogue 不含「」），en 為自然流暢的英文翻譯。
- 不要再輸出一般 narration / dialogue 型別，全部改為 bilingual。"""

VOCAB_OUTPUT = """
【輸出格式】只輸出一個 JSON 物件：
{
 "speakers": [{"name": "旁白", "gender": "未知", "age": "未知"}, {"name": "道不明", "gender": "男", "age": "青年"}],
 "segments": [
   {"type": "narration", "speaker": "旁白", "text": "……"},
   {"type": "dialogue", "speaker": "道不明", "text": "……"},
   {"type": "vocab", "speaker": "旁白", "utterances": [{"lang": "zh", "text": "……"}, {"lang": "en", "text": "……"}], "vocab": {"zh": "書", "en": "book", "spelling": "B-O-O-K", "example": "This is a book. 這是一本書。", "level": "A1"}}
 ]
}
不要輸出 JSON 以外的任何文字。"""

BILINGUAL_OUTPUT = """
【輸出格式】只輸出一個 JSON 物件：
{
 "speakers": [{"name": "旁白", "gender": "未知", "age": "未知"}, {"name": "道不明", "gender": "男", "age": "青年"}],
 "segments": [
   {"type": "bilingual", "speaker": "道不明", "zh": "……", "en": "……"},
   {"type": "bilingual", "speaker": "旁白", "zh": "……", "en": "……"},
   {"type": "vocab", "speaker": "旁白", "utterances": [{"lang": "zh", "text": "……"}, {"lang": "en", "text": "……"}], "vocab": {"zh": "書", "en": "book", "spelling": "B-O-O-K", "example": "This is a book. 這是一本書。", "level": "A1"}}
 ]
}
不要輸出 JSON 以外的任何文字。"""

PLAIN_OUTPUT = """
【輸出格式】只輸出一個 JSON 物件：
{
 "speakers": [{"name": "旁白", "gender": "未知", "age": "未知"}, {"name": "道不明", "gender": "男", "age": "青年"}],
 "segments": [
   {"type": "narration", "speaker": "旁白", "text": "……"},
   {"type": "dialogue", "speaker": "道不明", "text": "……", "attribution": {"method": "explicit", "confidence": 0.9, "evidence_text": "他說"}, "emotion": {"label": "neutral", "intensity": 0.2}, "tone": "平靜", "speaking_style": "自然"}
 ]
}
不要輸出 JSON 以外的任何文字。"""


def _level_direction(vocab_level: str) -> str:
    if not vocab_level or vocab_level == "AUTO":
        return ("每個 vocab 自動判定 CEFR 等級（A1/A2/B1/B2 任一），"
                "並在 level 欄填對應值（例如 level 填 A1）。")
    return (f"只挑 CEFR {vocab_level} 的常見簡單英文單字，並在 level 欄填「{vocab_level}」。"
            "若原文中找不到該等級的詞，可挑最接近且稍簡單的詞，並照實標註其實際等級。")


def _make_prompt(book: dict) -> str:
    """依書籍語言分類組出分析提示。"""
    category = settings.effective_category(book)
    level = book.get("vocabLevel") or "AUTO"
    parts = [BASE_PROMPT]
    if category == settings.CAT_EN:
        parts.append(EN_TASK)
        parts.append(PLAIN_OUTPUT)
    elif category == settings.CAT_ZH:
        parts.append(ZH_TASK)
        parts.append(PLAIN_OUTPUT)
    elif category == settings.CAT_BILINGUAL:
        parts.append(VOCAB_TASK.replace("{LEVEL_DIRECTION}", _level_direction(level)))
        parts.append(BILINGUAL_TASK)
        parts.append(BILINGUAL_OUTPUT)
    elif category == settings.CAT_OTHER:
        parts.append(PLAIN_OUTPUT)
    else:  # vocab
        parts.append(VOCAB_TASK.replace("{LEVEL_DIRECTION}", _level_direction(level)))
        parts.append(VOCAB_OUTPUT)
    return "\n\n".join(parts)

MAX_CHUNK = 6000  # 單次分析的文本上限（字元），超過則分段
_CHUNK_CONCURRENCY = 2  # 同章分段分析的並行數（免費層限流容忍度內）
_MALFORMED_JSON_RETRIES = 1
_MAX_REPAIR_INPUT_CHARS = 200_000

# NVIDIA NIM 大多模型不支援 OpenAI 的 json_object 模式，改用「提示+解析」
JSON_MODE_OK = settings.AI_PROVIDER != "nvidia"

# 記錄最後成功使用的模型（供除錯）
_last_model = None

# V4：thread-local 綁定的 AI provider（由 analysis executor 注入，避免依賴全域 env credentials）
_thread_local = threading.local()
_request_budget_local = threading.local()


class AnalysisRequestBudgetExceeded(RuntimeError):
    """單一 chunk 的 shared provider request budget 已耗盡。"""


class MalformedJSONError(analysis_svc.AnalysisValidationError):
    """主要回應與 bounded dedicated repair 都不是合法 JSON。"""


def _is_transient_provider_error(error) -> bool:
    status = getattr(error, "status_code", None)
    if status in (429, 529):
        return True
    return isinstance(error, TimeoutError) or "timeout" in str(error).lower()


def _is_timeout_error(error) -> bool:
    return isinstance(error, TimeoutError) or "timeout" in str(error).lower()


def _bind_provider(provider: dict | None):
    """V4 executor 在執行前注入綁定的 ai_providers row。"""
    _thread_local.provider = provider


def _bound_provider():
    return getattr(_thread_local, "provider", None)


def _client():
    provider = _bound_provider()
    if provider:
        kwargs = {
            "api_key": ai_provider_svc.decrypt_secret(provider.get("secret_ciphertext", "")),
            "base_url": provider["base_url"],
            "timeout": 300,
            "max_retries": 0,
        }
        return OpenAI(**kwargs)
    if not settings.AI_API_KEY:
        raise RuntimeError(
            "尚未設定 AI API key。請在 .env 設定 AI_API_KEY（NVIDIA nvapi-…）或 OPENAI_API_KEY，並重啟伺服器"
        )
    kwargs = {"api_key": settings.AI_API_KEY}
    if settings.AI_BASE_URL:
        kwargs["base_url"] = settings.AI_BASE_URL
    # 每一次請求設讀取逾時（5 分鐘），且停用 SDK 的自動重試：
    # 否則提供者沒回應時，單次請求預設可卡到約 10 分鐘、再乘上 2 次重試，
    # 長章節（分多段）就會看起來「卡同一章很久」。429/529 由 _retry 自己處理。
    kwargs["timeout"] = 300
    kwargs["max_retries"] = 0
    return OpenAI(**kwargs)


def _max_tokens_for(text: str) -> int:
    # 依文本長度估算輸出長度，避免被截斷
    n = int(len(text) * 1.4) + 1500
    return min(max(n, 4000), 32000)


_KNOWN_COMPLETION_TOKEN_LIMITS = {
    # OpenAI gpt-4o-mini rejects requests above this completion limit.
    "gpt-4o-mini": 16384,
}


def _completion_token_limit(model: str) -> int:
    """Return the safest completion limit known for the bound provider/model."""
    provider = _bound_provider() or {}
    for key in ("max_completion_tokens", "completion_token_limit", "max_output_tokens"):
        value = provider.get(key)
        if value is not None:
            try:
                return max(1, int(value))
            except (TypeError, ValueError):
                pass
    model_key = (model or "").lower()
    for known_model, limit in _KNOWN_COMPLETION_TOKEN_LIMITS.items():
        if model_key == known_model or model_key.startswith(known_model + "-"):
            return limit
    # Unknown OpenAI-compatible models must still receive a bounded request;
    # adapters can provide a more precise provider capability above.
    return 16384


def _repair_max_tokens(raw_response: str, model: str) -> int:
    requested = max(4000, int(len(raw_response) * 1.4) + 500)
    return min(requested, _completion_token_limit(model))


def _request_profile(model: str):
    return ai_request_profile.resolve(_bound_provider(), model)


def _chat_request_kwargs(*, model: str, messages: list[dict], completion_tokens: int,
                         temperature: float | None = None, **extra) -> dict:
    return _request_profile(model).request_kwargs(
        model=model, messages=messages, completion_tokens=completion_tokens,
        temperature=temperature, **extra,
    )


def _extract_json(raw: str) -> dict:
    """從模型輸出中抽出 JSON 物件（相容不含 json_object 模式的模型）。"""
    raw = (raw or "").strip()
    # 移除 ```json ... ``` 程式碼區塊
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start:end + 1]
    return json.loads(raw)


def _model_chain() -> list[str]:
    provider = _bound_provider()
    if provider:
        models = [provider.get("model") or ""]
        fb = provider.get("fallback_model")
        if fb and fb not in models:
            models.append(fb)
        return [m for m in models if m][:2]
    models = [settings.AI_MODEL]
    fb = getattr(settings, "AI_MODEL_FALLBACK", "")
    if fb and fb not in models:
        models.append(fb)
    return models[:2]


def _metric_inc(metrics, key: str, amount: int = 1):
    if metrics is None:
        return
    with metrics["lock"]:
        metrics[key] = metrics.get(key, 0) + amount
        snapshot = {
            "total_chunks": metrics.get("total_chunks", 0),
            "completed_chunks": metrics.get("completed_chunks", 0),
            "running_chunks": metrics.get("running_chunks", 0),
            "retry_count": metrics.get("retry_count", 0),
            "cache_hit_chunks": metrics.get("cache_hit_chunks", 0),
            "requested_chunks": metrics.get("requested_chunks", 0),
            "provider_request_count": metrics.get("request_count", 0),
            "reused_chunks": metrics.get("cache_hit_chunks", 0),
            "chunk_retry_count": metrics.get("retry_count", 0),
            "saved_provider_requests": metrics.get("saved_provider_requests", 0),
            "usage_metrics": usage_metrics.snapshot_unlocked(metrics),
        }
        callback = metrics.get("on_progress") if key in ("request_count", "retry_count") else None
    budget = getattr(_request_budget_local, "current", None)
    if budget is not None and key == "retry_count":
        budget["retries"] = budget.get("retries", 0) + amount
    if callback:
        callback({"stage": "analyzing", **snapshot})


def _invoke(create, metrics=None, *, request_kind="primary"):
    budget = getattr(_request_budget_local, "current", None)
    if budget is not None:
        budget.setdefault("lock", threading.Lock())
        budget.setdefault("usage", usage_metrics.empty())
        if budget["requests"] >= budget["max_requests"]:
            raise AnalysisRequestBudgetExceeded("chunk provider request budget exhausted")
        budget["requests"] += 1
    usage_metrics.record_attempt(metrics, kind=request_kind)
    if budget is not None:
        usage_metrics.record_attempt(budget, kind=request_kind)
    _metric_inc(metrics, "request_count")
    try:
        response = create()
    except Exception:
        raise
    usage_metrics.record_response(metrics, response)
    if budget is not None:
        usage_metrics.record_response(budget, response)
    return response


def _extract_with_repair(create, metrics=None, repair_create=None, *, request_kind="primary"):
    """解析 primary response；失敗時只對該 response 做一次 dedicated repair。"""
    response = _invoke(create, metrics, request_kind=request_kind)
    raw = response.choices[0].message.content or ""
    try:
        return _extract_json(raw)
    except json.JSONDecodeError as primary_error:
        budget = getattr(_request_budget_local, "current", None)
        if budget is not None and budget["malformed_retries"] >= _MALFORMED_JSON_RETRIES:
            raise MalformedJSONError(str(primary_error)) from primary_error
        if repair_create is None:
            raise MalformedJSONError(str(primary_error)) from primary_error
        if budget is not None:
            budget["malformed_retries"] += 1
        _metric_inc(metrics, "retry_count")
        _log.warning("AI response malformed JSON，改用 dedicated repair（1/%d）",
                     _MALFORMED_JSON_RETRIES)
        repair_input = raw[:_MAX_REPAIR_INPUT_CHARS]
        try:
            repaired = _invoke(lambda: repair_create(repair_input), metrics, request_kind="repair")
        except Exception as repair_request_error:
            if getattr(repair_request_error, "status_code", None) == 400:
                raise MalformedJSONError("dedicated JSON repair unsupported") from repair_request_error
            raise
        try:
            return _extract_json(repaired.choices[0].message.content or "")
        except json.JSONDecodeError as repair_error:
            raise MalformedJSONError(str(repair_error)) from repair_error


def _call_llm(text: str, context: str = "", system_prompt: str = BASE_PROMPT, metrics=None) -> dict:
    client = _client()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"以下是章節原文：\n\n{text}\n\n請只輸出一個 JSON 物件，不要額外文字或程式碼區塊。"},
    ]
    if context.strip():
        messages.insert(1, {"role": "system", "content": context.strip()})
    last_err = None
    for model in _model_chain():
        global _last_model
        kwargs = _chat_request_kwargs(
            model=model, messages=messages, completion_tokens=_max_tokens_for(text),
        )

        def repair_create(raw_response: str):
            return client.chat.completions.create(**_chat_request_kwargs(
                model=model,
                completion_tokens=_repair_max_tokens(raw_response, model),
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": (
                        "你是 JSON 語法修復器。只修復輸入 JSON 的語法，"
                        "不得重新分析小說、改變 speaker/emotion 或新增刪除語義資料。"
                        "只輸出一個合法 JSON 物件，不要 Markdown 或說明。"
                    )},
                    {"role": "user", "content": raw_response},
                ],
            ))

        # 先試 OpenAI 原生 JSON 模式（僅 OpenAI provider）
        if JSON_MODE_OK:
            try:
                result = _extract_with_repair(
                    lambda: client.chat.completions.create(response_format={"type": "json_object"}, **kwargs),
                    metrics, repair_create=repair_create)
                _last_model = model
                return result
            except Exception as e:
                status = getattr(e, "status_code", None)
                if _is_transient_provider_error(e):
                    r = _retry_json(client, kwargs, metrics)
                    if r is not None:
                        return r
                    if _is_timeout_error(e):
                        raise analysis_svc.AnalysisProviderTimeoutExhaustedError(
                            "provider timeout retry budget exhausted"
                        ) from e
                    last_err = e
                    continue  # 限流發生 → 換下一個模型
                if isinstance(e, MalformedJSONError):
                    raise
                if status != 400 and status is not None:
                    last_err = e
                    continue
                # 400 → 改用純文字輸出

        # 純文字路徑：依提示輸出 JSON，再做健壯解析
        try:
            result = _extract_with_repair(
                lambda: client.chat.completions.create(**kwargs), metrics,
                repair_create=repair_create)
            _last_model = model
            return result
        except Exception as e:
            status = getattr(e, "status_code", None)
            if _is_transient_provider_error(e):
                r = _retry_rate_limit(client, kwargs, metrics)
                if r is not None:
                    return r
                if _is_timeout_error(e):
                    raise analysis_svc.AnalysisProviderTimeoutExhaustedError(
                        "provider timeout retry budget exhausted"
                    ) from e
                last_err = e
                continue
            if isinstance(e, MalformedJSONError):
                raise
            last_err = e
            continue

    raise RuntimeError(f"AI 服務無法完成分析：{last_err}")


def _structured_capability() -> structured_output.StructuredOutputCapability:
    provider = _bound_provider() or {}
    # Explicit injected snapshots remain available for adapter/unit tests;
    # persisted production provider rows use the discovery service below.
    injected = provider.get("structured_output_capability") or provider.get("capabilities")
    if injected or "supports_strict_json_schema" in provider or "supportsStrictJsonSchema" in provider:
        raw = injected or provider
        return structured_output.capability_from_provider(raw, source=str(raw.get("source", "declared")))
    return structured_capability.load_for_analysis(provider)


def _structured_annotation_schema(*, include_vocab: bool = False) -> structured_output.AnnotationSchemaDescriptor:
    required_fields = [
        "segment_id", "type", "speaker", "speaker_candidate",
        "speaker_gender", "speaker_age", "attribution", "confidence",
    ]
    if include_vocab:
        required_fields.append("vocab")
    return structured_output.AnnotationSchemaDescriptor(
        version="1", required_fields=tuple(required_fields),
    )


def _call_structured_annotations(
    source_text: str,
    spans: list[dict],
    *,
    context: str = "",
    metrics=None,
    mode: str = "best_effort",
    allow_targeted_completion: bool = True,
    targeted_completion: bool = False,
    chunk_index: int | None = None,
    include_vocab: bool = False,
    vocab_level: str = "AUTO",
) -> list[dict]:
    """Request annotations for deterministic source spans only.

    This is the production bridge for the source-faithful pipeline.  The
    provider sees source as read-only context and may return only span
    references/annotations; canonical text and offsets are never accepted
    from the response.
    """
    schema = _structured_annotation_schema(include_vocab=include_vocab)
    capability = _structured_capability()
    selection = structured_output.select_output_mode(capability, schema, mode=mode)
    ids = [span["segment_id"] for span in spans]
    request = structured_output.build_annotation_request(
        ids, selection, schema,
        read_only_context=context,
    )
    segment_context = "\n".join(
        f"{span['segment_id']} | deterministic_type={span['type']} | "
        f"source={str(span.get('text') or '')[:1200]}"
        for span in spans
    )[:120000]
    prompt = (
        "你是小說章節標註器。只能回傳 JSON 物件中的 segments annotation。\n"
        "不得回傳 text、source_start、source_end、span_hash 或任何 canonical source 欄位。\n"
        "segment_id 必須只使用下列 deterministic source segment："
        + json.dumps(request["segment_ids"], ensure_ascii=False)
        + ("\n只處理指定的缺失 segment，不要回傳其他 segment。" if targeted_completion else "")
        + "\n必須恰好回傳上述每一個 segment_id 一次，不得省略、重複或只回傳前幾筆。"
        + "\n每個 segment 必須標註 type（只能是 narration 或 dialogue）、speaker/候選、attribution、confidence、emotion、tone、speaking_style。"
        "dialogue 若能從原文或上下文可靠判斷，請同時填 speaker_gender（男/女/未知）與 speaker_age（兒童/少年/青年/中年/老年/未知）；無法判斷填「未知」，不可猜測。"
        "deterministic_type 是 backend 根據原文決定的固定類型，回傳 type 必須與它一致；不得把 dialogue 改成 narration。"
        "每個 dialogue 必須根據台詞內容、前後文、引號與 attribution verbs 判斷 speaker；無法可靠判斷時保留 dialogue，speaker 可為 null，但 attribution 必須標示 unresolved 與 confidence/evidence。"
        "以下是每個 segment 的唯讀原文與固定類型，禁止重印或改寫為輸出：\n"
        + segment_context
    )
    if include_vocab:
        prompt += (
            "\n本書語言型別為中英單字；這不是要求原文先含英文，而是要把中文原文中的可教學詞語建立中英學習資料。"
            "每個 annotation 都必須包含 vocab 欄位；不需要教學的 annotation 填 null。"
            "每一組 deterministic segments 合計最多提供 2 個 vocab，不是每個 annotation 都要提供單字，"
            "請優先挑選該組中一般讀者真正會用到的常見詞；若該組有適合的詞，至少提供 1 個。"
            "vocab.zh 必須是該 source segment 中實際連續出現的詞或短語，vocab.en 是對應的常見英文；"
            "不得把不存在於 source 的中文詞或英文當成學習項目。"
            "需要教學時填一個物件，欄位為 zh、en、spelling、example、level。"
            "不得使用人名、地名、專有名詞或過度抽象的詞；"
            f"難度依 {vocab_level or 'AUTO'} 指示填寫。vocab 是學習資料，不是 canonical text。"
        )
    if context.strip():
        prompt += "\n\n補充 context（只讀）：\n" + context.strip()
    client = _client()
    model = _model_chain()[0]
    native_format = structured_output.build_adapter_response_format(
        selection, schema, adapter_key=str((_bound_provider() or {}).get("provider_type") or "openai"),
        segment_ids=ids,
    )
    kwargs = _chat_request_kwargs(
        model=model,
        completion_tokens=min(_max_tokens_for(source_text), _completion_token_limit(model)),
        messages=[
            {"role": "system", "content": "只輸出合法 JSON annotation，不要 Markdown。"},
            {"role": "user", "content": prompt},
        ],
    )
    if native_format is not None:
        kwargs["response_format"] = native_format
    if metrics is not None:
        with metrics["lock"]:
            metrics["structured_mode_requested"] = mode
            metrics["structured_mode_applied"] = selection.applied_mode
            metrics["structured_schema_version"] = schema.version
            metrics["structured_schema_hash"] = schema.schema_hash
            metrics["structured_fallback_reason"] = selection.fallback_reason
            metrics["structured_capability_source"] = selection.capability_source

    def validate_or_complete(payload):
        # Keep json_object/legacy fixtures readable while strict v2 providers
        # are required to emit these keys.  Missing values remain null; this
        # never invents a speaker or changes source-owned type.
        payload = {
            **payload,
            "segments": [
                {
                    **item,
                    "speaker": item.get("speaker"),
                    "speaker_candidate": item.get("speaker_candidate"),
                    "speaker_gender": item.get("speaker_gender", item.get("gender")),
                    "speaker_age": item.get("speaker_age", item.get("age")),
                    "attribution": item.get("attribution"),
                    "confidence": item.get("confidence"),
                    "vocab": item.get("vocab"),
                }
                for item in payload.get("segments", [])
                if isinstance(item, dict)
            ],
        }
        try:
            return structured_output.validate_annotation_payload(payload, set(ids), schema)
        except structured_output.StructuredOutputError as error:
            if error.code in {"annotation_coverage_incomplete", "annotation_coverage_low"}:
                diagnostics = dict(error.diagnostics or {})
                provider = _bound_provider() or {}
                diagnostics.update({
                    "structuredMode": selection.applied_mode,
                    "provider": provider.get("id") or provider.get("provider_type"),
                    "model": model,
                    "chunkIndex": chunk_index,
                })
                error.diagnostics = diagnostics
            if error.code != "annotation_coverage_incomplete" or not allow_targeted_completion:
                raise
            diagnostics = error.diagnostics or {}
            missing_ids = set(
                diagnostics.get("missingSegmentIdsSample")
                or diagnostics.get("missingSegmentIds") or []
            )
            if not missing_ids or int(diagnostics.get("missingCount", diagnostics.get("missingSegmentCount", 0)) or 0) > 64:
                raise
            valid = structured_output.validate_annotation_subset(payload, set(ids), schema)
            missing_spans = [span for span in spans if span.get("segment_id") in missing_ids]
            if len(missing_spans) != len(missing_ids):
                raise
            bounded_context = "\n".join(
                f"{span['segment_id']}: {str(span.get('text') or '')[:400]}"
                for span in missing_spans
            )[:8000]
            completion = _call_structured_annotations(
                bounded_context, missing_spans, context="", metrics=metrics, mode=mode,
                allow_targeted_completion=False, targeted_completion=True,
                chunk_index=chunk_index, include_vocab=include_vocab,
                vocab_level=vocab_level,
            )
            return valid + completion

    def repair_invalid_references(payload, invalid, missing_ids):
        if not invalid:
            raise structured_output.StructuredOutputError(
                "annotation_reference_invalid", "invalid segment reference cannot be repaired",
            )
        allowed = sorted(set(missing_ids))
        existing_ids = {
            str(item.get("segment_id")) for item in payload.get("segments", [])
            if isinstance(item, dict) and str(item.get("segment_id")) in ids
        }
        repair_payload = json.dumps({"segments": invalid[:64]}, ensure_ascii=False)[:12000]
        repair_kwargs = _chat_request_kwargs(
            model=model,
            completion_tokens=_repair_max_tokens(repair_payload, model),
            messages=[
                {"role": "system", "content": (
                    "你是 annotation reference 修復器。只能修改 segment_id，"
                    "不得改變其他 annotation 語義，不得新增或刪除 annotation，"
                    "不得輸出 text、source_start、source_end 或 span_hash。"
                )},
                {"role": "user", "content": (
                    "待補的合法 segment_id 只有：" + json.dumps(allowed, ensure_ascii=False)
                    + "\n請將每個下列 annotation 的 segment_id 各自重新綁定到一個待補 ID，"
                    "只輸出 JSON object：\n" + repair_payload
                )},
            ],
        )
        if native_format is not None:
            repair_kwargs["response_format"] = structured_output.build_adapter_response_format(
                selection, schema,
                adapter_key=str((_bound_provider() or {}).get("provider_type") or "openai"),
                segment_ids=allowed,
                require_exact_count=False,
            )
        try:
            repaired_response = _invoke(
                lambda: client.chat.completions.create(**repair_kwargs), metrics,
                request_kind="reference_repair",
            )
            repaired = {"segments": _extract_json(
                repaired_response.choices[0].message.content or ""
            ).get("segments", [])}
            repaired = {
                "segments": [
                    {
                        **item,
                        "speaker": item.get("speaker"),
                        "speaker_candidate": item.get("speaker_candidate"),
                        "speaker_gender": item.get("speaker_gender", item.get("gender")),
                        "speaker_age": item.get("speaker_age", item.get("age")),
                        "attribution": item.get("attribution"),
                        "confidence": item.get("confidence"),
                        "vocab": item.get("vocab"),
                    }
                    for item in repaired.get("segments", [])
                    if isinstance(item, dict)
                ],
            }
            repaired_valid = structured_output.validate_annotation_subset(
                repaired, set(missing_ids), schema,
            )
            repaired_ids = {item["segment_id"] for item in repaired_valid}
            if (repaired_ids.intersection(existing_ids)
                    or repaired_ids - set(missing_ids)
                    or len(repaired_ids) != len(invalid)
                    or len(repaired_valid) != len(invalid)):
                raise ValueError("reference repair coverage mismatch")
            return repaired_valid
        except Exception as repair_error:
            counts = {}
            for item in payload.get("segments", []):
                if isinstance(item, dict):
                    key = str(item.get("segment_id"))
                    counts[key] = counts.get(key, 0) + 1
            raise structured_output.StructuredOutputError(
                "annotation_reference_invalid", "annotation segment reference remains invalid",
                diagnostics={
                    "expectedSegmentCount": len(ids),
                    "returnedSegmentCount": len(payload.get("segments", [])),
                    "unknownSegmentIds": sorted({
                        str(item.get("segment_id")) for item in payload.get("segments", [])
                        if isinstance(item, dict) and str(item.get("segment_id")) not in ids
                    })[:64],
                    "duplicateSegmentIds": sorted(
                        key for key, count in counts.items() if count > 1
                    )[:64],
                    "missingSegmentIds": sorted(set(ids) - {
                        str(item.get("segment_id")) for item in payload.get("segments", [])
                        if isinstance(item, dict) and str(item.get("segment_id")) in ids
                    })[:64],
                    "segmentationVersion": source_faithful.SEGMENTATION_VERSION,
                    "structuredMode": selection.applied_mode,
                    "provider": str((_bound_provider() or {}).get("provider_type") or "unknown"),
                    "model": model,
                    "schemaVersion": schema.version,
                    "schemaHash": schema.schema_hash,
                    "promptVersion": ANALYSIS_PROMPT_VERSION,
                },
            ) from repair_error
    try:
        result = _extract_with_repair(
            lambda: client.chat.completions.create(**kwargs), metrics,
            repair_create=lambda raw: client.chat.completions.create(**_chat_request_kwargs(
                model=model, completion_tokens=_repair_max_tokens(raw, model), temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": (
                        "只修復 JSON 語法，不得重新分析、增刪 annotation 語義，"
                        "不得加入 text 或 source span 欄位。"
                    )},
                    {"role": "user", "content": raw},
                ],
            )),
            request_kind="targeted_completion" if targeted_completion else "primary",
        )
    except Exception as error:
        if _is_transient_provider_error(error):
            retried = _retry(
                lambda: client.chat.completions.create(**kwargs), metrics
            )
            if retried is not None:
                result = retried
            else:
                if _is_timeout_error(error):
                    raise analysis_svc.AnalysisProviderTimeoutExhaustedError(
                        "provider timeout retry budget exhausted"
                    ) from error
                raise
        elif (
            mode == "best_effort"
            and selection.applied_mode == "strict_json_schema"
            and getattr(error, "status_code", None) == 400
        ):
            if metrics is not None:
                with metrics["lock"]:
                    metrics["structured_mode_applied"] = "json_object"
                    metrics["structured_fallback_reason"] = "runtime_structured_output_unsupported"
            fallback = dict(kwargs)
            fallback.pop("response_format", None)
            fallback["response_format"] = {"type": "json_object"}
            result = _extract_with_repair(
                lambda: client.chat.completions.create(**fallback), metrics,
                repair_create=lambda raw: client.chat.completions.create(**_chat_request_kwargs(
                    model=model, completion_tokens=_repair_max_tokens(raw, model), temperature=0,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": "只修復 JSON 語法，不得改變 annotation 語義。"},
                        {"role": "user", "content": raw},
                    ],
                )),
                request_kind="targeted_completion" if targeted_completion else "primary",
            )
        else:
            raise
    if metrics is not None:
        with metrics["lock"]:
            budget = getattr(_request_budget_local, "current", None) or {}
            metrics["structured_repair_count"] = int(budget.get("malformed_retries", 0))
    try:
        return validate_or_complete(result)
    except structured_output.StructuredOutputError as error:
        if error.code not in {"invalid_segment_id", "duplicate_segment_id"} or not allow_targeted_completion:
            raise
        diagnostics = error.diagnostics or {}
        unknown_ids = set(diagnostics.get("unknownSegmentIds") or [])
        duplicate_ids = set(diagnostics.get("duplicateSegmentIds") or [])
        valid_items = []
        seen_valid = set()
        repair_items = []
        for item in result.get("segments", []):
            if not isinstance(item, dict):
                continue
            segment_id = str(item.get("segment_id"))
            if segment_id not in ids:
                if segment_id in unknown_ids:
                    repair_items.append(item)
                continue
            if segment_id in seen_valid:
                repair_items.append(item)
            else:
                seen_valid.add(segment_id)
                valid_items.append(item)
        if not repair_items:
            raise
        missing_ids = set(ids) - seen_valid
        repaired_items = repair_invalid_references(result, repair_items, missing_ids)
        return validate_or_complete({"segments": valid_items + repaired_items})


def _retry(create, metrics=None):
    """等待並重試 429/529，回傳結果；全部失敗回 None。"""
    for wait in (2, 4):
        time.sleep(wait)
        try:
            _metric_inc(metrics, "retry_count")
            budget = getattr(_request_budget_local, "current", None)
            if budget is not None:
                budget["transient_retries"] = budget.get("transient_retries", 0) + 1
            return _extract_with_repair(create, metrics, request_kind="retry")
        except Exception as e:
            if not _is_transient_provider_error(e):
                raise e
    return None


def _retry_rate_limit(client, kwargs: dict, metrics=None):
    return _retry(lambda: client.chat.completions.create(**kwargs), metrics)


def _retry_json(client, kwargs: dict, metrics=None):
    return _retry(lambda: client.chat.completions.create(response_format={"type": "json_object"}, **kwargs), metrics)


def _split_text(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK:
        return [text]
    paras = [p for p in re.split(r"\n+", text) if p.strip()]
    parts, buf = [], []
    size = 0
    for p in paras:
        if size + len(p) > MAX_CHUNK and buf:
            parts.append("\n".join(buf))
            buf, size = [], 0
        buf.append(p)
        size += len(p) + 1
    if buf:
        parts.append("\n".join(buf))
    return parts


def count_analysis_chunks(text: str) -> int:
    """回傳實際 analyzer 將使用的 chunk 數量。"""
    return len(_split_text(text))


def count_source_faithful_chunks(text: str) -> int:
    """Return deterministic source-faithful annotation group count."""
    segmentation = source_faithful.segment_source(text)
    return len(source_faithful.group_segments(
        segmentation, max_chars=MAX_CHUNK,
        max_segments=source_faithful.MAX_SEGMENTS_PER_GROUP,
    ))


def _clean(text: str) -> str:
    text = text.replace("—", "——")
    return text.strip()


_OUTER_DIALOGUE_QUOTES = {
    "「": "」", "『": "』", "“": "”", '"': '"',
}


def _strip_outer_dialogue_wrapper(text: str) -> str:
    """Remove one whole-text quote wrapper without touching inner dialogue."""
    if len(text) < 2:
        return text
    closing = _OUTER_DIALOGUE_QUOTES.get(text[0])
    return text[1:-1] if closing and text[-1] == closing else text


def _ensure_speakable(text: str) -> str:
    """片段若「只含省略號、不含任何中文字」就沒有可朗讀內容（Edge 會無聲/出錯）。
    此類片段改為「嗯……」，避免壞字。若原本已含中文字則維持原狀。"""
    if not text:
        return text
    if any("\u4e00" <= ch <= "\u9fff" for ch in text):
        return text
    if "\u2026" in text:
        return "嗯……"
    return text


def _normalize_segment(s: dict, fallback_speaker: str = "旁白") -> dict:
    typ = s.get("type", "narration")
    speaker = s.get("speaker") or (fallback_speaker if typ != "dialogue" else None)
    if typ == "vocab":
        utterances = s.get("utterances") or []
        for u in utterances:
            u.setdefault("lang", "zh")
            u["text"] = _clean(u.get("text", ""))
        utterances = [u for u in utterances if u.get("text")]
        vocab = s.get("vocab") or {"zh": "", "en": "", "spelling": "", "example": "", "level": "A1"}
        if not utterances and vocab.get("en"):
            utterances = [
                {"lang": "zh", "text": f"『{vocab.get('zh','')}』的英文是——"},
                {"lang": "en", "text": vocab["en"]},
                {"lang": "zh", "text": "，拼作 " + vocab.get("spelling", "") + "。"},
            ]
        return {"type": "vocab", "speaker": speaker, "utterances": utterances, "vocab": vocab}
    if typ == "bilingual":
        zh = _ensure_speakable(_clean(s.get("zh", "")))
        en = _clean(s.get("en", ""))
        return {"type": "bilingual", "speaker": speaker, "zh": zh, "en": en}
    text = _ensure_speakable(_clean(s.get("text", "")))
    if typ == "dialogue":
        text = _strip_outer_dialogue_wrapper(text)
    return {"type": typ, "speaker": speaker, "text": text}


def analyze_chapter(book: dict, seq: int, text: str, prev_text: str = "", on_progress=None,
                    partial_cache=None) -> dict:
    """分析單章。回傳 {speakers, segments, model}。會把角色名併入書級別名冊。

    prev_text：前一章結尾片段，協助判斷跨章節的說話者延續。
    """
    # 跨章節脈絡：已知角色名 + 上一章結尾
    context_lines = []
    known = [n for n in book.get("voices", {}) if n != "_english"]
    if known:
        context_lines.append(
            "本書已知說話者（請沿用這些名字，不要改名；若本章出現不在其中的新角色，仍請照實新增）："
            + "、".join(known)
        )
    tail = (prev_text or "")[-900:]
    if tail.strip():
        context_lines.append(
            "上一章最後的內容（用於判斷跨章節對話延續與本章開頭的說話者）：\n" + tail.strip()
        )
    context = "\n\n".join(context_lines)

    parts = _split_text(text)
    metrics = {"lock": threading.Lock(), "request_count": 0, "retry_count": 0,
               "total_chunks": len(parts), "completed_chunks": 0, "running_chunks": 0,
               "cache_hit_chunks": 0, "requested_chunks": 0, "saved_provider_requests": 0,
               "chunk_latencies": [], "failure_type": "", "usage": usage_metrics.empty(),
               "on_progress": on_progress}
    total_started = time.time()
    segments: list[dict] = []
    speaker_set: dict[str, dict] = {}  # name -> {"gender", "age"}
    system_prompt = _make_prompt(book)
    prompt_hash = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()

    def _completed_snapshot():
        with metrics["lock"]:
            metrics["completed_chunks"] += 1
            return {
                "total_chunks": len(parts), "completed_chunks": metrics["completed_chunks"],
                "running_chunks": metrics["running_chunks"], "retry_count": metrics["retry_count"],
            }

    def _analyze_part(idx: int, part: str) -> dict:
        _log.info("analyze %s ch%s 分段 %d/%d 開始（%d 字）", book["id"], seq, idx + 1, len(parts), len(part))
        t0 = time.time()
        chunk_context = context if idx == 0 else ""
        identity = partial_cache.identity_for(idx, part, context=chunk_context,
                                              prompt_hash=prompt_hash) if partial_cache else None
        if partial_cache and identity:
            cached = partial_cache.reuse(identity)
            if cached is not None:
                _metric_inc(metrics, "cache_hit_chunks")
                metrics["saved_provider_requests"] = getattr(partial_cache, "saved_provider_requests", 0)
                snapshot = _completed_snapshot()
                if on_progress:
                    on_progress({"stage": "analyzing", **snapshot})
                return cached
            claim = partial_cache.claim(identity)
            if claim.get("state") == "succeeded":
                cached = partial_cache.reuse(identity)
                if cached is not None:
                    _metric_inc(metrics, "cache_hit_chunks")
                    metrics["saved_provider_requests"] = getattr(partial_cache, "saved_provider_requests", 0)
                    snapshot = _completed_snapshot()
                    if on_progress:
                        on_progress({"stage": "analyzing", **snapshot})
                    return cached
            owner_token = claim.get("_owner_token")
            _metric_inc(metrics, "requested_chunks")
        else:
            owner_token = None
        with metrics["lock"]:
            metrics["running_chunks"] += 1
            snapshot = {
                "total_chunks": len(parts), "completed_chunks": metrics["completed_chunks"],
                "running_chunks": metrics["running_chunks"], "retry_count": metrics["retry_count"],
            }
        if on_progress:
            on_progress({"stage": "analyzing", **snapshot})
        budget = {"lock": threading.Lock(), "usage": usage_metrics.empty(),
                  "requests": 0, "max_requests": 6, "malformed_retries": 0,
                  "transient_retries": 0, "retries": 0}
        _request_budget_local.current = budget
        try:
            data = _call_llm(part, context=chunk_context, system_prompt=system_prompt, metrics=metrics)
            if partial_cache and identity and owner_token:
                partial_cache.record_metrics(identity, request_count=budget["requests"],
                                             retry_count=budget["retries"],
                                             usage_metrics=usage_metrics.snapshot(budget))
                partial_cache.publish(identity, owner_token, data)
        except MalformedJSONError:
            with metrics["lock"]:
                metrics["failure_type"] = "malformed_json"
            if partial_cache and identity and owner_token:
                partial_cache.record_metrics(identity, request_count=budget["requests"],
                                             retry_count=budget["retries"],
                                             usage_metrics=usage_metrics.snapshot(budget))
                partial_cache.fail(identity, owner_token, "malformed_json")
            raise
        except Exception as exc:
            with metrics["lock"]:
                metrics["failure_type"] = "provider_or_pipeline"
            if partial_cache and identity and owner_token:
                partial_cache.record_metrics(identity, request_count=budget["requests"],
                                             retry_count=budget["retries"],
                                             usage_metrics=usage_metrics.snapshot(budget))
                partial_cache.fail(identity, owner_token, type(exc).__name__)
            raise
        finally:
            _request_budget_local.current = None
            with metrics["lock"]:
                metrics["running_chunks"] = max(0, metrics["running_chunks"] - 1)
                metrics["chunk_latencies"].append(round(time.time() - t0, 1))
        with metrics["lock"]:
            snapshot = {
                "total_chunks": len(parts), "completed_chunks": metrics["completed_chunks"] + 1,
                "running_chunks": metrics["running_chunks"], "retry_count": metrics["retry_count"],
            }
            metrics["completed_chunks"] += 1
        if on_progress:
            on_progress({"stage": "analyzing", **snapshot})
        _log.info("analyze %s ch%s 分段 %d/%d 完成（%.1fs，模型=%s）",
                  book["id"], seq, idx + 1, len(parts), time.time() - t0, _last_model)
        return data

    # 同章多段並行送（輸出依序收集，品質不變）。並行度要小，避免撞免費層的 429/529。
    results: list[dict] = []
    try:
        if len(parts) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=_CHUNK_CONCURRENCY) as ex:
                results = list(ex.map(lambda p: _analyze_part(p[0], p[1]), enumerate(parts)))
        else:
            results = [_analyze_part(0, parts[0])]
    except Exception:
        if on_progress:
            with metrics["lock"]:
                on_progress({
                    "stage": "analyzing",
                    "total_chunks": len(parts),
                    "completed_chunks": metrics["completed_chunks"],
                    "running_chunks": metrics["running_chunks"],
                    "retry_count": metrics["retry_count"],
                    "cache_hit_chunks": metrics["cache_hit_chunks"],
                    "requested_chunks": metrics["requested_chunks"],
                    "provider_request_count": metrics["request_count"],
                    "reused_chunks": metrics["cache_hit_chunks"],
                    "chunk_retry_count": metrics["retry_count"],
                    "saved_provider_requests": metrics["saved_provider_requests"],
                    "total_duration": round(time.time() - total_started, 3),
                })
        _log.warning("analysis %s ch%s failed total=%.1fs chunks=%d requests=%d retries=%d failure_type=%s",
                     book["id"], seq, time.time() - total_started, len(parts),
                     metrics["request_count"], metrics["retry_count"], metrics["failure_type"])
        raise
    _log.info("analysis %s ch%s finished total=%.1fs chunks=%d requests=%d retries=%d chunk_latencies=%s",
              book["id"], seq, time.time() - total_started, len(parts),
              metrics["request_count"], metrics["retry_count"], metrics["chunk_latencies"])
    for i, data in enumerate(results):
        for sp in data.get("speakers", []):
            name = (sp.get("name") or "").strip()
            if name:
                speaker_set[name] = {
                    "gender": sp.get("gender", "未知"),
                    "age": sp.get("age") or "未知",
                }
        for local_segment_index, s in enumerate(data.get("segments", [])):
            normalized = _normalize_segment(s)
            # 只供 normalize failure diagnostics 使用；canonical artifact 會丟棄。
            normalized["_analysis_chunk_index"] = i
            normalized["_analysis_segment_index"] = local_segment_index
            segments.append(normalized)

    # Canonical v3 only accepts narration/dialogue.  Drop provider-only
    # vocab/bilingual items before normalize; otherwise empty legacy vocab
    # records can make a valid partial aggregate fail deterministically.
    segments = [s for s in segments if s.get("type") in ("narration", "dialogue")]

    # 名冊是 ready canonical artifact 的 projection，不能在 analyzer 階段寫回。
    speakers = []
    seen = set()
    for n, d in speaker_set.items():
        if n not in seen:
            speakers.append({"name": n, "gender": d.get("gender", "未知"),
                             "age": d.get("age", "未知"), "conflicts": []})
            seen.add(n)
    for seg in segments:
        name = seg.get("speaker")
        if name and name not in seen:
            speakers.append({"name": name, "gender": "未知", "age": "未知", "conflicts": []})
            seen.add(name)
    if not any(s["name"] == "旁白" for s in speakers):
        speakers.insert(0, {"name": "旁白", "gender": "未知", "age": "未知", "conflicts": []})
    return {
        "speakers": speakers, "segments": segments, "model": _last_model,
        "metrics": {
            "cacheHitChunks": metrics["cache_hit_chunks"],
            "requestedChunks": metrics["requested_chunks"],
            "providerRequestCount": metrics["request_count"],
            "reusedChunks": metrics["cache_hit_chunks"],
            "chunkRetryCount": metrics["retry_count"],
            "savedProviderRequests": metrics["saved_provider_requests"],
            "usageMetrics": usage_metrics.snapshot(metrics),
            "totalDuration": round(time.time() - total_started, 3),
        },
    }


def analyze_source_faithful(text: str, *, context: str = "", on_progress=None,
                            partial_cache=None, mode: str = "best_effort",
                            book: dict | None = None) -> dict:
    """Analyze deterministic source spans with annotation-only AI output.

    This entry point is intentionally separate from the legacy text-returning
    ``analyze_chapter`` until the v4 executor switches its persistence gate.
    It is the only path allowed to feed source-faithful canonicalization.
    """
    total_started = time.time()
    segmentation = source_faithful.segment_source(text)
    groups = source_faithful.group_segments(
        segmentation, max_chars=MAX_CHUNK,
        max_segments=source_faithful.MAX_SEGMENTS_PER_GROUP,
    )
    include_vocab = settings.effective_category(book or {}) == settings.CAT_VOCAB
    vocab_level = (book or {}).get("vocabLevel") or "AUTO"
    schema = _structured_annotation_schema(include_vocab=include_vocab)
    capability = _structured_capability()
    profile = _request_profile(_model_chain()[0])
    planned_selection = structured_output.select_output_mode(capability, schema, mode=mode)
    if partial_cache is not None:
        # The identity is planned from the capability snapshot before any
        # request.  A runtime strict decline is deliberately not published
        # under this key; it must be retried under the fallback identity.
        partial_cache.segmentation_version = segmentation["segmentationVersion"]
        partial_cache.output_mode = planned_selection.applied_mode
        partial_cache.annotation_schema_id = schema.schema_id
        partial_cache.annotation_schema_version = schema.version
        partial_cache.annotation_schema_hash = schema.schema_hash
        partial_cache.capability_version = capability.capability_version
        partial_cache.request_profile_version = profile.version
        partial_cache.request_profile_hash = profile.hash
        partial_cache.span_hashes_by_chunk = {
            group["chunk_index"]: [span["span_hash"] for span in group["segments"]]
            for group in groups
        }
    metrics = {
        "lock": threading.Lock(), "total_chunks": len(groups),
        "completed_chunks": 0, "running_chunks": 0, "retry_count": 0,
        "request_count": 0, "cache_hit_chunks": 0, "requested_chunks": 0,
        "saved_provider_requests": 0, "chunk_latencies": [],
        "usage": usage_metrics.empty(),
        "structured_mode_requested": mode, "structured_mode_applied": None,
        "structured_schema_version": None, "structured_schema_hash": None,
        "structured_fallback_reason": None, "structured_capability_source": None,
        "on_progress": on_progress,
    }
    metrics["structured_mode_applied"] = planned_selection.applied_mode
    metrics["structured_schema_version"] = schema.version
    metrics["structured_schema_hash"] = schema.schema_hash
    metrics["structured_capability_source"] = capability.source
    metrics["structured_fallback_reason"] = planned_selection.fallback_reason
    annotations: list[dict] = []
    normalized = source_faithful.normalize_source(text)
    for group in groups:
        started = time.time()
        spans = group["segments"]
        chunk_start = spans[0]["source_start"]
        chunk_end = spans[-1]["source_end"]
        chunk_text = normalized[chunk_start:chunk_end]
        local_context = "\n---\n".join(
            normalized[max(0, span["source_start"] - 160):
                       min(len(normalized), span["source_end"] + 160)]
            for span in spans
        )[:12000]
        annotation_context = "\n".join(x for x in (context, local_context) if x)
        identity = None
        owner_token = None
        if partial_cache:
            identity = partial_cache.identity_for(
                group["chunk_index"], chunk_text, context=context if group["chunk_index"] == 0 else "",
                prompt_hash=hashlib.sha256(
                    ANALYSIS_PROMPT_VERSION.encode("utf-8")
                ).hexdigest(),
            )
            cached = partial_cache.reuse(identity)
            if cached is not None:
                annotations.extend(cached.get("annotations", cached.get("segments", [])))
                cached_meta = cached.get("structuredOutput") or {}
                metrics["structured_mode_applied"] = cached_meta.get("appliedMode", metrics["structured_mode_applied"])
                metrics["structured_fallback_reason"] = cached_meta.get("fallbackReason", metrics["structured_fallback_reason"])
                metrics["cache_hit_chunks"] += 1
                metrics["saved_provider_requests"] = getattr(partial_cache, "saved_provider_requests", 0)
                metrics["completed_chunks"] += 1
                continue
            claim = partial_cache.claim(identity)
            if claim.get("state") == "succeeded":
                cached = partial_cache.reuse(identity)
                if cached is not None:
                    annotations.extend(cached.get("annotations", cached.get("segments", [])))
                    cached_meta = cached.get("structuredOutput") or {}
                    metrics["structured_mode_applied"] = cached_meta.get("appliedMode", metrics["structured_mode_applied"])
                    metrics["structured_fallback_reason"] = cached_meta.get("fallbackReason", metrics["structured_fallback_reason"])
                    metrics["cache_hit_chunks"] += 1
                    metrics["saved_provider_requests"] = getattr(partial_cache, "saved_provider_requests", 0)
                    metrics["completed_chunks"] += 1
                    continue
            owner_token = claim.get("_owner_token")
            metrics["requested_chunks"] += 1
        metrics["running_chunks"] += 1
        if on_progress:
            on_progress({"stage": "analyzing", "total_chunks": len(groups),
                         "completed_chunks": metrics["completed_chunks"],
                         "running_chunks": metrics["running_chunks"],
                         "structured_mode_requested": mode,
                         "structured_mode_applied": metrics.get("structured_mode_applied")})
        budget = {"lock": threading.Lock(), "usage": usage_metrics.empty(),
                  "requests": 0, "max_requests": 6, "malformed_retries": 0,
                  "transient_retries": 0, "retries": 0}
        _request_budget_local.current = budget
        try:
            result = _call_structured_annotations(
                chunk_text, spans, context=annotation_context,
                metrics=metrics, mode=mode, chunk_index=group["chunk_index"],
                include_vocab=include_vocab, vocab_level=vocab_level,
            )
            annotations.extend(result)
            if partial_cache and identity and owner_token and metrics.get("structured_mode_applied") == identity.get("outputMode"):
                partial_cache.record_metrics(identity, request_count=budget["requests"],
                                             retry_count=budget["retries"],
                                             usage_metrics=usage_metrics.snapshot(budget))
                partial_cache.publish(identity, owner_token, {
                    # Partial storage keeps the generic segment envelope;
                    # ``annotations`` remains the v4 reader-friendly alias.
                    "segments": result,
                    "annotations": result,
                    "structuredOutput": {
                        "requestedMode": metrics.get("structured_mode_requested"),
                        "appliedMode": metrics.get("structured_mode_applied"),
                        "schemaVersion": metrics.get("structured_schema_version"),
                        "schemaHash": metrics.get("structured_schema_hash"),
                        "fallbackReason": metrics.get("structured_fallback_reason"),
                        "capabilitySource": metrics.get("structured_capability_source"),
                    },
                })
            elif partial_cache and identity and owner_token:
                partial_cache.fail(identity, owner_token, "structured_mode_changed")
        except Exception as exc:
            # A failed v4 chunk must release its lease immediately.  Otherwise
            # a failed analysis leaves running partials that can block reuse or
            # make the next attempt appear permanently in progress.
            if partial_cache and identity and owner_token:
                partial_cache.record_metrics(
                    identity, request_count=budget["requests"],
                    retry_count=budget["retries"],
                    usage_metrics=usage_metrics.snapshot(budget),
                )
                partial_cache.fail(
                    identity, owner_token,
                    str(getattr(exc, "code", "") or type(exc).__name__),
                )
            raise
        finally:
            _request_budget_local.current = None
            metrics["running_chunks"] = max(0, metrics["running_chunks"] - 1)
            metrics["completed_chunks"] += 1
            metrics["chunk_latencies"].append(round(time.time() - started, 1))
            if on_progress:
                on_progress({"stage": "analyzing", "total_chunks": len(groups),
                             "completed_chunks": metrics["completed_chunks"],
                             "running_chunks": metrics["running_chunks"],
                             "structured_mode_requested": mode,
                             "structured_mode_applied": metrics.get("structured_mode_applied")})
    artifact = source_faithful.apply_annotations(segmentation, annotations, source=text)
    artifact["structuredOutput"] = {
        "requestedMode": metrics.get("structured_mode_requested"),
        "appliedMode": metrics.get("structured_mode_applied"),
        "schemaVersion": metrics.get("structured_schema_version"),
        "schemaHash": metrics.get("structured_schema_hash"),
        "fallbackReason": metrics.get("structured_fallback_reason"),
        "capabilitySource": metrics.get("structured_capability_source"),
    }
    artifact["metrics"] = {
        "totalChunks": metrics["total_chunks"],
        "completedChunks": metrics["completed_chunks"],
        "providerRequestCount": metrics["request_count"],
        "cacheHitChunks": metrics["cache_hit_chunks"],
        "requestedChunks": metrics["requested_chunks"],
        "retryCount": metrics["retry_count"],
        "repairCount": int(metrics.get("structured_repair_count", 0)),
        "savedProviderRequests": metrics["saved_provider_requests"],
        "usageMetrics": usage_metrics.snapshot(metrics),
        "totalDuration": round(time.time() - total_started, 3),
    }
    return artifact


def _merge_speaker_info(info: dict, seq: int, name: str, gender: str, age: str):
    """跨章節彙整角色性別/年齡。

    以「多數決」決定最終值：各章判定次數最多者勝；平手時保留先出現者。
    與最終值不同的章節記入 conflicts，供使用者確認。
    """
    gender = (gender or "未知").strip() or "未知"
    age = (age or "未知").strip() or "未知"
    cur = info.get(name)
    if cur is None:
        info[name] = {
            "firstCh": seq,
            "judgements": [{"ch": seq, "gender": gender, "age": age}],
            "gender": gender, "age": age, "conflicts": [],
        }
        return
    # 相容舊資料或由旁白 setdefault 建立的缺欄位 entry
    if not isinstance(cur.get("judgements"), list):
        cur["judgements"] = []
    cur["judgements"].append({"ch": seq, "gender": gender, "age": age})
    best_g, best_a = _majority(cur["judgements"])
    if best_g != "未知":
        cur["gender"] = best_g
    if best_a != "未知":
        cur["age"] = best_a
    cur["conflicts"] = [
        j for j in cur["judgements"]
        if (j["gender"] != "未知" and j["gender"] != cur["gender"])
        or (j["age"] != "未知" and j["age"] != cur["age"])
    ]


def _majority(judgements: list[dict]):
    """依各章判定取最多者；未知忽略；平手取先出現者。"""
    def pick(values):
        counts = {}
        order = []
        for v in values:
            if v == "未知":
                continue
            if v not in counts:
                counts[v] = 0
                order.append(v)
            counts[v] += 1
        if not counts:
            return "未知"
        best = order[0]
        for v in order:
            if counts[v] > counts[best]:
                best = v
        return best
    gs = [j["gender"] for j in judgements]
    as_ = [j["age"] for j in judgements]
    return pick(gs), pick(as_)
