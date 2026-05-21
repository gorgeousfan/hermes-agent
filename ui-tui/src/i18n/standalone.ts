/**
 * Standalone translation function for use outside of React components.
 * Used in non-component code (event handlers, command handlers, hooks)
 * where useI18n() hook is not available.
 */
import { $gatewayLocale, $userLocale } from './store.js';
import { en } from './en.js';
import { zh } from './zh.js';
import { resolveKey, interpolate } from './context.js';
import type { Locale, TUITranslations } from './types.js';

const LOCALES: Partial<Record<Locale, TUITranslations>> = {
  en,
  zh,
};

function resolveLocale(): Locale {
  const gatewayVal = ($gatewayLocale as any).value as Locale | null;
  const userVal = ($userLocale as any).value as Locale;
  return gatewayVal || userVal || 'en';
}

/**
 * Get translation for use outside React components.
 * Usage: getTuiT('status.ready') → 'Ready' | '就绪'
 */
export function getTuiT(key: string, params?: Record<string, string | number>): string {
  const locale = resolveLocale();
  const translations = LOCALES[locale] || en;

  const value = resolveKey(translations, key);
  if (value === undefined) {
    // Fallback to English
    const enValue = resolveKey(en, key);
    return enValue != null ? interpolate(enValue, params) : key;
  }
  return interpolate(value, params);
}
