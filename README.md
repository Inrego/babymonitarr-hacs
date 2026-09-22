# BabyMonitarr for Home Assistant

Home Assistant integration for [BabyMonitarr](https://github.com/Inrego/babymonitarr), a
self-hosted baby monitor for RTSP and Google Nest cameras.

> Status: scaffold. Nothing works yet â€” see `docs/DESIGN.md` for the agreed design.

## What it does

- Per-room devices with sound detection, sound level, stream health and a live camera.
- Global config entities for the sound threshold and audio settings.
- Acts as an mDNS proxy so BabyMonitarr can find Google Cast receivers even when it runs
  on a Docker bridge network. Casting itself stays a direct connection from BabyMonitarr
  to the receiver, so no latency is added.

## The camera: live video only, no still images

The camera entities stream over Home Assistant's native WebRTC. They have **no still
image**, because BabyMonitarr has no way to produce one — there is no snapshot endpoint
in the backend, and its only HLS exists for the lifetime of a cast session. Rather than
show you a fabricated or stale picture of your child's room, the integration returns
nothing.

What that means in the Home Assistant UI:

- The camera's idle preview (the thumbnail on a dashboard card, and the picture before
  you open the live view) is blank or broken, and the log records that the still image
  could not be fetched.
- Clicking through to the live view works normally and plays video.
- To skip the blank preview, set `camera_view: live` on your Picture Glance or Picture
  Entity card, which makes it go straight to the stream.
- `camera.snapshot` and anything else that wants a frame will fail. So does the
  `babymonitarr.snapshot` service, which exists only to tell you why.

Video is negotiated **passthrough-only**: BabyMonitarr forwards your camera's frames
without transcoding, so it can only answer with the codec the camera already produces. A
browser that does not offer that codec gets a clear error naming both — an H.265 camera
will not play in a browser offering only H.264. That is deliberate; the alternative is a
connection that succeeds and then sends undecodable frames.

## Development

The integration ships offline test harnesses that need neither Home Assistant nor a
BabyMonitarr backend — they stub the dependencies and drive the real integration code:

```
python tests/run_all.py
```

## Installation

Not published yet. Once it is, add this repository as a HACS custom repository.

## Configuration

Add the integration in Home Assistant, then enter the BabyMonitarr host and an API key
created in BabyMonitarr under Settings.
