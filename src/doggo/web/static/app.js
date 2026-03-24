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

  if (Array.isArray(status.last_scan)) {
    latestScan = new Map(status.last_scan.map((entry) => [entry.servo_id, entry]));
  }

  renderServoTable();
}

async function loadConfig() {
  try {
    latestConfig = await fetchJson("/api/config");
    busDevice.textContent = `${latestConfig.servo_bus.device} @ ${latestConfig.servo_bus.baud_rate}`;
    renderWalkingProfile(latestConfig);
    renderServoTable();
  } catch (error) {
    log(`Config load failed: ${error.message}`);
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

async function scanServos() {
  try {
    const payload = await fetchJson("/api/servos/scan");
    latestScan = new Map((payload.found || []).map((entry) => [entry.servo_id, entry]));
    renderServoTable();
    log(`Servo scan finished. Found ${(payload.found || []).length} servo(s).`);
    await refreshHealth();
  } catch (error) {
    log(`Servo scan failed: ${error.message}`);
  }
}

async function readPositions() {
  try {
    const payload = await fetchJson("/api/servos/positions");
    latestPositions = Object.fromEntries(
      Object.entries(payload.positions || {}).map(([servoId, position]) => [Number(servoId), position])
    );
    renderServoTable();
    log(`Read ${Object.keys(latestPositions).length} servo position(s).`);
  } catch (error) {
    log(`Position read failed: ${error.message}`);
  }
}

async function sendAction(url, label) {
  try {
    const status = await fetchJson(url, { method: "POST", body: "{}" });
    renderStatus(status);
    log(`${label} ok.`);
  } catch (error) {
    log(`${label} failed: ${error.message}`);
  }
}

async function sendTeleop(command) {
  updateAxisReadout(command.axes);

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
}

async function bootstrap() {
  setConnection(false);
  syncTouchInputs();
  updateAxisReadout({ forward: 0, strafe: 0, turn: 0 });
  bindButtons();
  bindInputs();
  await loadConfig();
  await refreshHealth();
  await readPositions();
  connectSocket();
  startTeleopLoop();
  window.setInterval(refreshHealth, 1500);
}

bootstrap();
