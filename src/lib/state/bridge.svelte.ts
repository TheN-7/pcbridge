/**
 * The single source of truth for the whole interface.
 *
 * Two rules make "change it once, see it everywhere" actually hold:
 *
 *  1. Every screen reads from this one store. Overview and Settings
 *     don't keep their own copy of the shared folder — they read the
 *     same field, so editing either updates both with no plumbing.
 *
 *  2. Writes never mutate locally first. An action POSTs to the Rust
 *     server, the server changes the real state, and the server pushes
 *     the new state back to *every* connected client over an event
 *     stream — including the window that made the change.
 *
 * Rule 2 is what makes the desktop window, a phone, and a browser tab
 * agree. Change the shared folder on the PC and the phone updates while
 * you're looking at it. If we optimistically mutated locally instead,
 * those surfaces would drift apart the moment one write failed.
 */

// ---------------------------------------------------------------- types

export type TransferState =
  | "queued"
  | "active"
  | "done"
  | "failed"
  | "cancelled";

export type TransferDirection = "upload" | "download";

export interface Transfer {
  id: string;
  name: string;
  deviceId: string | null;
  deviceName: string;
  direction: TransferDirection;
  state: TransferState;
  bytesDone: number;
  bytesTotal: number;
  /** Bytes per second; null once the transfer is no longer active. */
  rate: number | null;
  startedAt: string;
  finishedAt: string | null;
  error: string | null;
}

export type NetworkMode = "https" | "http";

export interface Settings {
  sharedFolder: string;
  pin: string;
  networkMode: NetworkMode;
  httpsPort: number;
  httpPort: number;
  theme: "system" | "dark" | "light";
  startWithWindows: boolean;
  requirePinEveryTime: boolean;
}

export interface ServerInfo {
  hostname: string;
  platform: string;
  lanAddress: string | null;
  tailscaleAddress: string | null;
  fingerprint: string;
  storageFree: number;
  storageTotal: number;
}

/** Exactly what the server sends on every state push. */
export interface Snapshot {
  serving: boolean;
  settings: Settings;
  server: ServerInfo;
  transfers: Transfer[];
  clients: ConnectedClient[];
  pendingSessions: PendingSession[];
  rememberedDevices: RememberedDevice[];
}

/** A browser that gave the right PIN and is waiting to be let in. */
export interface PendingSession {
  id: string;
  deviceId: string;
  label: string;
  address: string;
  status: "pending" | "approved" | "denied";
  createdAt: string;
}

export interface RememberedDevice {
  deviceId: string;
  label: string;
  rememberedAt: string;
}

/** A browser currently talking to this PC. Observed, never paired. */
export interface ConnectedClient {
  id: string;
  label: string;
  address: string;
  connectedAt: string;
  lastSeen: string;
  /** Greater than zero means the app is open on that device right now. */
  streams: number;
}

export type ConnectionState =
  | "connecting"
  | "live"
  | "offline"
  /** Reached the server, but the PIN was missing or wrong. */
  | "unauthorized";

// ------------------------------------------------------------- fallback

/**
 * Used only until the backend is reachable, so the interface is
 * inspectable during development. `connection` stays "offline" while
 * this is what you're looking at, and the UI says so rather than
 * pretending these numbers are real.
 */
const PLACEHOLDER: Snapshot = {
  serving: false,
  settings: {
    // Neutral on purpose. These values are compiled into the shipped
    // binary and are on screen for the moment before the first snapshot
    // arrives, so a developer's real username, machine name and network
    // addresses should not be among them — every user would briefly see
    // them, and anyone reading the executable finds them as plain text.
    // Blank fields also read honestly as "nothing yet" rather than as
    // settings someone might try to correct.
    sharedFolder: "",
    pin: "------",
    networkMode: "https",
    httpsPort: 8000,
    httpPort: 8001,
    theme: "system",
    startWithWindows: false,
    requirePinEveryTime: false,
  },
  server: {
    hostname: "",
    platform: "",
    lanAddress: null,
    tailscaleAddress: null,
    fingerprint: "",
    storageFree: 0,
    storageTotal: 0,
  },
  transfers: [],
  clients: [],
  pendingSessions: [],
  rememberedDevices: [],
};

// --------------------------------------------------------------- helpers

function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

/**
 * Phones and browsers are served the interface by the Rust server, so
 * the API is same-origin. In dev, Vite proxies /api and /events to it,
 * which keeps that same-origin path true there too. Only the packaged
 * Tauri window differs — it loads from the asset protocol and has to be
 * told where the local server is listening.
 */
