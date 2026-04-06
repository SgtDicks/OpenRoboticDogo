const statusView = document.getElementById("status-view");
const logView = document.getElementById("event-log");
const connectionState = document.getElementById("connection-state");
const robotName = document.getElementById("robot-name");
const busDevice = document.getElementById("bus-device");
const robotState = document.getElementById("robot-state");
const gaitState = document.getElementById("gait-state");
const healthSupervisor = document.getElementById("health-supervisor");
const healthServo = document.getElementById("health-servo");
const healthGait = document.getElementById("health-gait");
const healthSources = document.getElementById("health-sources");
const lastMessage = document.getElementById("last-message");
const walkingProfile = document.getElementById("walking-profile");
const servoTableBody = document.getElementById("servo-table-body");
const recordingSummary = document.getElementById("recording-summary");
const motionRecordButton = document.getElementById("motion-record");
const motionStopButton = document.getElementById("motion-stop");
const motionPlaybackButton = document.getElementById("motion-playback");
const motionSaveButton = document.getElementById("motion-save");
const motionSaveCurrentButton = document.getElementById("motion-save-current");
const motionPlaybackSavedButton = document.getElementById("motion-playback-saved");
const motionNameInput = document.getElementById("motion-name");
const motionDurationInput = document.getElementById("motion-duration-seconds");
const motionIdleInput = document.getElementById("motion-idle-seconds");
const motionPlaybackSpeedInput = document.getElementById("motion-playback-speed");
const savedRecordingsSelect = document.getElementById("saved-recordings");
const sequenceScriptInput = document.getElementById("sequence-script");
const sequenceRunButton = document.getElementById("sequence-run");
const sequenceStopButton = document.getElementById("sequence-stop");
const sequenceExampleButton = document.getElementById("sequence-example");
const sequenceSavedClipSelect = document.getElementById("sequence-saved-clip");
const sequenceInsertSavedButton = document.getElementById("sequence-insert-saved");
const sequenceStatus = document.getElementById("sequence-status");

const axisForward = document.getElementById("axis-forward");
const axisStrafe = document.getElementById("axis-strafe");
const axisTurn = document.getElementById("axis-turn");
const axisForwardBar = document.getElementById("axis-forward-bar");
const axisStrafeBar = document.getElementById("axis-strafe-bar");
const axisTurnBar = document.getElementById("axis-turn-bar");

const touchForward = document.getElementById("touch-forward");
const touchStrafe = document.getElementById("touch-strafe");
const touchTurn = document.getElementById("touch-turn");
const touchForwardValue = document.getElementById("touch-forward-value");
const touchStrafeValue = document.getElementById("touch-strafe-value");
const touchTurnValue = document.getElementById("touch-turn-value");

let socket = null;
let reconnectTimer = null;
let latestStatus = null;
let latestConfig = null;
let latestPositions = {};
let latestScan = new Map();
let lastTeleopErrorAt = 0;
let motionBusy = false;
let motionStatusTimer = null;
let latestSavedRecordings = [];
let sequenceBusy = false;
let sequenceStopRequested = false;

const sequenceManagedButtons = [
  document.getElementById("refresh-health"),
  document.getElementById("scan-servos"),
  document.getElementById("read-positions"),
  document.getElementById("pose-stand"),
  document.getElementById("pose-sit"),
  document.getElementById("pose-storage"),
  document.getElementById("pose-relax"),
].filter(Boolean);

const keyboardAxes = { forward: 0, strafe: 0, turn: 0 };
const touchAxes = { forward: 0, strafe: 0, turn: 0 };
const pressed = new Set();

function log(message) {
  const lines = logView.textContent.split("\n").slice(-20);
  lines.push(`${new Date().toLocaleTimeString()} ${message}`);
  logView.textContent = lines.join("\n");
}

function clamp(value, min = -1, max = 1) {
  return Math.max(min, Math.min(max, value));
}

function formatSigned(value) {
  return value.toFixed(2);
}

function formatLegName(name) {
  return name.replaceAll("_", " ");
}

function formatJointName(name) {
  return name.replaceAll("_", " ");
}

function setConnection(connected) {
  connectionState.textContent = connected ? "Connected" : "Disconnected";
  connectionState.className = connected ? "status online" : "status offline";
}

