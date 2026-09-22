"""Camera: the offer/answer exchange, ICE in both directions and error mapping.

Run directly: ``python tests/test_camera.py``. No Home Assistant install needed.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import stubs

stubs.install()

from custom_components.babymonitarr import camera as camera_module
from custom_components.babymonitarr.api import (
    BabyMonitarrConnectionError,
)
from custom_components.babymonitarr.camera import BabyMonitarrCamera
from custom_components.babymonitarr.coordinator import (
    BabyMonitarrCoordinator,
)

results = stubs.Results("camera")
check = results.check

OFFER_SDP = "v=0\r\no=- 1 1 IN IP4 0.0.0.0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"
ANSWER_SDP = "v=0\r\no=- 2 2 IN IP4 0.0.0.0\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n"


class FakeClient:
    """Records the frames the camera sends and replies with what a test sets."""

    def __init__(self) -> None:
        self.offers: list[tuple] = []
        self.candidates: list[tuple] = []
        self.stops: list[tuple] = []
        self.offer_reply: tuple = ("webrtc.answer", {"room_id": 1, "kind": "video",
                                                     "sdp": ANSWER_SDP})
        self.offer_error: Exception | None = None
        self.candidate_error: Exception | None = None
        # Frames the backend pushes while it is handling the offer, before its
        # reply: a supersede sends webrtc.closed for the peer it replaces.
        self.during_offer: list[tuple] = []

    def set_callbacks(self, message_callback, connection_callback) -> None:
        self.message_callback = message_callback
        self.connection_callback = connection_callback

    async def async_webrtc_offer(self, room_id, kind, sdp):
        self.offers.append((room_id, kind, sdp))
        for msg_type, data in self.during_offer:
            self.message_callback(msg_type, data, None)
        if self.offer_error is not None:
            raise self.offer_error
        return self.offer_reply

    async def async_webrtc_candidate(
        self, room_id, kind, candidate, sdp_mid=None, sdp_m_line_index=None
    ):
        if self.candidate_error is not None:
            raise self.candidate_error
        self.candidates.append((room_id, kind, candidate, sdp_mid, sdp_m_line_index))
        return "1"

    async def async_webrtc_stop(self, room_id, kind):
        self.stops.append((room_id, kind))
        return ("ack", {"command": "webrtc.stop"})


@dataclass
class Rig:
    """One coordinator, its fake transport and the camera under test."""

    hass: object
    coordinator: object
    client: FakeClient
    cam: BabyMonitarrCamera


def build_camera(features=("rooms", "webrtc")) -> Rig:
    """A coordinator primed with one room, plus the camera on it."""
    hass = stubs.Hass()
    entry = stubs.ConfigEntryStub()
    client = FakeClient()
    coordinator = BabyMonitarrCoordinator(hass, entry, client)
    coordinator.handle_message("hello", {"server_version": "1.6.0",
                                         "features": list(features)}, None)
    coordinator.handle_message("rooms", {"rooms": [{"id": 1, "name": "Nursery"}]}, None)
    cam = BabyMonitarrCamera(coordinator, 1)
    cam.hass = hass
    return Rig(hass, coordinator, client, cam)


def messages_of(sent, cls):
    return [m for m in sent if isinstance(m, cls)]


# --- entity shape -----------------------------------------------------------
rig = build_camera()
check("unique_id", rig.cam.unique_id, "abc123_room_1_camera")
check("declares its name as the device name",
      "_attr_name" in BabyMonitarrCamera.__dict__ and BabyMonitarrCamera._attr_name is None,
      True)
check("stream feature", rig.cam.supported_features, stubs.CameraEntityFeature.STREAM)
check("room device", rig.cam.device_info["identifiers"], {("babymonitarr", "abc123_room_1")})
# The Camera base must be initialised through the MRO, not by an explicit call.
check("camera base initialised", rig.cam.camera_base_initialised, True)
check("no session yet", rig.cam._session_id, None)
check(
    "client configuration is the default",
    rig.cam._async_get_webrtc_client_configuration(),
    stubs.WebRTCClientConfiguration(),
)


# --- the happy path ---------------------------------------------------------
async def test_offer_answer():
    rig = build_camera()
    sent = []

    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", sent.append)

    check("one offer sent", len(rig.client.offers), 1)
    check("offer room and kind", rig.client.offers[0][:2], (1, "video"))
    # HA's SDP goes over verbatim: the integration never builds an offer.
    check("offer sdp verbatim", rig.client.offers[0][2], OFFER_SDP)
    check("answered once", len(sent), 1)
    check("answer type", isinstance(sent[0], stubs.WebRTCAnswer), True)
    check("answer sdp", sent[0].answer, ANSWER_SDP)
    check("session tracked", rig.cam._session_id, "s1")

    # Server candidates arrive unsolicited, routed by (room, kind).
    rig.coordinator.handle_message(
        "webrtc.candidate",
        {"room_id": 1, "kind": "video", "candidate": "candidate:1 1 udp 1 10.0.0.1 1 typ host",
         "sdp_mid": "0", "sdp_m_line_index": 0},
        None,
    )
    candidates = messages_of(sent, stubs.WebRTCCandidate)
    check("server candidate relayed", len(candidates), 1)
    check("candidate string", candidates[0].candidate.candidate,
          "candidate:1 1 udp 1 10.0.0.1 1 typ host")
    check("candidate sdp_mid", candidates[0].candidate.sdp_mid, "0")
    check("candidate m-line index", candidates[0].candidate.sdp_m_line_index, 0)

    # The same candidate rewritten to the advertised address is a second, real one.
    rig.coordinator.handle_message(
        "webrtc.candidate",
        {"room_id": 1, "kind": "video", "candidate": "candidate:1 1 udp 1 203.0.113.9 1 typ host",
         "sdp_mid": "0", "sdp_m_line_index": 0},
        None,
    )
    check("both addresses relayed", len(messages_of(sent, stubs.WebRTCCandidate)), 2)

    # A candidate for the audio peer is not ours.
    rig.coordinator.handle_message(
        "webrtc.candidate",
        {"room_id": 1, "kind": "audio", "candidate": "candidate:9"},
        None,
    )
    check("other kind ignored", len(messages_of(sent, stubs.WebRTCCandidate)), 2)

    # Client ICE goes the other way.
    await rig.cam.async_on_webrtc_candidate(
        "s1", stubs.RTCIceCandidateInit("candidate:2 1 udp 1 10.0.0.2 2 typ srflx", "0", 0)
    )
    check("client candidate sent", len(rig.client.candidates), 1)
    check("client candidate payload", rig.client.candidates[0],
          (1, "video", "candidate:2 1 udp 1 10.0.0.2 2 typ srflx", "0", 0))

    # A candidate for a session we no longer hold is dropped.
    await rig.cam.async_on_webrtc_candidate("stale", stubs.RTCIceCandidateInit("candidate:3"))
    check("stale candidate dropped", len(rig.client.candidates), 1)

    # HA closing the session closes the backend peer.
    rig.cam.close_webrtc_session("s1")
    check("session cleared", rig.cam._session_id, None)
    check("stop scheduled", len(rig.hass.tasks), 1)
    await rig.hass.tasks[0]
    check("peer stopped", rig.client.stops, [(1, "video")])

    # With the session gone, a late server candidate has nowhere to go and is
    # dropped rather than crashing.
    before = len(sent)
    rig.coordinator.handle_message(
        "webrtc.candidate", {"room_id": 1, "kind": "video", "candidate": "candidate:4"}, None
    )
    check("late candidate dropped", len(sent), before)


asyncio.run(test_offer_answer())


# --- error mapping ----------------------------------------------------------
async def test_errors():
    # The codec mismatch the passthrough rule produces, passed through verbatim.
    rig = build_camera()
    sent = []
    backend_text = (
        "Room 1 is forwarded as H265, which the offer did not list. "
        "The offer listed: H264, VP8."
    )
    rig.client.offer_reply = ("error", {"code": "webrtc_codec_mismatch",
                                    "message": backend_text})
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", sent.append)
    check("mismatch is an error", isinstance(sent[0], stubs.WebRTCError), True)
    check("mismatch code preserved", sent[0].code, "webrtc_codec_mismatch")
    check("mismatch text verbatim", sent[0].message, backend_text)
    check("failed offer leaves no session", rig.cam._session_id, None)

    # The general failure, likewise.
    rig = build_camera()
    sent = []
    rig.client.offer_reply = ("error", {"code": "webrtc_failed",
                                    "message": "The WebRTC offer for room 1 was rejected: ..."})
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", sent.append)
    check("failed code", sent[0].code, "webrtc_failed")
    check("failed text", sent[0].message,
          "The WebRTC offer for room 1 was rejected: ...")

    # An unrecognised code is a generic failure, but the code is kept in the text.
    rig = build_camera()
    sent = []
    rig.client.offer_reply = ("error", {"code": "webrtc_brand_new", "message": "Something"})
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", sent.append)
    check("unknown code generalised", sent[0].code, "webrtc_failed")
    check("unknown code kept in text", sent[0].message, "webrtc_brand_new: Something")

    # A reply that is not an answer at all.
    rig = build_camera()
    sent = []
    rig.client.offer_reply = ("ack", {"command": "webrtc.offer"})
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", sent.append)
    check("non-answer is a failure", sent[0].code, "webrtc_failed")

    # A backend that does not advertise webrtc is refused before any frame.
    rig = build_camera(features=("rooms",))
    sent = []
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", sent.append)
    check("unsupported code", sent[0].code, "webrtc_not_supported")
    check("nothing offered", rig.client.offers, [])

    # The socket dying mid-offer.
    rig = build_camera()
    sent = []
    rig.client.offer_error = BabyMonitarrConnectionError("Not connected")
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", sent.append)
    check("disconnected code", sent[0].code, "webrtc_disconnected")
    check("disconnected text", sent[0].message, "Not connected")

    # A candidate that cannot be sent is best effort, not an error to the user.
    rig = build_camera()
    sent = []
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", sent.append)
    rig.client.candidate_error = BabyMonitarrConnectionError("Not connected")
    await rig.cam.async_on_webrtc_candidate("s1", stubs.RTCIceCandidateInit("candidate:5"))
    check("candidate failure stays quiet", len(messages_of(sent, stubs.WebRTCError)), 0)


asyncio.run(test_errors())


# --- lifecycle --------------------------------------------------------------
async def test_lifecycle():
    # webrtc.closed from the backend ends the session with its reason.
    rig = build_camera()
    sent = []
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", sent.append)
    rig.coordinator.handle_message(
        "webrtc.closed", {"room_id": 1, "kind": "video", "reason": "Closed by client"}, None
    )
    errors = messages_of(sent, stubs.WebRTCError)
    check("closed surfaced", len(errors), 1)
    check("closed reason", errors[0].message, "Closed by client")
    check("closed clears the session", rig.cam._session_id, None)

    # A dropped socket kills every peer server-side and sends no webrtc.closed,
    # so the coordinator has to tell the camera itself (protocol section 5).
    rig = build_camera()
    sent = []
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", sent.append)
    rig.coordinator.handle_connection(False)
    errors = messages_of(sent, stubs.WebRTCError)
    check("drop surfaced", len(errors), 1)
    check("drop code", errors[0].code, "webrtc_disconnected")
    check("drop clears the session", rig.cam._session_id, None)

    # A second session replaces the backend peer, so the first one is told.
    rig = build_camera()
    first, second = [], []
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", first.append)
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s2", second.append)
    check("first session superseded", messages_of(first, stubs.WebRTCError)[0].code,
          "webrtc_superseded")
    check("second session answered", len(messages_of(second, stubs.WebRTCAnswer)), 1)
    check("second session is current", rig.cam._session_id, "s2")
    # Server candidates now reach the new session only.
    rig.coordinator.handle_message(
        "webrtc.candidate", {"room_id": 1, "kind": "video", "candidate": "candidate:6"}, None
    )
    check("old session gets no candidates", len(messages_of(first, stubs.WebRTCCandidate)), 0)
    check("new session gets them", len(messages_of(second, stubs.WebRTCCandidate)), 1)

    # Removing the entity releases the peer.
    await rig.cam.async_will_remove_from_hass()
    check("peer released on removal", rig.client.stops, [(1, "video")])

    # Removing an idle camera asks for nothing.
    rig = build_camera()
    await rig.cam.async_will_remove_from_hass()
    check("idle removal is silent", rig.client.stops, [])


asyncio.run(test_lifecycle())


# --- a closed frame that races our own offer --------------------------------
async def test_closed_during_offer():
    """The supersede seam: closed frames are routed by (room, kind) only.

    When a second offer for the same room arrives, the backend closes the peer it
    replaces and reports it - on the wire that webrtc.closed lands *before* the
    answer to the offer that caused it, and is indistinguishable from a frame
    about the new peer. Acting on it would kill the session being set up.
    """
    rig = build_camera()
    first, second = [], []
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s1", first.append)

    rig.client.during_offer = [(
        "webrtc.closed",
        {"room_id": 1, "kind": "video",
         "reason": "Replaced by a new offer for the same room"},
    )]
    await rig.cam.async_handle_async_webrtc_offer(OFFER_SDP, "s2", second.append)

    check("new session answered", len(messages_of(second, stubs.WebRTCAnswer)), 1)
    check("new session not errored", len(messages_of(second, stubs.WebRTCError)), 0)
    check("new session still current", rig.cam._session_id, "s2")
    check("in-flight flag cleared", rig.cam._offer_in_flight, None)

    # And once the offer has settled, a genuine closed frame still ends it.
    rig.coordinator.handle_message(
        "webrtc.closed", {"room_id": 1, "kind": "video", "reason": "Peer connection failed"},
        None,
    )
    errors = messages_of(second, stubs.WebRTCError)
    check("later closed surfaced", len(errors), 1)
    check("later closed reason", errors[0].message, "Peer connection failed")
    check("later closed clears the session", rig.cam._session_id, None)


asyncio.run(test_closed_during_offer())


# --- no still image, and the older candidate shape ---------------------------
async def test_no_image():
    rig = build_camera()
    check("no still image", await rig.cam.async_camera_image(), None)
    check("no still image at a size", await rig.cam.async_camera_image(640, 480), None)


asyncio.run(test_no_image())

# HA 2024.11 - 2025.1 carried a candidate string and nothing else. The builder
# filters to whatever fields that core's dataclass actually has.
original_fields = camera_module._CANDIDATE_FIELDS
try:
    camera_module._CANDIDATE_FIELDS = {"candidate"}
    built = camera_module._build_candidate(
        {"candidate": "candidate:7", "sdp_mid": "0", "sdp_m_line_index": 0}
    )
    check("old core keeps the candidate", built.candidate, "candidate:7")
    check("old core drops sdp_mid", built.sdp_mid, None)
finally:
    camera_module._CANDIDATE_FIELDS = original_fields

check("empty candidate skipped", camera_module._build_candidate({"candidate": ""}), None)
check("missing candidate skipped", camera_module._build_candidate({}), None)

sys.exit(results.report())
