# Android App (PWA & Native Capacitor APK)

You can run the Smart Curtain app on Android in two ways:

## Option 1: Install as a PWA (Zero Build Needed)

1. Open `https://<your-domain>/` in Chrome on your Android phone.
2. Open the Chrome **⋮** menu → **Add to home screen** / **Install app**.
3. Sign in — the app runs standalone without browser chrome and talks directly to `https://<your-domain>/api`.

## Option 2: Build the Native Capacitor Android APK (Dockerized)

No Android Studio, JDK, or Node.js installation is required on Windows — Docker handles JDK 21, Android SDK 35, Node 22, Capacitor sync, native icon/splash generation, and Gradle.

1. Ensure `VITE_API_BASE_URL=https://<your-domain>/api` (or `DOMAIN`) is set in your gitignored `.env` file.
2. Run from the repository root in PowerShell:

```powershell
.\deploy\android\build-apk.ps1
```

3. The build writes the gitignored APK to `dist-mobile\SmartCurtain-debug.apk`.
4. Copy `SmartCurtain-debug.apk` to your Android phone (USB, Quick Share, Google Drive, or `adb install -r dist-mobile\SmartCurtain-debug.apk`) and tap it to install.

> [!NOTE]
> Even inside the APK, you can override the backend URL anytime on the login screen (**Server connection**) or in **Settings → Connection → API address**.
