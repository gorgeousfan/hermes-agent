import { atom } from 'nanostores';
import type { Locale } from './types.js';

/** Gateway-provided locale (from config.yaml display.language). */
export const $gatewayLocale = atom<Locale | null>(null);

/** User-persisted locale preference (localStorage). */
export const $userLocale = atom<Locale>('en');

/** Set locale from gateway config. */
export function setGatewayLocale(locale: Locale) {
  $gatewayLocale.set(locale);
}

/** Set user locale preference. */
export function setUserLocale(locale: Locale) {
  $userLocale.set(locale);
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem('tui-locale', locale);
    }
  } catch {
    // localStorage not available
  }
}
