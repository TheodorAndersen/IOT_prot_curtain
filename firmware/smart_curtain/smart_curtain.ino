// Smart Curtains firmware · protocol v3 (see 05_final_project/lorawan/PROTOCOL.md)
// Board: Arduino Pro Mini 3.3 V / 8 MHz. Wiring as in the 08_Curtain_Controller reference:
//   A0 outdoor LDR · A1 indoor LDR · buttons UP D3, DOWN D4, STOP D5 (HIGH = pressed, external pull-downs)
//   ULN2003 IN1..IN4 on D6..D9 · RN2483 TX → D10, RX ← D11, RST D12 · no home switch: calibrate by hand
//
// The protocol bytes (FPort 1) travel over one of two links, chosen with "LINK U|L" over USB and saved in EEPROM:
//   USB  (test): PC → Arduino "DL <hex>", Arduino → PC "UL <hex>", 9600 baud. The radio is held in reset.
//   LoRa (live): the RN2483 joins TTN (OTAA, Class C when its firmware supports it) and carries the same bytes.
// Always on USB, whichever link is active:
//   "#..." debug lines (never sent over LoRa). "#R ..." = radio status, "#R> ..." = raw RN2483 reply.
//   Test-only input: "BTN U|D|S" (virtual button), "TICK <seconds>" (decision interval), "CAL" (forget calibration),
//   "STATE", "LINK U|L", "RADIO <command>" (raw RN2483 command while joined, e.g. "RADIO mac get dr")
#include <EEPROM.h>
#include <SoftwareSerial.h>
#if __has_include("Credentials.h")
#include "Credentials.h"                       // appEui, appKey for the OTAA join (private: not in the hand-in zip)
const bool HAVE_KEYS = true;
#else
#include "Credentials.example.h"               // dummy keys: USB works, the LoRaWAN join will be refused
const bool HAVE_KEYS = false;
#endif

// ---------- hardware ----------
const byte PIN_OUT = A0, PIN_IN = A1;
const byte BTN[3] = {3, 4, 5};                 // UP, DOWN, STOP
const byte COIL[4] = {6, 7, 8, 9};
const byte PHASES[8] = {1, 3, 2, 6, 4, 12, 8, 9};
const int UP_SIGN = 1;                         // set to -1 if UP turns the motor the wrong way
const unsigned long STEP_US = 3000;            // 28BYJ-48 half-step period
const byte RADIO_RX = 10, RADIO_TX = 11, RADIO_RST = 12;

// ---------- protocol ----------
enum { R_HEARTBEAT, R_STARTUP, R_BUTTON, R_APP, R_AUTO, R_SCHEDULE, R_CALIBRATED, R_REJECTED };
enum { C_POSITION = 1, C_MODE = 2, C_AUTO = 3, C_SCHEDULE = 4 };
enum { F_AUTO = 1, F_MOVING = 2, F_CAL = 4, F_DAY = 8 };       // uplink flags; bits 4-6 = auto config
enum { A_SUN_OPEN = 1, A_SUN_CLOSE = 2, A_MAINTAIN = 4 };      // auto config bits (downlink AUTO byte 2)
enum { LINK_USB = 0, LINK_LORA = 1 };
const unsigned long HEARTBEAT_MS = 3600000UL;
const unsigned long POLL_MS = 60000UL;         // Class A only: a downlink can only arrive right after an uplink
const int DEADBAND = 5;                        // % indoor light error ignored by "maintain light"
const int MAX_STEP = 25;                       // max % of travel per decision

// ---------- saved state (EEPROM, written only when bytes change) ----------
struct Saved {
  byte magic, autoMode, config, target, thrOpen, thrClose, lastId, dayOpen;
  long travel, pos;                            // steps; pos 0 = fully down (closed), travel = fully up (open)
  byte link;                                   // LINK_USB or LINK_LORA
} st;
const byte MAGIC = 0xC3;
void save() { EEPROM.put(0, st); }

// ---------- motor ----------
int dir = 0;
byte phase = 0, moveReason = R_BUTTON;
long goal = 0;
unsigned long lastStep = 0;
bool calibrating = false;

// ---------- light ----------
unsigned long tickMs = 300000UL, tickStart = 0, lastSample = 0, sumOut = 0, sumIn = 0;
unsigned int samples = 0, liveOut = 0, liveIn = 0;
byte outPct = 0, inPct = 0;                    // last 5-min averages, 0-100 %
bool openArmed = true;                         // sun-open waits for darkness after any close

