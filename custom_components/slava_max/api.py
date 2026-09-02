from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

from .const import API_BASE

_LOGGER = logging.getLogger(__name__)


class SlavaMaxApiError(Exception):
    """Base MAX API error."""


class SlavaMaxAuthError(SlavaMaxApiError):
    """Authentication error."""


def _find_token(value: Any) -> str | None:
    """Recursively find a media token in a MAX upload response."""
    if isinstance(value, dict):
        token = value.get("token")
        if token:
            return str(token)
        for child in value.values():
            found = _find_token(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_token(child)
            if found:
                return found
    return None


def _token_from_url(url: str) -> str | None:
    """Try to obtain the image token from a signed upload URL."""
    try:
        query = parse_qs(urlparse(url).query)
    except ValueError:
        return None

    for key in ("token", "attachment_token", "photoToken", "photo_token"):
        values = query.get(key)
        if values:
            return str(values[0])
    return None


class SlavaMaxApi:
    """Async client for the official MAX Bot API."""

    def __init__(self, session: aiohttp.ClientSession, token: str) -> None:
        self._session = session
        self._token = token

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": self._token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_data: dict[str, Any] | None = None,
        timeout: int = 45,
        require_json: bool = True,
    ) -> dict[str, Any]:
        url = f"{API_BASE}{path}"

        try:
            async with self._session.request(
                method,
                url,
                headers=self.headers,
                params=params,
                json=json_data,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                raw = await response.read()
                text = raw.decode("utf-8", errors="replace").strip()

                if response.status == 401:
                    raise SlavaMaxAuthError(
                        "MAX API отклонил токен бота (HTTP 401)"
                    )

                if response.status >= 400:
                    detail = text[:1500] if text else "пустой ответ"
                    _LOGGER.error(
                        "MAX API %s %s -> HTTP %s: %s",
                        method,
                        path,
                        response.status,
                        detail,
                    )
                    raise SlavaMaxApiError(
                        f"MAX API HTTP {response.status}: {detail}"
                    )

                if not text:
                    return {"ok": True, "status": response.status}

                try:
                    parsed = json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    if require_json:
                        raise SlavaMaxApiError(
                            "MAX API вернул некорректный JSON"
                        )
                    return {
                        "ok": True,
                        "status": response.status,
                        "raw": text,
                    }

                if isinstance(parsed, dict):
                    return parsed

                if not require_json:
                    return {
                        "ok": True,
                        "status": response.status,
                        "result": parsed,
                    }

                raise SlavaMaxApiError("MAX API вернул JSON неожиданного типа")

        except asyncio.CancelledError:
            raise
        except SlavaMaxApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SlavaMaxApiError(
                f"Ошибка соединения с MAX API: {err}"
            ) from err
        except Exception as err:
            _LOGGER.exception("Unexpected MAX API error")
            raise SlavaMaxApiError(
                f"Неожиданная ошибка MAX API: {type(err).__name__}: {err}"
            ) from err

    async def get_me(self) -> dict[str, Any]:
        return await self._request("GET", "/me")

    async def get_updates(
        self,
        marker: int | None = None,
        *,
        timeout: int = 30,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "limit": 100,
            "timeout": timeout,
            "types": "message_created,message_callback,bot_started",
        }
        if marker is not None:
            params["marker"] = marker

        return await self._request(
            "GET",
            "/updates",
            params=params,
            timeout=timeout + 15,
        )

    async def send_message(
        self,
        *,
        text: str,
        target_type: str,
        target_id: int,
        fmt: str | None = "markdown",
        notify: bool = True,
        buttons: list[list[dict[str, Any]]] | None = None,
        disable_link_preview: bool = False,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            target_type: target_id,
            "disable_link_preview": (
                "true" if disable_link_preview else "false"
            ),
        }

        body: dict[str, Any] = {
            "text": text,
            "notify": notify,
        }

        if fmt in ("markdown", "html"):
            body["format"] = fmt

        final_attachments = list(attachments or [])
        if buttons:
            final_attachments.append(
                {
                    "type": "inline_keyboard",
                    "payload": {"buttons": buttons},
                }
            )
        if final_attachments:
            body["attachments"] = final_attachments

        return await self._request(
            "POST",
            "/messages",
            params=params,
            json_data=body,
            require_json=False,
        )

    async def edit_message(
        self,
        *,
        message_id: str,
        text: str,
        fmt: str | None = "markdown",
        notify: bool = True,
        buttons: list[list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        """Edit a message previously sent by this bot."""
        body: dict[str, Any] = {
            "text": text,
            "notify": notify,
        }
        if fmt in ("markdown", "html"):
            body["format"] = fmt

        if buttons is not None:
            body["attachments"] = []
            if buttons:
                body["attachments"].append(
                    {
                        "type": "inline_keyboard",
                        "payload": {"buttons": buttons},
                    }
                )

        result = await self._request(
            "PUT",
            "/messages",
            params={"message_id": message_id},
            json_data=body,
            require_json=False,
        )
        if result.get("success") is False:
            detail = str(
                result.get("message") or "MAX не смог отредактировать сообщение"
            )
            raise SlavaMaxApiError(f"MAX edit rejected: {detail}")
        return result

    async def send_image_token(
        self,
        *,
        token: str,
        text: str,
        target_type: str,
        target_id: int,
        fmt: str | None = "markdown",
        notify: bool = True,
        buttons: list[list[dict[str, Any]]] | None = None,
        disable_link_preview: bool = False,
    ) -> dict[str, Any]:
        attachment = {"type": "image", "payload": {"token": token}}
        delays = (0.8, 1.5, 3.0)
        last_error: SlavaMaxApiError | None = None

        for attempt in range(len(delays) + 1):
            try:
                return await self.send_message(
                    text=text,
                    target_type=target_type,
                    target_id=target_id,
                    fmt=fmt,
                    notify=notify,
                    buttons=buttons,
                    disable_link_preview=disable_link_preview,
                    attachments=[attachment],
                )
            except SlavaMaxApiError as err:
                last_error = err
                if "attachment.not.ready" not in str(err).lower():
                    raise
                if attempt >= len(delays):
                    raise
                await asyncio.sleep(delays[attempt])

        if last_error is not None:
            raise last_error
        raise SlavaMaxApiError("Неизвестная ошибка отправки изображения")

    async def upload_image(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str = "image/jpeg",
    ) -> str:
        """Upload image bytes to MAX and return an attachment token."""
        upload_info = await self._request(
            "POST",
            "/uploads",
            params={"type": "image"},
        )
        upload_url = str(upload_info.get("url") or "").strip()
        if not upload_url:
            raise SlavaMaxApiError(
                "MAX API не вернул URL для загрузки изображения"
            )
        token = _find_token(upload_info) or _token_from_url(upload_url)

        form = aiohttp.FormData()
        form.add_field(
            "data",
            data,
            filename=filename,
            content_type=content_type,
        )

        try:
            async with self._session.post(
                upload_url,
                data=form,
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=90),
            ) as response:
                raw = await response.read()
                text = raw.decode("utf-8", errors="replace").strip()
                if response.status >= 400:
                    detail = text[:1500] if text else "пустой ответ"
                    raise SlavaMaxApiError(
                        f"MAX image upload HTTP {response.status}: {detail}"
                    )
                if text:
                    try:
                        parsed = json.loads(text)
                    except (json.JSONDecodeError, ValueError):
                        parsed = None
                    found = _find_token(parsed)
                    if found:
                        token = found
        except asyncio.CancelledError:
            raise
        except SlavaMaxApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SlavaMaxApiError(
                f"Ошибка загрузки изображения в MAX: {err}"
            ) from err

        if not token:
            raise SlavaMaxApiError(
                "MAX не вернул token после загрузки изображения"
            )
        return token

    async def send_video_token(
        self,
        *,
        token: str,
        text: str,
        target_type: str,
        target_id: int,
        fmt: str | None = "markdown",
        notify: bool = True,
        buttons: list[list[dict[str, Any]]] | None = None,
        disable_link_preview: bool = False,
    ) -> dict[str, Any]:
        """Send an already uploaded MAX video token."""
        attachment = {"type": "video", "payload": {"token": token}}
        delays = (1.0, 2.0, 4.0, 7.0)
        last_error: SlavaMaxApiError | None = None

        for attempt in range(len(delays) + 1):
            try:
                return await self.send_message(
                    text=text,
                    target_type=target_type,
                    target_id=target_id,
                    fmt=fmt,
                    notify=notify,
                    buttons=buttons,
                    disable_link_preview=disable_link_preview,
                    attachments=[attachment],
                )
            except SlavaMaxApiError as err:
                last_error = err
                if "attachment.not.ready" not in str(err).lower():
                    raise
                if attempt >= len(delays):
                    raise
                await asyncio.sleep(delays[attempt])

        if last_error is not None:
            raise last_error
        raise SlavaMaxApiError("Неизвестная ошибка отправки видео")

    async def upload_video(
        self,
        *,
        data: bytes,
        filename: str,
        content_type: str = "video/mp4",
    ) -> str:
        """Upload video bytes to MAX and return an attachment token."""
        upload_info = await self._request(
            "POST",
            "/uploads",
            params={"type": "video"},
        )
        upload_url = str(upload_info.get("url") or "").strip()
        if not upload_url:
            raise SlavaMaxApiError(
                "MAX API не вернул URL для загрузки видео"
            )
        token = _find_token(upload_info) or _token_from_url(upload_url)

        form = aiohttp.FormData()
        form.add_field(
            "data",
            data,
            filename=filename,
            content_type=content_type,
        )

        try:
            async with self._session.post(
                upload_url,
                data=form,
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=180),
            ) as response:
                raw = await response.read()
                text = raw.decode("utf-8", errors="replace").strip()
                if response.status >= 400:
                    detail = text[:1500] if text else "пустой ответ"
                    raise SlavaMaxApiError(
                        f"MAX video upload HTTP {response.status}: {detail}"
                    )
                if text:
                    try:
                        parsed = json.loads(text)
                    except (json.JSONDecodeError, ValueError):
                        parsed = None
                    if parsed is not None:
                        token = token or _find_token(parsed)
        except asyncio.CancelledError:
            raise
        except SlavaMaxApiError:
            raise
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SlavaMaxApiError(
                f"Ошибка загрузки видео в MAX: {err}"
            ) from err

        if not token:
            raise SlavaMaxApiError(
                "MAX не вернул token после загрузки видео"
            )
        return token

    async def answer_callback(
        self,
        *,
        callback_id: str,
        text: str | None = None,
        fmt: str | None = "markdown",
        buttons: list[list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}

        if text is not None:
            message: dict[str, Any] = {"text": text}
            if fmt in ("markdown", "html"):
                message["format"] = fmt
            if buttons:
                message["attachments"] = [
                    {
                        "type": "inline_keyboard",
                        "payload": {"buttons": buttons},
                    }
                ]
            body["message"] = message

        return await self._request(
            "POST",
            "/answers",
            params={"callback_id": callback_id},
            json_data=body,
            require_json=False,
        )
