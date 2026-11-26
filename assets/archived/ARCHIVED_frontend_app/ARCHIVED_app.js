'use strict';
// Smart Curtain phone app (Group 7). Uses only the UI team's REST API (ui_spec/communication_spec.md):
// POST /login, GET /status, POST /position, PUT /mode, PUT /auto, PUT /settings.
// Position is % closed everywhere: 0 = fully open, 100 = fully closed.

const API_BASE = '';   // '' = the server that serves this page. Set to the backend's URL if it lives elsewhere.

const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const store = {
  get(k, d) { try { const v = localStorage.getItem('curtain.' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('curtain.' + k, JSON.stringify(v)); } catch (e) {} },
  del(k) { try { localStorage.removeItem('curtain.' + k); } catch (e) {} },
};

// ---------------------------------------------------------------- text (Danish + English)
const STR = {
  da: {
    'login.title': 'Log ind', 'login.lead': 'Log ind for at styre dit rullegardin.',
    'login.email': 'E-mail', 'login.password': 'Adgangskode', 'login.show': 'Vis adgangskode',
    'login.submit': 'Log ind', 'login.busy': 'Logger ind…',
    'login.missing': 'Skriv din e-mail og adgangskode.', 'login.failed': 'Det lykkedes ikke at logge ind. {msg}',
    'name.default': 'Rullegardin', 'name.title': 'Navn', 'name.placeholder': 'Fx Stuen',
    'name.note': 'Vises øverst i appen. Gemmes kun på denne telefon.',
    'pos.open': 'Åbent', 'pos.closed': 'Lukket', 'pos.word': 'lukket', 'pos.range': 'Gardinets position',
    'pos.textOpen': 'helt åbent', 'pos.textClosed': 'helt lukket', 'pos.text': '{p} lukket',
    'status.hint': 'Træk i gardinet, eller vælg herunder.',
    'status.down': 'Kører ned til {t}', 'status.up': 'Kører op til {t}', 'status.now': 'Nu {a}',
    'status.opening': 'Åbner helt…', 'status.closing': 'Lukker helt…',
    'status.failed': 'Nåede ikke {t}. Gardinet stoppede ved {a}.',
    'arrived': 'Gardinet er nu {pos}.',
    'auto.openTime': 'Åbner kl. {t}', 'auto.openSun': 'Åbner ved solopgang',
    'auto.closeTime': 'lukker kl. {t}', 'auto.closeSun': 'lukker ved solnedgang', 'auto.keepSummary': 'Holder lyset på {l}.',
    'preset.group': 'Hurtigvalg', 'preset.open': 'Åbn', 'preset.half': 'Halvvejs', 'preset.closed': 'Luk',
    'preset.openAria': 'Åbn helt', 'preset.halfAria': 'Kør halvvejs ned', 'preset.closedAria': 'Luk helt',
    'mode.title': 'Styring', 'mode.manual': 'Manuel', 'mode.auto': 'Automatisk',
    'mode.manualHelp': 'Gardinet bliver, hvor du sætter det.',
    'mode.autoHelp': 'Gardinet åbner og lukker selv efter dine valg herunder.',
    'auto.open': 'Åbn om morgenen', 'auto.close': 'Luk om aftenen', 'auto.keep': 'Lys indenfor',
    'auto.atTime': 'Fast tidspunkt', 'auto.sunrise': 'Ved solopgang', 'auto.sunset': 'Ved solnedgang', 'auto.time': 'Tidspunkt',
    'auto.sunriseNote': 'Åbner, når lyset udenfor kommer over {o}. Grænsen kan ændres under Indstillinger.',
    'auto.sunsetNote': 'Lukker, når lyset udenfor falder under {c}. Grænsen kan ændres under Indstillinger.',
    'auto.keepToggle': 'Hold et fast lysniveau', 'auto.keepDesc': 'Gardinet justerer sig, så rummet får det lys, du vælger.',
    'auto.level': 'Ønsket lysniveau', 'auto.keepNote': 'Kun mellem dagens åbning og lukning. Gardinet tjekker lyset hvert 5. minut.',
    'auto.timeMissing': 'Vælg et tidspunkt først.',
    'save.unsaved': 'Ikke gemt endnu', 'save.save': 'Gem', 'save.discard': 'Fortryd', 'save.saving': 'Gemmer…',
    'save.saved': 'Gemt og sendt til gardinet',
    'toast.toManual': 'Automatik er slået fra, fordi du flyttede gardinet.', 'toast.autoOn': 'Slå til igen',
    'toast.external': 'Gardinet styres nu manuelt. Det sker, når nogen bruger knapperne på gardinet.',
    'toast.error': 'Det lykkedes ikke. {msg}',
    'err.network': 'Kan ikke nå serveren.', 'err.session': 'Log ind igen.', 'err.server': 'Serveren svarede: {msg}',
    'offline.title': 'Ingen forbindelse til serveren.', 'offline.since': 'Sidst opdateret kl. {t}.', 'offline.retry': 'Prøv igen',
    'settings.title': 'Indstillinger', 'nav.back': 'Tilbage',
    'sun.title': 'Sollys udenfor', 'sun.open': 'Åbn, når lyset er over', 'sun.close': 'Luk, når lyset er under',
    'sun.note': 'Bruges af “Ved solopgang” og “Ved solnedgang”. 0 % er mørke, 100 % er fuldt sollys.',
    'sun.err': 'Luk-grænsen skal være lavere end åbn-grænsen.',
    'lang.title': 'Sprog',
    'install.title': 'Brug som app', 'install.s1': 'Åbn siden i Safari.', 'install.s2': 'Tryk på Del',
    'install.s3': 'Vælg “Føj til hjemmeskærm”, og tryk Tilføj.',
    'install.tip': 'Brug den som en app: tryk på Del, og vælg “Føj til hjemmeskærm”.', 'install.close': 'Luk tip',
    'logout': 'Log ud',
  },
  en: {
    'login.title': 'Log in', 'login.lead': 'Log in to control your roller blind.',
    'login.email': 'Email', 'login.password': 'Password', 'login.show': 'Show password',
    'login.submit': 'Log in', 'login.busy': 'Logging in…',
    'login.missing': 'Enter your email and password.', 'login.failed': 'Could not log in. {msg}',
    'name.default': 'Roller blind', 'name.title': 'Name', 'name.placeholder': 'E.g. Living room',
    'name.note': 'Shown at the top of the app. Saved on this phone only.',
    'pos.open': 'Open', 'pos.closed': 'Closed', 'pos.word': 'closed', 'pos.range': 'Blind position',
    'pos.textOpen': 'fully open', 'pos.textClosed': 'fully closed', 'pos.text': '{p} closed',
    'status.hint': 'Drag the blind, or pick a position below.',
    'status.down': 'Moving down to {t}', 'status.up': 'Moving up to {t}', 'status.now': 'Now {a}',
    'status.opening': 'Opening fully…', 'status.closing': 'Closing fully…',
    'status.failed': 'Did not reach {t}. The blind stopped at {a}.',
    'arrived': 'The blind is now {pos}.',
    'auto.openTime': 'Opens at {t}', 'auto.openSun': 'Opens at sunrise',
    'auto.closeTime': 'closes at {t}', 'auto.closeSun': 'closes at sunset', 'auto.keepSummary': 'Keeps the light at {l}.',
    'preset.group': 'Quick positions', 'preset.open': 'Open', 'preset.half': 'Halfway', 'preset.closed': 'Close',
    'preset.openAria': 'Open fully', 'preset.halfAria': 'Move halfway', 'preset.closedAria': 'Close fully',
    'mode.title': 'Control', 'mode.manual': 'Manual', 'mode.auto': 'Automatic',
    'mode.manualHelp': 'The blind stays where you put it.',
    'mode.autoHelp': 'The blind opens and closes by itself, using your choices below.',
    'auto.open': 'Open in the morning', 'auto.close': 'Close in the evening', 'auto.keep': 'Light indoors',
    'auto.atTime': 'At a set time', 'auto.sunrise': 'At sunrise', 'auto.sunset': 'At sunset', 'auto.time': 'Time',
    'auto.sunriseNote': 'Opens when the light outside rises above {o}. You can change the limit in Settings.',
    'auto.sunsetNote': 'Closes when the light outside drops below {c}. You can change the limit in Settings.',
    'auto.keepToggle': 'Keep a steady light level', 'auto.keepDesc': 'The blind adjusts itself to give the room the light you choose.',
    'auto.level': 'Light level', 'auto.keepNote': 'Only between the day’s opening and closing. The blind checks the light every 5 minutes.',
    'auto.timeMissing': 'Choose a time first.',
    'save.unsaved': 'Not saved yet', 'save.save': 'Save', 'save.discard': 'Discard', 'save.saving': 'Saving…',
    'save.saved': 'Saved and sent to the blind',
    'toast.toManual': 'Automatic is off because you moved the blind.', 'toast.autoOn': 'Turn back on',
    'toast.external': 'The blind is now manual. This happens when someone uses the buttons on the blind.',
    'toast.error': 'That did not work. {msg}',
    'err.network': 'Cannot reach the server.', 'err.session': 'Please log in again.', 'err.server': 'The server said: {msg}',
    'offline.title': 'No connection to the server.', 'offline.since': 'Last updated at {t}.', 'offline.retry': 'Try again',
    'settings.title': 'Settings', 'nav.back': 'Back',
    'sun.title': 'Daylight outside', 'sun.open': 'Open when the light is above', 'sun.close': 'Close when the light is below',
    'sun.note': 'Used by “At sunrise” and “At sunset”. 0% is dark, 100% is full sun.',
    'sun.err': 'The close limit must be lower than the open limit.',
    'lang.title': 'Language',
    'install.title': 'Use it as an app', 'install.s1': 'Open this page in Safari.', 'install.s2': 'Tap Share',
    'install.s3': 'Choose “Add to Home Screen”, then tap Add.',
    'install.tip': 'Use it like an app: tap Share, then “Add to Home Screen”.', 'install.close': 'Dismiss tip',
    'logout': 'Log out',
  },
};
let lang = store.get('lang', null) || ((navigator.language || '').toLowerCase().startsWith('da') ? 'da' : 'en');
function t(key, vars) {
  let s = STR[lang][key] ?? STR.en[key] ?? key;
  if (vars) s = s.replace(/\{(\w+)\}/g, (_, k) => vars[k]);
  return s;
}
const pct = v => (lang === 'da' ? v + ' %' : v + '%');
const posText = p => (p === 0 ? t('pos.textOpen') : p === 100 ? t('pos.textClosed') : t('pos.text', { p: pct(p) }));

// ---------------------------------------------------------------- state
let token = store.get('token', null);
let creds = store.get('creds', null);   // {username, md5 password}: lets the app log in again after a server restart (demo-grade, like the spec)
let status = null;                      // last GET /status answer
let online = true, lastOk = 0;
let wanted = null, wantedAt = 0, wantedFrom = 0;   // where the user sent the blind; shown solid until the real position arrives
let lastActual = null, lastChange = 0;
let failNote = null, failUntil = 0;
let dragP = null;                       // live position while dragging
let pendingMode = null, appModeAt = 0;  // tells the app's own mode changes apart from the buttons on the blind
let autoDirty = false, setDirty = false;
let pollTimer = null, polling = false;

// ---------------------------------------------------------------- API
class NetError extends Error {}
async function api(method, path, body, again = true) {
  const headers = { 'Content-Type': 'application/json' };
  if (token && path !== '/login') headers.Authorization = 'Bearer ' + token;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  let r;
  try {
    r = await fetch(API_BASE + path, { method, headers, body: body ? JSON.stringify(body) : undefined, signal: ctrl.signal, cache: 'no-store' });
  } catch (e) {
    throw new NetError(t('err.network'));
  } finally { clearTimeout(timer); }
  const data = await r.json().catch(() => ({}));
  if (r.status === 401 && path !== '/login') {
    if (again && await relogin()) return api(method, path, body, false);
    logout();
    throw new Error(t('err.session'));
  }
  if (!r.ok) throw new Error(data.error ? t('err.server', { msg: data.error }) : 'HTTP ' + r.status);
  return data;
}
async function relogin() {
  if (!creds) return false;
  try { token = (await api('POST', '/login', creds)).token; store.set('token', token); return true; } catch (e) { return false; }
}

// ---------------------------------------------------------------- screens
let cameFromHome = false;
function route() {
  const want = !token ? 'login' : location.hash === '#settings' ? 'settings' : 'home';
  const current = $$('.screen').find(s => !s.hidden);
  if (current && current.id === want) return;
  for (const s of $$('.screen')) s.hidden = s.id !== want;
  const el = $('#' + want);
  el.classList.remove('enter-fwd', 'enter-back');
  if (current) {
    void el.offsetWidth;
    el.classList.add(want === 'settings' ? 'enter-fwd' : 'enter-back');
    window.scrollTo(0, 0);
    const h = el.querySelector('h1');
    h.tabIndex = -1;
    h.focus({ preventScroll: true });
  }
  if (want === 'login') setTimeout(() => $('#email').value || $('#email').focus(), 50);
  render();
}
window.addEventListener('hashchange', route);
$('#toSettings').addEventListener('click', () => { cameFromHome = true; });
$('#backBtn').addEventListener('click', e => {
  e.preventDefault();
  if (cameFromHome) { cameFromHome = false; history.back(); } else location.replace('#');
});

// ---------------------------------------------------------------- login / logout
$('#loginForm').addEventListener('submit', async e => {
  e.preventDefault();
  const email = $('#email').value.trim(), pw = $('#password').value, err = $('#loginErr'), btn = $('#loginBtn');
  err.textContent = '';
  if (!email || !pw) { err.textContent = t('login.missing'); (email ? $('#password') : $('#email')).focus(); return; }
  btn.disabled = true; btn.textContent = t('login.busy');
  try {
    const c = { username: email, password: md5(pw) };
    token = (await api('POST', '/login', c)).token;
    creds = c; store.set('token', token); store.set('creds', c);
    $('#password').value = '';
    if (location.hash) location.replace('#');
    route();
    refresh();
  } catch (ex) {
    err.textContent = t('login.failed', { msg: ex.message });
  } finally { btn.disabled = false; btn.textContent = t('login.submit'); }
});
$('#pwToggle').addEventListener('click', () => {
  const i = $('#password'), show = i.type === 'password';
  i.type = show ? 'text' : 'password';
  $('#pwToggle').setAttribute('aria-pressed', show);
  $('#pwToggle use').setAttribute('href', show ? '#i-eye-slash' : '#i-eye');
});
function logout() {
  token = null; creds = null; status = null; wanted = null;
  store.del('token'); store.del('creds');
  clearTimeout(pollTimer);
  $('#window').classList.add('loading');
  if (location.hash) location.replace('#');
  route();
}
$('#logoutBtn').addEventListener('click', logout);

// ---------------------------------------------------------------- polling
// The spec polls GET /status every 5 s. While the blind is on its way, poll every second so the "now" line follows it.
async function refresh() {
  clearTimeout(pollTimer);
  if (!token || polling) return;
  polling = true;
  try {
    onStatus(await api('GET', '/status'));
    online = true; lastOk = Date.now();
  } catch (e) {
    if (e instanceof NetError) online = false;
  }
  polling = false;
  renderConn();
  schedule();
}
function schedule(ms) {
  clearTimeout(pollTimer);
  if (!token || document.hidden) return;
  pollTimer = setTimeout(refresh, ms ?? (wanted != null ? 1000 : online ? 5000 : 4000));
}
document.addEventListener('visibilitychange', () => {
  if (document.hidden) clearTimeout(pollTimer);
  else if (token) { renderSky(); refresh(); }
});
$('#retryBtn').addEventListener('click', refresh);

function onStatus(s) {
  const now = Date.now(), prev = status;
  if (prev && now - appModeAt < 2500) s.mode = prev.mode;           // a poll that raced our own mode change
  if (prev && prev.mode === 'automatic' && s.mode === 'manual' && now - appModeAt > 10000) toast(t('toast.external'));
  status = s;
  if (s.position !== lastActual) { lastActual = s.position; lastChange = now; }
  if (wanted != null) {
    if (Math.abs(s.position - wanted) <= 1) {
      announce(t('arrived', { pos: posText(s.position) }));
      wanted = null;
    } else if (s.mode === 'automatic' && now - appModeAt > 3000) {
      wanted = null;                                                // automatic took over
    } else if (now - wantedAt > 5000 + 120 * Math.abs(wanted - wantedFrom) && now - lastChange > 6000) {
      // Not moving any more. The wait scales with the distance, because over LoRaWAN the position
      // only updates when a move starts and ends (a full move takes about 9 s).
      failNote = t('status.failed', { t: pct(wanted), a: pct(s.position) });
      failUntil = now + 8000;
      announce(failNote);
      wanted = null;
    }
  }
  render();
}

// ---------------------------------------------------------------- rendering
function render() {
  renderName();
  if (!status) return;
  renderPosition();
  renderStatusLine();
  renderMode();
  renderAutoForm();
  renderSettingsForm();
}
function renderName() {
  $('#name').textContent = store.get('name', '') || t('name.default');
}
function shownP() { return dragP != null ? dragP : wanted != null ? wanted : status.position; }
function isMoving() { return wanted != null && dragP == null && Math.abs(status.position - wanted) > 1; }

function renderPosition() {
  const win = $('#window'), p = shownP(), a = status.position;
  win.classList.remove('loading');
  win.style.setProperty('--p', p);
  win.style.setProperty('--a', a);
  $('#ghost').hidden = !isMoving();
  $('#ghostTag').textContent = t('status.now', { a: pct(a) });
  $('#readout').innerHTML = p === 0 ? `<span class="num">${t('pos.open')}</span>`
    : p === 100 ? `<span class="num">${t('pos.closed')}</span>`
    : `<span class="num">${p}</span><span class="unit">${lang === 'da' ? ' %' : '%'}</span><span class="word">${t('pos.word')}</span>`;
  const r = $('#posRange');
  if (document.activeElement !== r || dragP == null) r.value = p;
  r.setAttribute('aria-valuetext', posText(p));
  for (const b of $$('.preset')) b.classList.toggle('current', +b.dataset.p === p && !isMoving() && dragP == null);
}

function renderStatusLine() {
  let txt = '';
  if (dragP != null) txt = '';
  else if (isMoving()) {
    txt = wanted === 0 ? t('status.opening') : wanted === 100 ? t('status.closing')
      : t(wanted > status.position ? 'status.down' : 'status.up', { t: pct(wanted) });
  }
  else if (failNote && Date.now() < failUntil) txt = failNote;
  else if ((pendingMode || status.mode) === 'automatic') {
    const open = status.autoOpen ? t('auto.openSun') : t('auto.openTime', { t: status.autoOpenTime });
    const close = status.autoClose ? t('auto.closeSun') : t('auto.closeTime', { t: status.autoCloseTime });
    txt = `${open}, ${close}.` + (status.maintainLight ? ' ' + t('auto.keepSummary', { l: pct(status.targetLightLevel) }) : '');
  } else txt = t('status.hint');
  $('#statusLine').textContent = txt;
}

function renderMode() {
  const m = pendingMode || status.mode;
  for (const r of $$('input[name=mode]')) r.checked = r.value === m;
  $('#modeHelp').textContent = t(m === 'automatic' ? 'mode.autoHelp' : 'mode.manualHelp');
  $('#autoPanel').hidden = m !== 'automatic';
  if (m !== 'automatic') hideBar('auto');
  else if (autoDirty && bars.auto.el.dataset.state === 'hidden') setBar('auto', 'dirty');
}

function radio(name) { return ($$(`input[name=${name}]`).find(r => r.checked) || {}).value; }
function setRadio(name, v) { for (const r of $$(`input[name=${name}]`)) r.checked = r.value === v; }
function fill(r) { r.style.setProperty('--v', ((r.value - r.min) / (r.max - r.min)) * 100 + '%'); }

function renderAutoForm() {
  if (autoDirty) return syncAutoUI();
  setRadio('openBy', status.autoOpen ? 'sun' : 'time');
  setRadio('closeBy', status.autoClose ? 'sun' : 'time');
  $('#openTime').value = status.autoOpenTime;
  $('#closeTime').value = status.autoCloseTime;
  $('#maintain').checked = !!status.maintainLight;
  $('#target').value = status.targetLightLevel;
  syncAutoUI();
}
function syncAutoUI() {
  const openSun = radio('openBy') === 'sun', closeSun = radio('closeBy') === 'sun';
  $('#openTimeRow').hidden = openSun;
  $('#closeTimeRow').hidden = closeSun;
  $('#openSunNote').hidden = !openSun;
  $('#closeSunNote').hidden = !closeSun;
  $('#openSunNote').textContent = t('auto.sunriseNote', { o: pct(status.outsideThresholdOpen) });
  $('#closeSunNote').textContent = t('auto.sunsetNote', { c: pct(status.outsideThresholdClose) });
  $('#targetRow').hidden = !$('#maintain').checked;
  $('#targetOut').textContent = pct($('#target').value);
  fill($('#target'));
}

function renderSettingsForm() {
  if (!setDirty) {
    $('#thrOpen').value = status.outsideThresholdOpen;
    $('#thrClose').value = status.outsideThresholdClose;
  }
  syncSetUI();
}
function syncSetUI() {
  $('#thrOpenOut').textContent = pct($('#thrOpen').value);
  $('#thrCloseOut').textContent = pct($('#thrClose').value);
  fill($('#thrOpen'));
  fill($('#thrClose'));
}

function renderConn() {
  const show = !!token && !online;
  $('#offline').hidden = !show;
  $('#staleText').textContent = show && lastOk
    ? t('offline.since', { t: new Date(lastOk).toLocaleTimeString(lang === 'da' ? 'da-DK' : 'en-GB', { hour: '2-digit', minute: '2-digit' }) })
    : '';
}

const pane = $('#pane');
function renderSky() {
  const d = new Date(), h = d.getHours() + d.getMinutes() / 60;
  const phase = h >= 8 && h < 18 ? 'day' : (h >= 6 && h < 8) || (h >= 18 && h < 21) ? 'dusk' : 'night';
  pane.classList.toggle('dusk', phase === 'dusk');
  pane.classList.toggle('night', phase === 'night');
}
setInterval(renderSky, 60000);

// ---------------------------------------------------------------- moving the blind
async function moveTo(p) {
  if (!status) return;
  failNote = null;
  if (wanted == null && p === status.position) { render(); return; }
  const wasAuto = (pendingMode || status.mode) === 'automatic';
  wanted = p; wantedFrom = status.position; wantedAt = lastChange = appModeAt = Date.now();
  haptic();
  render();
  try {
    await api('POST', '/position', { position: p });   // the blind switches itself to manual
    if (wasAuto) {
      status.mode = 'manual';
      appModeAt = Date.now();
      render();
      toast(t('toast.toManual'), t('toast.autoOn'), () => setMode('automatic'));
    }
  } catch (e) {
    wanted = null;
    render();
    toast(t('toast.error', { msg: e.message }));
  }
  schedule(400);
}
for (const b of $$('.preset')) b.addEventListener('click', () => moveTo(+b.dataset.p));

// Drag anywhere on the window. A 6 px threshold keeps taps from moving the blind.
const win = $('#window');
let pid = null, startY = 0, armed = false;
function pFromY(y) {
  const r = pane.getBoundingClientRect(), roller = 18, rail = 12;
  let p = Math.round(((y - r.top - roller - rail / 2) / (r.height - roller - rail)) * 100);
  p = Math.max(0, Math.min(100, p));
  for (const snap of [0, 50, 100]) if (Math.abs(p - snap) <= 2) p = snap;
  return p;
}
win.addEventListener('pointerdown', e => {
  if (!status || (e.pointerType === 'mouse' && e.button !== 0)) return;
  pid = e.pointerId; startY = e.clientY; armed = false;
  try { win.setPointerCapture(pid); } catch (x) {}
});
win.addEventListener('pointermove', e => {
  if (e.pointerId !== pid) return;
  if (!armed) {
    if (Math.abs(e.clientY - startY) < 6) return;
    armed = true;
    win.classList.add('dragging');
  }
  const p = pFromY(e.clientY);
  if (p === dragP) return;
  if (dragP != null && p % 50 === 0) haptic();
  dragP = p;
  renderPosition();
  renderStatusLine();
});
function endDrag(e, commit) {
  if (e.pointerId !== pid) return;
  pid = null;
  if (!armed) return;
  armed = false;
  win.classList.remove('dragging');
  const p = dragP;
  dragP = null;
  if (commit && p != null) moveTo(p); else render();
}
win.addEventListener('pointerup', e => endDrag(e, true));
win.addEventListener('pointercancel', e => endDrag(e, false));

// VoiceOver and keyboards use the native slider on top of the window. Send once the person stops adjusting.
let rangeTimer;
$('#posRange').addEventListener('input', e => {
  if (!status) return;
  dragP = +e.target.value;
  renderPosition();
  renderStatusLine();
  clearTimeout(rangeTimer);
  rangeTimer = setTimeout(() => { const p = dragP; dragP = null; if (p != null) moveTo(p); }, 700);
});

// ---------------------------------------------------------------- mode
async function setMode(m) {
  if (!status || (pendingMode || status.mode) === m) return;
  pendingMode = m; appModeAt = Date.now();
  render();
  try {
    await api('PUT', '/mode', { mode: m });
    status.mode = m;
  } catch (e) {
    toast(t('toast.error', { msg: e.message }));
  } finally {
    pendingMode = null; appModeAt = Date.now();
    render();
  }
}
for (const r of $$('input[name=mode]')) r.addEventListener('change', () => r.checked && setMode(r.value));

// ---------------------------------------------------------------- save bars (automatic settings, settings page)
const bars = {
  auto: { el: $('#autoBar'), text: $('#autoBarText'), timer: 0 },
  set: { el: $('#setBar'), text: $('#setBarText'), timer: 0 },
};
function setBar(which, state) {
  const b = bars[which];
  clearTimeout(b.timer);
  b.el.dataset.state = state;
  b.el.setAttribute('aria-hidden', state === 'hidden');
  b.text.innerHTML = state === 'saved' ? `<svg class="ic" aria-hidden="true"><use href="#i-check"/></svg>${t('save.saved')}`
    : state === 'saving' ? t('save.saving') : t('save.unsaved');
  for (const btn of b.el.querySelectorAll('button')) btn.disabled = state === 'saving';
  if (state === 'saved') { announce(t('save.saved')); b.timer = setTimeout(() => setBar(which, 'hidden'), 1800); }
  document.body.classList.toggle('bar-up', Object.values(bars).some(x => x.el.dataset.state !== 'hidden'));
}
function hideBar(which) { if (bars[which].el.dataset.state !== 'hidden') setBar(which, 'hidden'); }

for (const el of $$('#autoPanel input')) {
  el.addEventListener(el.type === 'range' ? 'input' : 'change', () => {
    autoDirty = true;
    syncAutoUI();
    setBar('auto', 'dirty');
  });
}
$('#autoDiscard').addEventListener('click', () => { autoDirty = false; renderAutoForm(); setBar('auto', 'hidden'); });
$('#autoSave').addEventListener('click', async () => {
  const openBy = radio('openBy'), closeBy = radio('closeBy');
  for (const [by, id] of [[openBy, '#openTime'], [closeBy, '#closeTime']]) {
    if (by === 'time' && !$(id).value) { toast(t('auto.timeMissing')); $(id).focus(); return; }
  }
  const body = {
    autoOpen: openBy === 'sun' ? 1 : 0, autoOpenTime: $('#openTime').value || status.autoOpenTime,
    autoClose: closeBy === 'sun' ? 1 : 0, autoCloseTime: $('#closeTime').value || status.autoCloseTime,
    maintainLight: $('#maintain').checked ? 1 : 0, targetLightLevel: +$('#target').value,
  };
  setBar('auto', 'saving');
  try {
    await api('PUT', '/auto', body);
    Object.assign(status, body);
    autoDirty = false;
    setBar('auto', 'saved');
    render();
  } catch (e) {
    setBar('auto', 'dirty');
    toast(t('toast.error', { msg: e.message }));
  }
});

for (const id of ['thrOpen', 'thrClose']) {
  $('#' + id).addEventListener('input', () => {
    let o = +$('#thrOpen').value, c = +$('#thrClose').value, clamped = false;
    if (c >= o) {                       // the spec needs close < open: hold the slider being moved at the limit
      clamped = true;
      if (id === 'thrOpen') $('#thrOpen').value = o = Math.min(100, c + 1);
      else $('#thrClose').value = c = Math.max(0, o - 1);
    }
    $('#sunErr').textContent = clamped ? t('sun.err') : '';
    setDirty = true;
    syncSetUI();
    setBar('set', 'dirty');
  });
}
$('#setDiscard').addEventListener('click', () => { setDirty = false; $('#sunErr').textContent = ''; render(); setBar('set', 'hidden'); });
$('#setSave').addEventListener('click', async () => {
  const body = { outsideThresholdOpen: +$('#thrOpen').value, outsideThresholdClose: +$('#thrClose').value };
  setBar('set', 'saving');
  try {
    await api('PUT', '/settings', body);
    Object.assign(status, body);
    setDirty = false;
    $('#sunErr').textContent = '';
    setBar('set', 'saved');
    render();
  } catch (e) {
    setBar('set', 'dirty');
    toast(t('toast.error', { msg: e.message }));
  }
});

// ---------------------------------------------------------------- name, language, install tip
$('#nameInput').value = store.get('name', '');
$('#nameInput').addEventListener('input', e => { store.set('name', e.target.value.trim()); renderName(); });
$('#nameInput').addEventListener('keydown', e => { if (e.key === 'Enter') e.target.blur(); });
for (const r of $$('input[name=lang]')) {
  r.addEventListener('change', () => { if (r.checked) { lang = r.value; store.set('lang', lang); applyI18n(); } });
}

const isIOS = /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const standalone = navigator.standalone === true || matchMedia('(display-mode: standalone)').matches;
$('#installTip').hidden = !isIOS || standalone || store.get('installDismissed', false);
$('#installGroup').hidden = standalone;
$('#installClose').addEventListener('click', () => { $('#installTip').hidden = true; store.set('installDismissed', true); });

function applyI18n() {
  document.documentElement.lang = lang;
  for (const el of $$('[data-i18n]')) el.textContent = t(el.dataset.i18n);
  for (const el of $$('[data-i18n-aria]')) el.setAttribute('aria-label', t(el.dataset.i18nAria));
  $('#nameInput').placeholder = t('name.placeholder');
  $('#posRange').setAttribute('aria-label', t('pos.range'));
  for (const [p, k] of [[0, 'openAria'], [50, 'halfAria'], [100, 'closedAria']]) $(`.preset[data-p="${p}"]`).setAttribute('aria-label', t('preset.' + k));
  setRadio('lang', lang);
  for (const w of Object.keys(bars)) if (bars[w].el.dataset.state !== 'hidden') setBar(w, bars[w].el.dataset.state);
  render();
  renderConn();
}

// ---------------------------------------------------------------- feedback helpers
let toastTimer;
function toast(msg, actionLabel, onAction) {
  $('#toastText').textContent = msg;
  const b = $('#toastAction');
  b.hidden = !actionLabel;
  if (actionLabel) { b.textContent = actionLabel; b.onclick = () => { hideToast(); onAction(); }; }
  $('#toast').dataset.state = 'shown';
  announce(msg);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(hideToast, actionLabel ? 7000 : 5000);
}
function hideToast() { $('#toast').dataset.state = 'hidden'; }
function announce(msg) {
  const el = $('#announce');
  el.textContent = '';
  setTimeout(() => { el.textContent = msg; }, 60);
}
// Haptic tick: vibrate() where it exists; on iPhone (Safari 18+) toggling a hidden native switch gives a tick.
const hapticLabel = (() => {
  const l = document.createElement('label'), i = document.createElement('input');
  i.type = 'checkbox'; i.setAttribute('switch', ''); i.tabIndex = -1;
  l.setAttribute('aria-hidden', 'true'); l.style.display = 'none';
  l.append(i); document.body.append(l);
  return l;
})();
function haptic() {
  if (navigator.vibrate) navigator.vibrate(8);
  else if (isIOS) hapticLabel.click();
}

// ---------------------------------------------------------------- start
for (const id of ['target', 'thrOpen', 'thrClose']) $('#' + id).classList.add('r');
applyI18n();
renderSky();
route();
if (token) refresh();