// ---------- uplink + budget guard ----------
long allowance = 3000;                         // ms of airtime; refills at 30 s/day
int costMs = 51;                               // 4-byte uplink: 51 ms at SF7, 165 ms at SF9
unsigned long lastRefill = 0, lastSent = 0;
bool havePending = false;
byte pendingReason = 0, lastReplyReason = R_APP;
byte up[4], upSent[4], upTries = 0;            // LoRa: the latest state waiting for the radio (newer replaces older)
bool upReady = false;

byte pct(unsigned int adc) { return (adc * 100UL + 511) / 1023; }
bool calibrated() { return st.travel > 0 && !calibrating; }
byte positionClosed() {                        // UI convention: 0 = fully open, 100 = fully closed, 255 = unknown
  if (!calibrated()) return 255;
  return 100 - (st.pos * 100 + st.travel / 2) / st.travel;
}
long stepsFor(byte closedPct) { return (st.travel * (100 - closedPct) + 50) / 100; }

void toHex(const byte *b, byte n, char *out) {
  const char hex[] = "0123456789ABCDEF";
  for (byte i = 0; i < n; i++) { out[2 * i] = hex[b[i] >> 4]; out[2 * i + 1] = hex[b[i] & 15]; }
  out[2 * n] = 0;
}
byte hexVal(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return 255;
}
byte parseHex(const char *p, byte *b, byte max) {          // returns the byte count, 0 on bad hex
  byte n = 0;
  for (; p[0] && p[1] && n < max; p += 2) {
    byte h = hexVal(p[0]), l = hexVal(p[1]);
    if (h > 15 || l > 15) return 0;
    b[n++] = h * 16 + l;
  }
  return n;
}

void sendState(byte reason, bool force) {
  if (!force && allowance < costMs) { havePending = true; pendingReason = reason; return; }
  allowance -= costMs;
  havePending = false;
  lastSent = millis();
  byte f = (st.autoMode ? F_AUTO : 0) | (dir ? F_MOVING : 0) | (calibrated() ? F_CAL : 0) |
           (st.dayOpen ? F_DAY : 0) | (st.config << 4);
  byte b[4] = {st.lastId, reason, f, positionClosed()};
  if (st.link == LINK_LORA) { memcpy(up, b, 4); upReady = true; upTries = 0; return; }   // the radio sends it
  char h[9];
  toHex(b, 4, h);
  Serial.print(F("UL ")); Serial.println(h);
}
void sendState(byte reason) { sendState(reason, false); }

void coilsOff() { for (byte i = 0; i < 4; i++) digitalWrite(COIL[i], LOW); }
void halt() { dir = 0; goal = st.pos; coilsOff(); }
void go(long dest, byte reason) {
  if (!calibrating) dest = constrain(dest, 0L, st.travel);
  goal = dest;
  moveReason = reason;
  dir = (goal > st.pos) - (goal < st.pos);
  lastStep = micros();
}

void serviceMotor() {
  if (!dir || micros() - lastStep < STEP_US) return;
  lastStep = micros();
  phase = (phase + 8 + dir * UP_SIGN) % 8;
  for (byte i = 0; i < 4; i++) digitalWrite(COIL[i], (PHASES[phase] >> i) & 1);
  st.pos += dir;
  if (st.pos == goal) {
    halt();
    if (!calibrating) { save(); sendState(moveReason, moveReason == R_APP || moveReason == R_SCHEDULE); }
  }
}

// ---------- automatic mode: runs on every tick and right after a settings change ----------
void decide(byte reason) {
  if (!st.autoMode || !calibrated()) return;
  if (outPct < st.thrClose) openArmed = true;
  if (!st.dayOpen && (st.config & A_SUN_OPEN) && openArmed && outPct > st.thrOpen) { st.dayOpen = 1; reason = R_AUTO; }
  else if (st.dayOpen && (st.config & A_SUN_CLOSE) && outPct < st.thrClose) { st.dayOpen = 0; openArmed = false; reason = R_AUTO; }
  long dest;
  if (!st.dayOpen) dest = 0;                                   // night: closed
  else if (st.config & A_MAINTAIN) {                           // day + maintain: nudge toward target
    int err = (int)st.target - (int)inPct;                     // > 0: too dark → open more
    dest = abs(err) <= DEADBAND ? st.pos : st.pos + st.travel * constrain(err, -MAX_STEP, MAX_STEP) / 100;
  } else dest = st.travel;                                     // day: fully open
  dest = constrain(dest, 0L, st.travel);
  save();
  if (dest != st.pos) go(dest, reason);
}

