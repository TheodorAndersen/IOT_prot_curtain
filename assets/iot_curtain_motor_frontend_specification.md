# IoT Automatic Curtain Motor — React Frontend, Mobile App & Debug Page Specification

> **Document Alignment:**
> 1. **Wire Protocol (`assets/PROTOCOL_FINAL.md`, 22 Nov 2026):** LoRaWAN Protocol **v4** (`% closed`: `0 = fully open`, `100 = fully closed`, `255 / null = uncalibrated`), TTN fair-use limit of **10 downlinks/device/day**, Class C `25 s` reply timeout (`75 s` in Class A fallback), and **2 uplinks per move** (move start + move stop).
> 2. **Backend REST API (`assets/iot_curtain_motor_backend_specification.md`):** Talks exclusively to the FastAPI backend (`/health`, `/login`, `/status`, `/position`, `/mode`, `/auto`, `/settings`, `/sync`, `/debug`), served behind Nginx at `/api/*` on Web/PWA or configured `VITE_API_BASE_URL` on Mobile.
> 3. **Frontend Ownership:** We now own the frontend completely. Legacy Node-RED exports (previously in `note-red-export_old_ui/`, now deleted) are retired and replaced by a single **React + TypeScript** codebase delivering a responsive **Web App**, installable **PWA**, and native **Mobile App (iOS & Android via Capacitor)**, complete with a dedicated **Debug & Diagnostics Page**.

---

## 0. Inventory & Audit of Reference Folders (`FrontEnd/`, `assets/archived/` & `firmware/`)

