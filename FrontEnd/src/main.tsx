import React from "react";
import ReactDOM from "react-dom/client";
import "./styles/tokens.css";
import "./styles/global.css";
import { App } from "./App";
import { storage } from "./utils/storage";

async function bootstrap() {
  await storage.hydrateNativeKeys([
    "token",
    "apiBaseUrl",
    "lang",
    "theme",
    "name",
    "instantSend",
  ]);
  ReactDOM.createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  );
}

void bootstrap();