function dominantAxis(...values) {
  return values.reduce((winner, candidate) => {
    if (Math.abs(candidate) > Math.abs(winner)) {
      return candidate;
    }
    return winner;
  }, 0);
}

function updateAxisBar(element, value) {
  element.style.width = `${Math.round(Math.abs(value) * 100)}%`;
}

function updateAxisReadout(axes) {
  axisForward.textContent = formatSigned(axes.forward);
  axisStrafe.textContent = formatSigned(axes.strafe);
  axisTurn.textContent = formatSigned(axes.turn);
  updateAxisBar(axisForwardBar, axes.forward);
  updateAxisBar(axisStrafeBar, axes.strafe);
  updateAxisBar(axisTurnBar, axes.turn);
}

function syncTouchInputs() {
  touchForward.value = `${touchAxes.forward}`;
  touchStrafe.value = `${touchAxes.strafe}`;
  touchTurn.value = `${touchAxes.turn}`;
  touchForwardValue.textContent = formatSigned(touchAxes.forward);
  touchStrafeValue.textContent = formatSigned(touchAxes.strafe);
  touchTurnValue.textContent = formatSigned(touchAxes.turn);
}

function setTouchAxes(nextAxes) {
  touchAxes.forward = clamp(nextAxes.forward ?? touchAxes.forward);
  touchAxes.strafe = clamp(nextAxes.strafe ?? touchAxes.strafe);
  touchAxes.turn = clamp(nextAxes.turn ?? touchAxes.turn);
  syncTouchInputs();
}

function resetTouchAxes() {
  setTouchAxes({ forward: 0, strafe: 0, turn: 0 });
}

function currentGamepadAxes() {
  const gamepads = navigator.getGamepads ? navigator.getGamepads() : [];
  const pad = [...gamepads].find(Boolean);
  if (!pad) {
    return { forward: 0, strafe: 0, turn: 0 };
  }

  const deadzone = 0.15;
  const applyDeadzone = (value) => (Math.abs(value) < deadzone ? 0 : value);
  return {
    forward: applyDeadzone(-(pad.axes[1] || 0)),
    strafe: applyDeadzone(pad.axes[0] || 0),
    turn: applyDeadzone(pad.axes[2] || 0),
  };
}

function updateKeyboardAxes() {
  keyboardAxes.forward = 0;
  keyboardAxes.strafe = 0;
  keyboardAxes.turn = 0;

  if (pressed.has("KeyW") || pressed.has("ArrowUp")) keyboardAxes.forward += 1;
  if (pressed.has("KeyS") || pressed.has("ArrowDown")) keyboardAxes.forward -= 1;
  if (pressed.has("KeyD") || pressed.has("ArrowRight")) keyboardAxes.strafe += 1;
  if (pressed.has("KeyA") || pressed.has("ArrowLeft")) keyboardAxes.strafe -= 1;
  if (pressed.has("KeyQ")) keyboardAxes.turn -= 1;
  if (pressed.has("KeyE")) keyboardAxes.turn += 1;
}

function mergedAxes() {
  const gamepadAxes = currentGamepadAxes();
  return {
    forward: clamp(dominantAxis(touchAxes.forward, keyboardAxes.forward, gamepadAxes.forward)),
    strafe: clamp(dominantAxis(touchAxes.strafe, keyboardAxes.strafe, gamepadAxes.strafe)),
    turn: clamp(dominantAxis(touchAxes.turn, keyboardAxes.turn, gamepadAxes.turn)),
  };
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json();
}

function stateTone(status) {
  if (status === true || status === "ready" || status === "connected" || status === "walking" || status === "standing") {
    return "state-online";
  }
  if (status === false || status === "offline" || status === "relaxed") {
    return "state-danger";
  }
  return "state-warning";
}

function renderWalkingProfile(config) {
  if (!config?.walking) {
    walkingProfile.innerHTML = '<span class="chip">Walking config unavailable</span>';
    return;
  }

  const walking = config.walking;
  const chips = [
    `gait: ${walking.gait}`,
    `cycle: ${walking.cycle_time_seconds}s`,
    `swing: ${walking.swing_ratio}`,
    `stride: ${walking.knee_stride_ticks}t`,
    `knee lift: ${walking.knee_lift_ticks}t`,
    `foot lift: ${walking.foot_lift_ticks}t`,
    `speed: ${walking.step_speed}`,
    `accel: ${walking.step_acceleration}`,
  ];
  walkingProfile.innerHTML = chips.map((chip) => `<span class="chip">${chip}</span>`).join("");
}

