// Tauri has no Node server to render on, and the Rust side serves this
// bundle to phones as plain static files. So: no SSR, prerender the
// shell, and let adapter-static's index.html fallback handle routing.
export const ssr = false;
export const prerender = true;
