// Smart Curtains (Group 7) — TTN v3 payload formatter, protocol v4.1. Spec: PROTOCOL.md
// Paste into TTN Console → Application → Payload formatters → Uplink AND Downlink ("Custom Javascript formatter").
// Written in ES5 on purpose: TTN's JavaScript engine is old. Position is % CLOSED (0 = open, 100 = closed).

var REASONS = ['heartbeat', 'startup', 'button', 'app_command', 'auto', 'schedule', 'calibrated', 'rejected'];

// ---------- Uplink: device → backend (FPort 1, always 4 bytes) ----------
function decodeUplink(input) {
  var b = input.bytes;
  if (input.fPort !== 1) return { errors: ['unknown fPort ' + input.fPort] };
  if (b.length !== 4) return { errors: ['uplink must be 4 bytes, got ' + b.length] };
  var f = b[2];
  return {
    data: {
      last_command_id: b[0],
      reason: REASONS[b[1]] || 'unknown',
      mode: (f & 1) ? 'automatic' : 'manual',
      moving: (f & 2) !== 0,
      calibrated: (f & 4) !== 0,
      day_open: (f & 8) !== 0,
      open_by_sun: (f & 16) !== 0,
      close_by_sun: (f & 32) !== 0,
      maintain_light: (f & 64) !== 0,
      calibrating: (f & 128) !== 0,          // v4.1: position is null until the bottom is set, then 100
      position: b[3] === 255 ? null : b[3]
    }
  };
}

// ---------- Downlink: backend → device (FPort 1) ----------
// { "command_id": 12, "type": "position", "position": 40 }
// { "command_id": 13, "type": "mode", "mode": "automatic" }
// { "command_id": 14, "type": "auto", "autoOpen": 1, "autoClose": 0, "maintainLight": 1,
//   "targetLightLevel": 35, "outsideThresholdOpen": 80, "outsideThresholdClose": 25 }
//   v4, optional in the same "auto" message: "mode": "manual" | "automatic", "day": true | false
// { "command_id": 15, "type": "schedule", "action": "open" }
// { "command_id": 16, "type": "calibrate" }                      v4.1: start calibration with the buttons
function pct(v) { return v >= 0 && v <= 100 && v % 1 === 0; }
function encodeDownlink(input) {
  var d = input.data;
  if (!(d.command_id >= 1 && d.command_id <= 255 && d.command_id % 1 === 0)) return { errors: ['command_id must be 1–255'] };
  var id = d.command_id;
  if (d.type === 'position') {
    if (!pct(d.position)) return { errors: ['position must be 0–100'] };
    return { fPort: 1, bytes: [id, 1, d.position] };
  }
  if (d.type === 'mode') {
    if (d.mode !== 'manual' && d.mode !== 'automatic') return { errors: ['mode must be manual or automatic'] };
    return { fPort: 1, bytes: [id, 2, d.mode === 'automatic' ? 1 : 0] };
  }
  if (d.type === 'auto') {
    if (!pct(d.targetLightLevel) || !pct(d.outsideThresholdOpen) || !pct(d.outsideThresholdClose)) return { errors: ['levels must be 0–100'] };
    if (d.outsideThresholdClose >= d.outsideThresholdOpen) return { errors: ['outsideThresholdClose must be lower than outsideThresholdOpen'] };
    var cfg = (d.autoOpen ? 1 : 0) | (d.autoClose ? 2 : 0) | (d.maintainLight ? 4 : 0);
    if (d.mode !== undefined) {
      if (d.mode !== 'manual' && d.mode !== 'automatic') return { errors: ['mode must be manual or automatic'] };
      cfg |= 32 | (d.mode === 'automatic' ? 8 : 0);           // bit 5 = set mode, bit 3 = automatic
    }
    if (d.day !== undefined) {
      if (d.day !== true && d.day !== false) return { errors: ['day must be true or false'] };
      cfg |= 64 | (d.day ? 16 : 0);                            // bit 6 = set day, bit 4 = day
    }
    return { fPort: 1, bytes: [id, 3, cfg, d.targetLightLevel, d.outsideThresholdOpen, d.outsideThresholdClose] };
  }
  if (d.type === 'schedule') {
    if (d.action !== 'open' && d.action !== 'close') return { errors: ['action must be open or close'] };
    return { fPort: 1, bytes: [id, 4, d.action === 'open' ? 0 : 1] };
  }
  if (d.type === 'calibrate') return { fPort: 1, bytes: [id, 5, 1] };
  return { errors: ['type must be position, mode, auto, schedule or calibrate'] };
}

function decodeDownlink(input) {
  var b = input.bytes;
  if (b.length < 3) return { errors: ['too short'] };
  var id = b[0];
  if (b[1] === 1) return { data: { command_id: id, type: 'position', position: b[2] } };
  if (b[1] === 2) return { data: { command_id: id, type: 'mode', mode: b[2] ? 'automatic' : 'manual' } };
  if (b[1] === 3 && b.length === 6) {
    var c = b[2], data = { command_id: id, type: 'auto', autoOpen: c & 1, autoClose: (c >> 1) & 1, maintainLight: (c >> 2) & 1,
      targetLightLevel: b[3], outsideThresholdOpen: b[4], outsideThresholdClose: b[5] };
    if (c & 32) data.mode = (c & 8) ? 'automatic' : 'manual';
    if (c & 64) data.day = (c & 16) !== 0;
    return { data: data };
  }
  if (b[1] === 4) return { data: { command_id: id, type: 'schedule', action: b[2] ? 'close' : 'open' } };
  if (b[1] === 5 && b[2] === 1) return { data: { command_id: id, type: 'calibrate' } };
  return { errors: ['unknown downlink'] };
}

if (typeof module !== 'undefined') module.exports = { decodeUplink: decodeUplink, encodeDownlink: encodeDownlink, decodeDownlink: decodeDownlink };
