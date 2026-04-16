from fastapi import HTTPException


def api_error(status_code: int, detail: str, code: str):
    raise HTTPException(status_code=status_code, detail={"code": code, "message": detail})


def error_payload(detail: str, code: str):
    return {"code": code, "message": detail}

