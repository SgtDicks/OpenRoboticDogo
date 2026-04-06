![OpenRoboticDog](illustration.png)

# OpenRoboticDogo

OpenRoboticDogo is our Doggo build: a low-cost open quadruped robot with printable parts, STS3215 servos, a Raspberry Pi control stack, browser teleop, and room for ESP32 joystick control plus future vision tracking.

![Doggo Build Photo](Pics%20and%20videos/PXL_20260322_135309000.RAW-01.jpg)

## Massive Credit to the Original Repo

This project owes a huge amount to the original open-source work at [garciamathias/OpenRoboticDog](https://github.com/garciamathias/OpenRoboticDog).

The original repo set the mechanical direction, the spirit of the project, and the starting point that made this Doggo build practical in the first place. This repository is our Pi-first control and build branch around that work, not an attempt to erase where it came from. If you are here because of this repo, please go look at the original project, star it, and give the original author the credit they deserve.

## Current Project Status

- Raspberry Pi 4B is the main brain.
- Waveshare Bus Servo Adapter is used for the STS3215 servo bus.
- Browser control is live for desktop and Steam Deck use.
- ESP32 joystick support is scaffolded as a low-latency manual input path.
- Servo discovery, calibration, stand, sit, storage, relax, motion recording, playback, and live web bring-up are working.
- A first walking pass exists, but it is still being tuned and is not moving correctly yet.

## Documentation

- [docs/project-status.md](docs/project-status.md)
  Current build status, what's working, and what still needs engineering work.
- [docs/operator-guide.md](docs/operator-guide.md)
  Day-to-day operator notes, main commands, and safe usage.
- [docs/motion-recording.md](docs/motion-recording.md)
  Recording, saving, and replaying motion clips.
- [docs/bringup.md](docs/bringup.md)
  Hardware bring-up and first servo discovery.
- [docs/architecture.md](docs/architecture.md)
  Software layout and design direction.

## Hardware and Print Files

This repo now contains both the printable robot files and the active control software:

- `3D Files/` for printable parts
- `Fusion360/` for editable CAD
- `Pics and videos/` for build media
- `src/`, `config/`, `firmware/`, and `docs/` for the new Doggo control stack

### New print files

- `3D Files/Shoe.stl`
  Print this in TPU as a slip-on shoe for the robot feet to improve grip.
- `3D Files/expanded top.stl`
  Updated top/body part added to the printable parts set.

## Media

The build media folder is now part of the repo:

- `Pics and videos/PXL_20260322_135309000.RAW-01.jpg`

Future videos and progress photos should also go in `Pics and videos/` so the README and project history stay together.

## Doggo Control Stack

The software here is a fresh control build shaped around the confirmed hardware:

- Raspberry Pi 4B as the main controller
- 12x STS3215 servos
- Waveshare bus adapter
- browser teleop for laptop, phone, and Steam Deck
- future person-following with cameras

### What is already working

- STS3215 serial packet layer and Waveshare adapter access
- servo scanning, ID assignment, single-servo moves, and readback
- saved stand, sit, storage, and calibration poses
- web dashboard with pose controls, live health, touch sliders, servo monitor, and motion capture tools
- browser sequencer for chaining pose commands, waits, playback, and save-current steps
- variable-duration motion recording, saved recordings, and playback
- first-pass crawl gait wiring for browser and future ESP32 teleop

### What still needs tuning

- walking gait signs and stride tuning
- smoother walk transitions
- measured IK and geometry-based motion
- camera tracking and follow behavior

## Requirements

Install the repo dependencies with:

```powershell
pip install -r requirements.txt
```

For editable development from this repo:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## Quick Start

```powershell
cd "H:\3D Prints\Robot Dogo\Dogo Code"
Copy-Item config\doggo.example.yaml config\doggo.local.yaml
```

Update `config\doggo.local.yaml` for your serial device, current servo mapping, and walking tuning.

### Scan the servo bus

```powershell
doggo --config config\doggo.local.yaml scan
```

### Start the web interface

```powershell
doggo --config config\doggo.local.yaml serve
```

Then open `http://<pi-ip>:8080`.

## Main Commands

```powershell
doggo --config config\doggo.local.yaml scan
doggo --config config\doggo.local.yaml read-all
doggo --config config\doggo.local.yaml read-pos --id 1
doggo --config config\doggo.local.yaml move --id 1 --position 2048
doggo --config config\doggo.local.yaml assign-id --current-id 1 --new-id 4
doggo --config config\doggo.local.yaml stand
doggo --config config\doggo.local.yaml sit
doggo --config config\doggo.local.yaml storage
doggo --config config\doggo.local.yaml relax
doggo --config config\doggo.local.yaml record --name wave --duration-ms 10000
doggo --config config\doggo.local.yaml stop-recording
doggo --config config\doggo.local.yaml save-recording --name Wave
doggo --config config\doggo.local.yaml save-current --name StandSnapshot
doggo --config config\doggo.local.yaml playback
doggo --config config\doggo.local.yaml playback --name Wave
doggo --config config\doggo.local.yaml serve
```

## Safety Notes

- Verify the STS3215 servo rail voltage against the servo datasheet before powering the bus.
- Start with one servo on the bus when bringing up new hardware.
- Tune walking with very small amplitudes first.
- Keep a quick path to `relax` while testing motion.

## Near-Term Roadmap

1. Finish tuning the first walking pass until the gait moves correctly.
2. Add safer live gait tuning controls in the browser.
3. Add measured geometry and IK.
4. Add camera-assisted person following.
