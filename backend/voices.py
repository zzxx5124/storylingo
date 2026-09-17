"""Edge TTS 語音清單與預設指派。"""

# 精選語音：中文（含台灣/香港腔）+ 英文（教學用）
VOICES = [
    {"id": "zh-CN-XiaoxiaoNeural", "name": "曉曉（國語·女）", "lang": "zh", "gender": "女", "region": "CN"},
    {"id": "zh-CN-XiaoyiNeural", "name": "曉伊（國語·女）", "lang": "zh", "gender": "女", "region": "CN"},
    {"id": "zh-CN-YunxiNeural", "name": "雲希（國語·男）", "lang": "zh", "gender": "男", "region": "CN"},
    {"id": "zh-CN-YunjianNeural", "name": "雲健（國語·男）", "lang": "zh", "gender": "男", "region": "CN"},
    {"id": "zh-CN-YunyangNeural", "name": "雲揚（國語·男·沉穩）", "lang": "zh", "gender": "男", "region": "CN"},
    {"id": "zh-TW-HsiaoChenNeural", "name": "曉臻（台灣腔·女）", "lang": "zh", "gender": "女", "region": "TW"},
    {"id": "zh-TW-YunJheNeural", "name": "雲哲（台灣腔·男）", "lang": "zh", "gender": "男", "region": "TW"},
    {"id": "zh-HK-HiuMaanNeural", "name": "曉敏（廣東話·女）", "lang": "zh", "gender": "女", "region": "HK"},
    {"id": "en-US-JennyNeural", "name": "Jenny（美式·女）", "lang": "en", "gender": "女", "region": "US"},
    {"id": "en-US-AriaNeural", "name": "Aria（美式·女）", "lang": "en", "gender": "女", "region": "US"},
    {"id": "en-US-GuyNeural", "name": "Guy（美式·男）", "lang": "en", "gender": "男", "region": "US"},
    {"id": "en-US-ChristopherNeural", "name": "Christopher（美式·男）", "lang": "en", "gender": "男", "region": "US"},
    {"id": "en-GB-SoniaNeural", "name": "Sonia（英式·女）", "lang": "en", "gender": "女", "region": "GB"},
    {"id": "en-GB-RyanNeural", "name": "Ryan（英式·男）", "lang": "en", "gender": "男", "region": "GB"},
]

VOICE_IDS = [v["id"] for v in VOICES]
ZH_VOICES = [v for v in VOICES if v["lang"] == "zh"]
EN_VOICES = [v for v in VOICES if v["lang"] == "en"]


def zh_default(gender: str = "未知") -> str:
    if gender == "男":
        return "zh-CN-YunxiNeural"
    if gender == "女":
        return "zh-CN-XiaoyiNeural"
    return "zh-CN-XiaoxiaoNeural"


ENGLISH_VOICE_DEFAULT = "en-US-JennyNeural"

# 旁白預設聲線
NARRATOR_DEFAULT = "zh-CN-XiaoxiaoNeural"
