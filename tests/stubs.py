"""Stand-ins for aiohttp, zeroconf and Home Assistant.

These harnesses run without Home Assistant installed: `install()` puts just enough
of each dependency into `sys.modules` for the integration to import, then the tests
drive the real integration code. Nothing here is a test double for BabyMonitarr's
own logic - only for what it sits on.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

REPO_ROOT = Path(__file__).resolve().parent.parent


def mod(name: str, **attrs: object) -> types.ModuleType:
    """Register one stub module."""
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


# --- aiohttp / yarl ---------------------------------------------------------


class WSMsgType:
    """The message types the receive loop switches on."""

    TEXT = "TEXT"
    CLOSED = "CLOSED"
    ERROR = "ERROR"


class WSServerHandshakeError(Exception):
    """Carries the HTTP status the handshake failed with."""

    status = 401


class URL:
    """Just enough yarl for the query-parameter auth fallback."""

    def __init__(self, value: str) -> None:
        self.value = value

    def with_query(self, query: dict[str, str]) -> URL:
        joined = "&".join(f"{k}={v}" for k, v in query.items())
        return URL(f"{self.value}?{joined}")


# --- Home Assistant ---------------------------------------------------------


class Bus:
    """Records fired events so a test can assert on them."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def async_fire(self, event_type: str, data: dict) -> None:
        self.events.append((event_type, data))


class ServiceCall:
    """Carries the validated call data to a service handler."""

    def __init__(self, data: dict) -> None:
        self.data = dict(data)


class ServiceRegistry:
    """Records registered services and calls them the way hass would."""

    def __init__(self) -> None:
        self.handlers: dict[tuple[str, str], object] = {}

    def async_register(self, domain, service, handler, schema=None) -> None:
        self.handlers[(domain, service)] = handler

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.handlers

    async def async_call(self, domain: str, service: str, data: dict) -> None:
        """Call a handler directly. The voluptuous stub validates nothing, so a
        harness passes data the real schema would already have coerced."""
        await self.handlers[(domain, service)](ServiceCall(data))


class ConfigEntries:
    """Just the lookup `services._coordinator` does."""

    def __init__(self, entries: list | None = None) -> None:
        self.entries = entries if entries is not None else []

    def async_loaded_entries(self, domain: str) -> list:
        return list(self.entries)


class Hass:
    """A minimal hass object: an event bus, services and task creation."""

    def __init__(self) -> None:
        self.bus = Bus()
        self.services = ServiceRegistry()
        self.config_entries = ConfigEntries()
        self.tasks: list[object] = []

    def async_create_task(self, coro: object) -> object:
        self.tasks.append(coro)
        return coro


class ConfigEntryStub:
    """A loaded config entry."""

    entry_id = "abc123"
    data: ClassVar[dict[str, str]] = {"host": "1.2.3.4:5000", "api_key": "k"}
    runtime_data = None


class DataUpdateCoordinator:
    """Counts pushes so a test can assert what did and did not update entities."""

    def __class_getitem__(cls, item: object) -> type:
        return cls

    def __init__(self, hass, logger, name=None, update_interval=None) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.pushes = 0

    def async_set_updated_data(self, data: object) -> None:
        self.pushes += 1


class Entity:
    """Resolves the _attr_* conventions the integration sets."""

    @property
    def unique_id(self) -> str | None:
        return getattr(self, "_attr_unique_id", None)

    @property
    def device_info(self) -> dict | None:
        return getattr(self, "_attr_device_info", None)

    @property
    def supported_features(self) -> int:
        return getattr(self, "_attr_supported_features", 0)

    @property
    def name(self) -> str | None:
        return getattr(self, "_attr_name", None)

    async def async_will_remove_from_hass(self) -> None:
        return None


class CoordinatorEntity(Entity):
    """Mirrors the real one closely enough for the entity MRO to behave."""

    def __class_getitem__(cls, item: object) -> type:
        return cls

    def __init__(self, coordinator, context=None) -> None:
        super().__init__()
        self.coordinator = coordinator

    @property
    def available(self) -> bool:
        return True


class HomeAssistantError(Exception):
    """Base HA error."""


class ServiceValidationError(HomeAssistantError):
    """Bad input to a service call."""


# --- sensor -----------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class EntityDescription:
    """The subset of the description fields this integration sets."""

    key: str
    translation_key: str | None = None
    device_class: object = None
    entity_category: object = None


@dataclass(frozen=True, kw_only=True)
class SensorEntityDescription(EntityDescription):
    """Adds the sensor-only fields."""

    state_class: object = None
    native_unit_of_measurement: str | None = None
    suggested_display_precision: int | None = None


class SensorEntity(Entity):
    """Resolves `native_value` and `extra_state_attributes` like the real base."""

    @property
    def state(self) -> object:
        return self.native_value

    @property
    def native_value(self) -> object:
        return None

    @property
    def extra_state_attributes(self) -> dict | None:
        return None


class SensorDeviceClass:
    """Only the one this integration uses."""

    SOUND_PRESSURE = "sound_pressure"


class SensorStateClass:
    """Only the one this integration uses."""

    MEASUREMENT = "measurement"


class UnitOfSoundPressure:
    """Only the one this integration uses."""

    DECIBEL = "dB"


class Platform:
    """The platform names this integration forwards to."""

    BINARY_SENSOR = "binary_sensor"
    CAMERA = "camera"
    NUMBER = "number"
    SENSOR = "sensor"
    SWITCH = "switch"


# --- camera -----------------------------------------------------------------


class Camera(Entity):
    """Tracks that the real Camera base was initialised through the MRO."""

    def __init__(self) -> None:
        super().__init__()
        self.camera_base_initialised = True


