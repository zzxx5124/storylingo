"""統一 HTTPException 訊息常量（對齊既有前端訊息）。"""
from fastapi import HTTPException


class CodedHTTPException(HTTPException):
    """保留既有 detail 同時提供可供前端判斷的 machine-readable code。"""

    def __init__(self, status_code: int, detail: str, code: str):
        super().__init__(status_code, detail)
        self.code = code


def not_found(msg: str = "找不到資源") -> HTTPException:
    return HTTPException(404, msg)


def bad_request(msg: str, code: str | None = None) -> HTTPException:
    if code:
        return CodedHTTPException(400, msg, code)
    return HTTPException(400, msg)


def unauthorized(msg: str = "請先登入") -> HTTPException:
    return HTTPException(401, msg)


def forbidden(msg: str = "權限不足") -> HTTPException:
    return HTTPException(403, msg)


def conflict(msg: str, code: str | None = None) -> HTTPException:
    if code:
        return CodedHTTPException(409, msg, code)
    return HTTPException(409, msg)


def too_many_requests(msg: str = "請求過於頻繁") -> HTTPException:
    return HTTPException(429, msg, headers={"Retry-After": "60"})


def payload_too_large(msg: str = "檔案太大") -> HTTPException:
    return HTTPException(413, msg)