function renderSavedRecordings(savedRecordings = []) {
  latestSavedRecordings = Array.isArray(savedRecordings) ? savedRecordings : [];
  if (!latestSavedRecordings.length) {
    savedRecordingsSelect.innerHTML = '<option value="">No saved clips yet</option>';
    sequenceSavedClipSelect.innerHTML = '<option value="">No saved clips yet</option>';
    refreshBusyState();
    return;
  }

  const options = latestSavedRecordings
    .map((entry) => `<option value="${entry.name}">${entry.name} (${(entry.duration_ms / 1000).toFixed(1)}s)</option>`)
    .join("");

  const applySavedRecordingOptions = (select) => {
    const currentValue = select.value;
    select.innerHTML = options;
    if (latestSavedRecordings.some((entry) => entry.name === currentValue)) {
      select.value = currentValue;
    }
  };

  applySavedRecordingOptions(savedRecordingsSelect);
  applySavedRecordingOptions(sequenceSavedClipSelect);
  refreshBusyState();
}

function renderRecording(recording) {
  if (!recording?.available) {
    recordingSummary.textContent = recording?.active
      ? "Recording now."
      : "No recording saved yet.";
    return;
  }

  const durationSeconds = (recording.duration_ms / 1000).toFixed(1);
  const servoCount = Array.isArray(recording.servo_ids) ? recording.servo_ids.length : 0;
  const stopReason = recording.stop_reason ? ` | stop=${recording.stop_reason}` : "";
  recordingSummary.textContent =
    `${recording.name} | ${recording.frame_count} frames | ${durationSeconds}s | `
    + `${recording.sample_ms}ms sample | ${servoCount} servos${stopReason}`;
}

function setMotionControlsBusy(isBusy) {
  motionRecordButton.disabled = isBusy;
  motionPlaybackButton.disabled = isBusy;
  motionSaveButton.disabled = isBusy;
  motionSaveCurrentButton.disabled = isBusy;
  motionPlaybackSavedButton.disabled = isBusy || !latestSavedRecordings.length;
  motionStopButton.disabled = !isBusy;
  motionNameInput.disabled = isBusy;
  motionDurationInput.disabled = isBusy;
  motionIdleInput.disabled = isBusy;
  motionPlaybackSpeedInput.disabled = isBusy;
  savedRecordingsSelect.disabled = isBusy || !latestSavedRecordings.length;
}

function setSequenceControlsBusy(isBusy, stopEnabled = false) {
  sequenceRunButton.disabled = isBusy;
  sequenceExampleButton.disabled = isBusy;
  sequenceScriptInput.disabled = isBusy;
  sequenceSavedClipSelect.disabled = isBusy || !latestSavedRecordings.length;
  sequenceInsertSavedButton.disabled = isBusy || !latestSavedRecordings.length;
  sequenceStopButton.disabled = !stopEnabled;
  sequenceManagedButtons.forEach((button) => {
    button.disabled = isBusy;
  });
}

function refreshBusyState() {
  const uiBusy = motionBusy || sequenceBusy;
  setMotionControlsBusy(uiBusy);
  setSequenceControlsBusy(uiBusy, sequenceBusy);
}

function applyPositions(positions = {}) {
  latestPositions = Object.fromEntries(
    Object.entries(positions || {}).map(([servoId, position]) => [Number(servoId), position])
  );
  renderServoTable();
}

function updateSequenceStatus(message) {
  sequenceStatus.textContent = message;
}

function stopMotionCountdown() {
  if (motionStatusTimer) {
    window.clearInterval(motionStatusTimer);
    motionStatusTimer = null;
  }
}

function startRecordingCountdown(durationMs, idleStopSeconds) {
  stopMotionCountdown();
  const deadline = Date.now() + durationMs;

  const renderCountdown = () => {
    const remainingMs = Math.max(0, deadline - Date.now());
    const remainingSeconds = (remainingMs / 1000).toFixed(1);
    const idleText = idleStopSeconds > 0
      ? ` Auto-stop also triggers after ${idleStopSeconds.toFixed(1)}s of low movement.`
      : "";
    recordingSummary.textContent =
      `Recording now. ${remainingSeconds}s left. If Doggo is relaxed, move it by hand through the motion you want to capture.${idleText}`;
  };

  renderCountdown();
  motionStatusTimer = window.setInterval(renderCountdown, 100);
}

