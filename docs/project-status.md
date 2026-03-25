# Project Status

This document describes the state of Doggo as the repo works today.

## Hardware this repo is built around

- Raspberry Pi 4B as the main controller
- 12x STS3215 bus servos
- Waveshare Bus Servo Adapter
- ESP32 reserved for low-latency joystick control and fallback control
- Browser-based operator UI for desktop, phone, and Steam Deck
- USB cameras planned for person tracking later

## Working now

- Servo bus communication over the Waveshare adapter
- Servo scan, readback, single-servo move, and ID assignment
- Saved whole-body poses:
  - `stand`
  - `sit`
  - `storage`
  - `relax`
- Per-leg stand and storage testing
- Web dashboard for:
  - health view
  - scan/read positions
  - body pose buttons
  - touch, keyboard, and gamepad teleop
  - servo telemetry
  - motion recording and playback
- Motion recording features:
  - variable duration recording
  - stop button
  - idle auto-stop
  - save named recordings
  - playback of last or saved clips

## In progress

- Walking gait tuning
- Better sit-to-stand and stand-to-sit polish
- Safer browser-side gait tuning tools
- ESP32 joystick end-to-end bring-up on hardware

## Not done yet

- Measured inverse kinematics
- IMU and balance control
- Object tracking and person-follow behavior
- Rearranged production-ready config separation between calibration and runtime clips
- Onboard display support

## Important repository folders

- `src/doggo/`
  Main Python control stack.
- `src/doggo/hardware/`
  STS3215 packet and serial bus layer.
- `src/doggo/control/`
  Supervisor, gait planner, and command logic.
- `src/doggo/web/`
  FastAPI app and browser dashboard.
- `config/`
  Example config, local robot config, and saved recordings.
- `config/recordings/`
  Named motion clips saved from the web UI or CLI.
- `firmware/esp32_joystick/`
  ESP32 joystick firmware.
- `docs/`
  Project docs and operator notes.
- `3D Files/`
  Printable robot parts, including the TPU shoes.

## Recommended next engineering steps

1. Finish a safe forward-only walking tune from stand.
2. Add browser controls for gait tuning values.
3. Split calibration data from runtime recording files more cleanly.
4. Add camera ingestion and a simple target-selection pipeline.
