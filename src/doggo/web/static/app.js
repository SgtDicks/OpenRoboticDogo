const statusView = document.getElementById("status-view");
const logView = document.getElementById("event-log");
const connectionState = document.getElementById("connection-state");
const axisForward = document.getElementById("axis-forward");
const axisStrafe = document.getElementById("axis-strafe");
const axisTurn = document.getElementById("axis-turn");

let socket = null;
const keyboardAxes = { forward: 0, strafe: 0, turn: 0 };
const pressed = new Set();

function log(message) {
  const lines = logView.textContent.split("\n").slice(-16);
  lines.push(`${new Date().toLocaleTimeString()} ${message}`);
  logView.textContent = lines.join("\n");
}

function setConnection(connected) {
  connectionState.textContent = connected ? "Connected" : "Disconnected";
  connectionState.className = connected ? "status online" : "status offline";
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

function renderStatus(status) {
  statusView.textContent = JSON.stringify(status, null, 2);
}

function connectSocket() {
  if (socket && socket.readyState === WebSocket.OPEN) {
    return;
  }
  const protocol = window.location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${window.location.host}/ws/control`);
  socket.addEventListener("open", () => {
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
  });
  socket.addEventListener("error", () => {
    setConnection(false);
    log("WebSocket error.");
  });
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
  const gamepad = currentGamepadAxes();
  return {
    forward: Math.max(-1, Math.min(1, keyboardAxes.forward || gamepad.forward)),
    strafe: Math.max(-1, Math.min(1, keyboardAxes.strafe || gamepad.strafe)),
    turn: Math.max(-1, Math.min(1, keyboardAxes.turn || gamepad.turn)),
  };
}

async function sendAction(url) {
  try {
    const status = await fetchJson(url, { method: "POST", body: "{}" });
    renderStatus(status);
    log(`POST ${url} ok`);
  } catch (error) {
    log(`POST ${url} failed: ${error.message}`);
  }
}

async function sendScan() {
  try {
    const status = await fetchJson("/api/servos/scan");
    renderStatus(status);
    log("Servo scan finished.");
  } catch (error) {
    log(`Servo scan failed: ${error.message}`);
  }
}

async function sendTeleop(command) {
  axisForward.textContent = command.axes.forward.toFixed(2);
  axisStrafe.textContent = command.axes.strafe.toFixed(2);
  axisTurn.textContent = command.axes.turn.toFixed(2);

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
    log(`Teleop POST failed: ${error.message}`);
  }
}

function startTeleopLoop() {
  setInterval(() => {
    const axes = mergedAxes();
    const command = {
      source: "web",
      mode: "teleop",
      axes,
      buttons: { stand: false, relax: false, stop: false },
      timestamp_ms: Date.now(),
    };
    sendTeleop(command);
  }, 75);
}

window.addEventListener("keydown", (event) => {
  pressed.add(event.code);
  updateKeyboardAxes();
});

window.addEventListener("keyup", (event) => {
  pressed.delete(event.code);
  updateKeyboardAxes();
});

document.getElementById("connect-ws").addEventListener("click", connectSocket);
document.getElementById("scan-servos").addEventListener("click", sendScan);
document.getElementById("stand-pose").addEventListener("click", () => sendAction("/api/pose/stand"));
document.getElementById("relax-pose").addEventListener("click", () => sendAction("/api/pose/relax"));

setConnection(false);
connectSocket();
startTeleopLoop();

setInterval(async () => {
  try {
    const status = await fetchJson("/api/health");
    renderStatus(status);
  } catch (error) {
    log(`Health check failed: ${error.message}`);
  }
}, 1500);
