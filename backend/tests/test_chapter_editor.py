from fastapi.testclient import TestClient

from backend.main import app
from backend.tests.conftest import new_author, publish_book


def test_editor_receives_original_text_but_public_reader_does_not():
    author = new_author("chapter_editor_author")
    book = publish_book(author, filename="editor-original.txt")
    public = TestClient(app)

    public_data = public.get(f"/api/books/{book['id']}/chapters/0")
    assert public_data.status_code == 200
    assert "chapter" not in public_data.json()

    editor_data = author.get(f"/api/books/{book['id']}/chapters/0")
    assert editor_data.status_code == 200
    assert editor_data.json()["chapter"]["text"]