function flattenServoLayout(config) {
  if (!config?.legs) {
    return [];
  }

  const rows = [];
  Object.entries(config.legs).forEach(([legName, leg]) => {
    Object.entries(leg).forEach(([jointName, joint]) => {
      rows.push({
        servoId: joint.id,
        legName,
        jointName,
        minTicks: joint.min_ticks,
        maxTicks: joint.max_ticks,
      });
    });
  });
  rows.sort((left, right) => left.servoId - right.servoId);
  return rows;
}

function formatVoltage(rawVoltage) {
  if (typeof rawVoltage !== "number") {
    return "--";
  }
  return `${(rawVoltage / 10).toFixed(1)}V`;
}

function formatTemperature(rawTemperature) {
  if (typeof rawTemperature !== "number") {
    return "--";
  }
  return `${rawTemperature}C`;
}

function renderServoTable() {
  const layout = flattenServoLayout(latestConfig);
  if (!layout.length) {
    servoTableBody.innerHTML = '<tr><td colspan="7">Waiting for config...</td></tr>';
    return;
  }

  servoTableBody.innerHTML = layout
    .map((row) => {
      const scan = latestScan.get(row.servoId);
      const position = latestPositions[row.servoId];
      const status = position != null ? "live" : scan ? "scanned" : "unknown";
      return `
        <tr>
          <td><strong>${row.servoId}</strong></td>
          <td>${formatLegName(row.legName)}</td>
          <td>${formatJointName(row.jointName)}</td>
          <td>${position != null ? position : "--"} <span class="summary-note">(${row.minTicks}-${row.maxTicks})</span></td>
          <td>${formatVoltage(scan?.voltage)}</td>
          <td>${formatTemperature(scan?.temperature)}</td>
          <td>${status}</td>
        </tr>
      `;
    })
    .join("");
}

function renderStatus(status) {
  latestStatus = status;
  statusView.textContent = JSON.stringify(status, null, 2);

  robotName.textContent = status.robot || latestConfig?.robot?.name || "Doggo";
  robotState.textContent = status.state || "unknown";
  robotState.className = stateTone(status.state);

  const servoConnected = Boolean(status.servo_bus?.connected);
  healthSupervisor.textContent = status.state || "unknown";
  healthSupervisor.className = stateTone(status.state);
  healthServo.textContent = servoConnected ? "connected" : "offline";
  healthServo.className = stateTone(servoConnected ? "connected" : "offline");
  healthGait.textContent = status.gait?.ready ? "ready" : "blocked";
  healthGait.className = stateTone(status.gait?.ready ? "ready" : "blocked");

  const activeSources = status.active_sources?.length ? status.active_sources.join(", ") : "none";
  healthSources.textContent = activeSources;
  healthSources.className = status.active_sources?.length ? "state-online" : "state-warning";

  const gaitReason = status.gait?.ready ? "Crawl gait ready" : status.gait?.reason || "Unavailable";
  gaitState.textContent = gaitReason;
  lastMessage.textContent = status.last_message || "No supervisor message yet.";
  renderRecording(status.recording);
  renderSavedRecordings(status.recording?.saved_recordings || latestSavedRecordings);

  if (Array.isArray(status.last_scan)) {
    latestScan = new Map(status.last_scan.map((entry) => [entry.servo_id, entry]));
  }

  renderServoTable();
}

async function loadConfig() {
  try {
    latestConfig = await fetchJson("/api/config");
    busDevice.textContent = `${latestConfig.servo_bus.device} @ ${latestConfig.servo_bus.baud_rate}`;
    if (!motionPlaybackSpeedInput.value && latestConfig?.motion?.default_speed) {
      motionPlaybackSpeedInput.value = `${latestConfig.motion.default_speed}`;
    }
    renderWalkingProfile(latestConfig);
    renderServoTable();
  } catch (error) {
    log(`Config load failed: ${error.message}`);
  }
}

async function loadRecording() {
  try {
    const payload = await fetchJson("/api/motion/recording");
    renderRecording(payload.recording);
    renderSavedRecordings(payload.saved_recordings || payload.recording?.saved_recordings || []);
  } catch (error) {
    log(`Recording load failed: ${error.message}`);
  }
}

