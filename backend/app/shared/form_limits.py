"""
Form parsing limits for routes that carry a whole file as a base64 text field.

Starlette caps each non-file form field at 1 MB. /api/onboarding/documents,
/api/onboarding/portfolio and /api/uploads take the file itself in `file_data`,
and portfolio samples go up to 12 MB (16 MB once base64-encoded). Only the
per-field size is raised here; the field and file counts keep Starlette's
defaults for every content type.
"""
from typing import Callable

from fastapi import APIRouter, Request, Response
from fastapi.routing import APIRoute

BASE64_FILE_FIELD_MAX_BYTES = 16 * 1024 * 1024


class _Base64FileFormRequest(Request):
    def form(
        self,
        *,
        max_files: int | float = 1000,
        max_fields: int | float = 1000,
        max_part_size: int = BASE64_FILE_FIELD_MAX_BYTES,
    ):
        return super().form(max_files=max_files, max_fields=max_fields, max_part_size=max_part_size)


class Base64FileFormRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            return await handler(_Base64FileFormRequest(request.scope, request.receive))

        return route_handler


def base64_file_form_post(router: APIRouter, path: str) -> Callable:
    """`@router.post(path)` for a route whose form carries a base64 file."""

    def register(endpoint: Callable) -> Callable:
        router.add_api_route(path, endpoint, methods=["POST"], route_class_override=Base64FileFormRoute)
        return endpoint

    return register