// ---------- buttons ----------
bool raw[3], stable[3];
unsigned long changedAt[3];

void calibrationPress(byte b) {
  if (b == 0) {                                                // UP: start from the bottom
    if (!calibrating) { calibrating = true; st.pos = 0; st.travel = 0; Serial.println(F("# Calibrating: press STOP at the top")); }
    go(2000000L, R_CALIBRATED);
  } else if (b == 1) {                                         // DOWN: back toward the start
    if (calibrating && st.pos > 0) go(0, R_CALIBRATED);
  } else {                                                     // STOP: this is the top
    halt();
    if (calibrating && st.pos > 0) {
      st.travel = st.pos;
      calibrating = false;
      save();
      Serial.print(F("# Calibrated: travel steps ")); Serial.println(st.travel);
      sendState(R_CALIBRATED, true);
    }
  }
}

void press(byte b) {                                           // 0 UP, 1 DOWN, 2 STOP
  Serial.print(F("#B ")); Serial.println("UDS"[b]);
  if (!calibrated()) { calibrationPress(b); return; }
  bool modeChanged = st.autoMode;
  st.autoMode = 0;                                             // the person at the window wins
  if (b == 2) { halt(); save(); sendState(R_BUTTON); return; }
  long dest = b == 0 ? st.travel : 0;
  if (dest == st.pos) { save(); if (modeChanged) sendState(R_BUTTON); return; }
  go(dest, R_BUTTON);
}

void serviceButtons() {
  for (byte i = 0; i < 3; i++) {
    bool v = digitalRead(BTN[i]);
    if (v != raw[i]) { raw[i] = v; changedAt[i] = millis(); }
    if (v != stable[i] && millis() - changedAt[i] >= 30) { stable[i] = v; if (v) press(i); }
  }
}

// ---------- downlinks (from either link) ----------
void downlink(const byte *b, byte n) {
  if (n < 3 || b[0] == 0) return;
  byte id = b[0], type = b[1];
  if (id == st.lastId) { sendState(lastReplyReason, true); return; }   // duplicate: repeat the reply only
  bool ok;
  switch (type) {
    case C_POSITION: ok = n == 3 && b[2] <= 100 && calibrated(); break;
    case C_MODE:     ok = n == 3 && b[2] <= 1; break;
    case C_AUTO:     ok = n == 6 && b[2] <= 7 && b[3] <= 100 && b[4] <= 100 && b[5] < b[4]; break;
    case C_SCHEDULE: ok = n == 3 && b[2] <= 1; break;
    default:         ok = false;
  }
  st.lastId = id;
  if (!ok) { lastReplyReason = R_REJECTED; save(); sendState(R_REJECTED, true); return; }
  lastReplyReason = type == C_SCHEDULE ? R_SCHEDULE : R_APP;
  switch (type) {
    case C_POSITION:
      st.autoMode = 0;
      go(stepsFor(b[2]), R_APP);                 // reply below says "moving" if the motor will run
      break;
    case C_MODE:
      st.autoMode = b[2];
      break;
    case C_AUTO:
      st.config = b[2]; st.target = b[3]; st.thrOpen = b[4]; st.thrClose = b[5];
      openArmed = true;
      break;
    case C_SCHEDULE:
      st.dayOpen = b[2] == 0;
      if (!st.dayOpen) openArmed = false;
      break;
  }
  save();
  sendState(lastReplyReason, true);              // "got it" before anything else happens
  if (type != C_POSITION && !dir) decide(lastReplyReason);
}

// ---------- RN2483 LoRaWAN link (live) ----------
// One command at a time, written one character per loop pass, so the motor and buttons never wait for the radio.
SoftwareSerial radio(RADIO_RX, RADIO_TX);
enum { RS_OFF, RS_RESET, RS_BOOT, RS_BAUD, RS_SETUP, RS_JOINING, RS_RETRY, RS_IDLE, RS_TX, RS_TX_WAIT, RS_RAW };
byte rs = RS_OFF, setupStep = 0, dr = 5, joinFails = 0;
bool classC = false;
unsigned long rsSince = 0, txAfter = 0;
char rIn[64], rOut[64], hweui[17];
byte rInLen = 0, rOutLen = 0, rOutPos = 0;

