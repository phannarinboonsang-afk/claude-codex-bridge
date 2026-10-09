import { Injectable, signal, effect } from '@angular/core';

export type AppTheme = 'dark' | 'light';

@Injectable({
  providedIn: 'root',
})
export class ThemeService {
  private readonly THEME_KEY = 'agentbridge_theme';

  // Active theme signal
  theme = signal<AppTheme>('dark');


  constructor() {
    this.initTheme();

    // Reactively update HTML attribute whenever theme signal changes
    effect(() => {
      const current = this.theme();
      document.documentElement.setAttribute('data-theme', current);
      try {
        localStorage.setItem(this.THEME_KEY, current);
      } catch {
        // Ignore storage errors in private browsing
      }
    });
  }

  private initTheme(): void {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(this.THEME_KEY);
    } catch {
      // Ignore
    }

    if (saved === 'light' || saved === 'dark') {
      this.theme.set(saved);
      document.documentElement.setAttribute('data-theme', saved);
    } else {
      // Check system preference
      const prefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
      const initial: AppTheme = prefersLight ? 'light' : 'dark';
      this.theme.set(initial);
      document.documentElement.setAttribute('data-theme', initial);
    }
  }

  toggleTheme(): void {
    this.theme.update((curr) => (curr === 'dark' ? 'light' : 'dark'));
  }

  isDark(): boolean {
    return this.theme() === 'dark';
  }
}
