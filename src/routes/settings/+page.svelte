<script lang="ts">
  import { bridge, formatBytes } from "$lib/state/bridge.svelte";

  function isTauri(): boolean {
    return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
  }

  // Text fields are the one place local state is correct: a PIN or port
  // is only meaningful once it's finished being typed. Broadcasting
  // every keystroke would push half-typed PINs to every connected
  // device, and briefly lock them all out. Toggles and pickers have no
  // such intermediate state, so they save immediately.
  let pinDraft = $state(bridge.settings.pin);
  let httpsDraft = $state(String(bridge.settings.httpsPort));
  let httpDraft = $state(String(bridge.settings.httpPort));
  let notice = $state("");
  let error = $state("");

  // Re-sync drafts when the real value changes underneath us — someone
  // regenerating the PIN on another device shouldn't leave this box
  // showing a stale one.
  let lastPin = $state(bridge.settings.pin);
  $effect(() => {
    if (bridge.settings.pin !== lastPin) {
      lastPin = bridge.settings.pin;
      pinDraft = bridge.settings.pin;
    }
  });

  const portsChanged = $derived(
    httpsDraft !== String(bridge.settings.httpsPort) ||
      httpDraft !== String(bridge.settings.httpPort),
  );

  function flash(message: string) {
    notice = message;
    error = "";
    setTimeout(() => (notice = ""), 2500);
  }

  async function chooseFolder() {
    if (!isTauri()) {
      error = "Choosing a folder needs the desktop app. Type the path instead.";
      return;
    }
    try {
      const { open } = await import("@tauri-apps/plugin-dialog");
      const picked = await open({
        directory: true,
        multiple: false,
        defaultPath: bridge.settings.sharedFolder,
      });
      if (typeof picked === "string") {
        await bridge.setSharedFolder(picked);
        flash("Shared folder updated everywhere.");
      }
    } catch (err) {
      error = `Couldn't open the folder picker: ${err}`;
    }
  }

  async function savePin() {
    const pin = pinDraft.trim();
    if (pin.length < 4) {
      error = "Use at least 4 characters, so it isn't trivially guessable.";
      return;
    }
    error = "";
    await bridge.setPin(pin);
    flash("PIN saved. Paired devices will need it next time they connect.");
  }

  async function newPin() {
    await bridge.regeneratePin();
    flash("New PIN generated.");
  }

  async function savePorts() {
    const https = Number(httpsDraft);
    const http = Number(httpDraft);
    if (!Number.isInteger(https) || https < 1 || https > 65535) {
      error = "Device port must be a number between 1 and 65535.";
      return;
    }
    if (!Number.isInteger(http) || http < 1 || http > 65535) {
      error = "Local port must be a number between 1 and 65535.";
      return;
    }
    if (https === http) {
      error = "The two ports must be different.";
      return;
    }
    error = "";
    await bridge.updateSettings({ httpsPort: https, httpPort: http });
    flash("Ports saved. Restart PC Bridge for them to take effect.");
  }

  async function setTheme(theme: "system" | "dark" | "light") {
    await bridge.updateSettings({ theme });
  }

  async function toggleAutostart(enabled: boolean) {
    await bridge.updateSettings({ startWithWindows: enabled });
    if (!isTauri()) return;
    try {
      const auto = await import("@tauri-apps/plugin-autostart");
      if (enabled) await auto.enable();
      else await auto.disable();
    } catch (err) {
      error = `Saved, but couldn't change the Windows startup entry: ${err}`;
    }
  }

  async function copyFingerprint() {
    await navigator.clipboard.writeText(bridge.server.fingerprint);
    flash("Fingerprint copied.");
  }
</script>

