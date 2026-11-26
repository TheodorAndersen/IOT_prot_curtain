import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { closedToOpenPct } from "../utils/formatters";
import { storage } from "../utils/storage";
import { da } from "./da";
import { en } from "./en";

export type Lang = "da" | "en";
export type I18nKey = keyof typeof en;

const DICTS: Record<Lang, Record<I18nKey, string>> = { da, en };

interface I18nContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: I18nKey, vars?: Record<string, string | number>) => string;
  pct: (val: number) => string;
  posText: (val: number) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

function detectDefaultLang(): Lang {
  const saved = storage.get<Lang | null>("lang", null);
  if (saved === "da" || saved === "en") return saved;
  const nav = typeof navigator !== "undefined" ? navigator.language.toLowerCase() : "en";
  return nav.startsWith("da") ? "da" : "en";
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectDefaultLang);

  const setLang = useCallback((next: Lang) => {
    setLangState(next);
    storage.set("lang", next);
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const t = useCallback(
    (key: I18nKey, vars?: Record<string, string | number>) => {
      let str = DICTS[lang][key] ?? en[key] ?? key;
      if (vars) {
        str = str.replace(/\{(\w+)\}/g, (_, k: string) =>
          vars[k] !== undefined ? String(vars[k]) : `{${k}}`
        );
      }
      return str;
    },
    [lang]
  );

  const pct = useCallback((val: number) => (lang === "da" ? `${val} %` : `${val}%`), [lang]);

  const posText = useCallback(
    (closedVal: number) =>
      closedVal === 0
        ? t("pos.textOpen")
        : closedVal === 100
          ? t("pos.textClosed")
          : t("pos.text", { p: pct(closedToOpenPct(closedVal)) }),
    [pct, t]
  );

  return (
    <I18nContext.Provider value={{ lang, setLang, t, pct, posText }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used inside I18nProvider");
  return ctx;
}
