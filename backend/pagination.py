"""Small, shared pagination primitives for normalized collection endpoints."""

from math import ceil


DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
COMMON_MAX_PAGE_SIZE = 100


def parse_pagination(page=DEFAULT_PAGE, page_size=DEFAULT_PAGE_SIZE, *, max_page_size=COMMON_MAX_PAGE_SIZE):
    """Validate one-based page parameters without silently clamping them."""
    try:
        page = int(page)
        page_size = int(page_size)
    except (TypeError, ValueError) as error:
        raise ValueError("分頁參數必須是整數") from error
    if page < 1:
        raise ValueError("頁碼必須從 1 開始")
    if page_size < 1 or page_size > max_page_size:
        raise ValueError(f"每頁筆數必須介於 1 至 {max_page_size}")
    return page, page_size


def page_result(items, *, total, page, page_size):
    """Build the one canonical collection envelope used by platform pages."""
    total = max(0, int(total))
    total_pages = ceil(total / page_size) if total else 0
    return {
        "items": items,
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }
