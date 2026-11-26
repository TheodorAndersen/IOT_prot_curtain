# Smart Curtain phone app

A web app that runs like a native app from the iPhone home screen. It controls the Group 7 roller blind through the UI team's REST API (`../ui_spec/communication_spec.md`): `/login`, `/status`, `/position`, `/mode`, `/auto`, `/settings`. Plain HTML, CSS and JavaScript, with no build step.

## Put it on your iPhone
1. On the PC, start the server: `python server.py` in `../local_test`. It prints `Phone app: http://<PC address>:8080/app/`. The test panel at http://localhost:8080 shows the same address.
2. On the iPhone, open that address in **Safari**. The phone must be on the same network as the PC.
3. Tap **Share**, then **Add to Home Screen**, then **Add**. The "Curtain" icon now opens the app full screen.
4. Log in. The local test server accepts any e-mail and password. The phone remembers the login.

**At DTU:** the DTU Wi-Fi may stop the phone from reaching the PC. If so, turn on the iPhone's **Personal Hotspot**, connect the PC to it, restart `server.py`, and use the new address it prints.

## What it does
- **Move the blind:** drag anywhere on the window, or tap Open / Halfway / Close. While it moves, the blind shows where it is going, and a dashed "Now X %" line shows where it really is.
- **Manual / Automatic:** in Automatic, choose when it opens (a set time or sunrise) and closes (a set time or sunset), and whether it keeps a steady indoor light level. Changes are sent only when you press **Save**. That saves LoRaWAN downlinks, since TTN allows 10 per day.
- **Settings:** outdoor light limits for sunrise and sunset, the blind's name, language (Dansk / English), and how to install the app.

## Inclusive design
- Big touch targets (44 px or more), high contrast (WCAG AA), and light and dark mode that follow the phone.
- Follows the iPhone's text size setting (Dynamic Type).
- Dragging is never required: the tiles do the same job, and VoiceOver and keyboards get a native slider.
- Every change is explained in plain words: what the blind is doing, why Automatic turned off, what happens at sunrise.
- Mistakes are easy to undo: "Turn back on" after leaving Automatic, and "Discard" for unsaved changes.
- Respects Reduce Motion and Increase Contrast. Light levels and states never rely on colour alone.
- Danish and English, picked from the phone's language.

## Files
| File | What |
|---|---|
| `index.html` | Screens (login, home, settings) and the inlined icon sprite (Phosphor Icons, MIT) |
| `style.css` | Design tokens, light/dark, layout, the window drawing |
| `app.js` | API calls, polling, drag, mode, save bars, texts (DA/EN) |
| `md5.js` | MD5 for the password, as the spec asks (simulated security) |
| `manifest.webmanifest`, `icons/` | Home-screen name and icons |

To use the real backend instead of the local test server, serve these files from it, or set `API_BASE` at the top of `app.js` (the backend must allow CORS).