<div class="page">
  <header>
    <h1>Settings</h1>
    <p class="lede">
      Changes here apply everywhere at once — this window, any browser, and
      every connected device.
    </p>
  </header>

  {#if notice}<p class="notice" role="status">{notice}</p>{/if}
  {#if error}<p class="error" role="alert">{error}</p>{/if}

  <!-- Sharing -->
  <section class="card">
    <h2>Sharing</h2>

    <div class="field">
      <label class="lbl" for="folder">Shared folder</label>
      <p class="help">
        Everything inside this folder is reachable by anyone who has the
        PIN. Nothing outside it is.
      </p>
      <div class="row">
        <input id="folder" class="input mono" value={bridge.settings.sharedFolder} readonly />
        <button class="btn" onclick={chooseFolder}>Choose…</button>
      </div>
    </div>

    <div class="field">
      <label class="lbl" for="pin">PIN</label>
      <p class="help">
        Devices type this to connect. Changing it disconnects everyone
        until they enter the new one.
      </p>
      <div class="row">
        <input id="pin" class="input mono" bind:value={pinDraft} maxlength="32" />
        <button class="btn" onclick={savePin} disabled={pinDraft === bridge.settings.pin}>
          Save
        </button>
        <button class="btn ghost" onclick={newPin}>Generate</button>
      </div>
    </div>
  </section>

  <!-- Network -->
  <section class="card">
    <h2>Network</h2>

    <div class="grid2">
      <div class="field">
        <label class="lbl" for="https">Device port</label>
        <p class="help">
          The port phones and other computers connect to. Whether it's
          encrypted depends on the Connection setting on Overview.
        </p>
        <input id="https" class="input mono" bind:value={httpsDraft} inputmode="numeric" />
      </div>

      <div class="field">
        <label class="lbl" for="http">Local port</label>
        <p class="help">This machine only. Never reachable from the network.</p>
        <input id="http" class="input mono" bind:value={httpDraft} inputmode="numeric" />
      </div>
    </div>

    <div class="row">
      <button class="btn" onclick={savePorts} disabled={!portsChanged}>Save ports</button>
      {#if portsChanged}
        <span class="help inline">Takes effect after a restart.</span>
      {/if}
    </div>

    <div class="field">
      <span class="lbl">Certificate fingerprint</span>
      <p class="help">
        Identifies this PC's certificate. When a browser warns that the
        connection isn't private, you can check these characters match
        before continuing.
      </p>
      <button class="fingerprint mono" onclick={copyFingerprint}>
        {bridge.server.fingerprint || "—"}
      </button>
    </div>
  </section>

  <!-- Appearance & startup -->
  <section class="card">
    <h2>Appearance</h2>

    <div class="field">
      <span class="lbl">Theme</span>
      <div class="segmented" role="group" aria-label="Theme">
        {#each [["system", "System"], ["dark", "Dark"], ["light", "Light"]] as [value, label] (value)}
          <button
            class="seg"
            class:on={bridge.settings.theme === value}
            aria-pressed={bridge.settings.theme === value}
            onclick={() => setTheme(value as "system" | "dark" | "light")}
          >
            {label}
          </button>
        {/each}
      </div>
    </div>

    <div class="field">
      <label class="toggle">
        <input
          type="checkbox"
          checked={bridge.settings.startWithWindows}
          onchange={(e) => toggleAutostart(e.currentTarget.checked)}
        />
        <span>Start PC Bridge when I sign in to Windows</span>
      </label>
    </div>
  </section>

  <!-- About -->
  <section class="card">
    <h2>This PC</h2>
    <dl class="kv">
      <dt class="lbl">Name</dt>
      <dd class="mono">{bridge.server.hostname}</dd>
      <dt class="lbl">System</dt>
      <dd class="mono">{bridge.server.platform}</dd>
      <dt class="lbl">Local address</dt>
      <dd class="mono">{bridge.server.lanAddress ?? "not on a network"}</dd>
      <dt class="lbl">Tailscale</dt>
      <dd class="mono">{bridge.server.tailscaleAddress ?? "not running"}</dd>
      <dt class="lbl">Disk</dt>
      <dd class="mono">
        {#if bridge.server.storageTotal > 0}
          {formatBytes(bridge.server.storageFree)} free of
          {formatBytes(bridge.server.storageTotal)}
        {:else}
          unknown
        {/if}
      </dd>
    </dl>
  </section>
</div>

<style>
  .page {
    display: flex;
    flex-direction: column;
    gap: var(--sp-4);
    padding: var(--sp-6) var(--sp-5);
    max-width: 720px;
  }

  h1 {
    font-size: var(--fs-xl);
  }

  h2 {
    font-size: var(--fs-md);
    letter-spacing: -0.02em;
  }

  .lede {
    color: var(--muted);
    margin: var(--sp-1) 0 0;
    max-width: 58ch;
  }

  .card {
    border: 1px solid var(--line);
    border-radius: var(--r-lg);
    background: var(--raised);
    backdrop-filter: var(--blur);
    -webkit-backdrop-filter: var(--blur);
    box-shadow: var(--shadow-md);
    padding: var(--sp-5);
    display: flex;
    flex-direction: column;
    gap: var(--sp-4);
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .lbl {
    font-family: var(--mono);
    font-size: var(--fs-xs);
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: var(--dim);
  }

  .help {
    margin: 0;
    font-size: var(--fs-sm);
    color: var(--muted);
    max-width: 56ch;
  }

  .help.inline {
    align-self: center;
  }

  .row {
    display: flex;
    gap: var(--sp-2);
    align-items: center;
    flex-wrap: wrap;
  }

  .input {
    flex: 1;
    min-width: 160px;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--r-sm);
    color: var(--text);
    padding: 9px 12px;
    font-size: var(--fs-sm);
    transition: border-color var(--fast) var(--ease);
  }

  .input:focus {
    outline: none;
    border-color: var(--signal-line);
  }

  .input[readonly] {
    color: var(--muted);
  }

  .btn {
    border-radius: var(--r-sm);
    padding: 9px 16px;
    font-family: var(--sans);
    font-size: var(--fs-sm);
    font-weight: 600;
    border: 1px solid var(--line);
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
    transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease);
  }

  .btn:hover:not(:disabled) {
    background: var(--raised-hover);
    border-color: color-mix(in srgb, var(--text) 18%, var(--line));
  }

  .btn:disabled {
    opacity: 0.45;
    cursor: default;
  }

  .btn.ghost {
    background: transparent;
    border-color: transparent;
    color: var(--muted);
  }

  .btn.ghost:hover:not(:disabled) {
    background: var(--raised);
  }

  .fingerprint {
    text-align: left;
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--r-sm);
    color: var(--muted);
    padding: 9px 12px;
    font-size: var(--fs-xs);
    cursor: pointer;
    word-break: break-all;
    line-height: 1.7;
    transition: border-color var(--fast) var(--ease), color var(--fast) var(--ease);
  }

  .fingerprint:hover {
    color: var(--text);
    border-color: var(--signal-line);
  }

  .segmented {
    display: inline-flex;
    gap: 3px;
    border: 1px solid var(--line);
    border-radius: var(--r-sm);
    background: var(--surface);
    padding: 3px;
    width: fit-content;
  }

  .seg {
    background: transparent;
    border: 0;
    border-radius: 7px;
    padding: 7px 16px;
    color: var(--muted);
    font-family: var(--sans);
    font-size: var(--fs-sm);
    cursor: pointer;
    transition: background var(--fast) var(--ease), color var(--fast) var(--ease);
  }

  .seg.on {
    background: var(--signal-grad);
    color: var(--on-signal);
    font-weight: 600;
    box-shadow: var(--shadow-sm);
  }

  .toggle {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    font-size: var(--fs-sm);
    cursor: pointer;
  }

  .grid2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--sp-4);
  }

  .kv {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 7px var(--sp-4);
    align-items: baseline;
    margin: 0;
    font-size: var(--fs-sm);
  }

  .kv dd {
    margin: 0;
    word-break: break-all;
  }

  .notice,
  .error {
    margin: 0;
    padding: 10px var(--sp-3);
    border-radius: var(--r-sm);
    font-size: var(--fs-sm);
    font-weight: 600;
    border: 1px solid transparent;
  }

  .notice {
    background: var(--live-soft);
    color: var(--live);
    border-color: color-mix(in srgb, var(--live) 30%, transparent);
  }

  .error {
    background: var(--fault-soft);
    color: var(--fault);
    border-color: color-mix(in srgb, var(--fault) 30%, transparent);
  }

  @media (max-width: 720px) {
    .page {
      padding: var(--sp-4);
    }
    .grid2 {
      grid-template-columns: 1fr;
    }
  }
</style>
