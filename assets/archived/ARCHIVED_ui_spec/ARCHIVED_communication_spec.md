# Smart curtain – Frontend, backend & firmware communication spec
Nov 19, 2026 · @Andreas

The UI prototype sends five kinds of requests to the REST backend and polls one for status; this is everything it sends and reads. All bodies are JSON, and every request except /login carries the header Authorization: Bearer <token>.
## Messages


| Request | Sent when | JSON body |
| --- | --- | --- |
| POST /login | "Log in" is pressed | {"username": "demo@example.com", "password": "<md5 hex>"} → answer {"token": "..."} |
| GET /status | Every 5 s, while logged in and a device is registered | none (answer below) |
| POST /position | The curtain handle is released (Manual mode only) | {"position": 40} |
| PUT /mode | Manual or Automatic is tapped | {"mode": "automatic"} |
| PUT /auto | "Save changes" under Automatic is pressed | {"autoOpen": 1, "autoOpenTime": "07:30", "autoClose": 0, "autoCloseTime": "22:45", "maintainLight": 1, "targetLightLevel": 35} |
| PUT /settings | "Save" on the Settings page is pressed | {"outsideThresholdOpen": 80, "outsideThresholdClose": 25} |
Not sent: the language choice, device registration (simulated in Node-RED) and log out.
## Value formats
- **Booleans:** 1 = on, 0 = off.

- **position:** 0–100, 0 = fully open, 100 = fully closed.

- **targetLightLevel, outsideThresholdOpen, outsideThresholdClose:** 0–100 (%).

- **Times:** "HH:MM", 24-hour, Danish local time.

- **password:** MD5 hash of the password (simulated security).
## GET /status answer
{   "position": 50,   "mode": "manual",   "autoOpen": 0, "autoOpenTime": "07:30",   "autoClose": 1, "autoCloseTime": "21:00",   "maintainLight": 0, "targetLightLevel": 60,   "outsideThresholdOpen": 80, "outsideThresholdClose": 10 }

position is where the curtain actually is; the other values are the last saved settings.
## Rules for backend and firmware
- autoOpen = 1: open when outside light rises above outsideThresholdOpen ("Open at sunrise"). 0: open at autoOpenTime.

- autoClose = 1: close when outside light falls below outsideThresholdClose ("Close at sundown"). 0: close at autoCloseTime.

- maintainLight = 1: hold the inside light at targetLightLevel, only between the day's first opening and closing.

- outsideThresholdClose must be lower than outsideThresholdOpen; answer 400 otherwise.

- Errors: 400 with {"error": "text"} for bad values, 401 when the token is missing or wrong (the UI then goes to Login).
