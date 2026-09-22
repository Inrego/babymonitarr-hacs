# BabyMonitarr × Home Assistant — agreed design

Living version: https://claude.ai/code/artifact/193870a5-1527-4595-bca1-19f7ef585d1b

## Decision

A custom integration distributed through HACS, not MQTT discovery. MQTT cannot provide a
camera entity, a config flow, or — decisively — any way to call HA services, which is what
solves cast discovery.

The backend exposes a plain WebSocket endpoint alongside its existing SignalR hub, so the
HA side needs no SignalR client. Auth is a BabyMonitarr API key entered in the config flow.
Trust flows one way: no HA long-lived access token is stored in BabyMonitarr.

## Cast discovery: HA as an mDNS proxy

The integration declares `zeroconf` in `manifest.json` dependencies, takes HA's shared
browser with `await zeroconf.async_get_async_instance(hass)`, and runs an
`AsyncServiceBrowser` for `_googlecast._tcp.local.`. That yields address, port and the full
TXT record (`id`, `fn`, `md`, `ca`) — the same fields `Services/CastDeviceService.cs`
already parses. Those are pushed to the backend, which keeps connecting with Sharpcaster
directly. No added stream latency, no lost session control.

Only discovery needs link-local multicast; once the address is known, port 8009 is ordinary
outbound TCP that a bridge-network container routes fine.

Identity lines up for free: the TXT `id` is the value already stored in
`CastDevice.DeviceId`, so a device seen by both paths dedupes.

Caveats: HA itself must have mDNS access (host networking). Chromecast addresses move on
DHCP renewal, so keep the connection live and re-push on change. The proxy need not survive
HA being offline — last-known host plus re-resolve on connect failure is enough.

## Entities

One HA device per room:

| Entity | Platform | Notes |
| --- | --- | --- |
| `<room>_sound` | binary_sensor | `device_class: sound`. Driven by the backend event, 30 s clear-hold. |
| `<room>_sound_level` | sensor | dB, `state_class: measurement`, `device_class: sound_pressure`. Throttled server-side. |
| `<room>` | camera | HA native camera WebRTC (2024.11+) on the existing offer/answer flow; HLS and stills as fallback. |
| `<room>_monitoring` | switch | The always-on subscription. Defaults off. |
| `<room>_stream_online` | binary_sensor | `device_class: connectivity`. |
| `<room>_casting` | binary_sensor | |
| `<room>_cast_targets` | sensor | |

One global device: `number.sound_threshold`, `number.threshold_pause`,
`number.volume_adjustment`, `switch.audio_filter` (all `entity_category: config`),
`sensor.active_room`, `sensor.connected_viewers`, `update.babymonitarr`.

Services: `babymonitarr.cast_room(room, targets[])`, `babymonitarr.stop_cast(room)`,
`babymonitarr.snapshot(room)`, plus device triggers for the sound event.

Talk-back is out of scope; there is no two-way audio path in the backend.

## Backend prerequisites

1. **Monitoring subscription (blocker).** `AudioStreamingService` starts readers lazily and
   stops them when the last subscriber leaves, so sound detection only runs while someone is
   streaming. The integration registers as an ordinary subscriber — the same kind an app
   client registers — and simply stays registered. No new lifecycle, no second code path;
   the only new piece is a subscriber that never asks for a WebRTC peer.
2. **Sound state vs. event.** `SoundThresholdExceeded` is an edge with a
   `ThresholdPauseDuration` mute window. Drive the binary_sensor from the event with a
   separate 30 s hold, and fire `babymonitarr_sound_detected` on the bus for repeats.
3. **Throttled level broadcast**, ~1 Hz or on-change, configurable.
4. **`ICastTransport` seam** in `CastSessionService`.
5. **API-key auth** on the WebSocket endpoint.

## Build order

1. Backend: WebSocket endpoint, monitoring subscriber, throttled level broadcast, sound
   state, API-key auth.
2. HA integration: config flow, WebSocket client, coordinator, binary_sensor, sensor,
   switch, number.
3. Camera entity with native WebRTC.
4. mDNS proxy: Zeroconf browser here, new `CastDevice` origin in the backend, cast entities
   and services.
5. Optional HA cast transport behind `ICastTransport` — reaches Sonos/AirPlay targets
   Sharpcaster cannot drive. Lower priority; it costs the keep-alive and adds start latency.