async function resolveApiBase(): Promise<string> {
  if (!isTauri()) return "";
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    return await invoke<string>("api_base");
  } catch {
    return "";
  }
}

const PIN_KEY = "pcbridge.pin";

/**
 * Where the PIN lives for a browser session.
 *
 * The desktop window never needs one: it reaches the server over
 * loopback, which is only reachable from this machine, so being able to
 * connect already proves you're sitting at it. A browser on another
 * device has no such proof and must present the PIN on every request.
 */
export function storedPin(): string {
  if (isTauri() || typeof localStorage === "undefined") return "";
  return localStorage.getItem(PIN_KEY) ?? "";
}

export function rememberPin(pin: string) {
  if (typeof localStorage !== "undefined") localStorage.setItem(PIN_KEY, pin);
}

export function forgetPin() {
  if (typeof localStorage !== "undefined") localStorage.removeItem(PIN_KEY);
}

/**
 * Appends the PIN as a query parameter.
 *
 * A header would be tidier, but `EventSource` cannot set headers and
 * neither can a plain download link — and both are load-bearing here.
 * Rather than invent a second token scheme for those two cases, the
 * query parameter is used consistently everywhere. It's inside the TLS
 * tunnel, so it isn't exposed on the network; the real cost is that it
 * can appear in logs, which is why this is only used off-loopback.
 */
export function withPin(url: string): string {
  const pin = storedPin();
  if (!pin) return url;
  return url + (url.includes("?") ? "&" : "?") + "pin=" + encodeURIComponent(pin);
}

// ----------------------------------------------------------- the store

class Bridge {
  snapshot = $state<Snapshot>(PLACEHOLDER);
  connection = $state<ConnectionState>("connecting");
  lastError = $state<string | null>(null);

  #apiBase = "";
  #events: EventSource | null = null;
  #retry: ReturnType<typeof setTimeout> | null = null;
  #retryDelay = 1000;

  // Convenience accessors so screens read `bridge.settings.sharedFolder`
  // rather than reaching through `bridge.snapshot` every time.
  get serving() { return this.snapshot.serving; }
  get settings() { return this.snapshot.settings; }
  get server() { return this.snapshot.server; }
  get transfers() { return this.snapshot.transfers; }
  get clients() { return this.snapshot.clients ?? []; }
  get pendingSessions() { return this.snapshot.pendingSessions ?? []; }
  get rememberedDevices() { return this.snapshot.rememberedDevices ?? []; }

  resolveSession(id: string, approve: boolean, remember: boolean) {
    return this.#post(`/api/sessions/${id}/resolve`, { approve, remember });
  }

  forgetDevice(deviceId: string) {
    return this.#post(`/api/remembered/${deviceId}/forget`);
  }

  /** Devices with the app open right now, as opposed to merely seen recently. */
  get liveClients() {
    return this.clients.filter((c) => c.streams > 0);
  }

  get activeTransfers() {
    return this.snapshot.transfers.filter(
      (t) => t.state === "active" || t.state === "queued",
    );
  }

  get isPlaceholder() {
    return this.connection !== "live";
  }

  get needsPin() {
    return this.connection === "unauthorized";
  }

