// Vitest setup file (loaded via vitest.config.ts setupFiles).
// jsdom does not implement window.matchMedia; AntD's responsive grid + multi-Select
// call it on mount. Provide a benign stub so tests run on the jsdom environment.

if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined, // legacy MediaQueryList API
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

// jsdom also lacks ResizeObserver, which some AntD components touch on mount.
if (typeof globalThis.ResizeObserver === 'undefined') {
  class StubResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  // @ts-expect-error — assignment to global polyfill for jsdom.
  globalThis.ResizeObserver = StubResizeObserver;
}