function recordingName() {
  const value = motionNameInput.value.trim();
  return value || "last_capture";
}

function sequenceScriptStorageKey() {
  return `${window.location.pathname}:sequence-script`;
}

function recordDurationMs() {
  const seconds = Number(motionDurationInput.value || 10);
  const safeSeconds = Number.isFinite(seconds) ? seconds : 10;
  return Math.max(1, Math.min(300, safeSeconds)) * 1000;
}

function idleStopSeconds() {
  const seconds = Number(motionIdleInput.value || 0);
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return null;
  }
  return Math.max(0.5, Math.min(600, seconds));
}

function playbackSpeed() {
  const value = motionPlaybackSpeedInput.value.trim();
  if (!value) {
    return null;
  }

  const speed = Number(value);
  if (!Number.isFinite(speed) || speed <= 0) {
    return null;
  }

  return Math.max(1, Math.min(4095, Math.round(speed)));
}

function normalizeSequenceLine(line, lineNumber) {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith("#")) {
    return null;
  }

  const lower = trimmed.toLowerCase();
  if (["stand", "sit", "storage", "relax", "scan", "read-positions"].includes(lower)) {
    return {
      type: "action",
      command: lower,
      label: trimmed,
      lineNumber,
    };
  }

  if (lower === "play-last") {
    return {
      type: "playback",
      name: null,
      label: "play-last",
      lineNumber,
    };
  }

  if (lower.startsWith("play-saved ")) {
    const name = trimmed.slice("play-saved ".length).trim();
    if (!name) {
      throw new Error(`Line ${lineNumber}: play-saved needs a clip name.`);
    }
    return {
      type: "playback",
      name,
      label: `play-saved ${name}`,
      lineNumber,
    };
  }

  if (lower.startsWith("save-current ")) {
    const name = trimmed.slice("save-current ".length).trim();
    if (!name) {
      throw new Error(`Line ${lineNumber}: save-current needs a clip name.`);
    }
    return {
      type: "save-current",
      name,
      label: `save-current ${name}`,
      lineNumber,
    };
  }

  if (lower.startsWith("wait ")) {
    const secondsText = trimmed.slice("wait ".length).trim();
    const seconds = Number(secondsText);
    if (!Number.isFinite(seconds) || seconds < 0) {
      throw new Error(`Line ${lineNumber}: wait needs a non-negative number of seconds.`);
    }
    return {
      type: "wait",
      seconds,
      label: `wait ${seconds}s`,
      lineNumber,
    };
  }

  throw new Error(`Line ${lineNumber}: unsupported command "${trimmed}".`);
}

function parseSequenceScript(source) {
  const steps = [];
  source.split(/\r?\n/).forEach((line, index) => {
    const step = normalizeSequenceLine(line, index + 1);
    if (step) {
      steps.push(step);
    }
  });
  return steps;
}

function saveSequenceDraft() {
  window.localStorage.setItem(sequenceScriptStorageKey(), sequenceScriptInput.value);
}

function loadSequenceDraft() {
  const stored = window.localStorage.getItem(sequenceScriptStorageKey());
  if (stored) {
    sequenceScriptInput.value = stored;
  }
}

function loadSequenceExample() {
  sequenceScriptInput.value = [
    "stand",
    "wait 1.0",
    "play-saved Wave",
    "wait 0.5",
    "sit",
    "relax",
  ].join("\n");
  saveSequenceDraft();
  updateSequenceStatus("Example sequence loaded.");
}

function appendSequenceLine(line) {
  const existing = sequenceScriptInput.value.trimEnd();
  sequenceScriptInput.value = existing ? `${existing}\n${line}` : line;
  saveSequenceDraft();
}

function insertSavedClipStep() {
  const name = sequenceSavedClipSelect.value;
  if (!name) {
    updateSequenceStatus("Pick a saved clip first.");
    return;
  }

  appendSequenceLine(`play-saved ${name}`);
  updateSequenceStatus(`Added saved clip step for ${name}.`);
}

async function waitForSequence(seconds) {
  const durationMs = Math.max(0, Math.round(seconds * 1000));
  const startedAt = Date.now();

  while ((Date.now() - startedAt) < durationMs) {
    if (sequenceStopRequested) {
      return;
    }

    const remainingMs = Math.max(0, durationMs - (Date.now() - startedAt));
    updateSequenceStatus(`Waiting ${(remainingMs / 1000).toFixed(1)}s before the next step.`);
    await new Promise((resolve) => window.setTimeout(resolve, Math.min(100, remainingMs || 100)));
  }
}

