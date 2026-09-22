"""Camera platform for BabyMonitarr: Home Assistant native WebRTC.

One camera per room, and **native WebRTC is the only path it has**. There is no
still image and no HLS fallback: the backend has no snapshot capability at all,
and its only HLS exists for the lifetime of a cast session behind a per-session
token. That is a correction to DESIGN.md, recorded in section 9 of the protocol
doc, not an omission here.

Direction matters. The backend's SignalR flow makes the *backend* the offerer,
which Home Assistant's camera API cannot consume; the ``webrtc.*`` path exposes an
answer path instead, so this entity hands the backend whatever SDP Home Assistant
gave it and gets an answer back (protocol section 8.1). It never builds an offer of
its own.

The answer is passthrough-only: the backend can only send the codec the camera
already produces, so an offer that does not list that codec is rejected outright
with ``webrtc_codec_mismatch`` rather than silently transcoding. Those errors are
surfaced to the frontend verbatim - the message is written to be read by a human.
"""

from __future__ import annotations

import logging
from dataclasses import fields
from typing import Any

from homeassistant.components.camera import (
    Camera,
    CameraEntityFeature,
    WebRTCAnswer,
    WebRTCCandidate,
    WebRTCClientConfiguration,
    WebRTCError,
    WebRTCSendMessage,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import BabyMonitarrError
from .const import (
    ERROR_WEBRTC_CODEC_MISMATCH,
    ERROR_WEBRTC_FAILED,
    MSG_ERROR,
    MSG_WEBRTC_ANSWER,
    MSG_WEBRTC_CANDIDATE,
    MSG_WEBRTC_CLOSED,
    WEBRTC_KIND_VIDEO,
)
from .coordinator import BabyMonitarrCoordinator
from .entity import BabyMonitarrRoomEntity

try:  # HA 2025.2+ renamed the candidate type and gave it the SDP fields.
    from homeassistant.components.camera import (  # type: ignore[attr-defined]
        RTCIceCandidateInit as RTCIceCandidateType,
    )
except ImportError:  # HA 2024.11 - 2025.1: a bare candidate string.
    from homeassistant.components.camera import (  # type: ignore[attr-defined]
        RTCIceCandidate as RTCIceCandidateType,
    )

_LOGGER = logging.getLogger(__name__)

# Which of the protocol's candidate fields this core's candidate type accepts.
# Reading the dataclass beats branching on a version number.
_CANDIDATE_FIELDS = {field.name for field in fields(RTCIceCandidateType)}

# Codes for the failures that happen on this side rather than the backend's.
ERROR_NOT_SUPPORTED = "webrtc_not_supported"
ERROR_DISCONNECTED = "webrtc_disconnected"
ERROR_SUPERSEDED = "webrtc_superseded"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one camera per room, adding more as rooms arrive."""
    coordinator: BabyMonitarrCoordinator = entry.runtime_data

    @callback
    def _add_rooms(room_ids: list[int]) -> None:
        async_add_entities(
            BabyMonitarrCamera(coordinator, room_id) for room_id in room_ids
        )

    coordinator.async_add_new_rooms_callback(_add_rooms)
    if coordinator.data.rooms:
        _add_rooms(list(coordinator.data.rooms))


class BabyMonitarrCamera(BabyMonitarrRoomEntity, Camera):
    """A room's video, over native WebRTC and nothing else."""

    # The camera is the room device's main feature, so it takes the device's name:
    # camera.nursery, as DESIGN.md's entity table spells it.
    _attr_name = None
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, coordinator: BabyMonitarrCoordinator, room_id: int) -> None:
        """Initialise the camera."""
        super().__init__(coordinator, room_id, "camera")
        # CoordinatorEntity.__init__ stops the chain before Camera's, so Camera's
        # own attributes (``_webrtc_provider`` and friends) are never set and
        # adding the entity raises AttributeError. Run it explicitly.
        Camera.__init__(self)
        # One peer per (connection, room, kind), so one live session per camera.
        self._session_id: str | None = None
        self._send_message: WebRTCSendMessage | None = None
        self._unsub_webrtc: CALLBACK_TYPE | None = None
        # Set while an offer is awaiting its reply. See _handle_webrtc_message.
        self._offer_in_flight: str | None = None

    # --- native WebRTC ------------------------------------------------------

    async def async_handle_async_webrtc_offer(
        self, offer_sdp: str, session_id: str, send_message: WebRTCSendMessage
    ) -> None:
        """Hand Home Assistant's offer to the backend and return its answer."""
        if not self.coordinator.data.webrtc_supported:
            send_message(
                WebRTCError(
                    ERROR_NOT_SUPPORTED,
                    "This BabyMonitarr backend does not advertise the 'webrtc' "
                    "feature, so it cannot answer a camera offer.",
                )
            )
            return

        # The backend replaces the peer for a room that already has one, so an
        # earlier session is dead the moment this offer lands. Say so rather than
        # leaving the frontend on a frozen picture.
        if self._session_id is not None and self._session_id != session_id:
            self._async_end_session(
                ERROR_SUPERSEDED,
                "Another Home Assistant session started streaming this camera.",
            )

        self._session_id = session_id
        self._send_message = send_message
        # Register before offering: the backend starts trickling candidates as
        # soon as it has answered, and a candidate with nowhere to go is dropped.
        self._unsub_webrtc = self.coordinator.async_register_webrtc(
            self.room_id, WEBRTC_KIND_VIDEO, self._handle_webrtc_message
        )

        self._offer_in_flight = session_id
        try:
            msg_type, data = await self.coordinator.client.async_webrtc_offer(
                self.room_id, WEBRTC_KIND_VIDEO, offer_sdp
            )
        except BabyMonitarrError as err:
            self._async_clear_session(session_id)
            send_message(WebRTCError(ERROR_DISCONNECTED, str(err)))
            return
        finally:
            if self._offer_in_flight == session_id:
                self._offer_in_flight = None

        payload = data or {}
        if msg_type == MSG_ERROR:
            code, message = _webrtc_error(payload)
            _LOGGER.debug("WebRTC offer for room %s failed: %s", self.room_id, message)
            self._async_clear_session(session_id)
            send_message(WebRTCError(code, message))
            return

        sdp = payload.get("sdp")
        if msg_type != MSG_WEBRTC_ANSWER or not isinstance(sdp, str) or not sdp:
            self._async_clear_session(session_id)
            send_message(
                WebRTCError(
                    ERROR_WEBRTC_FAILED,
                    f"BabyMonitarr answered the offer with {msg_type!r} instead of "
                    "an SDP answer.",
                )
            )
            return

        send_message(WebRTCAnswer(sdp))

    async def async_on_webrtc_candidate(
        self, session_id: str, candidate: Any
    ) -> None:
        """Trickle one of Home Assistant's ICE candidates to the backend."""
        if session_id != self._session_id:
            # A candidate for a session we have already replaced or closed.
            return
        candidate_str = getattr(candidate, "candidate", None)
        if not candidate_str:
            return
        try:
            await self.coordinator.client.async_webrtc_candidate(
                self.room_id,
                WEBRTC_KIND_VIDEO,
                candidate_str,
                sdp_mid=getattr(candidate, "sdp_mid", None),
                sdp_m_line_index=getattr(candidate, "sdp_m_line_index", None),
            )
        except BabyMonitarrError as err:
            # Candidates are best effort; ICE copes with losing one.
            _LOGGER.debug("Could not send an ICE candidate: %s", err)

    @callback
    def close_webrtc_session(self, session_id: str) -> None:
        """Close the backend peer when Home Assistant closes the session."""
        if session_id != self._session_id:
            return
        self._async_clear_session(session_id)
        self.hass.async_create_task(self._async_stop_peer())

    @callback
    def _async_get_webrtc_client_configuration(self) -> WebRTCClientConfiguration:
        """Return the client configuration.

        Deliberately the default: everything here is on the LAN and the backend
        advertises its own reachable address among its candidates, so the
        integration contributes no ICE servers of its own. Home Assistant still
        merges the ones it is configured with. This is where a backend-supplied
        STUN or TURN server would go if one ever existed.
        """
        return WebRTCClientConfiguration()

    # --- session bookkeeping ------------------------------------------------

    @callback
    def _handle_webrtc_message(self, msg_type: str, data: dict[str, Any]) -> None:
        """Relay a server-side signalling frame to the frontend."""
        send_message = self._send_message
        if send_message is None:
            return

        if msg_type == MSG_WEBRTC_CANDIDATE:
            candidate = _build_candidate(data)
            if candidate is not None:
                # The backend may send the same candidate twice with different
                # addresses - as gathered and as advertised. Both are real; pass
                # both on and let ICE pick.
                send_message(WebRTCCandidate(candidate))
            return

        if msg_type == MSG_WEBRTC_CLOSED:
            if self._offer_in_flight is not None:
                # A closed frame that lands while our own offer is in flight is
                # about the peer this offer is replacing, or about the half-built
                # one whose failure the reply itself reports. Routing is by
                # (room, kind) only, so the old peer's frame is indistinguishable
                # from the new peer's - and acting on it would kill the session we
                # are still setting up. The offer's reply is the verdict.
                _LOGGER.debug(
                    "Ignoring webrtc.closed for room %s during an in-flight offer: %s",
                    self.room_id,
                    data.get("reason"),
                )
                return
            reason = data.get("reason") or "The BabyMonitarr peer was closed."
            self._async_end_session(ERROR_DISCONNECTED, str(reason))

    @callback
    def _async_end_session(self, code: str, message: str) -> None:
        """Tell the current session it is over, then forget it."""
        send_message = self._send_message
        session_id = self._session_id
        self._async_clear_session(session_id)
        if send_message is not None:
            send_message(WebRTCError(code, message))

    @callback
    def _async_clear_session(self, session_id: str | None) -> None:
        """Drop the routing hook and the session, if it is still the current one."""
        if session_id is not None and session_id != self._session_id:
            return
        if self._unsub_webrtc is not None:
            self._unsub_webrtc()
            self._unsub_webrtc = None
        self._session_id = None
        self._send_message = None

    async def _async_stop_peer(self) -> None:
        """Ask the backend to close the peer. Stopping a dead peer is a success."""
        try:
            await self.coordinator.client.async_webrtc_stop(
                self.room_id, WEBRTC_KIND_VIDEO
            )
        except BabyMonitarrError as err:
            # The socket is gone, which already closed every peer it owned.
            _LOGGER.debug("Could not stop the WebRTC peer: %s", err)

    async def async_will_remove_from_hass(self) -> None:
        """Release the backend peer when the entity goes away."""
        had_session = self._session_id is not None
        self._async_clear_session(None)
        if had_session:
            await self._async_stop_peer()
        await super().async_will_remove_from_hass()

    # --- no still image -----------------------------------------------------

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return no image, because the backend cannot produce one.

        Nothing in BabyMonitarr encodes a frame to JPEG or PNG (protocol doc,
        section 9.2). Returning None is the honest answer; fabricating a
        placeholder would put a picture in front of a parent that is not their
        child's room. Home Assistant answers the still-image endpoint with an
        error, so previews are blank until the live view is opened.
        """
        return None


def _webrtc_error(payload: dict[str, Any]) -> tuple[str, str]:
    """Map a backend ``error`` frame onto a WebRTCError code and message.

    ``webrtc_codec_mismatch`` and ``webrtc_failed`` are passed through as they
    are: the backend writes both messages for a human, and the mismatch one names
    the room's codec and everything the offer did list. An unrecognised code is
    treated as a generic failure, per the protocol's forward-compatibility rule,
    but keeps the code in the text so nothing is lost.
    """
    code = payload.get("code")
    message = str(payload.get("message") or "BabyMonitarr gave no reason.")
    if code in (ERROR_WEBRTC_CODEC_MISMATCH, ERROR_WEBRTC_FAILED):
        return str(code), message
    if code:
        return ERROR_WEBRTC_FAILED, f"{code}: {message}"
    return ERROR_WEBRTC_FAILED, message


def _build_candidate(data: dict[str, Any]) -> Any | None:
    """Build this core's ICE candidate object from a ``webrtc.candidate`` frame."""
    candidate = data.get("candidate")
    if not isinstance(candidate, str) or not candidate:
        return None
    available = {
        "candidate": candidate,
        "sdp_mid": data.get("sdp_mid"),
        "sdp_m_line_index": data.get("sdp_m_line_index"),
    }
    return RTCIceCandidateType(
        **{key: value for key, value in available.items() if key in _CANDIDATE_FIELDS}
    )
