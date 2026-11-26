import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "dk.dtu.group7.smartcurtain",
  appName: "Smart Curtain",
  webDir: "dist",
  server: {
    androidScheme: "https",
  },
  android: {
    adjustMarginsForEdgeToEdge: "auto",
  },
};

export default config;