| Folder / Archive | What It Contains | Reuse / Design Value | Known Gaps / Status |
|---|---|---|---|
| `assets/archived/ARCHIVED_frontend_app/` *(archived from `FrontEnd/app/`)* | Hardware team's standalone vanilla HTML/CSS/JS iOS-first PWA (`ARCHIVED_index.html`, `ARCHIVED_style.css`, `ARCHIVED_app.js`, `ARCHIVED_md5.js`, `ARCHIVED_manifest.webmanifest`, `ARCHIVED_icons/`) | **Primary visual & accessibility baseline:** polished window drawing with time-of-day sky (`day`/`dusk`/`night`), light/dark CSS tokens (`#f2a541` sun amber), Dynamic Type, invisible `<input type="range">` VoiceOver overlay, haptics, sticky save bars, DA/EN i18n. | **Archived.** 1. Immediately sends `POST /position` on pointer release / preset click / 700 ms slider stop (wastes TTN's 10 DL/day budget on mistaps).<br>2. Breaks on `position: null` (uncalibrated).<br>3. No 24h downlink budget badge.<br>4. No Debug page or `POST /sync`.<br>5. Persists raw `{username, md5}` in `localStorage`. |
| `FrontEnd/local_test/` | Python bench test server (`server.py`) + side-by-side developer UI (`ui/`) | **Primary UX reference for our Debug Page:** Device card, LoRaWAN/TTN card, active/last command readout with round-trip ms, and chronological `DL` / `UL` wire log with monospace hex bytes. | Kept for local USB bench testing only (`server.py` implements 19 Nov **v3**, `cfg <= 7`, and USB `#L` sensor lines not sent over LoRaWAN; its optional `/app/` route referenced `FrontEnd/app/`). |
| `assets/archived/ARCHIVED_ui_spec/` *(archived from `FrontEnd/ui_spec/`)* | Original 19 Nov REST spec (`ARCHIVED_communication_spec.md`) + Danish mockups (`ARCHIVED_screen_manual.jpeg`, `ARCHIVED_screen_automatic.jpeg`) | Confirms canonical field formats (`0 = open`, `100 = closed`, `0/1` strict ints, `"HH:MM"` Danish local time, MD5 login hash). | **Archived.** Reflects pre-v4 behavior (`POST /position` only in manual mode; no `/debug`, `/sync`, or budget fields). |
| `assets/archived/ARCHIVED_frontend_suggestions.md` | Pre-React suggestions written for the retired Node-RED UI | Key behavioral recommendations (#1 staged manual send, #2 24h budget pill, #3 error state clearing, #5 `POST /sync`) are directly incorporated into Sections 4–5 of this spec. | **Archived.** Node-RED hygiene items (#4, #7) and optional `FPort 2` chart (#6, excluded by `PROTOCOL_FINAL.md`) are superseded. |
| `note-red-export_old_ui/` & `deploy/nodered/` | Legacy Node-RED flows and container build | Historical reference superseded by this React spec. | **Deleted.** |
| `firmware/smart_curtain/` *(renamed from `smart_curtain_newer_maybe/`)* | Group 7 Arduino Pro Mini + RN2483 sketch (`smart_curtain.ino`) | Authoritative reference for physical behavior (button overrides, 5-min sensor tick, ±5 % deadband, 25 % max step, EEPROM state, `lastId` persistence). | **Critical Version Check:** Checked-in `.ino` file is the **19 Nov v3 build** (`# Smart Curtains fw v3`, rejects `cfg > 7`). Ensure hardware runs the 22 Nov v4 build before enabling `PROTOCOL_VERSION=v4` on the backend. |
| `firmware/08_Curtain_Controller_older/` | Obsolete v1 prototype (`08_Curtain_Controller.ino`, `Radio.ino`) | Incompatible port 2 protocol, inverted `0 = closed` scale, Class A only. | **Deleted.** |

---

## 1. Technical Architecture (Web + Mobile App From One React Codebase)

### Core Stack
* **Framework & Language:** **React 19** + **TypeScript 5** (strict mode) + **Vite 6**.
* **Routing:** **React Router v7** (`BrowserRouter` on Web/PWA; `HashRouter` fallback supported seamlessly if packaged in offline file contexts, though Capacitor uses `http://localhost` / `capacitor://localhost` so `BrowserRouter` works cleanly across all targets).
* **Server State & Adaptive Polling:** **TanStack Query (`@tanstack/react-query` v5)** — handles caching, background visibility pausing, focus refetching, adaptive `refetchInterval`, and optimistic/pending UI states.
* **Icons:** **`@phosphor-icons/react`** (MIT license, matching the Phosphor icon family already used in `assets/archived/ARCHIVED_frontend_app/`).
* **Styling:** **CSS Custom Properties + CSS Modules** (zero heavy runtime CSS-in-JS; preserves exact pixel control over the animated `<CurtainWindow />`, safe-area insets, Dynamic Type, `prefers-contrast`, and `prefers-reduced-motion`).

### Single-Codebase Delivery Targets (Web + PWA + Native Mobile App)

| Target | How It Is Built & Served | Platform Behavior |
|---|---|---|
| **1. Web App (Desktop & Mobile Browser)** | `npm run build` → static `frontend/dist/` served by **Nginx** at `/` (`https://<domain>/` or `http://127.0.0.1:8080/`). | Talks to same-origin `/api/*` (proxied by Nginx to `backend:8000/*`), avoiding CORS preflight overhead on Web. |
| **2. Installable PWA (iOS Safari / Android Chrome)** | `vite-plugin-pwa` generates `manifest.webmanifest` (`name: "Smart Curtain"`, `short_name: "Curtain"`, `display: "standalone"`) and service worker (`sw.js`), reusing `assets/archived/ARCHIVED_frontend_app/ARCHIVED_icons/`. | Precaches static JS/CSS/icons only; **NetworkOnly for `/api/*`** so curtain state and auth responses are never served stale from a SW cache. |
| **3. Native Mobile App (iOS & Android)** | **Capacitor 7** (`@capacitor/core`, `@capacitor/ios`, `@capacitor/android`) wraps `frontend/dist/` directly into native Xcode and Android Studio projects. | Uses `@capacitor/preferences` for token/settings persistence, `@capacitor/haptics` for native Taptic Engine / Android vibration, `@capacitor/status-bar`, and `@capacitor/network` for instant offline detection. Defaults API base URL to `VITE_API_BASE_URL` (e.g., `https://curtain.example.com/api`) with a runtime override on Login/Settings. |

> **Why React + Capacitor over React Native:** Both our Web dashboard and Mobile app share the exact same custom SVG/CSS `<CurtainWindow />`, responsive layouts, DA/EN i18n dictionary, and Debug diagnostics table. Capacitor lets one TypeScript/React codebase ship to browsers, iOS Home Screen PWA, iOS App Store/TestFlight, and Android APK with native haptics and storage—without maintaining two UI implementations.

### Recommended `frontend/` Directory Layout

```text
frontend/
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts                  # Vite + React + vite-plugin-pwa + /api dev proxy
├── capacitor.config.ts             # App ID, webDir: "dist", iOS/Android config
├── public/
│   └── icons/                      # Copied from assets/archived/ARCHIVED_frontend_app/ARCHIVED_icons/ (remove ARCHIVED_ prefix: 192, 512, maskable, apple-touch, favicon)
└── src/
    ├── main.tsx
    ├── App.tsx                     # Router, QueryClientProvider, AuthGuard, AppShell
    ├── types/
    │   └── api.ts                  # Exact TypeScript interfaces for REST & GET /debug
    ├── api/
    │   ├── client.ts               # Fetch wrapper, timeout (8 s), Bearer injection, 401 handler
    │   └── endpoints.ts            # login, getStatus, postPosition, putMode, putAuto, putSettings, postSync, getDebug, getHealth
    ├── hooks/
    │   ├── useStatusPolling.ts     # Adaptive 5 s / 1.5 s GET /status polling
    │   ├── useDebugPolling.ts      # Scoped GET /debug polling (2 s / 5 s / paused)
    │   ├── useHaptics.ts           # Capacitor Haptics -> navigator.vibrate -> iOS switch fallback
    │   └── useSkyPhase.ts          # Time-of-day sky phase ('day' | 'dusk' | 'night')
    ├── i18n/
    │   ├── index.ts                # React Context + t(key, vars) + pct() formatter
    │   ├── da.ts                   # Danish translations
    │   └── en.ts                   # English translations
    ├── utils/
    │   ├── md5.ts                  # RFC 1321 MD5 digest (lowercase 32-char hex)
    │   ├── storage.ts              # Unified async/sync storage adapter (Preferences + localStorage)
    │   └── formatters.ts           # Relative time ("18 s ago"), local clock conversion, flag decoder
    ├── styles/
    │   ├── tokens.css              # Light, dark, high-contrast & semantic debug tokens
    │   └── global.css              # Reset, safe-area layout, accessibility utilities
    ├── components/
    │   ├── AppShell.tsx            # Top bar (desktop), bottom nav (mobile), offline banner
    │   ├── CurtainWindow.tsx       # Interactive roller blind + ghost line + sky + range overlay
    │   ├── BudgetBadge.tsx         # Soft rolling 24h downlink counter pill (X / 10)
    │   ├── SaveBar.tsx             # Sticky bottom confirm/save/discard bar
    │   ├── Toast.tsx               # Accessible aria-live toast with optional Undo action
    │   └── StatusPill.tsx          # Semantic badge (CONFIRMED, IN_FLIGHT, REJECTED, NOT_DELIVERED)
    └── features/
        ├── auth/LoginPage.tsx
        ├── control/
        │   ├── HomePage.tsx
        │   ├── PositionControls.tsx
        │   └── AutoRulesPanel.tsx
        ├── settings/SettingsPage.tsx
        └── debug/
            ├── DebugPage.tsx
            ├── FlagsInspector.tsx
            ├── CommandCard.tsx
            ├── UplinkCard.tsx
            └── WireLogTable.tsx
```

---

## 2. Design System, Responsive Layout, Accessibility & Localization

### Design Tokens (`src/styles/tokens.css`, Ported & Extended from `assets/archived/ARCHIVED_frontend_app/ARCHIVED_style.css`)
* **Light Mode:** `--bg: #f2f3f5`, `--surface: #fcfcfd`, `--surface-2: #e9ebee`, `--text: #16181d`, `--text-2: #555a66`, `--line: #dfe2e7`, `--line-strong: #c3c8d0`, `--accent: #f2a541`, `--on-accent: #16181d`, `--accent-ink: #8f5200` (5.9:1 contrast on light surfaces), `--danger: #b4261f`.
* **Dark Mode (`prefers-color-scheme: dark` or explicit theme override):** `--bg: #0d0e11`, `--surface: #17191d`, `--surface-2: #24272d`, `--text: #f1f2f4`, `--text-2: #a6abb6`, `--line: #2b2e35`, `--accent-ink: #f5b65e`, `--danger: #ff7b72`.
* **Window & Blind Tokens:** `--frame`, `--sill`, `--roller`, `--rail`, `--fabric`, `--weave`, `--frame-w: 10px`, `--sill-h: 22px`, `--roller-h: 18px`, `--rail-h: 12px`.
* **Semantic Debug Tokens (Light / Dark):**
  * `--ok: #1b7f4b` / `#4ade80` (`CONFIRMED`, connected, in-sync)
  * `--warn: #9a5b00` / `#fbbf24` (`IN_FLIGHT`, approaching budget `8–9/10`, out-of-sync)
  * `--danger: #b4261f` / `#ff7b72` (`REJECTED`, `NOT_DELIVERED`, disconnected, budget `>= 10/10`)
  * `--dl-tint` (amber/blue badge for Downlinks) and `--ul-tint` (emerald/teal badge for Uplinks)
  * `--font-mono`: `ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace` with `font-variant-numeric: tabular-nums` for hex bytes, timestamps, and counters.
* **Shape Language:** Buttons, segmented controls, and time pills use `border-radius: 999px`; cards and preset tiles use `18px`; the `<CurtainWindow />` uses `26px`.

### Responsive Breakpoints
* **Mobile Portrait (`< 768px`):** Single-column layout (`max-width: 480px` centered), iOS/Android safe-area insets (`env(safe-area-inset-*)`), sticky bottom navigation bar (`Control`, `Settings`, `Debug`) plus sticky bottom `<SaveBar />` sliding cleanly above the nav bar when dirty/staged.
* **Mobile Landscape (`orientation: landscape` and `max-height: 500px`):** Two-column grid placing `<CurtainWindow />` on the left and presets/mode/rules on the right.
* **Tablet & Desktop (`>= 768px`):** Centered workspace (`max-width: 1080px`) with persistent top navigation header (`Control`, `Settings`, `Debug`, live transport dot, sign-out):
  * Home (`/`) uses a 2-column split: left column = readout, `<CurtainWindow />`, presets & position confirm controls; right column = Mode toggle & Automatic rules panel.
  * Debug (`/debug`) uses a 2-to-3-column grid for System/Budget, Device State + Flags Inspector, Last Command Sent / In-Flight, Last Uplink Received, and a full-width Unified Wire Log table underneath.

### Non-Negotiable WCAG AA Accessibility Checklist
* All interactive controls have minimum **`44 × 44 px`** touch targets (`52–54 px` on primary actions & drag handle).
* `<CurtainWindow />` pairs pointer drag with an invisible full-overlay `<input type="range" min={0} max={100} step={5}>` (`aria-label`, `value = 100 - position`, `aria-valuetext="60% open"`) so VoiceOver, TalkBack, and keyboard arrow users have full parity.
* `aria-live="polite"` region announces position arrival, command failure, and save confirmations.
* Respects `@media (prefers-reduced-motion: reduce)` and `@media (prefers-contrast: more)`.
* Follows iOS Dynamic Type (`font: -apple-system-body` when supported).
* Never relies on color alone for states, sky phase, or budget warnings (every badge pairs icon + text + numeric values).
* **Cross-Platform Haptics (`useHaptics`):** Tries `@capacitor/haptics` (`ImpactStyle.Light`) on native mobile → `navigator.vibrate(8)` on Android Web → hidden `<input type="checkbox" switch>` click on iOS Safari 18+.

### Localization (`da` / `en`)
* Auto-detects `navigator.language.startsWith('da') ? 'da' : 'en'` on first launch; persisted in storage when changed in Settings.
* Covers all UI copy: Login, Home/Control, Uncalibrated guide, Staged Position Bar, Downlink Budget warnings (`X / 10`), Settings, and Debug Page labels (technical protocol constants like `FPort 1`, `CONFIRMED`, `app_command`, and hex payloads stay untranslated in monospace for engineering clarity).

---

## 3. Backend API Integration, Authentication & Adaptive Polling

### TypeScript API Contract (`src/types/api.ts`)

```typescript
export type CurtainMode = "manual" | "automatic";
export type CommandKind = "set_position" | "set_mode" | "set_auto_settings" | "set_schedule";
export type CommandStatus =
  | "QUEUED"
  | "IN_FLIGHT"
  | "CONFIRMED"
  | "REJECTED"
  | "NOT_DELIVERED"
  | "SUPERSEDED";

export interface HealthResponse {
  ok: boolean;
  transportConnected: boolean;
  protocolVersion: "v3" | "v4";
}

export interface StatusResponse {
  position: number | null; // 0 = open, 100 = closed, null = uncalibrated
  mode: CurtainMode;
  autoOpen: 0 | 1;
  autoOpenTime: string; // "HH:MM"
  autoClose: 0 | 1;
  autoCloseTime: string; // "HH:MM"
  maintainLight: 0 | 1;
  targetLightLevel: number;
  outsideThresholdOpen: number;
  outsideThresholdClose: number;
  moving: boolean;
  calibrated: boolean;
  day: boolean;
  targetPosition: number | null;
  lastSeenAt: string | null;
  downlinksUsed24h: number;
  downlinkBudget24h: number;
  downlinkNextSlotAt: string | null;
}

export interface DecodedFlags {
  automatic: boolean;
  moving: boolean;
  calibrated: boolean;
  day: boolean;
  openBySun: boolean;
  closeBySun: boolean;
  keepIndoorLight: boolean;
}

export interface DebugCommand {
  id: number;
  cmdId: number | null;
  kind: CommandKind;
  args: Record<string, unknown>;
  status: CommandStatus;
  attempts: number;
  wireFport: number | null;
  wirePayloadHex: string | null;
  description: string;
  createdAt: string;
  sentAt: string | null;
  resolvedAt: string | null;
  roundTripMs: number | null;
}

export interface DebugUplink {
  id: number;
  receivedAt: string;
  ageSec: number;
  lastCmdId: number;
  reason: "heartbeat" | "startup" | "button" | "app_command" | "auto" | "schedule" | "calibrated" | "rejected";
  mode: CurtainMode;
  flags: number;
  flagsHex: string;
  decodedFlags: DecodedFlags;
  position: number | null;
  rawFport: number;
  rawPayloadHex: string;
  description: string;
}

export interface DebugResponse {
  server: {
    serverTime: string;
    scheduleTimezone: string;
    localTime: string;
    transportMode: "ttn_mqtt" | "mock";
    transportConnected: boolean;
    protocolVersion: "v3" | "v4";
    confirmedDownlinks: boolean;
    handshakeTimeoutSec: number;
    handshakeMaxAttempts: number;
  };
  device: {
    ttnDeviceId: string;
    devEui: string;
    name: string;
    position: number | null;
    targetPosition: number | null;
    reportedMode: CurtainMode;
    desiredMode: CurtainMode;
    effectiveMode: CurtainMode;
    moving: boolean;
    calibrated: boolean;
    day: boolean;
    computedDayHint: boolean | null;
    flagsRaw: number;
    flagsHex: string;
    decodedFlags: DecodedFlags;
    lastReason: string | null;
    lastCmdIdAck: number;
    nextCmdId: number;
    lastSeenAt: string | null;
    lastSeenAgoSec: number | null;
    settingsInSync: boolean;
    savedSettings: {
      autoOpen: 0 | 1;
      autoOpenTime: string;
      autoClose: 0 | 1;
      autoCloseTime: string;
      maintainLight: 0 | 1;
      targetLightLevel: number;
      outsideThresholdOpen: number;
      outsideThresholdClose: number;
    };
    lastConfirmedAutoSettings: {
      autoOpen: 0 | 1;
      autoClose: 0 | 1;
      maintainLight: 0 | 1;
      targetLightLevel: number;
      outsideThresholdOpen: number;
      outsideThresholdClose: number;
    } | null;
  };
  budget: {
    downlinksUsed24h: number;
    downlinkBudget24h: number;
    downlinkNextSlotAt: string | null;
  };
  inFlightCommand: DebugCommand | null;
  queuedCommands: DebugCommand[];
  lastCommandSent: DebugCommand | null;
  lastUplinkReceived: DebugUplink | null;
  recentCommands: DebugCommand[];
  recentUplinks: DebugUplink[];
}
```

### Authentication & Runtime Base URL Resolution
* **Base URL Resolution (`getApiBaseUrl()`):**
  1. User-configured override in storage (`curtain.apiBaseUrl`), if set (useful on native mobile or multi-environment testing).
  2. Otherwise `import.meta.env.VITE_API_BASE_URL` if defined at build time.
  3. Otherwise `"/api"` on Web/PWA (same-origin Nginx proxy).
* **Login (`POST /login`):** Computes lowercase 32-char MD5 hex of the password in `src/utils/md5.ts`, sends `{"username", "password"}`, and persists **only the returned Bearer token** (never store the plaintext password or raw MD5 hash in `localStorage`).
* **Session Expiry (`401`):** Any `401` response on a protected endpoint clears the stored token, invalidates TanStack Query caches, and navigates cleanly to `/login` with a *"Please log in again"* prompt.

### Adaptive Polling Strategy
* **`GET /status` (`useStatusPolling`):**
  * `5000 ms` when idle.
  * `1500 ms` while `status?.moving === true` or a client-triggered command awaits arrival/mode confirmation.
  * `refetchIntervalInBackground: false` (pauses automatically when browser tab or mobile app is backgrounded; immediately refetches on foreground/focus and after any mutation).
* **`GET /debug?limit=20` (`useDebugPolling`):**
  * Active **only** while `/debug` is mounted.
  * User-selectable refresh rate: **`2000 ms` (default)**, `5000 ms`, or `Paused`, plus an immediate **Refresh now** button.
* **Offline / Stale Handling:** Network timeouts (`8 s` `AbortController`) or offline state (`navigator.onLine === false` / Capacitor Network listener) show a non-blocking offline banner displaying the last successful sync time (`"Last updated at 14:22"`) and a **Try again** button.

---

## 4. Detailed Screen Specifications

### 4.1 App Shell & Navigation
* **Public Route:** `/login` (redirects to `/` if authenticated).
* **Protected Routes:** `/` (Control), `/settings` (Settings), `/debug` (Debug & Diagnostics).
* **Chrome:**
  * **Desktop/Tablet (`>= 768px`):** Clean top header with brand icon, route tabs (`Control`, `Settings`, `Debug`), live transport status indicator (`Connected` / `Offline`), and current mode pill.
  * **Mobile (`< 768px`):** Compact page header + bottom tab bar with 3 items (`Control`, `Settings`, `Debug`), each with icon + label (`>= 44 px` height, `env(safe-area-inset-bottom)` padding). If an `IN_FLIGHT` command or moving state is active, the Debug tab shows a subtle amber dot.

### 4.2 Login Screen (`/login`)
* App icon (`72 × 72`), title, lead text, email field (`autocomplete="username"`), password field with accessible eye toggle (`aria-pressed`), inline error alert (`role="alert"`), and full-width submit button (`Log in` / `Logging in…`).
* **Collapsible "Server Connection" Accordion:** Collapsed on Web (`/api` default), expanded by default on native Capacitor builds if no server URL is configured yet. Lets users enter/test (`GET /health`) a custom backend URL (e.g., `https://curtain.example.com/api`).

### 4.3 Home / Control Screen (`/`)
1. **Top Header & Downlink Budget Pill:**
   * Displays locally customized blind name (default `"Roller blind"` / `"Rullegardin"`).
   * Prominent tabular-nums readout displaying **`% open`** (`100 - position`, keeping the REST API & LoRaWAN wire bytes at `% closed` per `PROTOCOL_FINAL.md` and `smart_curtain.ino`): **`Open`** (`100% open` / wire `0`), **`Closed`** (`0% open` / wire `100`), **`60 % open`** (wire `40`), or **`Not calibrated`** (`position === null`).
   * **Rolling 24h Downlink Budget Pill (`<BudgetBadge />`):** Reads `downlinksUsed24h / downlinkBudget24h` (`3 / 10`):
     * `< 8`: neutral muted pill (`3 / 10 downlinks`).
     * `8–9`: amber warning (*"Approaching 10/24h TTN fair-use limit"*).
     * `>= 10`: red soft advisory displaying `downlinkNextSlotAt` in local time (*"10/10 fair-use limit reached — next slot around 14:20"*).
     * **Never blocks buttons:** Soft advisory only per `PROTOCOL_FINAL.md` and backend rules.
   * **Plain-Language Status Line:** Shows current move target (`"Moving down to 60%..."`), arrival confirmation, error/timeout notice, automatic summary (`"Opens at 07:30, closes at sunset."`), or manual hint.
2. **Uncalibrated Guard Banner (When `calibrated === false || position === null`):**
   * Replaces generic error states with a clear amber card:
     * *"Curtain is not calibrated — remote moves are disabled."*
     * Physical calibration steps: *(1) Roll curtain fully down by hand → (2) Press physical **Up** → (3) Press **Stop** at the top.*
     * Helpful link: *"Already calibrated on the device? Open Debug and run **Force Sync**."*
   * *(Future extension note: see [`calibration_protocol_proposal.md`](calibration_protocol_proposal.md) for a proposed confirm-before-sending "Recalibrate Curtain" flow backed by `POST /calibrate` and LoRaWAN Opcode `5`, while keeping travel calibration stored in EEPROM.)*
   * Disables confirming `POST /position` (while allowing `PUT /mode`, `PUT /auto`, and Settings edits).
3. **`<CurtainWindow />` & Quick Presets:**
   * Renders the sky (`day` / `dusk` / `night`), sun/moon/stars, window mullions, roller, woven fabric (`--p`), bottom rail, draggable circular handle, and invisible `<input type="range">` accessibility overlay.
   * Pointer interactions require a `6 px` vertical drag threshold before arming (preventing scroll/tap misfires) and gently snap within `±2 %` of `0`, `50`, and `100` with subtle haptic ticks.
   * 3 quick-preset tiles below the window: **`Open`** (`100% open` / wire `0`), **`Halfway`** (`50%`), **`Closed`** (`0% open` / wire `100`).
4. **Downlink-Safe Confirm-Before-Sending Workflow (Default UX):**
   * Because LoRaWAN allows **10 downlinks/device/day**, **every downlink-generating control action** in the Control screen (`/`) and Settings (`/settings`) stages changes locally behind the sticky bottom `<SaveBar />` instead of firing immediately:
     * **Position moves (`POST /position`):** Dragging the handle, moving the accessible slider, or tapping a preset tile stages wire `stagedPos` locally while the solid blind tracks `stagedPos` and the dashed **ghost line (`--a`)** stays at `status.position` (`"Now (100 - X)%"`).
       * In `manual` mode: `<SaveBar />` shows **`Target: (100 - Y)% open`** · **`Cancel`** · **`Move to (100 - Y)%`**
       * In `automatic` mode: `<SaveBar />` shows **`Switch to Manual & move to (100 - Y)% open`** · **`Cancel`** · **`Confirm Move`** (one `POST /position { position: Y }`, which per v4 `PROTOCOL_FINAL.md` both switches to `manual` and moves in **1 Type 1 downlink**).
     * **Mode switching (`PUT /mode`):** Tapping **`Manual`** or **`Automatic`** stages `stagedMode` locally and **never** calls `PUT /mode` until confirmed in `<SaveBar />` (**`Switch to Automatic mode?`** / **`Switch to Manual mode?`**). When switching `Manual → Automatic` is staged, `<AutoRulesPanel />` opens immediately in draft preview so the user can review or adjust rules and thresholds (or tap **`Manual`** / **`Cancel`** with **zero downlinks**) before sending.
     * **Automatic rules (`PUT /auto`) & inline outdoor thresholds (`PUT /settings`):** Editing `autoOpen`, `autoOpenTime`, `autoClose`, `autoCloseTime`, `maintainLight`, `targetLightLevel`, or the inline sunrise/sunset outdoor thresholds (`outsideThresholdOpen` / `outsideThresholdClose`) sets `autoDirty` / `thrDirty` locally and requires explicit **`Confirm & Send`** (or **`Discard`**) in `<SaveBar />`.
     * **Minimal-downlink commit order** when confirming combined mode + rule/threshold changes:
       * `manual → automatic`: `PUT /auto` (if `autoDirty`) → `PUT /settings` (if `thrDirty`) → `PUT /mode {"mode": "automatic"}` (so all backend settings are saved before enabling automatic mode, coalescing into **1 Type 3 downlink** under v4).
       * `automatic → manual`: `PUT /mode {"mode": "manual"}` → `PUT /auto` (if `autoDirty`) → `PUT /settings` (if `thrDirty`) (avoids an unnecessary extra automatic config sync before leaving automatic mode).
       * Same mode: `PUT /auto` (if `autoDirty`) and/or `PUT /settings` (if `thrDirty`).
   * *(Optional power-user preference in Settings: **"Instant for moves & mode"**, `OFF` by default, bypasses confirmation only for position moves and pure mode switches when no rule/threshold edits are pending; automatic rules and thresholds always confirm before sending.)*
5. **LoRaWAN-Aware Movement Tracking & External Override Undo:**
   * Over LoRaWAN, the curtain only sends uplinks **when moving starts** (`moving = true`, `position` unchanged) and **when it stops** (`moving = false`, new `position`). The UI **must not** fake 1 Hz intermediate percentages; instead it displays `ghost` at `status.position`, solid fabric at `targetPosition`, and a subtle pulsing status (`"Moving to 40% (currently 0%)..."`) driven by `status.moving` and `status.targetPosition`.
   * If `status.moving` stays `false` past the `25 s` command window without position progress (or `targetPosition` clears while `Math.abs(position - target) > 1`), display *"Did not reach Y%. Blind is at X%."*
   * If `status.mode` flips from `'automatic'` to `'manual'` without a recent app action (e.g., someone pressed physical `Up`/`Down`/`Stop` at the window), show a toast: *"Blind switched to Manual (window button pressed)"* with a **`Turn back on`** action (under confirm-before-sending, tapping it stages `Automatic` in `<SaveBar />` for confirmation rather than firing an unconfirmed downlink).
6. **Mode Selector & Automatic Rules Panel:**
   * Large segmented pill control: **`Manual`** / **`Automatic`** (stages `stagedMode`; confirmation commits `PUT /mode`).
   * Displayed whenever the active **or staged** mode is `Automatic`, showing three clean cards matching `assets/archived/ARCHIVED_frontend_app/` & `assets/archived/ARCHIVED_ui_spec/ARCHIVED_screen_automatic.jpeg`:
     * **Open in the morning:** `At a set time` (`<input type="time">`) vs `At sunrise` (inline **`Open when outside light is above`** `1–100%` threshold slider when selected).
     * **Close in the evening:** `At a set time` (`<input type="time">`) vs `At sunset` (inline **`Close when outside light is below`** `0–99%` threshold slider when selected, live-clamped so `outsideThresholdClose < outsideThresholdOpen`).
     * **Light indoors:** `Keep a steady light level` switch + `targetLightLevel` slider (`0–100%`) + explanatory note (*"Active between the day's opening and closing; checks every 5 minutes"*).
   * **Dirty-State & Poll Isolation:** `stagedMode`, `autoDirty`, and `thrDirty` prevent 5-second background `GET /status` polls from overwriting staged or unsaved inputs until confirmed or cancelled/discarded in the unified `<SaveBar />`.

### 4.4 Settings Screen (`/settings`)
1. **Outdoor Sunlight Thresholds (`PUT /settings`):**
   * Two sliders: **`Open when outside light is above`** (`outsideThresholdOpen`, `0–100%`) and **`Close when outside light is below`** (`outsideThresholdClose`, `0–100%`).
   * Live slider clamping enforces `outsideThresholdClose < outsideThresholdOpen` (`thrClose <= thrOpen - 1`) with inline validation text if clamped, plus sticky **`Confirm & Send` / `Discard`** bar (these same clamped sliders are also exposed inline inside `<AutoRulesPanel />` on `/`).
2. **Local App Preferences (Stored on Device Only):**
   * **Blind Name:** Text input (max 28 chars).
   * **Language:** `Dansk` / `English`.
   * **Appearance:** `System` / `Light` / `Dark`.
   * **Command Confirmation:** `Confirm before sending (saves downlinks)` (default) vs `Instant for moves & mode` (rules and thresholds always confirm before sending).
   * **Server Connection:** Displays current API base URL with edit modal (especially for Capacitor mobile builds).
3. **Install as App (PWA):** Displayed only when running in a mobile browser and not already in `display-mode: standalone` or Capacitor native.
4. **Sign Out:** Clears Bearer token and returns to `/login`.

### 4.5 Debug & Diagnostics Page (`/debug`)

Built specifically so developers and operators can verify live device state, protocol bytes, queue health, and the **last command sent / last uplink received** from either desktop browser or mobile app—without needing USB attached to the Pro Mini.

#### Top Controls Bar
* **Auto-Refresh Selector:** Segmented control **`2 s`** (default) | **`5 s`** | **`Paused`**.
* **`Refresh Now`:** Immediately refetches `GET /debug?limit=20`.
* **`Force State Sync` (`POST /sync`):** Opens a 1-step inline confirmation (*"Send 1 Type 2 downlink to request an immediate state uplink?"*) and enqueues `POST /sync` — essential when bringing up a fresh DB with an already-calibrated blind.
* **`Copy Diagnostics JSON`:** Copies the full `GET /debug` JSON snapshot to the clipboard for bug reports.

#### Card 1 — System, Transport & Downlink Budget Strip
* **Backend & Protocol:** `Protocol v4` (or `v3`), `Unconfirmed downlinks`, `Handshake 25 s × 2 tries`, Backend UTC time & Danish local time (`Europe/Copenhagen`).
* **Transport Status:** `TTN MQTT` or `Mock Simulator`, green **`Connected`** / red **`Disconnected`** badge.
* **Rolling 24h Budget Bar:** Visual progress meter (`downlinksUsed24h / downlinkBudget24h`), warning state at `>= 8` / `>= 10`, and formatted `downlinkNextSlotAt` countdown.

#### Card 2 — Live Curtain State, 7-Bit Flags Inspector & Settings Diff
* **Core Telemetry Table:**
  * `Position` (`60% open (wire: 40% closed)` or `null (uncal)`) · `Target Position` (`null` or `X% open (wire: (100 - X)% closed)`)
  * `Reported Mode` vs `Desired Mode` vs `Effective Mode` (highlights amber badge if `reportedMode !== desiredMode` during a pending transition)
  * `Moving` (`Yes` / `No`) · `Calibrated` (`Yes` / `No`)
  * `Day / Night` (`Day` / `Night`) + **`Clock Day Hint`** (`Day` / `Night` / `Unknown — sensor controlled`)
  * `Last Reason` (`app_command`, `auto`, `button`, `schedule`, `startup`, `heartbeat`, `calibrated`, `rejected`)
  * `Last Cmd ID Ack` (`#22`) · `Next Cmd ID` (`#23`)
  * `Last Seen` (ISO timestamp + live ticking relative age e.g. `18 s ago`, warning highlight if `> 65 min`)
  * `TTN Device ID` & `DevEUI` (`0004A30B00E8F83E`)
* **7-Bit Flags Byte Inspector:** Displays `flagsHex` (`0x24`), 8-bit binary (`00100100`), and 7 individual pill indicators (`bit 0 automatic`, `bit 1 moving`, `bit 2 calibrated`, `bit 3 day`, `bit 4 openBySun`, `bit 5 closeBySun`, `bit 6 keepIndoorLight`).
* **Settings Sync Diff (`settingsInSync`):** Side-by-side comparison of `savedSettings` vs `lastConfirmedAutoSettings` (highlighting mismatched fields in amber, or showing `"Never confirmed yet"` when `null`).

#### Card 3 — Last Command Sent, In-Flight & Queue (Prominent)
* **Active In-Flight Banner (shown whenever `inFlightCommand !== null`):**
  * Pulsing amber header: **`IN FLIGHT · Attempt 1 / 2`** with a live elapsed seconds counter vs `handshakeTimeoutSec` (`12 s / 25 s`).
  * Command `#cmdId`, `kind`, human-readable `description`, decoded `args`, and `wireFport` + uppercase hex (`16 01 28`).
* **Last Command Sent Summary (`lastCommandSent`):**
  * Prominent outcome pill: `CONFIRMED` (green), `REJECTED` (red), `NOT_DELIVERED` (red), `IN_FLIGHT` (amber), `SUPERSEDED` (muted).
  * `#cmdId`, `kind`, `attempts`, `sentAt`, `resolvedAt`, and **`roundTripMs`** (formatted `840 ms` or `2.1 s`).
  * Human-readable `description`, collapsible JSON `args`, and raw `FPort` + **uppercase monospace hex bytes** with a 1-tap **Copy Hex** button.
* **Queued Commands List (`queuedCommands`):** Shows FIFO waiting rows (`kind`, `args`, `createdAt`) or `"Queue empty"`.

#### Card 4 — Last Uplink Received (Prominent)
* Displays `lastUplinkReceived` (`receivedAt` + ticking `ageSec` relative time), `#lastCmdId`, `reason` pill, `mode`, `position`, `flagsHex`, decoded flag chips, human-readable `description`, and raw **`FPort` + uppercase monospace hex bytes** (`16 03 24 28`) with a **Copy Hex** button.

#### Card 5 — Unified Wire Traffic Log (`DL` / `UL`)
* Merges `recentCommands` (`DL`) and `recentUplinks` (`UL`) into a single reverse-chronological timeline (newest first).
* **Filter Tabs:** `All` · `Commands (DL)` · `Uplinks (UL)` · `Errors / Rejected`.
* **Columns / Mobile Card Rows:** Local `HH:MM:SS`, Direction badge (`DL` / `UL`), `#cmdId`, Status/Reason pill, Human-readable summary, Round-trip / age, and monospace uppercase hex payload.

> **USB Bench Companion Note:** When developers need live 1 Hz raw ADC readings (`#L`), virtual button presses (`BTN U/D/S`), shortened decision ticks (`TICK 10`), or calibration reset (`CAL`) directly over USB serial, they continue using the local bench panel in `FrontEnd/local_test/`. In deployed LoRaWAN mode, `/debug` provides full visibility over the network without USB.

---

## 5. Edge Cases & Error Handling Matrix

| Scenario | Frontend Behavior |
|---|---|
| `position === null` or `calibrated === false` | Render `"Not calibrated"`, display calibration banner on Home, disable position confirmation button; allow Mode/Auto/Settings & `/debug` `POST /sync`. |
| Unsaved / staged edits during 5 s poll | `stagedMode`, `autoDirty`, `thrDirty`, and `setDirty` flags prevent `GET /status` refreshes from overwriting controlled draft state until confirmed or cancelled/discarded. |
| `400 Bad Request` (`{"error": "..."}`) | Keep form dirty, highlight field if applicable, show concise server error message in toast/inline alert. |
| `401 Unauthorized` | Clear Bearer token from storage, cancel pending queries, redirect to `/login` with session-expired message. |
| `409 Conflict` (`curtain is not calibrated` / `device not found`) | Clear pending optimistic state, force immediate `GET /status` refresh, show error toast. |
| `downlinksUsed24h >= 10` | Show red budget pill with `downlinkNextSlotAt` local time; **do not disable controls**. |
| Network offline / 8 s request timeout | Show persistent non-blocking Offline banner with last update timestamp and **Try again** button; keep Debug controls read-only/graceful. |
| `NOT_DELIVERED` or `REJECTED` in Debug / Move | Surface clear failure reason (`Did not reach target` / `Device rejected command`) without leaving the curtain stuck in a phantom `"moving"` state. |

---

## 6. Build, Nginx / Docker Serving & Capacitor Mobile Packaging

### Development Proxy (`vite.config.ts`)
* Proxy `/api/*` → `http://127.0.0.1:8080` (or `http://127.0.0.1:8000` when running FastAPI directly) with `rewrite: p => p.replace(/^\/api/, '')`.
* Developers run `uvicorn main:app --reload` (with `TRANSPORT_MODE=mock`) + `npm run dev` on `http://localhost:5173`.

### Production Web & GCP Deployment (`nginx:1.31.9-alpine`)
* Multi-stage `frontend/Dockerfile` (`node:22-alpine` → `npm ci && npm run build` → copy `dist/` into Nginx image or shared volume).
* Nginx serves `/` with SPA fallback (`try_files $uri $uri/ /index.html;`) and proxies `/api/` → `http://backend:8000/`.
* Cache headers: `no-cache` on `index.html`, `manifest.webmanifest`, and `sw.js`; `public, max-age=31536000, immutable` on `/assets/*`.

### Mobile App Build (Capacitor iOS & Android)
* Install Capacitor dependencies: `@capacitor/core`, `@capacitor/cli`, `@capacitor/ios`, `@capacitor/android`, `@capacitor/haptics`, `@capacitor/preferences`, `@capacitor/status-bar`, `@capacitor/network`.
* Configure `VITE_API_BASE_URL=https://<your-domain>/api` when bundling for mobile:
  ```bash
  npm run build
  npx cap sync
  npx cap open ios      # Build / run on iPhone via Xcode
  npx cap open android  # Build APK / run on Android Studio
  ```
* **HTTPS & CORS Note:** Native iOS/Android WebViews forbid cleartext HTTP in release builds unless explicitly opted in; always point production mobile builds at the **HTTPS** Nginx endpoint (`https://<your-domain>/api`), which connects seamlessly via the backend's `CORSMiddleware`.

---

## 7. Testing & Verification Strategy

1. **Unit Tests (`Vitest` + `@testing-library/react`):**
   * `md5.ts`: verify `"curtain"` → `"2805ba35a46fd703543fb61625cd9393"`.
   * Flag decoder & hex formatter: verify `0x24` (`36`) decodes to `{ calibrated: true, closeBySun: true, ... }`.
   * Staged position workflow: dragging / clicking `Halfway` stages `50%` and shows `<SaveBar />` without calling `POST /position` until confirmed.
   * Staged mode switching: toggling `Manual ↔ Automatic` stages `stagedMode`, reveals `<AutoRulesPanel />` in draft preview, and calls `PUT /mode` only upon confirmation (toggling back or tapping `Cancel` sends 0 requests).
   * Combined automatic rules & inline threshold workflow: verifies live `outsideThresholdClose < outsideThresholdOpen` clamping, poll isolation, and minimal-downlink commit order (`PUT /auto` → `PUT /settings` → `PUT /mode` when confirming `Manual → Automatic` with edits).
   * Uncalibrated state: `position: null` renders `"Not calibrated"` and disables move submission.
   * Settings slider clamping: enforces `outsideThresholdClose < outsideThresholdOpen`.
   * Debug Page rendering: verifies `lastCommandSent`, `inFlightCommand`, `lastUplinkReceived`, and unified `DL`/`UL` log filtering render all fields accurately from mock `GET /debug` fixtures.
2. **End-to-End Tests (`Playwright` against FastAPI `TRANSPORT_MODE=mock`):**
   * Login with seeded demo credentials → verify Home renders `50% open` → stage `60% open` (wire `40`) → confirm → verify 1 `POST /position { position: 40 }` call updates status → navigate to `/debug` → verify `lastCommandSent` shows `#cmdId`, `set_position`, `CONFIRMED`, and `wirePayloadHex` ending in `0128`, and `lastUplinkReceived` shows `app_command` at `60% open (wire: 40% closed)`.
3. **Accessibility & Mobile Viewport Checks:**
   * Keyboard-only navigation across `/`, `/settings`, and `/debug`; VoiceOver/screen-reader slider attributes; `375 × 812` portrait and `812 × 375` landscape responsive verification.
