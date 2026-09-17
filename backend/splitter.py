"""章節拆分：依「第N章」標題或分隔線切章。"""
import re

# 匹配「第1章」「第100章」「第一章」以及傳統戲曲常見的「第01齣」標題。
# 兩者都代表作品的章節邊界；保留既有 title capture 以免改變既有匯入契約。
CHAPTER_HEAD_RE = re.compile(r"^第\s*([0-9０-９一二三四五六七八九十百千零〇两]+)\s*(?:章|齣)[　 \t]*(.*)$")

# 分隔線（═ ─ = 等連續字元）
SEP_RE = re.compile(r"^[═─=＝\s]{6,}$")


def _is_sep(line: str) -> bool:
    return bool(SEP_RE.match(line.strip()))


def split_chapters(text: str, mode: str = "title", char_limit: int = 3000) -> list[dict]:
    """依 mode 拆分章節，回傳 [{seq, title, text, chars}]。

    mode="title"：偵測「第N章」標題與分隔線（預設）。
    mode="chars"：依字數分段，靠近 char_limit 時在段落邊界切開。
    mode="blank"：依連續空行（>=2）或分隔線分段。
    """
    mode = (mode or "title").lower()
    if mode == "chars":
        return _split_by_chars(text, char_limit)
    if mode == "blank":
        return _split_by_blank(text)
    return _split_by_title(text)


def _split_by_title(text: str) -> list[dict]:
    """依「第N章」標題或分隔線切章。text 不含標題行與分隔線。"""
    lines = text.split("\n")

    title = ""
    body: list[str] = []
    chapters: list[dict] = []
    seq = 0

    # 開頭標題/作者/標籤清整：記錄書名以便去掉重複標題行
    book_title = lines[0].strip() if lines else ""

    for line in lines:
        stripped = line.strip()
        if not stripped or _is_sep(line):
            # 空行與分隔線：保留換段資訊（收斂為單一空行）
            if body and body[-1] != "":
                body.append("")
            continue
        m = CHAPTER_HEAD_RE.match(stripped)
        if m:
            if title or body:
                chapters.append({"seq": seq, "title": title or "序章", "text": "\n".join(body).strip()})
                seq += 1
            title = m.group(2).strip() or "章節"
            body = []
            continue
        # 去重複標題行（章首再次出現標題名）
        if body == [] and stripped == title and seq > 0:
            continue
        body.append(stripped)

    if title or body:
        chapters.append({"seq": seq, "title": title or "序章", "text": "\n".join(body).strip()})

    # 清掉開頭的非正文（書名/作者/標籤）— 只針對第 0 章
    if chapters:
        pro = chapters[0]
        kept = []
        skip_tags = True
        for ln in pro["text"].split("\n"):
            s = ln.strip()
            if skip_tags:
                if not s:
                    continue
                if s == book_title:
                    continue
                if s.startswith("《") and s.endswith("》"):
                    continue  # 書名
                if re.match(r"^[（(]\s*作者\s*[)）]", s) or \
                   re.match(r"^(作者|著者|編者|作者：)[：:]", s) or re.match(r"^作者[：:]\S", s):
                    continue  # 作者行
                if re.match(r"^【[^】]+】$", s):
                    continue
                if len(s) <= 8 and "「" not in s and re.match(r"^[\u4e00-\u9fff·．。，]{1,8}$", s) and kept and kept[-1][0].isalpha() is False:
                    # 可能作者名，僅移除書名正下方的單行短作者名
                    if len(kept) == 0:
                        continue
                skip_tags = False
            kept.append(ln)
        pro["text"] = "\n".join(kept).strip()
        if not pro["text"]:
            chapters.pop(0)
            for i, c in enumerate(chapters):
                c["seq"] = i

    for c in chapters:
        c["chars"] = len(c["text"])
    return chapters


def _split_by_chars(text: str, char_limit: int = 3000) -> list[dict]:
    """依字數分段：累積到接近 char_limit 時在最近的段落邊界切開；
    緊湊結構（單一超長段落）則在接近 limit 處精確截斷。"""
    try:
        char_limit = max(int(char_limit), 200)
    except (TypeError, ValueError):
        char_limit = 3000

    # 先切成段落（段落間以一空行分隔）
    paragraphs: list[str] = []
    cur: list[str] = []
    for line in text.split("\n"):
        if line.strip():
            cur.append(line)
        elif cur:
            paragraphs.append("\n".join(cur))
            cur = []
    if cur:
        paragraphs.append("\n".join(cur))

    # 把超長段落拆成 <= char_limit 的子段，以免單段造成超大章節
    units: list[str] = []
    for p in paragraphs:
        while len(p) > char_limit:
            idx = p.rfind("\n", 0, char_limit)  # 優先在換行處切，保留行結構
            take = idx if idx > 0 else char_limit
            units.append(p[:take])
            p = p[take:]
        if p:
            units.append(p)

    chapters: list[dict] = []
    buf: list[str] = []
    acc = 0

    def flush():
        nonlocal buf, acc
        body = "\n\n".join(buf).strip()
        if body:
            chapters.append({"seq": len(chapters), "title": f"第 {len(chapters) + 1} 段", "text": body})
        buf, acc = [], 0

    for u in units:
        buf.append(u)
        acc += len(u)
        if acc >= char_limit:
            flush()
    flush()

    for c in chapters:
        c["chars"] = len(c["text"])
    return chapters


def _split_by_blank(text: str) -> list[dict]:
    """依空行／分隔線分段：連續 2 個以上空行或分隔線視為章節邊界。保留原文行內容。"""
    lines = text.split("\n")
    chapters: list[dict] = []
    buf: list[str] = []
    blank_run = 0

    def flush():
        body = "\n".join(buf).strip()
        if body:
            chapters.append({"seq": len(chapters), "title": f"段落 {len(chapters) + 1}", "text": body})
        buf.clear()

    for line in lines:
        if _is_sep(line):
            flush()
            blank_run = 0
            continue
        if not line.strip():
            blank_run += 1
            if blank_run == 1:
                buf.append("")
            continue
        if blank_run >= 2:
            flush()
        blank_run = 0
        buf.append(line)
    flush()

    for c in chapters:
        c["chars"] = len(c["text"])
    return chapters


def empty_chapter_text(seq: int, title: str) -> str:
    return ""
