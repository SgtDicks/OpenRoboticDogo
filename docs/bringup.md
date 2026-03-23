# Bring-up

## 1. Power safely first

- Verify the STS3215 servo rail voltage against the servo datasheet before connecting the full dog.
- Power the Raspberry Pi separately from the servo bus.
- Start with one servo on the bus, not all 12.

## 2. Create a local config

```powershell
Copy-Item config\doggo.example.yaml config\doggo.local.yaml
```

Update:

- `servo_bus.device`
- `web.port` if needed
- `esp32.listen_port` if needed

## 3. Discover a single servo

```powershell
doggo --config config\doggo.local.yaml scan
doggo --config config\doggo.local.yaml read-pos --id 1
```

If the servo does not answer, stop and re-check:

- power
- ground commonality
- Waveshare adapter device name
- servo bus polarity and connector orientation

## 4. Assign IDs one servo at a time

Mounting servos before IDs are unique makes recovery harder than it needs to be.

```powershell
doggo --config config\doggo.local.yaml assign-id --current-id 1 --new-id 4
```

Repeat until all 12 IDs are unique.

## 5. Capture neutral positions

For each servo:

1. Put the joint in the mechanical neutral position.
2. Read the present tick value with `read-pos`.
3. Copy that value into the matching `neutral_ticks` field in `config\doggo.local.yaml`.
4. If increasing ticks moves the joint the wrong way later, flip `direction` from `1` to `-1`.

## 6. Tune a conservative stand pose

Start with very small changes from neutral. If a joint binds, stop, relax torque, and reduce the target range.

```powershell
doggo --config config\doggo.local.yaml move --id 1 --position 2048
doggo --config config\doggo.local.yaml stand
doggo --config config\doggo.local.yaml relax
```

## 7. Start the browser UI

```powershell
doggo --config config\doggo.local.yaml serve
```

Open `http://<pi-ip>:8080`.

## 8. Only add walking after this is true

- All 12 IDs respond reliably
- Every joint neutral is known
- Directions are correct
- Safe min/max limits are known
- A stand pose works repeatedly

That is the point where gait work becomes productive instead of risky.
