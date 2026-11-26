// Run: node test_formatter.js   (checks ttn_payload_formatter.js against PROTOCOL.md v4.1)
// Messages marked "Captured" are real ones from the Pro Mini over the USB test link (19 Nov 2026).
const f = require('./ttn_payload_formatter.js');
const assert = require('assert');
const up = hex => f.decodeUplink({ bytes: [...Buffer.from(hex, 'hex')], fPort: 1 }).data;
const down = data => Buffer.from(f.encodeDownlink({ data }).bytes).toString('hex').toUpperCase();

// Captured from the device
assert.deepEqual(up('000100FF'), { last_command_id: 0, reason: 'startup', mode: 'manual', moving: false, calibrated: false,
  day_open: false, open_by_sun: false, close_by_sun: false, maintain_light: false, calibrating: false, position: null });
assert.equal(up('150759FF').reason, 'rejected');
assert.equal(up('150759FF').mode, 'automatic');
assert.equal(up('150759FF').open_by_sun, true);
assert.equal(up('150759FF').maintain_light, true);
assert.equal(up('120528FF').reason, 'schedule');
// A calibrated move: reply "moving" at 0 % closed, then stopped at 40 % closed
assert.equal(up('16030600').moving, true);
assert.equal(up('16030428').position, 40);

// Downlinks: identical to what the local test server sent to the device
assert.equal(down({ command_id: 21, type: 'position', position: 40 }), '150128');
assert.equal(down({ command_id: 19, type: 'mode', mode: 'automatic' }), '130201');
assert.equal(down({ command_id: 20, type: 'auto', autoOpen: 1, autoClose: 0, maintainLight: 1, targetLightLevel: 35,
  outsideThresholdOpen: 80, outsideThresholdClose: 10 }), '14030523500A');
assert.equal(down({ command_id: 18, type: 'schedule', action: 'open' }), '120400');
assert.deepEqual(f.decodeDownlink({ bytes: [20, 3, 5, 35, 80, 10] }).data.outsideThresholdClose, 10);
assert.equal(f.decodeDownlink({ bytes: [20, 3, 5, 35, 80, 10] }).data.mode, undefined);   // v3-style: mode untouched

// v4: mode and day in the same "auto" message (bit 5 set mode, bit 3 automatic, bit 6 set day, bit 4 day)
const v4 = { command_id: 22, type: 'auto', autoOpen: 0, autoClose: 0, maintainLight: 1, targetLightLevel: 27,
  outsideThresholdOpen: 80, outsideThresholdClose: 10 };
assert.equal(down({ ...v4, mode: 'automatic', day: false }), '16036C1B500A');                     // 4|32|8|64 = 0x6C
assert.equal(down({ ...v4, mode: 'automatic', day: true }), '16037C1B500A');                     // + 16 = 0x7C
assert.equal(down({ ...v4, mode: 'manual' }), '1603241B500A');                                   // 4|32 = 0x24
assert.deepEqual(f.decodeDownlink({ bytes: [22, 3, 0x7C, 27, 80, 10] }).data,
  { ...v4, mode: 'automatic', day: true });
assert.deepEqual(f.decodeDownlink({ bytes: [22, 3, 0x24, 27, 80, 10] }).data, { ...v4, mode: 'manual' });
assert.ok(f.encodeDownlink({ data: { ...v4, mode: 'auto' } }).errors);
assert.ok(f.encodeDownlink({ data: { ...v4, day: 1 } }).errors);

// v4.1 calibration, captured from the board over USB (26 Nov): start → bottom set → calibrated
assert.equal(down({ command_id: 39, type: 'calibrate' }), '270501');
assert.deepEqual(f.decodeDownlink({ bytes: [39, 5, 1] }).data, { command_id: 39, type: 'calibrate' });
assert.ok(f.decodeDownlink({ bytes: [39, 5, 0] }).errors);
assert.equal(up('2703A8FF').calibrating, true);
assert.equal(up('2703A8FF').calibrated, false);
assert.equal(up('2703A8FF').position, null);
assert.equal(up('2702A864').calibrating, true);
assert.equal(up('2702A864').position, 100);
assert.equal(up('28062C00').reason, 'calibrated');
assert.equal(up('28062C00').calibrating, false);
assert.equal(up('28062C00').calibrated, true);

// Validation
assert.ok(f.encodeDownlink({ data: { command_id: 0, type: 'mode', mode: 'manual' } }).errors);
assert.ok(f.encodeDownlink({ data: { command_id: 5, type: 'position', position: 101 } }).errors);
assert.ok(f.encodeDownlink({ data: { command_id: 5, type: 'auto', targetLightLevel: 50, outsideThresholdOpen: 20, outsideThresholdClose: 30 } }).errors);
assert.ok(f.decodeUplink({ bytes: [1, 2, 3], fPort: 1 }).errors);

console.log('ALL TESTS PASS');
