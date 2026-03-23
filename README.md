![OpenRoboticDog](illustration.png)

# OpenRoboticDogo

OpenRoboticDogo is an affordable open-source quadruped robot project built around 3D-printable hardware and a Raspberry Pi control stack. This repo now contains both the original mechanical design assets and a fresh software stack for Doggo.

## Hardware in this repo

- `3D Files/` for printable parts
- `Fusion360/` for editable CAD
- `max_v1/` for earlier design assets

## Doggo Control Stack

The software here is a new build shaped around the confirmed hardware:

- Raspberry Pi 4B as the main brain
- Waveshare Bus Servo Adapter for 12x STS3215 servos
- ESP32 joystick controller as a low-latency manual input and fallback path
- Browser control for desktop and Steam Deck
- Future person-following with USB cameras

## What is here already

- Pi-side Python service for servo access, safety states, and the web API
- Real STS3215 serial packet layer for the Waveshare adapter
- Servo scan, ID assignment, individual servo jog, stand, storage, sit, and relax flows
- Browser UI for desktop and Steam Deck use
- ESP32 firmware scaffold that sends joystick commands to the Pi over UDP
- Docs for safe bring-up, architecture, and milestone order

Walking is intentionally scaffolded rather than faked. Real calibration and measured leg geometry come first so the eventual gait code is worth trusting.

## Current assumptions

- Web and Steam Deck share the same browser-based control surface
- ESP32 joystick input is sent to the Pi over Wi-Fi UDP
- The live servo mapping, poses, and calibration data are stored in `config/doggo.local.yaml`

## Quick start

```powershell
cd "H:\3D Prints\Robot Dogo\Dogo Code"
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item config\doggo.example.yaml config\doggo.local.yaml
```

Update `config\doggo.local.yaml` for your Pi serial device, current servo layout, and network details.

### Scan the servo bus

```powershell
doggo --config config\doggo.local.yaml scan
```

### Start the Pi service

```powershell
doggo --config config\doggo.local.yaml serve
```

Then open `http://<pi-ip>:8080`.

## Main commands

```powershell
doggo --config config\doggo.local.yaml scan
doggo --config config\doggo.local.yaml read-pos --id 1
doggo --config config\doggo.local.yaml assign-id --current-id 1 --new-id 4
doggo --config config\doggo.local.yaml move --id 1 --position 2048
doggo --config config\doggo.local.yaml stand
doggo --config config\doggo.local.yaml storage
doggo --config config\doggo.local.yaml sit
doggo --config config\doggo.local.yaml relax
doggo --config config\doggo.local.yaml serve
```

## Safety notes

- Verify the STS3215 servo supply voltage against the servo datasheet before powering the bus. Do not assume the Pi or controller input voltage is the servo voltage.
- Bring the system up with one servo connected first, not all 12.
- Assign IDs before mounting all servos into the dog.
- Keep `stand_pose` conservative until the first neutral calibration is complete.

## Next milestones

1. Finish measured calibration for every leg and joint.
2. Add safer motion-state transitions and calibration UI tools.
3. Add measured leg geometry and inverse kinematics.
4. Add walking gait and teleop polish.
5. Add person-follow mode using onboard cameras.
