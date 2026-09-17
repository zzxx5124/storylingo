"""搜尋／過濾：q 命中、空結果、serial 過濾與組合。"""
from backend.tests.conftest import new_author, publish_book


def _items(client, **kw):
    r = client.get("/api/books", params={k: v for k, v in kw.items() if v is not None})
    assert r.status_code == 200, r.text
    data = r.json()
    return data["items"] if isinstance(data, dict) else data


def test_q_matches_title(client):
    a = new_author()
    publish_book(a, filename="星海探險.txt")
    publish_book(a, filename="田園日記.txt")
    hits = _items(client, q="星海")
    assert len(hits) == 1 and hits[0]["title"] == "星海探險"


def test_q_matches_synopsis(client):
    a = new_author()
    book = publish_book(a, filename="航海誌.txt")
    a.put(f"/api/books/{book['id']}", json={"synopsis": "一艘改造船的奇幻旅程"})
    hits = _items(client, q="奇幻旅程")
    assert len(hits) == 1 and hits[0]["id"] == book["id"]


def test_q_empty_result(client):
    a = new_author()
    publish_book(a, filename="平凡日常.txt")
    assert _items(client, q="絕不存在的關鍵字qq") == []


def test_serial_filter(client):
    a = new_author()
    b1 = publish_book(a, filename="連載中書.txt")
    b2 = publish_book(a, filename="完結書.txt")
    a.put(f"/api/books/{b2['id']}", json={"serial": "完結"})
    lianzai = _items(client, serial="連載")
    wanjie = _items(client, serial="完結")
    assert {x["id"] for x in lianzai} == {b1["id"]}
    assert {x["id"] for x in wanjie} == {b2["id"]}


def test_q_and_serial_combination(client):
    a = new_author()
    b1 = publish_book(a, filename="星海探險.txt")
    b2 = publish_book(a, filename="星海前傳.txt")
    a.put(f"/api/books/{b2['id']}", json={"serial": "完結"})
    hits = _items(client, q="星海", serial="連載")
    assert [x["id"] for x in hits] == [b1["id"]]


def test_guest_cannot_see_drafts_in_search(client):
    a = new_author()
    import backend.tests.conftest as _c
    book = _c.upload_book(a)
    assert book["status"] == "draft"
    assert _items(client, q=book["title"]) == []