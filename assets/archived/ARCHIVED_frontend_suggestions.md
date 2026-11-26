# Frontend Suggestions (`note-red-export/flows (2).json`)

Recommendations for the Node-RED UI team based on comparing [`note-red-export/flows (2).json`](../note-red-export/flows%20(2).json) against LoRaWAN Protocol v3/v4 and the backend's 10 downlinks/device/day TTN budget.

---

### 1. Explicit "Send Command" Button for Manual Moves (Prevents Accidental Downlinks & Cuts Automatic → Manual Override to 1 Downlink)
- **Current behavior:** In `Home page (Figma curtain)`, `onUp()` and `onKey()` fire `POST /position` immediately on handle release (risking accidental downlinks from mistaps or mid-thought re-adjustments), and `onDown()` / `onKey()` block dragging when `st.mode !== "manual"` (forcing `PUT /mode {"mode": "manual"}` → **1 downlink**, followed by `POST /position` → **1 downlink** = **2 downlinks total**).
- **Proposed UI behavior (mirroring the Automatic section's "Save changes" pattern):**
  - Allow dragging/nudging the curtain handle freely (even when currently in Automatic mode) to stage a target `%` locally without sending any LoRaWAN command yet.
  - Add an explicit **"Send command"** button in the manual controls (disabled when no target move is staged, or enabled whenever the staged `%` should be applied, and showing visual pending/dirty feedback until confirmed).
  - Pressing **"Send command"** sends a single `POST /position {"position": N}` request that explicitly serves as both **"switch to manual mode"** and **"move to position `N`"**.
  - *(Optional bonus saving: if tapping the **Manual** pill switches only the UI view locally until **"Send command"** is pressed—or keeps switching mode only via an explicit action when moving isn't desired—most manual overrides won't ever emit a separate `PUT /mode` downlink.)*
- **Matching backend & firmware behavior (1 `type 1` downlink):**
  - Per `Smart_Curtains_LoRaWAN_Protocol_v3.pdf`, downlink `type 1 (set_position)` (`[id] 01 [pos]`) already moves the curtain to `pos` **and** switches the device to `manual` mode on arrival (*"Go to pos % closed. Switches to manual."*).
  - Once the frontend adopts this **"Send command"** workflow, relax `POST /position`'s `409 "curtain is in automatic mode"` guard in the backend so `POST /position` updates `desired_mode = "manual"`, supersedes any queued `set_mode` / `set_schedule`, and enqueues **only 1 `type 1 (set_position)` downlink** (keeping the `409 "curtain is not calibrated"` check).
  - **Net result:** Zero accidental downlinks from mistaps or handle adjustments, and `Automatic → Manual move` drops from **2 downlinks → 1 downlink** with zero firmware changes.

### 2. Display Rolling 24h Downlink Budget Counter & Soft Warning Badge
- **Backend support (additive fields on `GET /status`):** The backend now tracks every MQTT downlink publish in an append-only `downlink_transmissions` table using a **rolling 24-hour window** and returns three additive fields on `GET /status`:
  - `downlinksUsed24h` (`int`): downlinks published in the last 24 hours.
  - `downlinkBudget24h` (`int`): TTN Fair Use reference budget (`10`).
  - `downlinkNextSlotAt` (`string | null`): UTC ISO-8601 (`Z`) timestamp when the oldest downlink in the current 24h window expires (`oldest_sent_at + 24h`), or `null` when `downlinksUsed24h === 0`.
- **Suggested UI presentation:**
  - Show a compact counter badge on the Home card (and/or Settings card), e.g., **`Downlinks (24h): 3 / 10`**, updated automatically by the existing 5 s `GET /status` poll (pairs naturally with Suggestion #1 **"Send command"** and **"Save changes"** so users see the counter advance by `1` per intentional command).
  - Apply **soft visual severity only**:
    - `< 8`: neutral / subtle muted style
    - `8–9`: amber warning (*"Approaching 10/24h TTN fair-use limit"*)
    - `≥ 10`: red advisory displaying `downlinkNextSlotAt` converted to local time (e.g., *"10/10 fair-use budget reached — next slot frees around 14:20"*)
- **Do NOT hard-block UI buttons:** The backend intentionally never blocks with HTTP `429` — this counter measures MQTT `down/replace` publishes as a close estimate rather than exact gateway air transmissions (queues replaced while offline don't transmit over air, while `confirmed: true` MAC retries happen inside TTN). Keeping controls enabled ensures live demos and urgent manual overrides are never locked out.

### 3. Clear `st.pending` When `POST /position` Fails (`400` / `409`)
- **Current behavior:** `sendPosition()` sets `st.pending = st.target` before sending, and `onMsg` displays `home.moving` (*"Moving to {target} % …"*) whenever `st.pending !== null`. If `POST /position` fails (e.g., `409 "curtain is not calibrated"`), the error shows briefly, and the next 5 s `GET /status` poll clears/replaces the message using the stale `st.pending`.
- **Suggestion:** In `Home page`'s `onMsg`, when `msg.topic === "result" && msg.payload && !msg.payload.ok && msg.payload.for === "position"`, reset `st.pending = null` and restore `st.target = st.actual ?? st.target`.

### 4. Clean Up Node-RED Export Hygiene
- **Remove orphaned `remote-config` node (`KOLT box` / `nodered04.remote-red.com`):** Unreferenced by any node, and causes Node-RED to refuse starting flows if `node-red-contrib-remote` isn't installed (`deploy/nodered/Dockerfile` currently strips it at build time).
- **Remove the 5 unused `mqtt-broker` nodes** (`localhost`, `maqiatto.com`, etc.).
- **Update `ui_base.site.name`:** Rename from `"Concert Buddies"` to `"Smart Curtain"`.
- **Use container environment variable for `CURTAIN_API_URL`:** Remove the hardcoded tab-level `env: [{name: "CURTAIN_API_URL", value: "http://localhost:1880/mock"}]` (or leave it pointing to `env.get("CURTAIN_API_URL")` without shadowing container env vars in Docker/GCP).

### 5. Add an Optional "Sync Status" Button (`POST /sync`)
- **Current behavior:** `setMode(mode)` guards with `if (st.mode === mode) return;`, so tapping **Manual** on a fresh backend DB (where `is_calibrated` starts `false`) sends nothing.
- **Suggestion:** Add a small refresh/sync button on the Home or Devices card calling `POST /sync` (no body, Bearer auth) to request a 1-downlink `FPort 1` state update without toggling modes.

### 6. Optional Outdoor Light Telemetry Chart (`GET /telemetry?hours=24`)
- The backend already stores optional `FPort 2` outside light readings (`0–1024` raw ADC, 5-minute intervals) and exposes `GET /telemetry?hours=24`. Adding a lightweight chart card on Home or Settings would surface that data if enabled by firmware.

### 7. Align `Mock auto settings` Tab with Strict 6-Field `PUT /auto`
- `saveAuto()` in `flows (2).json` already sends all 6 fields correctly, while the built-in `Mock auto settings` function node still accepts partial bodies. Requiring all 6 fields in the mock endpoint ensures local mock testing catches partial-payload regressions early.