async function refreshHealth() {
  try {
    const status = await fetchJson("/api/health");
    renderStatus(status);
  } catch (error) {
    log(`Health check failed: ${error.message}`);
  }
}

async function scanServos(throwOnError = false) {
  try {
    const payload = await fetchJson("/api/servos/scan");
    latestScan = new Map((payload.found || []).map((entry) => [entry.servo_id, entry]));
    renderServoTable();
    log(`Servo scan finished. Found ${(payload.found || []).length} servo(s).`);
    await refreshHealth();
  } catch (error) {
    log(`Servo scan failed: ${error.message}`);
    if (throwOnError) {
      throw error;
    }
  }
}

async function readPositions(throwOnError = false) {
  try {
    const payload = await fetchJson("/api/servos/positions");
    applyPositions(payload.positions || {});
    log(`Read ${Object.keys(latestPositions).length} servo position(s).`);
  } catch (error) {
    log(`Position read failed: ${error.message}`);
    if (throwOnError) {
      throw error;
    }
  }
}

async function sendAction(url, label, throwOnError = false) {
  try {
    const status = await fetchJson(url, { method: "POST", body: "{}" });
    renderStatus(status);
    log(`${label} ok.`);
  } catch (error) {
    log(`${label} failed: ${error.message}`);
    if (throwOnError) {
      throw error;
    }
  }
}

async function recordMotion() {
  const durationMs = recordDurationMs();
  const idleSeconds = idleStopSeconds();
  motionBusy = true;
  refreshBusyState();
  motionRecordButton.textContent = "Recording...";
  startRecordingCountdown(durationMs, idleSeconds || 0);
  log(`Motion recording started for ${(durationMs / 1000).toFixed(1)} seconds.`);
  try {
    const payload = await fetchJson("/api/motion/record", {
      method: "POST",
      body: JSON.stringify({
        name: recordingName(),
        duration_ms: durationMs,
        sample_ms: 100,
        idle_stop_seconds: idleSeconds,
        idle_threshold_ticks: 15,
      }),
    });
    renderRecording(payload.recording);
    renderSavedRecordings(payload.saved_recordings || []);
    renderStatus(payload.status);
    log(`Motion record ok: ${payload.clip.name} captured (${payload.clip.stop_reason}).`);
  } catch (error) {
    recordingSummary.textContent = "Motion recording failed. Check the event log and try again.";
    log(`Motion record failed: ${error.message}`);
  } finally {
    stopMotionCountdown();
    motionBusy = false;
    refreshBusyState();
    motionRecordButton.textContent = "Record";
  }
}

async function stopMotionRecording() {
  try {
    const payload = await fetchJson("/api/motion/record/stop", {
      method: "POST",
      body: "{}",
    });
    renderRecording(payload.recording);
    renderSavedRecordings(payload.saved_recordings || []);
    renderStatus(payload.status);
    recordingSummary.textContent = "Stop requested. Waiting for the current sample to finish.";
    log("Motion recording stop requested.");
  } catch (error) {
    log(`Motion stop failed: ${error.message}`);
  }
}

async function saveMotionRecording() {
  try {
    const payload = await fetchJson("/api/motion/recording/save", {
      method: "POST",
      body: JSON.stringify({ name: recordingName() }),
    });
    renderRecording(payload.recording);
    renderSavedRecordings(payload.saved_recordings || []);
    renderStatus(payload.status);
    log(`Saved recording as ${payload.saved.name}.`);
  } catch (error) {
    log(`Save recording failed: ${error.message}`);
  }
}

