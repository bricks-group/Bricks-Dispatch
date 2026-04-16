from math import ceil


def normalize_pagination(page: int, page_size: int, max_page_size: int = 100) -> tuple[int, int, int]:
    safe_page = max(page, 1)
    safe_page_size = min(max(page_size, 1), max_page_size)
    skip = (safe_page - 1) * safe_page_size
    return safe_page, safe_page_size, skip


def paginated_response(items: list, total: int, page: int, page_size: int):
    total_pages = ceil(total / page_size) if page_size else 1
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(total_pages, 1),
    }

