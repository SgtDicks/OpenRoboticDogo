# Motion Recording

Doggo can record servo motion over time and replay it later.

This is useful for:

- capturing hand-guided motions while relaxed
- testing repeated pose transitions
- saving small demo clips
- turning manual motion experiments into repeatable playback

## What gets recorded

The recorder samples all configured servos and stores:

- timestamp per frame
- servo positions for each frame
- sample interval
- total duration
- stop reason

Playback uses the recorded frame timing and derives servo speed from the recorded movement, unless you override speed manually.

## Record from the web UI

Open the dashboard and use the `Motion Capture` panel.

You can set:

- clip name
- recording length in seconds
- idle auto-stop in seconds

Buttons:

- `Record`
- `Stop`
- `Play Last`
- `Save Last As`
- `Play Saved`

## Recommended relaxed capture workflow

1. Put Doggo in `Relax`.
2. Set a clip name.
3. Set the record duration.
4. Optionally set idle auto-stop.
5. Press `Record`.
6. Move Doggo by hand through the motion.
7. Press `Stop` if needed, or let duration or idle-stop end the capture.
8. Press `Save Last As` if you want to keep the clip.
9. Use `Play Last` or `Play Saved` to test replay.

## Stop reasons

The saved recording reports why capture ended:

- `duration`
  The requested recording time elapsed.
- `manual`
  The operator pressed `Stop`.
- `idle`
  No major servo movement happened for the configured idle timeout.

## CLI recording commands

### Record a clip

```powershell
doggo --config config\doggo.local.yaml record --name wave --duration-ms 15000 --sample-ms 100
```

### Record with idle auto-stop

```powershell
doggo --config config\doggo.local.yaml record --name hand_pose --duration-ms 30000 --sample-ms 100 --idle-stop-seconds 2.0 --idle-threshold-ticks 15
```

### Stop an active recording

```powershell
doggo --config config\doggo.local.yaml stop-recording
```

### Save the last recording

```powershell
doggo --config config\doggo.local.yaml save-recording --name Wave
```

### Play back the last recording

```powershell
doggo --config config\doggo.local.yaml playback
```

### Play back a saved recording

```powershell
doggo --config config\doggo.local.yaml playback --name Wave
```

## Where recording files live

Last captured recording state:

```text
config/doggo.local.state.recording.json
```

Named saved recordings:

```text
config/recordings/
```

## Notes and limits

- The recorder uses the configured servo IDs from the local config.
- Recording does not magically fix an unsafe motion. If the captured motion is unstable, playback will still be unstable.
- Longer recordings create larger JSON files.
- Playback locks the motion path while running so other web actions do not overlap on the servo bus.
