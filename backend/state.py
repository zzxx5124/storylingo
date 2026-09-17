"""全站共享狀態：每書寫鎖。"""
import threading

_book_lock_map: dict[str, threading.Lock] = {}
_book_lock_guard = threading.Lock()


def book_lock(bid: str) -> threading.Lock:
    with _book_lock_guard:
        if bid not in _book_lock_map:
            _book_lock_map[bid] = threading.Lock()
        return _book_lock_map[bid]
