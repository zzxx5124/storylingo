"""Repository-level architecture guardrails.

These checks intentionally stay small and deterministic. They protect invariants
that are easy for an AI coding agent to accidentally bypass while leaving
normal implementation choices flexible.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = (
    ROOT / "AGENTS.md",
    ROOT / "PROJECT_MAP.md",
    ROOT / "ARCHITECTURE.md",
    ROOT / ".github" / "workflows" / "ci.yml",
    ROOT / "backend" / "jobs.py",
    ROOT / "backend" / "services" / "generation_orchestration.py",
    ROOT / "backend" / "services" / "generation_pipeline.py",
    ROOT / "frontend" / "services" / "api.js",
)


def iter_text_files(directory: Path, suffixes: tuple[str, ...]):
    if not directory.exists():
        return
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix.lower() in suffixes:
            yield path


def fail(message: str) -> None:
    print(f"ARCHITECTURE GUARD: FAIL: {message}")
    raise SystemExit(1)


def main() -> int:
    for path in REQUIRED_FILES:
        if not path.is_file():
            fail(f"required architecture file is missing: {path.relative_to(ROOT)}")

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    project_map = (ROOT / "PROJECT_MAP.md").read_text(encoding="utf-8")
    architecture = (ROOT / "ARCHITECTURE.md").read_text(encoding="utf-8")

    # AGENTS.md is the policy document. Check only policy markers that belong
    # there; implementation/domain identifiers are guarded in the map/architecture docs below.
    required_agent_rules = (
        "AGENTS.md",
        "PROJECT_MAP.md",
        "ARCHITECTURE.md",
        "NovelApi",
        "SQLite",
        "root cause",
        "browser-first",
    )
    for marker in required_agent_rules:
        if marker not in agents:
            fail(f"AGENTS.md lost required collaboration rule/marker: {marker}")

    if "唯一授權的 current 文件" not in agents:
        fail("AGENTS.md must preserve the three-file current-document contract")

    # Backend long-running generation work must stay on the persistent job queue.
    for path in iter_text_files(ROOT / "backend", (".py",)):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "BackgroundTasks" in text:
            fail(f"BackgroundTasks is forbidden in backend source: {path.relative_to(ROOT)}")

    # Application frontend code must use NovelApi rather than introducing a new
    # transport boundary. Service Worker, API adapter, release infrastructure,
    # and browser tests are explicit exceptions.
    for path in iter_text_files(ROOT / "frontend", (".js", ".mjs", ".cjs")):
        rel = path.relative_to(ROOT).as_posix()
        if (
            rel == "frontend/services/api.js"
            or rel == "frontend/release.js"
            or rel == "frontend/sw.js"
            or rel.startswith("frontend/e2e/")
            or "/__tests__/" in rel
            or rel.endswith(".test.js")
            or rel.endswith(".spec.js")
        ):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\bfetch\s*\(", text):
            fail(f"direct fetch() detected outside the canonical API boundary: {rel}")

        # Only flag actual calls to the legacy endpoint, not documentation or
        # admin links such as /api/tts/spec.
        legacy_call = re.search(
            r"(?:\bapi|\bNovelApi\.request)\s*\(\s*[\"'`]\/api\/(?:analyze|tts)(?:[\"'`])",
            text,
        )
        if legacy_call:
            fail(f"legacy generation endpoint caller detected in {rel}: {legacy_call.group(0)}")

    # Cross-document terminology that must remain stable.
    for marker in ("generation_jobs", "SQLite", "NovelApi"):
        if marker not in project_map:
            fail(f"PROJECT_MAP.md lost canonical architecture marker: {marker}")
        if marker not in architecture:
            fail(f"ARCHITECTURE.md lost canonical architecture marker: {marker}")

    print("ARCHITECTURE GUARD: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