class CameraEntityFeature:
    """Only STREAM matters here."""

    STREAM = 2


@dataclass(frozen=True)
class WebRTCAnswer:
    """The SDP answer handed back to the frontend."""

    answer: str


@dataclass(frozen=True)
class RTCIceCandidateInit:
    """HA 2025.2+ candidate shape."""

    candidate: str
    sdp_mid: str | None = None
    sdp_m_line_index: int | None = None


@dataclass(frozen=True)
class WebRTCCandidate:
    """One trickled candidate."""

    candidate: RTCIceCandidateInit


@dataclass(frozen=True)
class WebRTCError:
    """A negotiation failure, with the code and text the user sees."""

    code: str
    message: str


@dataclass
class WebRTCClientConfiguration:
    """The configuration handed to the browser."""

    configuration: dict = field(default_factory=dict)


class _Marker:
    """Accepts anything: stands in for voluptuous validators."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass


def install() -> None:
    """Install every stub. Safe to call once, before importing the integration."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    mod(
        "aiohttp",
        ClientSession=object,
        ClientWebSocketResponse=object,
        WSMsgType=WSMsgType,
        WSServerHandshakeError=WSServerHandshakeError,
        ClientError=Exception,
    )
    mod("yarl", URL=URL)
    mod(
        "voluptuous",
        Schema=_Marker,
        Required=_Marker,
        Optional=_Marker,
        All=_Marker,
        In=_Marker,
    )
    mod("zeroconf", ServiceStateChange=_Marker)
    mod(
        "zeroconf.asyncio",
        AsyncServiceBrowser=object,
        AsyncServiceInfo=object,
        AsyncZeroconf=object,
    )

    ha = mod("homeassistant")
    ha.__path__ = []
    mod("homeassistant.config_entries", ConfigEntry=ConfigEntryStub)
    mod(
        "homeassistant.core",
        HomeAssistant=Hass,
        callback=lambda func: func,
        CALLBACK_TYPE=object,
        ServiceCall=ServiceCall,
    )
    mod(
        "homeassistant.const",
        Platform=Platform,
        EntityCategory=object,
        UnitOfSoundPressure=UnitOfSoundPressure,
    )
    mod(
        "homeassistant.exceptions",
        ConfigEntryAuthFailed=Exception,
        ConfigEntryNotReady=Exception,
        ConfigEntryError=Exception,
        HomeAssistantError=HomeAssistantError,
        ServiceValidationError=ServiceValidationError,
    )
    mod("homeassistant.helpers").__path__ = []
    mod("homeassistant.helpers.aiohttp_client", async_get_clientsession=lambda h: None)
    mod(
        "homeassistant.helpers.config_validation",
        string=str,
        ensure_list=list,
        config_entry_only_config_schema=lambda domain: None,
    )
    mod("homeassistant.helpers.typing", ConfigType=dict, StateType=object)
    mod("homeassistant.helpers.debounce", Debouncer=object)
    mod("homeassistant.helpers.device_registry", DeviceInfo=dict)
    mod("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    mod(
        "homeassistant.helpers.update_coordinator",
        DataUpdateCoordinator=DataUpdateCoordinator,
        CoordinatorEntity=CoordinatorEntity,
    )
    mod("homeassistant.helpers.entity", Entity=Entity, EntityDescription=EntityDescription)
    sys.modules["homeassistant.helpers"].config_validation = sys.modules[
        "homeassistant.helpers.config_validation"
    ]
    mod("homeassistant.components").__path__ = []
    mod(
        "homeassistant.components.sensor",
        SensorDeviceClass=SensorDeviceClass,
        SensorEntity=SensorEntity,
        SensorEntityDescription=SensorEntityDescription,
        SensorStateClass=SensorStateClass,
    )
    mod("homeassistant.components.zeroconf", async_get_async_instance=None)
    mod(
        "homeassistant.components.camera",
        Camera=Camera,
        CameraEntityFeature=CameraEntityFeature,
        WebRTCAnswer=WebRTCAnswer,
        WebRTCCandidate=WebRTCCandidate,
        WebRTCClientConfiguration=WebRTCClientConfiguration,
        WebRTCError=WebRTCError,
        WebRTCSendMessage=object,
        RTCIceCandidateInit=RTCIceCandidateInit,
    )
    ha.config_entries = sys.modules["homeassistant.config_entries"]


class Results:
    """Collects assertion failures so one run reports all of them."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.failures: list[str] = []

    def check(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.failures.append(f"{label}: got {got!r}, want {want!r}")

    def expect_raises(self, label, exc, func, *args) -> None:
        try:
            func(*args)
        except exc:
            return
        except Exception as err:  # noqa: BLE001 - the point is to report the type
            self.failures.append(
                f"{label}: raised {type(err).__name__}, want {exc.__name__}"
            )
            return
        self.failures.append(f"{label}: nothing raised, want {exc.__name__}")

    async def expect_raises_async(self, label, exc, coro) -> None:
        """`expect_raises` for an awaitable, which the service handlers all are."""
        try:
            await coro
        except exc:
            return
        except Exception as err:  # noqa: BLE001 - the point is to report the type
            self.failures.append(
                f"{label}: raised {type(err).__name__}, want {exc.__name__}"
            )
            return
        self.failures.append(f"{label}: nothing raised, want {exc.__name__}")

    def report(self) -> int:
        if self.failures:
            print(f"{self.name}: FAILURES")
            for failure in self.failures:
                print(" -", failure)
            return 1
        print(f"{self.name}: ALL OK")
        return 0
