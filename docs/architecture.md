# Architecture

## Design direction

Doggo is set up as a Pi-first robot:

- Raspberry Pi 4B runs the main state machine, servo bus access, web app, and later vision
- ESP32 acts as a low-latency manual input device and fallback path
- Browser UI is the main operator surface for laptop, phone, and Steam Deck
- The same web UI can later be shown on an onboard screen

## Current software slices

### `src/doggo/hardware`

Owns the STS3215 packet protocol and the Waveshare bus adapter serial access. The goal here is to keep servo control isolated from the rest of the stack so we can test and evolve higher-level motion without rewriting bus code.

### `src/doggo/control`

Owns robot modes and command arbitration:

- idle
- standing
- relaxing
- teleop requested
- walking via a first-pass crawl gait planner

The supervisor is the place where joystick, browser, and future vision commands get merged.

### `src/doggo/web`

Owns the FastAPI service and the browser UI. It exposes:

- health/status
- servo scan
- read/move/assign-id
- stand/relax
- teleop over REST and WebSocket

### `firmware/esp32_joystick`

Owns the joystick input side. For now it sends small UDP JSON packets to the Pi so the protocol stays simple and easy to inspect.

## Why the browser is the main UI

Using a browser-based control surface lets us support:

- desktop/laptop
- Steam Deck
- future onboard display

without maintaining three separate front ends.

## What is intentionally missing

- Measured-leg IK
- Inverse kinematics
- Balance control
- IMU fusion
- Person-follow logic

Those pieces still depend on real calibration data and measured geometry. The current crawl gait is a conservative joint-space teleop layer, not the final locomotion stack.