async function saveCurrentPosition(name = recordingName(), throwOnError = false) {
  try {
    const payload = await fetchJson("/api/motion/current/save", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
    applyPositions(payload.saved?.frames?.[0]?.positions || {});
    renderRecording(payload.recording);
    renderSavedRecordings(payload.saved_recordings || []);
    renderStatus(payload.status);
    log(`Saved current position as ${payload.saved.name}.`);
  } catch (error) {
    log(`Save current position failed: ${error.message}`);
    if (throwOnError) {
      throw error;
    }
  }
}

async function playbackMotion(name = null, throwOnError = false) {
  const speed = playbackSpeed();
  motionBusy = true;
  refreshBusyState();
  motionPlaybackButton.textContent = name ? "Play Last" : "Playing...";
  motionPlaybackSavedButton.textContent = name ? "Playing..." : "Play Saved";
  recordingSummary.textContent = name
    ? `Playing saved clip ${name} now.`
    : "Playing the last motion clip now.";
  log(name ? `Saved motion playback started: ${name}.` : "Motion playback started.");
  try {
    const payload = await fetchJson("/api/motion/playback", {
      method: "POST",
      body: JSON.stringify({ name, speed }),
    });
    renderRecording(payload.recording);
    renderSavedRecordings(payload.saved_recordings || []);
    renderStatus(payload.status);
    log(`Motion playback ok: ${payload.clip.name} replayed${speed ? ` at speed ${speed}` : ""}.`);
  } catch (error) {
    recordingSummary.textContent = "Motion playback failed. Check the event log and try again.";
    log(`Motion playback failed: ${error.message}`);
    if (throwOnError) {
      throw error;
    }
  } finally {
    motionBusy = false;
    refreshBusyState();
    motionPlaybackButton.textContent = "Play Last";
    motionPlaybackSavedButton.textContent = "Play Saved";
  }
}

async function executeSequenceStep(step) {
  if (step.type === "wait") {
    await waitForSequence(step.seconds);
    return;
  }

  if (step.type === "action") {
    if (step.command === "stand") {
      await sendAction("/api/pose/stand", "Stand", true);
      return;
    }
    if (step.command === "sit") {
      await sendAction("/api/pose/sit", "Sit", true);
      return;
    }
    if (step.command === "storage") {
      await sendAction("/api/pose/storage", "Storage", true);
      return;
    }
    if (step.command === "relax") {
      await sendAction("/api/pose/relax", "Relax", true);
      return;
    }
    if (step.command === "scan") {
      await scanServos(true);
      return;
    }
    if (step.command === "read-positions") {
      await readPositions(true);
      return;
    }
  }

  if (step.type === "playback") {
    await playbackMotion(step.name, true);
    return;
  }

  if (step.type === "save-current") {
    await saveCurrentPosition(step.name, true);
    return;
  }

  throw new Error(`Unsupported sequence step type: ${step.type}`);
}

async function runSequence() {
  let steps;
  try {
    steps = parseSequenceScript(sequenceScriptInput.value);
  } catch (error) {
    updateSequenceStatus(error.message);
    log(`Sequence parse failed: ${error.message}`);
    return;
  }

  if (!steps.length) {
    updateSequenceStatus("Enter at least one sequence line to run.");
    return;
  }

  sequenceBusy = true;
  sequenceStopRequested = false;
  refreshBusyState();
  log(`Sequence started with ${steps.length} step(s).`);

  try {
    for (let index = 0; index < steps.length; index += 1) {
      if (sequenceStopRequested) {
        break;
      }

      const step = steps[index];
      updateSequenceStatus(`Running step ${index + 1}/${steps.length}: ${step.label}`);
      log(`Sequence ${index + 1}/${steps.length}: ${step.label}`);
      await executeSequenceStep(step);
    }

    if (sequenceStopRequested) {
      updateSequenceStatus("Sequence stop requested. No new steps will start.");
      log("Sequence stop requested.");
    } else {
      updateSequenceStatus(`Sequence complete. Ran ${steps.length} step(s).`);
      log("Sequence complete.");
    }
  } catch (error) {
    updateSequenceStatus(`Sequence failed: ${error.message}`);
    log(`Sequence failed: ${error.message}`);
  } finally {
    sequenceBusy = false;
    sequenceStopRequested = false;
    refreshBusyState();
  }
}

function requestSequenceStop() {
  if (!sequenceBusy) {
    updateSequenceStatus("No sequence is running.");
    return;
  }
  sequenceStopRequested = true;
  updateSequenceStatus("Sequence stop requested. Waiting for the current step to finish.");
}

async function sendTeleop(command) {
  updateAxisReadout(command.axes);

  if (motionBusy || sequenceBusy) {
    return;
  }

  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify(command));
    return;
  }

  try {
    const status = await fetchJson("/api/control/teleop", {
      method: "POST",
      body: JSON.stringify(command),
    });
    renderStatus(status);
  } catch (error) {
    const now = Date.now();
    if (now - lastTeleopErrorAt > 2500) {
      log(`Teleop failed: ${error.message}`);
      lastTeleopErrorAt = now;
    }
  }
}

