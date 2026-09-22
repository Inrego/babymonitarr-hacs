# BabyMonitarr for Home Assistant

Home Assistant integration for [BabyMonitarr](https://github.com/Inrego/babymonitarr), a
self-hosted baby monitor for RTSP and Google Nest cameras.

> Status: scaffold. Nothing works yet — see `docs/DESIGN.md` for the agreed design.

## What it does

- Per-room devices with sound detection, sound level, stream health and a live camera.
- Global config entities for the sound threshold and audio settings.
- Acts as an mDNS proxy so BabyMonitarr can find Google Cast receivers even when it runs
  on a Docker bridge network. Casting itself stays a direct connection from BabyMonitarr
  to the receiver, so no latency is added.

## Installation

Not published yet. Once it is, add this repository as a HACS custom repository.

## Configuration

Add the integration in Home Assistant, then enter the BabyMonitarr host and an API key
created in BabyMonitarr under Settings.
