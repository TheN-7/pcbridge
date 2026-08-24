/**
 * Browsing the shared folder.
 *
 * Kept separate from the main store on purpose: a directory listing is
 * *navigation*, not application state. It belongs to one screen, changes
 * as you click around, and pushing every listing to every connected
 * device would be noise — your phone doesn't care which folder the PC
 * window happens to be looking at.
 *
 * Settings and devices stay in `bridge`, where "one source, many
 * windows" genuinely matters. This is the deliberate exception.
 */

export interface Entry {
  name: string;
  isDir: boolean;
  size: number;
  modified: number | null;
}

export interface Listing {
  path: string;
  parent: string | null;
  entries: Entry[];
}

import { storedPin } from "./bridge.svelte";

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

async function apiBase(): Promise<string> {
  if (!isTauri()) return "";
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<string>("api_base");
  } catch {
    return "";
  }
}

class FileBrowser {
  path = $state("");
  parent = $state<string | null>(null);
  entries = $state<Entry[]>([]);
  loading = $state(false);
  error = $state<string | null>(null);
  selected = $state<Set<string>>(new Set());

  #base: string | null = null;

  async #url(path: string, params: Record<string, string> = {}) {
    if (this.#base === null) this.#base = await apiBase();
    const url = new URL(
      this.#base + path,
      typeof location === "undefined" ? "http://localhost" : location.origin,
    );
    for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
    // Browsers authenticate per request; the desktop window is trusted by
    // virtue of reaching loopback at all, and `storedPin` returns empty
    // there so nothing is appended.
    const pin = storedPin();
    if (pin) url.searchParams.set("pin", pin);
    return url.toString();
  }

  async open(path = "") {
    this.loading = true;
    this.error = null;
    try {
      const res = await fetch(await this.#url("/api/files/list", { path }));
      if (!res.ok) throw new Error((await res.text()) || `Failed (${res.status})`);
      const listing = (await res.json()) as Listing;
      this.path = listing.path;
      this.parent = listing.parent;
      this.entries = listing.entries;
      // Selection is per-folder — carrying it across a navigation would
      // mean a later "delete selected" hits things you can no longer see.
      this.selected = new Set();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    } finally {
      this.loading = false;
    }
  }

  refresh() {
    return this.open(this.path);
  }

  enter(entry: Entry) {
    if (!entry.isDir) return;
    return this.open(this.path ? `${this.path}/${entry.name}` : entry.name);
  }

  goUp() {
    if (this.parent === null) return;
    return this.open(this.parent);
  }

  childPath(name: string) {
    return this.path ? `${this.path}/${name}` : name;
  }

  toggle(name: string) {
    const next = new Set(this.selected);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    this.selected = next;
  }

  clearSelection() {
    this.selected = new Set();
  }

  async downloadUrl(name: string) {
    return this.#url("/api/files/download", { path: this.childPath(name) });
  }

  async #act(path: string, params: Record<string, string>) {
    this.error = null;
    const res = await fetch(await this.#url(path, params), { method: "POST" });
    if (!res.ok) {
      this.error = (await res.text()) || `Failed (${res.status})`;
      return false;
    }
    await this.refresh();
    return true;
  }

  mkdir(name: string) {
    return this.#act("/api/files/mkdir", { path: this.childPath(name) });
  }

  remove(name: string) {
    return this.#act("/api/files/delete", { path: this.childPath(name) });
  }

  rename(name: string, newName: string) {
    return this.#act("/api/files/rename", {
      path: this.childPath(name),
      new_name: newName,
    });
  }

  async removeSelected() {
    const names = [...this.selected];
    for (const name of names) {
      const res = await fetch(
        await this.#url("/api/files/delete", { path: this.childPath(name) }),
        { method: "POST" },
      );
      if (!res.ok) {
        this.error = (await res.text()) || `Couldn't delete ${name}`;
        break;
      }
    }
    this.clearSelection();
    await this.refresh();
  }

  async upload(files: FileList | File[]) {
    const form = new FormData();
    for (const file of files) {
      // webkitRelativePath is set for folder uploads and carries the
      // structure; the server recreates it under the current folder.
      const rel = (file as File & { webkitRelativePath?: string })
        .webkitRelativePath;
      form.append("files", file, rel || file.name);
    }

    this.loading = true;
    this.error = null;
    try {
      const res = await fetch(await this.#url("/api/files/upload", { path: this.path }), {
        method: "POST",
        body: form,
      });
      if (!res.ok) throw new Error((await res.text()) || `Upload failed (${res.status})`);
      await this.refresh();
    } catch (err) {
      this.error = err instanceof Error ? err.message : String(err);
    } finally {
      this.loading = false;
    }
  }
}

export const browser = new FileBrowser();

export function formatModified(ms: number | null): string {
  if (!ms) return "";
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** Extension-driven glyph. No image assets to bundle or keep in sync. */
export function glyphFor(entry: Entry): string {
  if (entry.isDir) return "▸";
  const ext = entry.name.includes(".")
    ? entry.name.split(".").pop()!.toLowerCase()
    : "";
  const map: Record<string, string> = {
    jpg: "▨", jpeg: "▨", png: "▨", gif: "▨", webp: "▨", heic: "▨", svg: "▨",
    mp4: "▶", mov: "▶", mkv: "▶", avi: "▶", webm: "▶",
    mp3: "♪", wav: "♪", flac: "♪", m4a: "♪", ogg: "♪",
    zip: "◲", rar: "◲", "7z": "◲", tar: "◲", gz: "◲",
    pdf: "▤", doc: "▤", docx: "▤", txt: "▤", md: "▤",
    xls: "▦", xlsx: "▦", csv: "▦",
    exe: "▣", msi: "▣", apk: "▣",
  };
  return map[ext] ?? "▪";
}
