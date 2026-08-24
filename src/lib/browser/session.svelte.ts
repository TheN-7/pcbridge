/**
 * Browser-side session and file browsing.
 *
 * Entirely separate from the desktop store. A browser is not a trusted
 * surface: it never sees settings, the device list, transfer controls, or
 * even the PIN after the first exchange. It holds a session id and can
 * browse, download, and upload — nothing else.
 *
 * The restriction is enforced by the server (see `browser_routes`); this
 * file simply doesn't ask for anything it isn't allowed.
 */

export type SessionStatus = "pending" | "approved" | "denied";
export type Phase = "pin" | "waiting" | "ready" | "denied";

export interface Entry {
  name: string;
  isDir: boolean;
  size: number;
  modified: number | null;
}

const SESSION_KEY = "pcbridge.session";

function storedSession(): string {
  if (typeof localStorage === "undefined") return "";
  return localStorage.getItem(SESSION_KEY) ?? "";
}

class BrowserSession {
  phase = $state<Phase>("pin");
  error = $state<string | null>(null);
  checking = $state(false);

  path = $state("");
  parent = $state<string | null>(null);
  entries = $state<Entry[]>([]);
  loading = $state(false);
  selected = $state<Set<string>>(new Set());

  #sid = "";
  #poll: ReturnType<typeof setInterval> | null = null;

  /** Open only while this tab is actually on the file browser — see
   *  `#goLive`. It carries no data; its sole purpose is that holding it
   *  open is what tells the PC's Devices screen this device is here
   *  right now, not just "seen recently". */
  #liveEvents: EventSource | null = null;

  /** Resumes a previous session if it's still approved. */
  async start() {
    const saved = storedSession();
    if (!saved) return;

    this.#sid = saved;
    const status = await this.#status();

    if (status === "approved") {
      this.phase = "ready";
      this.#goLive();
      await this.open("");
    } else {
      // Pending or gone: make them enter the PIN again rather than
      // silently resurrecting a request the user may have ignored.
      this.#forget();
    }
  }

  /** Holds the liveness connection open for as long as the tab is on the
   *  file browser. Safe to call repeatedly — it no-ops once open. */
  #goLive() {
    if (this.#liveEvents || typeof EventSource === "undefined") return;
    this.#liveEvents = new EventSource(this.#url("/api/session/events"));
  }

  #goQuiet() {
    this.#liveEvents?.close();
    this.#liveEvents = null;
  }

  #forget() {
    this.#sid = "";
    if (typeof localStorage !== "undefined") localStorage.removeItem(SESSION_KEY);
    this.#goQuiet();
    this.phase = "pin";
  }

  #url(path: string, params: Record<string, string> = {}) {
    const url = new URL(path, location.origin);
    for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
    if (this.#sid) url.searchParams.set("sid", this.#sid);
    return url.toString();
  }

  async #status(): Promise<SessionStatus | null> {
    try {
      const res = await fetch(`/api/session?sid=${encodeURIComponent(this.#sid)}`);
      if (!res.ok) return null;
      const body = await res.json();
      return body.status as SessionStatus;
    } catch {
      return null;
    }
  }

  /** Exchanges the PIN for a session, then waits to be allowed in. */
  async submitPin(pin: string) {
    this.checking = true;
    this.error = null;

    try {
      const res = await fetch("/api/session", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ pin }),
      });

      if (res.status === 401) {
        this.error = "That PIN wasn't accepted.";
        return;
      }
      if (!res.ok) {
        this.error = (await res.text()) || "Couldn't reach the PC.";
        return;
      }

      const body = await res.json();
      this.#sid = body.sessionId;
      localStorage.setItem(SESSION_KEY, this.#sid);

      if (body.status === "approved") {
        this.phase = "ready";
        this.#goLive();
        await this.open("");
      } else {
        this.phase = "waiting";
        this.#watch();
      }
    } catch (err) {
      this.error = `Couldn't reach the PC: ${err}`;
    } finally {
      this.checking = false;
    }
  }

  /** Polls while waiting for someone at the PC to decide. */
  #watch() {
    if (this.#poll) clearInterval(this.#poll);
    this.#poll = setInterval(async () => {
      const status = await this.#status();
      if (status === "approved") {
        this.#stopWatching();
        this.phase = "ready";
        this.#goLive();
        await this.open("");
      } else if (status === "denied") {
        this.#stopWatching();
        this.phase = "denied";
        this.#forget();
        this.phase = "denied";
      }
    }, 1500);
  }

  #stopWatching() {
    if (this.#poll) clearInterval(this.#poll);
    this.#poll = null;
  }

  stop() {
    this.#stopWatching();
    this.#goQuiet();
  }

  // ---- browsing ----------------------------------------------------

  async open(path = "") {
    this.loading = true;
    this.error = null;
    try {
      const res = await fetch(this.#url("/api/files/list", { path }));
      if (res.status === 403 || res.status === 401) {
        // Access was revoked mid-session — from the PC's Devices screen,
        // or because the app restarted.
        this.#forget();
        return;
      }
      if (!res.ok) throw new Error((await res.text()) || `Failed (${res.status})`);

      const listing = await res.json();
      this.path = listing.path;
      this.parent = listing.parent;
      this.entries = listing.entries;
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

  selectAll() {
    this.selected = new Set(this.entries.map((e) => e.name));
  }

  clearSelection() {
    this.selected = new Set();
  }

  get selectedSize() {
    return this.entries
      .filter((e) => this.selected.has(e.name) && !e.isDir)
      .reduce((sum, e) => sum + e.size, 0);
  }

  downloadOne(name: string) {
    location.href = this.#url("/api/files/download", { path: this.childPath(name) });
  }

  /** Bulk download: everything selected, as one zip. */
  downloadSelected() {
    if (this.selected.size === 0) return;
    const url = new URL("/api/files/download-zip", location.origin);
    for (const name of this.selected) url.searchParams.append("path", this.childPath(name));
    url.searchParams.set("sid", this.#sid);
    location.href = url.toString();
  }

  /** A folder downloads as a zip of itself. */
  downloadFolder(name: string) {
    const url = new URL("/api/files/download-zip", location.origin);
    url.searchParams.append("path", this.childPath(name));
    url.searchParams.set("sid", this.#sid);
    location.href = url.toString();
  }

  async upload(files: FileList | File[]) {
    const form = new FormData();
    for (const file of files) {
      const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath;
      form.append("files", file, rel || file.name);
    }

    this.loading = true;
    this.error = null;
    try {
      const res = await fetch(this.#url("/api/files/upload", { path: this.path }), {
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

export const session = new BrowserSession();

export function formatBytes(n: number): string {
  if (!Number.isFinite(n) || n < 0) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = n;
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i++;
  }
  return i === 0 ? `${Math.round(value)} B` : `${value.toFixed(1)} ${units[i]}`;
}

export function formatModified(ms: number | null): string {
  if (!ms) return "";
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function glyphFor(entry: Entry): string {
  if (entry.isDir) return "▸";
  const ext = entry.name.includes(".") ? entry.name.split(".").pop()!.toLowerCase() : "";
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
