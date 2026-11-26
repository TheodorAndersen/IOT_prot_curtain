// Template for Credentials.h (OTAA keys from the TTN console: End device → Activation information).
// Put the real file next to smart_curtain.ino as Credentials.h. It is private: keep it out of the hand-in zip.
// Without Credentials.h the firmware still builds and the USB test link works; only the LoRaWAN join fails.
// DevEUI is not here: the firmware uses the RN2483's own hardware EUI (printed as "#R eui ..." at startup).
const char *appEui = "0000000000000000";                   // JoinEUI / AppEUI, 16 hex digits
const char *appKey = "00000000000000000000000000000000";   // AppKey, 32 hex digits