void enter(byte s) { rs = s; rsSince = millis(); }
void radioCmd(const __FlashStringHelper *cmd, const char *arg, byte next) {
  strcpy_P(rOut, (PGM_P)cmd);
  strncat(rOut, arg, sizeof(rOut) - 3 - strlen(rOut));
  strcat(rOut, "\r\n");
  rOutLen = strlen(rOut); rOutPos = 0;
  enter(next);
}

void radioStart() {                                          // hardware reset, then auto-baud, then the join script
  pinMode(RADIO_RST, OUTPUT); digitalWrite(RADIO_RST, LOW);
  rOutLen = rOutPos = rInLen = 0; classC = false;
  enter(RS_RESET);
  Serial.println(F("#R starting"));
}
void radioStop() {                                           // USB link: keep the radio silent in reset
  radio.end();
  pinMode(RADIO_RST, OUTPUT); digitalWrite(RADIO_RST, LOW);
  rs = RS_OFF; upReady = false;
}
void printLink() { Serial.println(st.link == LINK_LORA ? F("#R link lora") : F("#R link usb")); }

void rejoinLater(const char *why) {
  Serial.print(F("#R failed ")); Serial.print(why); Serial.println(F(", retry in 30 s"));
  joinFails++;
  dr = joinFails & 1 ? 3 : 5;                                // alternate SF7 / SF9 in case the gateway is far
  enter(RS_RETRY);
}

void setupSend() {
  char n[4];
  switch (setupStep) {
    case 0: radioCmd(F("sys get ver"), "", RS_SETUP); break;
    case 1: radioCmd(F("sys get hweui"), "", RS_SETUP); break;
    case 2: radioCmd(F("mac reset 868"), "", RS_SETUP); break;
    case 3: radioCmd(F("mac set deveui "), hweui, RS_SETUP); break;
    case 4: radioCmd(F("mac set appeui "), appEui, RS_SETUP); break;
    case 5: radioCmd(F("mac set appkey "), appKey, RS_SETUP); break;
    case 6: itoa(dr, n, 10); radioCmd(F("mac set dr "), n, RS_SETUP); break;
    case 7: radioCmd(F("mac set rx2 3 869525000"), "", RS_SETUP); break;   // TTN EU868 RX2: SF9
    case 8: radioCmd(F("mac set class c"), "", RS_SETUP); break;
    case 9: radioCmd(F("mac join otaa"), "", RS_SETUP); break;
  }
}

void setupReply(char *s) {
  bool ok = !strcmp(s, "ok");
  switch (setupStep) {
    case 0:
      if (strncmp(s, "RN2483", 6)) return;                   // ignore boot noise until the version line
      Serial.print(F("#R ver ")); Serial.println(s);
      break;
    case 1: {
      byte b[8];
      if (strlen(s) != 16 || parseHex(s, b, 8) != 8) return;
      strcpy(hweui, s);
      Serial.print(F("#R eui ")); Serial.println(s);
      if (!HAVE_KEYS) { rejoinLater("no Credentials.h"); return; }
      break;
    }
    case 8: classC = ok; break;                              // older RN2483 firmware: "invalid_param" → Class A
    case 9:
      if (!ok) { rejoinLater(s); return; }
      enter(RS_JOINING);
      Serial.print(F("#R joining dr")); Serial.println(dr);
      return;
    default:
      if (!ok) { rejoinLater(s); return; }
  }
  setupStep++;
  setupSend();
}

void retryUplink(const char *why) {
  Serial.print(F("#R tx error ")); Serial.println(why);
  if (!upReady && ++upTries < 6) { memcpy(up, upSent, 4); upReady = true; }   // unless a newer state replaced it
  enter(RS_IDLE);
  txAfter = millis() + 3000UL * upTries;                     // no_free_ch (duty cycle) clears within seconds
}
void txDone() { Serial.println(F("#R tx ok")); enter(RS_IDLE); txAfter = millis() + 1000; }