  /** Call once, from the root layout. Safe to call again; it no-ops. */
  async connect() {
    if (this.#events) return;
    this.#apiBase = await resolveApiBase();

    // A browser must prove its PIN before the stream opens. `EventSource`
    // reports every failure identically — it has no status code — so a
    // wrong PIN would otherwise be indistinguishable from the server
    // being down, and the interface would sit retrying forever instead of
    // asking for a PIN.
    if (!isTauri()) {
      if (!storedPin()) {
        this.connection = "unauthorized";
        return;
      }
      const ok = await this.checkPin(storedPin());
      if (!ok) {
        forgetPin();
        this.connection = "unauthorized";
        return;
      }
    }

    this.#openStream();
  }

  /** Returns true if the server accepts this PIN. */
  async checkPin(pin: string): Promise<boolean> {
    try {
      const url = `${this.#apiBase}/api/health?pin=${encodeURIComponent(pin)}`;
      const res = await fetch(url);
      return res.ok;
    } catch {
      return false;
    }
  }

  /** Used by the PIN screen: verify, remember, then start streaming. */
  async unlock(pin: string): Promise<boolean> {
    const ok = await this.checkPin(pin);
    if (!ok) return false;
    rememberPin(pin);
    this.connection = "connecting";
    this.#openStream();
    return true;
  }

  lock() {
    this.disconnect();
    forgetPin();
    this.connection = "unauthorized";
  }

  #openStream() {
    if (typeof EventSource === "undefined") return;

    this.connection = "connecting";
    const source = new EventSource(withPin(`${this.#apiBase}/events`));
    this.#events = source;

    source.onopen = () => {
      this.connection = "live";
      this.lastError = null;
      this.#retryDelay = 1000;
    };

    source.onmessage = (event) => {
      try {
        this.snapshot = JSON.parse(event.data) as Snapshot;
        this.connection = "live";
      } catch (err) {
        this.lastError = `Could not read an update from the server: ${err}`;
      }
    };

    // EventSource reconnects on its own, but only for some failures, and
    // it hammers the server on others. Own the retry with a backoff so a
    // stopped server doesn't turn into a request flood.
    source.onerror = () => {
      this.connection = "offline";
      source.close();
      this.#events = null;
      if (this.#retry) clearTimeout(this.#retry);
      this.#retry = setTimeout(() => this.#openStream(), this.#retryDelay);
      this.#retryDelay = Math.min(this.#retryDelay * 2, 15_000);
    };
  }

  disconnect() {
    if (this.#retry) clearTimeout(this.#retry);
    this.#retry = null;
    this.#events?.close();
    this.#events = null;
  }

  /**
   * Every mutation goes through here. Deliberately does not touch local
   * state — the server's push is what updates the UI, so all surfaces
   * change together or not at all.
   */
  async #post(path: string, body?: unknown): Promise<boolean> {
    try {
      const res = await fetch(withPin(`${this.#apiBase}${path}`), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (res.status === 401) {
        // The PIN changed on the PC, or was never right. Drop it so the
        // gate reappears rather than leaving every action silently
        // failing with no explanation.
        forgetPin();
        this.connection = "unauthorized";
        return false;
      }
      if (!res.ok) {
        const detail = await res.text().catch(() => "");
        this.lastError = detail || `Request failed (${res.status})`;
        return false;
      }
      this.lastError = null;
      return true;
    } catch (err) {
      this.lastError = `Could not reach the server: ${err}`;
      return false;
    }
  }

  // ---- actions -----------------------------------------------------

  startServing() { return this.#post("/api/serving", { serving: true }); }
  stopServing() { return this.#post("/api/serving", { serving: false }); }

  updateSettings(patch: Partial<Settings>) {
    return this.#post("/api/settings", patch);
  }

  setNetworkMode(mode: NetworkMode) { return this.updateSettings({ networkMode: mode }); }

  setSharedFolder(path: string) { return this.updateSettings({ sharedFolder: path }); }
  setPin(pin: string) { return this.updateSettings({ pin }); }

  regeneratePin() { return this.#post("/api/settings/pin/regenerate"); }

  /** Puts a fresh pairing code on screen and says what it encodes.
   *
   *  The code itself never comes back here — only the URL, for showing
   *  underneath the QR, and how long it lasts. The image is fetched
   *  separately from /api/pair-qr, so the credential is never sitting in
   *  the DOM waiting to be copied out of a screenshot of the devtools. */
  async newPairCode(
    remember: boolean,
  ): Promise<{ url: string | null; expiresInSeconds: number } | null> {
    try {
      const res = await fetch(withPin(`${this.#apiBase}/api/pair-code`), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ remember }),
      });
      if (!res.ok) {
        this.lastError = (await res.text()) || `Request failed (${res.status})`;
        return null;
      }
      this.lastError = null;
      return await res.json();
    } catch (err) {
      this.lastError = `Could not reach the server: ${err}`;
      return null;
    }
  }

  /** Where the QR image lives. Cache-busted per code so the browser
   *  can't show the previous one after a new code is minted. */
  pairQrUrl(nonce: number): string {
    return withPin(`${this.#apiBase}/api/pair-qr?v=${nonce}`);
  }


  cancelTransfer(id: string) { return this.#post(`/api/transfers/${id}/cancel`); }

  clearFinishedTransfers() { return this.#post("/api/transfers/clear-finished"); }
}

export const bridge = new Bridge();

// --------------------------------------------------------- formatting

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

export function formatRate(bytesPerSecond: number | null): string {
  if (bytesPerSecond === null) return "";
  return `${formatBytes(bytesPerSecond)}/s`;
}