function connectSocket() {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) {
    return;
  }

  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${window.location.host}/ws/control`);

  socket.addEventListener("open", () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    setConnection(true);
    log("WebSocket connected.");
  });

  socket.addEventListener("message", (event) => {
    try {
      renderStatus(JSON.parse(event.data));
    } catch {
      log("Received non-JSON WebSocket message.");
    }
  });

  socket.addEventListener("close", () => {
    setConnection(false);
    log("WebSocket disconnected.");
    reconnectTimer = window.setTimeout(connectSocket, 1800);
  });

  socket.addEventListener("error", () => {
    setConnection(false);
    log("WebSocket error.");
  });
}

function startTeleopLoop() {
  window.setInterval(() => {
    const axes = mergedAxes();
    sendTeleop({
      source: "web",
      mode: "teleop",
      axes,
      buttons: { stand: false, relax: false, stop: false },
      timestamp_ms: Date.now(),
    });
  }, 75);
}

function bindButtons() {
  document.getElementById("connect-ws").addEventListener("click", connectSocket);
  document.getElementById("refresh-health").addEventListener("click", refreshHealth);
  document.getElementById("scan-servos").addEventListener("click", scanServos);
  document.getElementById("read-positions").addEventListener("click", readPositions);
  document.getElementById("pose-stand").addEventListener("click", () => sendAction("/api/pose/stand", "Stand"));
  document.getElementById("pose-sit").addEventListener("click", () => sendAction("/api/pose/sit", "Sit"));
  document.getElementById("pose-storage").addEventListener("click", () => sendAction("/api/pose/storage", "Storage"));
  document.getElementById("pose-relax").addEventListener("click", () => sendAction("/api/pose/relax", "Relax"));
  motionRecordButton.addEventListener("click", recordMotion);
  motionStopButton.addEventListener("click", stopMotionRecording);
  motionPlaybackButton.addEventListener("click", () => playbackMotion());
  motionSaveButton.addEventListener("click", saveMotionRecording);
  motionSaveCurrentButton.addEventListener("click", saveCurrentPosition);
  motionPlaybackSavedButton.addEventListener("click", () => {
    if (!savedRecordingsSelect.value) {
      return;
    }
    playbackMotion(savedRecordingsSelect.value);
  });
  sequenceRunButton.addEventListener("click", runSequence);
  sequenceStopButton.addEventListener("click", requestSequenceStop);
  sequenceExampleButton.addEventListener("click", loadSequenceExample);
  sequenceInsertSavedButton.addEventListener("click", insertSavedClipStep);
  document.getElementById("center-axes").addEventListener("click", resetTouchAxes);

  document.querySelectorAll(".preset").forEach((button) => {
    button.addEventListener("click", () => {
      setTouchAxes({
        forward: Number(button.dataset.forward || 0),
        strafe: Number(button.dataset.strafe || 0),
        turn: Number(button.dataset.turn || 0),
      });
    });
  });
}

function bindInputs() {
  [touchForward, touchStrafe, touchTurn].forEach((input) => {
    input.addEventListener("input", () => {
      setTouchAxes({
        forward: Number(touchForward.value),
        strafe: Number(touchStrafe.value),
        turn: Number(touchTurn.value),
      });
    });
  });

  window.addEventListener("keydown", (event) => {
    pressed.add(event.code);
    updateKeyboardAxes();
  });

  window.addEventListener("keyup", (event) => {
    pressed.delete(event.code);
    updateKeyboardAxes();
  });

  sequenceScriptInput.addEventListener("input", saveSequenceDraft);
}

async function bootstrap() {
  setConnection(false);
  syncTouchInputs();
  updateAxisReadout({ forward: 0, strafe: 0, turn: 0 });
  loadSequenceDraft();
  bindButtons();
  bindInputs();
  await loadConfig();
  await loadRecording();
  refreshBusyState();
  updateSequenceStatus("No sequence running.");
  await refreshHealth();
  await readPositions();
  connectSocket();
  startTeleopLoop();
  window.setInterval(refreshHealth, 1500);
}

bootstrap();