void radioLine(char *s) {
  if (!strncmp(s, "mac_rx ", 7)) {                           // a downlink: Class A after our uplink, Class C any time
    Serial.print(F("#R rx ")); Serial.println(s + 7);
    if (rs == RS_TX_WAIT) txDone();
    char *p = strchr(s + 7, ' ');
    byte b[16], n = p ? parseHex(p + 1, b, sizeof(b)) : 0;
    if (atoi(s + 7) == 1 && n) downlink(b, n);
    return;
  }
  switch (rs) {
    case RS_SETUP: setupReply(s); break;
    case RS_JOINING:
      if (strcmp(s, "accepted")) { rejoinLater(s); break; }   // "denied": no join accept heard
      enter(RS_IDLE);
      joinFails = 0;
      costMs = dr == 5 ? 51 : 165;
      txAfter = millis();
      Serial.print(F("#R joined class ")); Serial.println(classC ? 'C' : 'A');
      break;
    case RS_TX:
      if (!strcmp(s, "ok")) enter(RS_TX_WAIT);
      else if (!strcmp(s, "not_joined") || !strncmp(s, "frame_counter", 13)) { retryUplink(s); rejoinLater(s); }
      else retryUplink(s);
      break;
    case RS_TX_WAIT:
      if (!strcmp(s, "mac_tx_ok")) txDone(); else retryUplink(s);
      break;
    case RS_RAW: Serial.print(F("#R> ")); Serial.println(s); enter(RS_IDLE); break;
    default: break;                                          // boot noise
  }
}

void serviceRadio() {
  if (rs == RS_OFF) return;
  while (radio.available()) {
    char c = radio.read();
    if (c == '\r') continue;
    if (c == '\n') { rIn[rInLen] = 0; if (rInLen) radioLine(rIn); rInLen = 0; }
    else if (rInLen < sizeof(rIn) - 1) rIn[rInLen++] = c;
  }
  if (rOutPos < rOutLen) { radio.write(rOut[rOutPos++]); return; }
  unsigned long t = millis() - rsSince;
  switch (rs) {
    case RS_RESET: if (t >= 500) { digitalWrite(RADIO_RST, HIGH); enter(RS_BOOT); } break;
    case RS_BOOT:                                            // RN2483 boots at 57600: break + 0x55 sets it to 9600
      if (t < 500) break;
      radio.begin(9600);
      pinMode(RADIO_TX, OUTPUT); digitalWrite(RADIO_TX, LOW); delay(10); digitalWrite(RADIO_TX, HIGH); delay(2);
      radio.write((byte)0x55);
      enter(RS_BAUD);
      break;
    case RS_BAUD: if (t >= 100) { rInLen = 0; setupStep = 0; setupSend(); } break;
    case RS_SETUP: if (t >= 5000) rejoinLater("no answer"); break;
    case RS_JOINING: if (t >= 20000) rejoinLater("join timeout"); break;
    case RS_RETRY: if (t >= 30000) radioStart(); break;
    case RS_TX: case RS_TX_WAIT: if (t >= 20000) retryUplink("no answer"); break;
    case RS_RAW: if (t >= 5000) { Serial.println(F("#R> (no answer)")); enter(RS_IDLE); } break;
    case RS_IDLE:
      if (upReady && (long)(millis() - txAfter) >= 0) {
        char h[9];
        memcpy(upSent, up, 4); upReady = false;
        toHex(upSent, 4, h);
        radioCmd(F("mac tx uncnf 1 "), h, RS_TX);
        Serial.print(F("#R tx ")); Serial.println(h);
      } else if (!classC && !upReady && millis() - lastSent >= POLL_MS) sendState(R_HEARTBEAT);
      break;
  }
}

// ---------- USB serial: test link + test-only commands ----------
char line[40];
byte used = 0;

