from backend.splitter import split_chapters


def test_title_split_supports_traditional_drama_scene_headings():
    source = """牡丹亭\n\n第01齣 標目\n柳夢梅道白。\n\n第02齣 言懷\n杜麗娘唱曲。\n"""

    chapters = split_chapters(source, mode="title")

    assert [chapter["title"] for chapter in chapters] == ["標目", "言懷"]
    assert [chapter["seq"] for chapter in chapters] == [0, 1]
    assert chapters[0]["text"] == "柳夢梅道白。"
    assert chapters[1]["text"] == "杜麗娘唱曲。"
