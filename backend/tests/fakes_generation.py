"""Deterministic, non-network providers used by orchestration race tests."""
from __future__ import annotations

import threading
import time


class FakeProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str, retryable: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.retry_after = retry_after


class FakeAIProvider:
    def __init__(self, *, delay: float = 0, outcome: str = "success", barrier: threading.Event | None = None):
        self.delay = delay
        self.outcome = outcome
        self.barrier = barrier
        self.calls = 0

    def analyze(self, text: str) -> dict:
        self.calls += 1
        if self.barrier:
            self.barrier.wait(timeout=5)
        if self.delay:
            time.sleep(self.delay)
        if self.outcome != "success":
            raise FakeProviderError(self.outcome, code=self.outcome, retryable=self.outcome in {
                "provider_busy", "rate_limited", "network_transient", "timeout"})
        return {"provider": "fake-ai", "textHash": __import__("hashlib").sha256(text.encode()).hexdigest()}


class FakeTTSProvider:
    def __init__(self, *, delay: float = 0, outcome: str = "success", barrier: threading.Event | None = None):
        self.delay = delay
        self.outcome = outcome
        self.barrier = barrier
        self.calls = []
        self.cancel_requested = False

    def synthesize(self, text: str, *, segment_id: str = "segment") -> bytes:
        self.calls.append(segment_id)
        if self.barrier:
            self.barrier.wait(timeout=5)
        if self.cancel_requested:
            raise FakeProviderError("cancelled", code="cancelled")
        if self.delay:
            time.sleep(self.delay)
        if self.outcome != "success":
            raise FakeProviderError(self.outcome, code=self.outcome, retryable=self.outcome in {
                "provider_busy", "rate_limited", "network_transient", "timeout"})
        return f"fake-audio:{segment_id}:{text}".encode("utf-8")
