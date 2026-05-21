import { useStore } from '@nanostores/react';
import { createContext, useContext, type ReactNode } from 'react';
import { en } from './en.js';
import { zh } from './zh.js';
import { $gatewayLocale, $userLocale, setUserLocale } from './store.js';
import type { Locale, TUITranslations } from './types.js';

// Import more locales as needed
// import { ja } from './ja.js';
// import { de } from './de.js';

const LOCALES: Partial<Record<Locale, TUITranslations>> = {
  en,
  zh,
  // ja,
  // de,
  // es,
  // fr,
  // ko,
  // zh-hant: zh, // fallback to simplified
};

export interface I18nContextValue {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  t: (key: string, params?: Record<string, string | number>) => string;
}

const defaultContext: I18nContextValue = {
  locale: 'en',
  setLocale: () => {},
  t: (key: string) => key,
};

export const I18nContext = createContext<I18nContextValue>(defaultContext);

// Simple nested key resolver: 'session.available_tools' -> translations.session.available_tools
export function resolveKey(translations: TUITranslations, key: string): string | undefined {
  const parts = key.split('.');
  let current: any = translations;

  for (const part of parts) {
    if (current == null || typeof current !== 'object') {
      return undefined;
    }
    current = current[part];
  }

  return typeof current === 'string' ? current : undefined;
}

// Replace {param} placeholders with values
export function interpolate(template: string, params?: Record<string, string | number>): string {
  if (!params) return template;

  return template.replace(/\{(\w+)\}/g, (match, key) => {
    return params[key] != null ? String(params[key]) : match;
  });
}

export function useI18n() {
  return useContext(I18nContext);
}

export function I18nProvider({ children }: { children: ReactNode }) {
  const gatewayLocale = useStore($gatewayLocale);
  const userLocale = useStore($userLocale);
  const locale = gatewayLocale || userLocale;
  const translations = LOCALES[locale] || en;

  const t = (key: string, params?: Record<string, string | number>) => {
    const value = resolveKey(translations, key);
    if (value === undefined) {
      // Fallback to English
      const enValue = resolveKey(en, key);
      return enValue != null ? interpolate(enValue, params) : key;
    }
    return interpolate(value, params);
  };

  const setLocale = (newLocale: Locale) => {
    setUserLocale(newLocale);
  };

  return (
    <I18nContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </I18nContext.Provider>
  );
}