void handleLine(char *s) {
  if (!strncmp(s, "DL ", 3)) {
    if (st.link == LINK_LORA) { Serial.println(F("# DL over USB ignored: link is lora")); return; }
    byte b[16], n = parseHex(s + 3, b, sizeof(b));
    if (n) downlink(b, n);
  } else if (!strncmp(s, "BTN ", 4)) {
    const char *k = strchr("UDS", s[4]);
    if (s[4] && k) press(k - "UDS");
  } else if (!strncmp(s, "TICK ", 5)) {
    long sec = atol(s + 5);
    if (sec >= 5 && sec <= 3600) { tickMs = sec * 1000UL; tickStart = millis(); }
  } else if (!strcmp(s, "CAL")) {
    halt(); calibrating = false; st.travel = 0; st.pos = 0; save();
    Serial.println(F("# Calibration cleared: curtain fully down, press UP, then STOP at the top"));
    sendState(R_HEARTBEAT, true);
  } else if (!strcmp(s, "STATE")) {
    sendState(R_HEARTBEAT, true);
  } else if (!strncmp(s, "LINK ", 5) && (s[5] == 'U' || s[5] == 'L')) {
    byte l = s[5] == 'L' ? LINK_LORA : LINK_USB;
    if (l != st.link) {
      st.link = l; save();
      if (l == LINK_LORA) radioStart(); else { radioStop(); costMs = 51; }
      printLink();
      sendState(R_HEARTBEAT, true);                          // tell the backend where we are, on the new link
    } else printLink();
  } else if (!strncmp(s, "RADIO ", 6)) {
    if (rs == RS_IDLE && rOutPos >= rOutLen) radioCmd(F(""), s + 6, RS_RAW);
    else Serial.println(rs == RS_OFF ? F("#R> (radio off: link is usb)") : F("#R> (radio busy, try again)"));
  }
}

void serviceLink() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c != '\n') { if (used < sizeof(line) - 1) line[used++] = c; continue; }
    line[used] = 0; used = 0;
    handleLine(line);
  }
}

// ---------- sensors, ticks, heartbeat ----------
void serviceSensors() {
  if (millis() - lastSample < 1000) return;
  lastSample = millis();
  analogRead(PIN_OUT); liveOut = analogRead(PIN_OUT);
  analogRead(PIN_IN);  liveIn = analogRead(PIN_IN);
  sumOut += liveOut; sumIn += liveIn; samples++;
  if (millis() - tickStart >= tickMs) {
    tickStart = millis();
    outPct = pct(sumOut / samples); inPct = pct(sumIn / samples);
    sumOut = sumIn = samples = 0;
    if (!dir) decide(R_AUTO);
  }
  if (Serial.availableForWrite() < 50) return;               // never block the motor for debug output
  // #L outRaw inRaw outAvg% inAvg% posSteps travelSteps secondsToTick tickSeconds allowanceMs
  Serial.print(F("#L ")); Serial.print(liveOut); Serial.print(' '); Serial.print(liveIn); Serial.print(' ');
  Serial.print(outPct); Serial.print(' '); Serial.print(inPct); Serial.print(' ');
  Serial.print(st.pos); Serial.print(' '); Serial.print(st.travel); Serial.print(' ');
  Serial.print((tickMs - (millis() - tickStart)) / 1000); Serial.print(' '); Serial.print(tickMs / 1000);
  Serial.print(' '); Serial.println(allowance);
}

void serviceBudget() {
  while (millis() - lastRefill >= 2880) {                    // 86 400 000 ms / 30 000 ms = 2880
    lastRefill += 2880;
    if (allowance < 3000) allowance++;
  }
  if (havePending && allowance >= costMs && !dir) sendState(pendingReason);
  if (millis() - lastSent >= HEARTBEAT_MS) sendState(R_HEARTBEAT, true);
}

void setup() {
  Serial.begin(9600);
  for (byte i = 0; i < 3; i++) pinMode(BTN[i], INPUT);
  for (byte i = 0; i < 4; i++) pinMode(COIL[i], OUTPUT);
  coilsOff();
  EEPROM.get(0, st);
  if (st.magic != MAGIC) {
    st = {MAGIC, 0, 0, 60, 80, 25, 0, 0, 0, 0, LINK_USB};
    save();
  }
  if (st.travel < 0 || st.pos < 0 || st.pos > st.travel) { st.travel = 0; st.pos = 0; save(); }
  if (st.link > LINK_LORA) { st.link = LINK_USB; save(); }  // EEPROM from before the LoRa link existed
  goal = st.pos;
  analogRead(PIN_OUT); outPct = pct(analogRead(PIN_OUT));
  analogRead(PIN_IN);  inPct = pct(analogRead(PIN_IN));
  Serial.println(F("# Smart Curtains fw v3"));
  printLink();
  if (st.link == LINK_LORA) radioStart(); else radioStop();
  if (!calibrated()) Serial.println(F("# Not calibrated: curtain fully down, press UP, then STOP at the top"));
  sendState(R_STARTUP, true);
}

void loop() {
  serviceButtons();
  serviceMotor();
  serviceLink();
  serviceSensors();
  serviceBudget();
  serviceRadio();
}
