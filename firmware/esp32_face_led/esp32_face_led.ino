/*
 * esp32_face_led — Robot BODY controller (Phase 4)
 * Board: ESP32-S3-DevKitC-1 (N16R8). Onboard WS2812 RGB on GPIO48.
 *
 * ROLE: this board is NOT the front-end. The phone (mic/speaker/camera/face) is.
 * This board is a *subscriber* to the same control-server WebSocket the phone
 * uses: it reads `phase` events and reflects the robot's state on the RGB LED
 * (and later drives motors via the `tool` events). It never calls the pipeline.
 *
 *   phone  ──prompt/audio──►  control server (orchestrator)  ──events──►  phone + THIS board
 *
 * Libraries (Arduino IDE → Library Manager):
 *   - "WebSockets" by Markus Sattler (links2004/arduinoWebSockets)
 *   - "ArduinoJson" by Benoit Blanchon
 *   - "Adafruit NeoPixel"
 *
 * Board: select "ESP32S3 Dev Module". Set PSRAM enabled if needed.
 */

#include <WiFi.h>
#include <WebSocketsClient.h>
#include <ArduinoJson.h>
#include <Adafruit_NeoPixel.h>

// ---- CONFIG: set these for your network + fleet host ----
const char* WIFI_SSID = "YOUR_WIFI";
const char* WIFI_PASS = "YOUR_PASS";
const char* HOST      = "192.168.1.50";   // IP of the machine running src.server.app
const uint16_t PORT   = 8010;
const char* WS_PATH   = "/ws";

#define LED_PIN   48      // onboard WS2812 on the DevKitC-1
#define LED_COUNT 1

Adafruit_NeoPixel pixel(LED_COUNT, LED_PIN, NEO_GRB + NEO_KHZ800);
WebSocketsClient ws;

// target color we ease toward (so phase changes fade, not snap)
uint8_t tr = 0, tg = 0, tb = 0;     // target
float   cr = 0, cg = 0, cb = 0;     // current
uint8_t pulse = 0;                  // 0..255 brightness pulse for "thinking"
bool    pulsing = false;

void setPhaseColor(const char* phase) {
  pulsing = false;
  if      (!strcmp(phase, "listening")) { tr = 0;   tg = 90;  tb = 255; }  // blue
  else if (!strcmp(phase, "thinking"))  { tr = 150; tg = 60;  tb = 255; pulsing = true; } // purple pulse
  else if (!strcmp(phase, "speaking"))  { tr = 0;   tg = 220; tb = 70;  }  // green
  else if (!strcmp(phase, "error"))     { tr = 255; tg = 30;  tb = 20;  }  // red
  else                                   { tr = 6;   tg = 8;   tb = 16;  }  // inactive: dim
}

void onWsEvent(WStype_t type, uint8_t* payload, size_t len) {
  switch (type) {
    case WStype_CONNECTED:
      Serial.println("[ws] connected to control server");
      setPhaseColor("inactive");
      break;
    case WStype_DISCONNECTED:
      Serial.println("[ws] disconnected");
      tr = 40; tg = 20; tb = 0;   // amber = link down
      break;
    case WStype_TEXT: {
      // events look like {"type":"phase","phase":"speaking", ...}
      StaticJsonDocument<512> doc;
      DeserializationError err = deserializeJson(doc, payload, len);
      if (err) return;
      const char* t = doc["type"] | "";
      if (!strcmp(t, "phase")) {
        const char* p = doc["phase"] | "inactive";
        Serial.printf("[phase] %s\n", p);
        setPhaseColor(p);
      }
      // TODO Phase 4: else if (!strcmp(t, "tool")) drive motors from tool calls.
      break;
    }
    default: break;
  }
}

void setup() {
  Serial.begin(115200);
  pixel.begin();
  pixel.setBrightness(120);
  pixel.show();

  WiFi.begin(WIFI_SSID, WIFI_PASS);
  Serial.print("[wifi] connecting");
  while (WiFi.status() != WL_CONNECTED) { delay(300); Serial.print("."); }
  Serial.printf("\n[wifi] %s\n", WiFi.localIP().toString().c_str());

  ws.begin(HOST, PORT, WS_PATH);
  ws.onEvent(onWsEvent);
  ws.setReconnectInterval(2000);   // auto-reconnect to the hub
}

void loop() {
  ws.loop();

  // ease current color toward target; add a soft pulse while "thinking"
  cr += (tr - cr) * 0.12f;
  cg += (tg - cg) * 0.12f;
  cb += (tb - cb) * 0.12f;
  float b = 1.0f;
  if (pulsing) { pulse += 4; b = 0.45f + 0.55f * (0.5f + 0.5f * sinf(pulse * 0.05f)); }
  pixel.setPixelColor(0, pixel.Color((uint8_t)(cr * b), (uint8_t)(cg * b), (uint8_t)(cb * b)));
  pixel.show();
  delay(16);  // ~60 fps
}
