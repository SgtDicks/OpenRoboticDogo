# Operator Guide

This is the quickest way to run Doggo safely with the current software.

## Before you move anything

- Make sure the Raspberry Pi power and servo power are separate and stable.
- Verify the servo bus voltage is correct for the STS3215 servos.
- Keep a fast path to `relax`.
- Start with Doggo supported off the ground if you are testing a new motion.

## Main local config

Most real operation uses:

```powershell
config\doggo.local.yaml
```

That file contains:

- servo IDs
- current pose values
- current walking tuning
- recording and motion settings

## Start the web dashboard

```powershell
doggo --config config\doggo.local.yaml serve
```

Open:

```text
http://127.0.0.1:8080
```

Or from another machine:

```text
http://<pi-ip>:8080
```

## Main body actions

From the web UI or CLI you can use:

```powershell
doggo --config config\doggo.local.yaml stand
doggo --config config\doggo.local.yaml sit
doggo --config config\doggo.local.yaml storage
doggo --config config\doggo.local.yaml relax
```

## Useful CLI commands

### Bus and servo checks

```powershell
doggo --config config\doggo.local.yaml scan
doggo --config config\doggo.local.yaml read-all
doggo --config config\doggo.local.yaml read-pos --id 1
doggo --config config\doggo.local.yaml save-current --name StandSnapshot
doggo --config config\doggo.local.yaml move --id 1 --position 2048
doggo --config config\doggo.local.yaml assign-id --current-id 1 --new-id 4
```

### Controlled single-servo testing

```powershell
doggo --config config\doggo.local.yaml step-test --id 1 --delta 40 --steps 10 --hold-ms 250
```

### Per-leg checks

```powershell
doggo --config config\doggo.local.yaml stand --leg front_left
doggo --config config\doggo.local.yaml storage --leg rear_right
doggo --config config\doggo.local.yaml sss --leg front_right
```

## Web dashboard sections

### Quick actions

- Refresh health
- Scan bus
- Read positions
- Save Current As

### Body poses

- Stand
- Sit
- Storage
- Relax

### Sequencer

- One command per line
- Supports `stand`, `sit`, `storage`, `relax`, `scan`, `read-positions`
- Supports `play-last`, `play-saved NAME`, `save-current NAME`, and `wait SECONDS`
- Can insert the currently selected saved clip into the script with `Add Saved Clip Step`
- Uses the current playback speed field for playback steps

### Touch trim and teleop

- On-screen sliders
- Keyboard:
  - `WASD` or arrow keys for movement
  - `Q` and `E` for turn
- Gamepad:
  - left stick for forward/strafe
  - right stick X for turn

### Servo monitor

Shows:

- configured servo IDs
- leg and joint mapping
- live positions
- voltage
- temperature

## Motion rules to remember

- `relax` disables torque so you can reposition the robot by hand.
- While the web server owns the servo bus, separate CLI commands may fail if they try to open the same serial port.
- Walking is still under tuning. Use very small inputs.
- If Doggo starts doing the wrong thing, use `Relax`.

## If something feels wrong

1. Stop sending new movement commands.
2. Hit `Relax`.
3. Read current positions.
4. Use `Save Current As` if you want to keep the live readback as a one-frame clip.
5. Compare the live posture to the saved pose values in `config\doggo.local.yaml`.
6. Re-capture or retune the pose before trying again.
