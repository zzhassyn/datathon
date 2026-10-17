"""Public UI errors: stable codes and Russian messages, never exception details."""

from collections.abc import Mapping

from starlette.responses import JSONResponse

from cashguard.contracts.ui import UiHttpError, UiHttpErrorCode

_MESSAGES: dict[int, tuple[UiHttpErrorCode, str]] = {
    400: ("UPLOAD_ABORTED", "Передача файла прервана. Выберите видео и повторите загрузку."),
    401: ("UNAUTHORIZED", "Нужен действующий код доступа."),
    404: ("NOT_FOUND", "Задание или файл не найдены. Возможно, срок хранения истёк."),
    413: ("FILE_TOO_LARGE", "Размер загрузки превышает допустимый лимит."),
    415: ("UNSUPPORTED_MEDIA", "Файл повреждён или не является поддерживаемым видео MOV/MP4."),
    422: ("INVALID_REQUEST", "Нужен корректный запрос с одним видео в поле file."),
    429: ("RATE_LIMITED", "Сейчас слишком много проверок. Повторите позже."),
    507: ("INSUFFICIENT_STORAGE", "На сервере недостаточно места. Повторите позже."),
}


def ui_error(status: int, detail: object = None, headers: Mapping[str, str] | None = None):
    code, message = _MESSAGES.get(
        status, ("INTERNAL", "Внутренняя ошибка сервера. Повторите позже.")
    )
    if status == 422 and detail == "upload deadline exceeded":
        code, message = "UPLOAD_TIMEOUT", "Истекло время отправки файла. Повторите загрузку."
    retry = None
    output_headers = dict(headers or {})
    if status == 429:
        try:
            retry = max(1, int(output_headers.get("Retry-After", "1")))
        except ValueError:
            retry = 1
        output_headers["Retry-After"] = str(retry)
    output_headers["Cache-Control"] = "no-store"
    body = UiHttpError(code=code, message_ru=message, retry_after_sec=retry)
    return JSONResponse(body.model_dump(mode="json"), status_code=status, headers=output_headers)
