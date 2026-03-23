#include <WiFi.h>
#include <WiFiUdp.h>

// Replace these before uploading.
const char* WIFI_SSID = "REPLACE_WIFI_SSID";
const char* WIFI_PASSWORD = "REPLACE_WIFI_PASSWORD";

// Replace with your Raspberry Pi IP address.
IPAddress PI_IP(192, 168, 1, 50);
const uint16_t PI_UDP_PORT = 8765;

// Change these pins to match your joystick/controller wiring.
const int JOY_FORWARD_PIN = 34;
const int JOY_STRAFE_PIN = 35;
const int STAND_BUTTON_PIN = 25;
const int RELAX_BUTTON_PIN = 26;
const int DEADMAN_PIN = 27;

const uint32_t SEND_INTERVAL_MS = 25;
const int ADC_CENTER = 2048;
const int ADC_RANGE = 2048;
const int DEADZONE = 140;

WiFiUDP udp;
uint32_t lastSendMs = 0;

float normalizeAxis(int rawValue) {
  int delta = rawValue - ADC_CENTER;
  if (abs(delta) < DEADZONE) {
    return 0.0f;
  }

  float normalized = (float)delta / (float)ADC_RANGE;
  if (normalized > 1.0f) {
    normalized = 1.0f;
  }
  if (normalized < -1.0f) {
    normalized = -1.0f;
  }
  return normalized;
}

String boolJson(bool value) {
  return value ? "true" : "false";
}

void sendTeleop() {
  bool deadmanHeld = digitalRead(DEADMAN_PIN) == LOW;
  bool standPressed = digitalRead(STAND_BUTTON_PIN) == LOW;
  bool relaxPressed = digitalRead(RELAX_BUTTON_PIN) == LOW;

  float forward = deadmanHeld ? -normalizeAxis(analogRead(JOY_FORWARD_PIN)) : 0.0f;
  float strafe = deadmanHeld ? normalizeAxis(analogRead(JOY_STRAFE_PIN)) : 0.0f;
  float turn = 0.0f;

  String payload =
    String("{\"source\":\"esp32\",\"mode\":\"teleop\",\"axes\":{\"forward\":") +
    String(forward, 3) +
    String(",\"strafe\":") +
    String(strafe, 3) +
    String(",\"turn\":") +
    String(turn, 3) +
    String("},\"buttons\":{\"stand\":") +
    boolJson(standPressed) +
    String(",\"relax\":") +
    boolJson(relaxPressed) +
    String(",\"stop\":") +
    boolJson(!deadmanHeld) +
    String("},\"timestamp_ms\":") +
    String((uint32_t)millis()) +
    String("}");

  udp.beginPacket(PI_IP, PI_UDP_PORT);
  udp.print(payload);
  udp.endPacket();
}

void connectWifi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(400);
  }
}

void setup() {
  Serial.begin(115200);

  pinMode(STAND_BUTTON_PIN, INPUT_PULLUP);
  pinMode(RELAX_BUTTON_PIN, INPUT_PULLUP);
  pinMode(DEADMAN_PIN, INPUT_PULLUP);

  connectWifi();
  udp.begin(PI_UDP_PORT);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi();
  }

  uint32_t now = millis();
  if (now - lastSendMs >= SEND_INTERVAL_MS) {
    lastSendMs = now;
    sendTeleop();
  }
}
