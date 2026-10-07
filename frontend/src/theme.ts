import { useCallback, useLayoutEffect, useState } from 'react';

export type ThemePreference = 'system' | 'light' | 'dark';
const storageKey = 'plasma-circuit.theme';
const systemQuery = '(prefers-color-scheme: dark)';

function readPreference(): ThemePreference {
  try {
    const saved = localStorage.getItem(storageKey);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {
    // Appearance still works when browser storage is unavailable.
  }
  return 'system';
}

function applyTheme(preference: ThemePreference, systemDark = window.matchMedia(systemQuery).matches) {
  document.documentElement.dataset.theme = preference === 'system' ? (systemDark ? 'dark' : 'light') : preference;
}

// Apply the saved appearance before React renders the first frame.
export function initializeTheme() {
  applyTheme(readPreference());
}

export function useTheme() {
  const [preference, setPreference] = useState<ThemePreference>(readPreference);

  useLayoutEffect(() => {
    const query = window.matchMedia(systemQuery);
    const update = () => applyTheme(preference, query.matches);
    update();
    if (preference !== 'system') return;
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, [preference]);

  const setTheme = useCallback((next: ThemePreference) => {
    applyTheme(next);
    setPreference(next);
    try {
      localStorage.setItem(storageKey, next);
    } catch {
      // Keep the current preference for this session when storage is unavailable.
    }
  }, []);

  return [preference, setTheme] as const;
}
