import { Capacitor } from "@capacitor/core";
import { Preferences } from "@capacitor/preferences";

const PREFIX = "curtain.";

export const storage = {
  get<T>(key: string, fallback: T): T {
    try {
      const raw = localStorage.getItem(PREFIX + key);
      return raw === null ? fallback : (JSON.parse(raw) as T);
    } catch {
      return fallback;
    }
  },

  set<T>(key: string, value: T): void {
    const serialized = JSON.stringify(value);
    try {
      localStorage.setItem(PREFIX + key, serialized);
    } catch {
      // Ignore storage quota errors
    }
    if (Capacitor.isNativePlatform()) {
      void Preferences.set({ key: PREFIX + key, value: serialized }).catch(() => {});
    }
  },

  del(key: string): void {
    try {
      localStorage.removeItem(PREFIX + key);
    } catch {
      // Ignore
    }
    if (Capacitor.isNativePlatform()) {
      void Preferences.remove({ key: PREFIX + key }).catch(() => {});
    }
  },

  async hydrateNativeKeys(keys: string[]): Promise<void> {
    if (!Capacitor.isNativePlatform()) return;
    await Promise.all(
      keys.map(async (k) => {
        try {
          const { value } = await Preferences.get({ key: PREFIX + k });
          if (value !== null) {
            localStorage.setItem(PREFIX + k, value);
          }
        } catch {
          // Ignore
        }
      })
    );
  },
};
