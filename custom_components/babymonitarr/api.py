"""WebSocket transport for the BabyMonitarr backend.

This module speaks the wire protocol in ``docs/ha-websocket-protocol.md`` (v1) and
nothing else: it knows about frames, auth, reconnects and resync, and deliberately
knows nothing about Home Assistant entities. Everything it receives is handed to a
callback; everything it sends comes in through the command helpers.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from yarl import URL

from .const import (
    CMD_CAST_DISCOVERED,
    CMD_CAST_SET_TARGETS,
    CMD_CAST_START,
    CMD_CAST_STOP,
    CMD_CAST_STOP_DEVICE,
    CMD_GET_STATE,
    CMD_PING,
    CMD_SET_ACTIVE_ROOM,
    CMD_SET_GLOBAL_SETTINGS,
    CMD_SET_MONITORING,
    MSG_HELLO,
    PROTOCOL_VERSION,
    RECONNECT_INITIAL_DELAY,
    RECONNECT_MAX_DELAY,
    REQUEST_TIMEOUT,
    WS_PATH,
)

_LOGGER = logging.getLogger(__name__)

MessageCallback = Callable[[str, dict[str, Any] | None, str | None], None]
ConnectionCallback = Callable[[bool], None]


class BabyMonitarrError(Exception):
    """Base error for the BabyMonitarr transport."""


class BabyMonitarrConnectionError(BabyMonitarrError):
    """The backend could not be reached."""


class BabyMonitarrAuthError(BabyMonitarrError):
    """The API key was missing or rejected (HTTP 401 before the upgrade)."""


def normalise_ws_url(host: str) -> str:
    """Turn whatever the user typed into a ``ws(s)://host[:port]/ha/ws`` URL.

    Accepts ``1.2.3.4``, ``1.2.3.4:5000``, ``http://host``, ``https://host`` and
    ``ws(s)://host``, with or without a trailing path.
    """
    candidate = host.strip()
    if "://" not in candidate:
        candidate = f"http://{candidate}"

    split = urlsplit(candidate)
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}.get(split.scheme)
    if scheme is None or not split.netloc:
        raise ValueError(f"Unsupported backend URL: {host}")

    base = split.path.rstrip("/")
    path = base if base.endswith(WS_PATH) else f"{base}{WS_PATH}"
    return urlunsplit((scheme, split.netloc, path, "", ""))


class BabyMonitarrClient:
    """Maintains one authenticated WebSocket connection to the backend."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        url: str,
        api_key: str,
        message_callback: MessageCallback | None = None,
        connection_callback: ConnectionCallback | None = None,
    ) -> None:
        """Initialise the client. ``url`` must already be a ws(s) URL."""
        self._session = session
        self._url = url
        self._api_key = api_key
        self._message_callback = message_callback
        self._connection_callback = connection_callback

        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task[None] | None = None
        self._first_attempt: asyncio.Future[None] | None = None
        self._pending: dict[str, asyncio.Future[tuple[str, dict[str, Any] | None]]] = {}
        self._closing = False
        self._msg_id = 0
        # The header is preferred; a proxy that strips it pushes us onto the
        # query parameter for the rest of the session.
        self._use_query_auth = False

    def set_callbacks(
        self,
        message_callback: MessageCallback | None,
        connection_callback: ConnectionCallback | None,
    ) -> None:
        """Attach the consumer of this transport (the coordinator)."""
        self._message_callback = message_callback
        self._connection_callback = connection_callback

    @property
    def url(self) -> str:
        """The WebSocket URL in use."""
        return self._url

    @property
    def connected(self) -> bool:
        """Whether a socket is currently open."""
        return self._ws is not None and not self._ws.closed

    # --- connection management ---------------------------------------------

    async def _async_open(self) -> aiohttp.ClientWebSocketResponse:
        """Open one socket, preferring header auth and falling back to the query."""
        try:
            if not self._use_query_auth:
                try:
                    return await self._session.ws_connect(
                        self._url,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                        heartbeat=30,
                    )
                except aiohttp.WSServerHandshakeError as err:
                    if err.status != 401:
                        raise
                    _LOGGER.debug("Header auth rejected, retrying with access_token")
                    self._use_query_auth = True

            url = URL(self._url).with_query({"access_token": self._api_key})
            return await self._session.ws_connect(url, heartbeat=30)
        except aiohttp.WSServerHandshakeError as err:
            if err.status == 401:
                raise BabyMonitarrAuthError("Invalid API key") from err
            raise BabyMonitarrConnectionError(str(err)) from err
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise BabyMonitarrConnectionError(str(err)) from err

    async def async_validate(self) -> dict[str, Any]:
        """Connect, complete the handshake, and return the ``hello`` payload.

        Used by the config flow. Raises :class:`BabyMonitarrAuthError` or
        :class:`BabyMonitarrConnectionError`.
        """
        ws = await self._async_open()
        try:
            msg = await ws.receive(timeout=10)
            if msg.type is not aiohttp.WSMsgType.TEXT:
                raise BabyMonitarrConnectionError(
                    f"Expected a hello frame, got {msg.type}"
                )
            frame = msg.json()
            if frame.get("type") != MSG_HELLO:
                raise BabyMonitarrConnectionError(
                    f"Expected a hello frame, got {frame.get('type')!r}"
                )
            return frame.get("data") or {}
        except TimeoutError as err:
            raise BabyMonitarrConnectionError("Timed out waiting for hello") from err
        except (ValueError, TypeError, AttributeError) as err:
            raise BabyMonitarrConnectionError("Malformed hello frame") from err
        finally:
            await ws.close()

    async def async_start(self) -> None:
        """Start the receive loop and wait for the first connection attempt.

        The first attempt is surfaced to the caller so that an unreachable backend
        or a rejected key fails config entry setup instead of retrying quietly in
        the background.
        """
        self._closing = False
        self._first_attempt = asyncio.get_running_loop().create_future()
        self._task = asyncio.create_task(self._async_run())
        try:
            await self._first_attempt
        except BabyMonitarrError:
            await self.async_stop()
            raise

    async def async_stop(self) -> None:
        """Stop the receive loop and close the socket."""
        self._closing = True
        if self._first_attempt is not None and not self._first_attempt.done():
            self._first_attempt.cancel()
        if self._ws is not None:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _async_run(self) -> None:
        """Connect, receive, and reconnect with backoff until stopped."""
        delay = RECONNECT_INITIAL_DELAY
        while not self._closing:
            try:
                self._ws = await self._async_open()
            except BabyMonitarrAuthError as err:
                # A rejected key will not fix itself; stop trying.
                self._settle_first_attempt(err)
                _LOGGER.error("BabyMonitarr rejected the API key; giving up")
                return
            except BabyMonitarrConnectionError as err:
                if self._settle_first_attempt(err):
                    # Setup reported the failure; HA will retry the config entry.
                    return
                _LOGGER.debug(
                    "BabyMonitarr connect failed (%s), retrying in %ss", err, delay
                )
            else:
                self._settle_first_attempt(None)
                delay = RECONNECT_INITIAL_DELAY
                self._notify_connection(True)
                try:
                    await self._async_receive_loop(self._ws)
                except Exception:
                    _LOGGER.exception("BabyMonitarr receive loop failed")
                finally:
                    self._ws = None
                    self._fail_pending(
                        BabyMonitarrConnectionError("Connection lost")
                    )
                    self._notify_connection(False)
                if self._closing:
                    return

            await asyncio.sleep(delay)
            delay = min(delay * 2, RECONNECT_MAX_DELAY)

    async def _async_receive_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Drain one socket until it closes.

        The backend pushes a full snapshot (``hello`` ... ``ready``) on every
        accepted connection, so a reconnect resyncs by itself - protocol section 5.
        Nothing is polled.
        """
        async for msg in ws:
            if msg.type is not aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
                continue
            try:
                frame = msg.json()
            except ValueError:
                _LOGGER.debug("Ignoring non-JSON frame")
                continue
            if not isinstance(frame, dict):
                continue
            msg_type = frame.get("type")
            if not isinstance(msg_type, str):
                continue
            data = frame.get("data")
            if not isinstance(data, dict):
                data = None
            ref = frame.get("ref")
            if isinstance(ref, str):
                self._resolve_pending(ref, msg_type, data)
            if self._message_callback is not None:
                # Unknown types reach the callback too; it ignores what it does
                # not know, which is what keeps webrtc.* and cast.* additive.
                self._message_callback(
                    msg_type, data, ref if isinstance(ref, str) else None
                )

    def _settle_first_attempt(self, err: BabyMonitarrError | None) -> bool:
        """Resolve the future ``async_start`` waits on. True if it was pending."""
        future = self._first_attempt
        if future is None or future.done():
            return False
        if err is None:
            future.set_result(None)
        else:
            future.set_exception(err)
        return True

    def _resolve_pending(
        self, ref: str, msg_type: str, data: dict[str, Any] | None
    ) -> None:
        """Hand a ref-carrying reply to whoever is waiting for it."""
        future = self._pending.pop(ref, None)
        if future is not None and not future.done():
            future.set_result((msg_type, data))

    def _fail_pending(self, err: BabyMonitarrError) -> None:
        """Fail every in-flight request, e.g. because the socket went away."""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(err)
        self._pending.clear()

    def _notify_connection(self, connected: bool) -> None:
        if self._connection_callback is not None:
            self._connection_callback(connected)

    # --- client -> server commands -----------------------------------------

    def _next_id(self) -> str:
        self._msg_id += 1
        return str(self._msg_id)

    async def async_send(
        self, msg_type: str, data: dict[str, Any] | None = None
    ) -> str:
        """Send one command frame and return the ``id`` it was sent with."""
        msg_id = self._next_id()
        await self._async_send_frame(msg_id, msg_type, data)
        return msg_id

    async def _async_send_frame(
        self, msg_id: str, msg_type: str, data: dict[str, Any] | None
    ) -> None:
        """Put one command frame on the wire."""
        ws = self._ws
        if ws is None or ws.closed:
            raise BabyMonitarrConnectionError("Not connected")
        frame: dict[str, Any] = {"v": PROTOCOL_VERSION, "type": msg_type, "id": msg_id}
        if data is not None:
            frame["data"] = data
        try:
            await ws.send_json(frame)
        except (aiohttp.ClientError, ConnectionResetError) as err:
            raise BabyMonitarrConnectionError(str(err)) from err

    async def async_request(
        self,
        msg_type: str,
        data: dict[str, Any] | None = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> tuple[str, dict[str, Any] | None]:
        """Send a command and wait for the frame that echoes its ``id`` as ``ref``.

        Returns ``(reply_type, reply_data)``. The caller decides what to make of
        the type: a command may be answered with ``ack``, with ``error``, or with
        a purpose-built reply such as ``cast.start_result``.
        """
        future: asyncio.Future[tuple[str, dict[str, Any] | None]] = (
            asyncio.get_running_loop().create_future()
        )
        msg_id = self._next_id()
        self._pending[msg_id] = future
        try:
            await self._async_send_frame(msg_id, msg_type, data)
            async with asyncio.timeout(timeout):
                return await future
        except TimeoutError as err:
            raise BabyMonitarrConnectionError(
                f"Timed out waiting for a reply to {msg_type}"
            ) from err
        finally:
            self._pending.pop(msg_id, None)

    async def async_ping(self) -> str:
        """Application-level liveness check."""
        return await self.async_send(CMD_PING)

    async def async_get_state(self) -> str:
        """Ask for a fresh snapshot. Never call this on a timer."""
        return await self.async_send(CMD_GET_STATE)

    async def async_set_monitoring(self, room_id: int, enabled: bool) -> str:
        """Turn a room's always-on subscription on or off."""
        return await self.async_send(
            CMD_SET_MONITORING, {"room_id": room_id, "enabled": enabled}
        )

    async def async_set_global_settings(self, **settings: Any) -> str:
        """Partial update of the global audio settings."""
        return await self.async_send(CMD_SET_GLOBAL_SETTINGS, dict(settings))

    async def async_set_active_room(self, room_id: int) -> str:
        """Set the active room."""
        return await self.async_send(CMD_SET_ACTIVE_ROOM, {"room_id": room_id})

    # --- cast.* commands (protocol section 8.2) ----------------------------

    async def async_cast_discovered(self, devices: list[dict[str, Any]]) -> str:
        """Push the mDNS proxy's whole current set of receivers.

        Always the full set, never a delta: the backend upserts and never removes
        on absence, so a device that vanished simply stops being refreshed.
        """
        return await self.async_send(CMD_CAST_DISCOVERED, {"devices": devices})

    async def async_cast_start(
        self, room_id: int, device_ids: list[str] | None = None
    ) -> tuple[str, dict[str, Any] | None]:
        """Start casting a room. Returns the ``cast.start_result`` (or ``error``).

        Omitting ``device_ids`` casts to the room's saved targets.
        """
        data: dict[str, Any] = {"room_id": room_id}
        if device_ids:
            data["device_ids"] = device_ids
        return await self.async_request(CMD_CAST_START, data)

    async def async_cast_stop(self, room_id: int) -> tuple[str, dict[str, Any] | None]:
        """Stop every session for a room."""
        return await self.async_request(CMD_CAST_STOP, {"room_id": room_id})

    async def async_cast_stop_device(
        self, device_id: str
    ) -> tuple[str, dict[str, Any] | None]:
        """Stop one device without touching the room's other targets."""
        return await self.async_request(CMD_CAST_STOP_DEVICE, {"device_id": device_id})

    async def async_cast_set_targets(
        self, room_id: int, device_ids: list[str]
    ) -> tuple[str, dict[str, Any] | None]:
        """Replace a room's saved target selection. Starts and stops nothing."""
        return await self.async_request(
            CMD_CAST_SET_TARGETS, {"room_id": room_id, "device_ids": device_ids}
        )
