<script lang="ts">
  import LinkChannel from "$lib/components/LinkChannel.svelte";
  import StatusPill from "$lib/components/StatusPill.svelte";
  import { bridge, formatBytes, formatRate } from "$lib/state/bridge.svelte";

  // Nothing is copied into local state. Every value below is read
  // straight from the shared store, so a change made in Settings — or on
  // another device entirely — lands here without this file knowing.
  const server = $derived(bridge.server);
  const settings = $derived(bridge.settings);
  const onlineCount = $derived(bridge.liveClients.length);
  const active = $derived(bridge.activeTransfers);

  let copied = $state("");
  async function copy(value: string, which: string) {
    try {
      await navigator.clipboard.writeText(value);
      copied = which;
      setTimeout(() => (copied = ""), 1600);
    } catch {
      copied = "";
    }
  }

  const scheme = $derived(settings.networkMode === "http" ? "http" : "https");

  /** The address to actually type into a browser on another device. */
  function url(hostPort: string) {
    return `${scheme}://${hostPort}`;
  }

  function toggleServing() {
    if (bridge.serving) bridge.stopServing();
    else bridge.startServing();
  }
</script>

<div class="page">
  <header class="head">
    <h1>Overview</h1>
    <StatusPill
      state={bridge.serving ? "live" : "off"}
      label={bridge.serving ? "Serving" : "Stopped"}
    />
  </header>

  <section class="card">
    <div class="card-head">
      <span class="label">This PC</span>
      <span class="mono host">{server.hostname}</span>
    </div>

    <LinkChannel
      from={server.hostname}
      to={onlineCount === 1 ? "1 device" : `${onlineCount} devices`}
      active={bridge.serving && active.length > 0}
    />

    <div class="field">
      <span class="label">Connection</span>
      <div class="segmented" role="group" aria-label="Connection type">
        <button
          class="seg"
          class:on={scheme === "https"}
          aria-pressed={scheme === "https"}
          onclick={() => bridge.setNetworkMode("https")}
        >
          HTTPS
        </button>
        <button
          class="seg"
          class:on={scheme === "http"}
          aria-pressed={scheme === "http"}
          onclick={() => bridge.setNetworkMode("http")}
        >
          HTTP
        </button>
      </div>
      {#if scheme === "https"}
        <p class="modehelp">
          Encrypted. Browsers show a one-time security warning per device —
          tap Advanced, then Proceed. It's remembered after that.
        </p>
      {:else}
        <p class="modehelp warn">
          No warning, but nothing is encrypted: your PIN and every file
          cross the network in the clear. Only use this on a network you
          trust.
        </p>
      {/if}
    </div>

    <dl class="kv">
      {#if server.lanAddress}
        <dt class="label">Address</dt>
        <dd>
          <button class="copy accent" onclick={() => copy(url(server.lanAddress!), "lan")}>
            {url(server.lanAddress)}
            <span class="hint">{copied === "lan" ? "copied" : "copy"}</span>
          </button>
        </dd>
      {/if}

      {#if server.tailscaleAddress}
        <dt class="label">Tailscale</dt>
        <dd>
          <button class="copy" onclick={() => copy(url(server.tailscaleAddress!), "ts")}>
            {url(server.tailscaleAddress)}
            <span class="hint">{copied === "ts" ? "copied" : "copy"}</span>
          </button>
        </dd>
      {/if}

      <dt class="label">PIN</dt>
      <dd>
        <button class="copy" onclick={() => copy(settings.pin, "pin")}>
          {settings.pin}
          <span class="hint">{copied === "pin" ? "copied" : "copy"}</span>
        </button>
      </dd>

      <dt class="label">Sharing</dt>
      <dd class="mono path">{settings.sharedFolder}</dd>
    </dl>

    <div class="btnrow">
      <button class="btn primary" onclick={toggleServing}>
        {bridge.serving ? "Stop sharing" : "Start sharing"}
      </button>
      <a class="btn" href="/settings">Change folder</a>
      <button class="btn ghost" onclick={() => bridge.regeneratePin()}>New PIN</button>
    </div>

    {#if bridge.lastError}
      <p class="error">{bridge.lastError}</p>
    {/if}
  </section>

  <section class="card">
    <div class="card-head">
      <span class="label">Connected devices</span>
      <a class="linkbtn" href="/devices">See all</a>
    </div>

    {#if bridge.liveClients.length === 0}
      <p class="empty">
        Nobody's connected. Open this PC's address in a browser on your
        phone or another computer.
      </p>
    {:else}
      <ul class="devices">
        {#each bridge.liveClients as c (c.id)}
          <li class="dev">
            <span class="dev-glyph" aria-hidden="true">
              {c.label.toLowerCase().includes("android") ||
              c.label.toLowerCase().includes("iphone")
                ? "▫"
                : "▪"}
            </span>
            <span class="dev-info">
              <span class="dev-name">{c.label}</span>
              <span class="dev-addr mono">{c.address}</span>
            </span>
            <StatusPill state="live" label="Viewing" />
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  {#each active as t (t.id)}
    <section class="xfer" aria-label="Transfer: {t.name}">
      <span class="xfer-name mono">
        {t.direction === "upload" ? "↑" : "↓"} {t.name}
      </span>
      <span
        class="bar"
        role="progressbar"
        aria-valuenow={t.bytesTotal ? Math.round((t.bytesDone / t.bytesTotal) * 100) : 0}
        aria-valuemin="0"
        aria-valuemax="100"
      >
        <i style="width:{t.bytesTotal ? (t.bytesDone / t.bytesTotal) * 100 : 0}%"></i>
      </span>
      <span class="xfer-meta mono">
        {formatBytes(t.bytesDone)} / {formatBytes(t.bytesTotal)}
        {#if t.rate}· {formatRate(t.rate)}{/if}
      </span>
    </section>
  {/each}
</div>

<style>
  .page {
    display: flex;
    flex-direction: column;
    gap: var(--sp-3);
    padding: var(--sp-5);
    max-width: 780px;
  }

  .head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-4);
    margin-bottom: var(--sp-1);
  }

  h1 {
    font-size: var(--fs-xl);
  }

  .card {
    border: 1px solid var(--line);
    border-radius: var(--r);
    background: var(--raised);
    padding: var(--sp-4);
    display: flex;
    flex-direction: column;
    gap: var(--sp-4);
  }

  .card-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-3);
  }

  .host {
    font-size: var(--fs-xs);
    color: var(--dim);
  }

  .linkbtn {
    color: var(--signal);
    font-size: var(--fs-sm);
    text-decoration: none;
  }

  .linkbtn:hover {
    text-decoration: underline;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
  }

  .segmented {
    display: inline-flex;
    border: 1px solid var(--line);
    border-radius: var(--r-sm);
    overflow: hidden;
    width: fit-content;
  }

  .seg {
    background: transparent;
    border: 0;
    padding: 7px 18px;
    color: var(--muted);
    font-family: var(--mono);
    font-size: var(--fs-sm);
    letter-spacing: 0.04em;
    cursor: pointer;
    transition: background var(--fast) var(--ease), color var(--fast) var(--ease);
  }

  .seg + .seg {
    border-left: 1px solid var(--line);
  }

  .seg.on {
    background: var(--signal-soft);
    color: var(--signal);
    font-weight: 700;
  }

  .modehelp {
    margin: 0;
    font-size: var(--fs-sm);
    color: var(--muted);
    max-width: 56ch;
  }

  /* Cleartext is a real downgrade, so it reads as one rather than as a
     neutral alternative. */
  .modehelp.warn {
    color: var(--warn);
  }

  .kv {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 7px var(--sp-4);
    align-items: baseline;
    margin: 0;
  }

  .kv dd {
    margin: 0;
    min-width: 0;
  }

  .path {
    font-size: var(--fs-sm);
    word-break: break-all;
  }

  .copy {
    display: inline-flex;
    align-items: baseline;
    gap: var(--sp-2);
    background: none;
    border: 0;
    padding: 2px 6px;
    margin-left: -6px;
    border-radius: var(--r-sm);
    color: var(--text);
    font-family: var(--mono);
    font-variant-numeric: tabular-nums;
    font-size: var(--fs-sm);
    cursor: pointer;
    text-align: left;
    word-break: break-all;
    transition: background var(--fast) var(--ease);
  }

  .copy:hover {
    background: var(--signal-soft);
  }

  .copy.accent {
    color: var(--signal);
  }

  .hint {
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--dim);
    opacity: 0;
    transition: opacity var(--fast) var(--ease);
  }

  .copy:hover .hint,
  .copy:focus-visible .hint {
    opacity: 1;
  }

  .btnrow {
    display: flex;
    gap: var(--sp-2);
    flex-wrap: wrap;
  }

  .btn {
    border-radius: var(--r-sm);
    padding: var(--sp-2) 14px;
    font-family: var(--sans);
    font-size: var(--fs-sm);
    font-weight: 600;
    border: 1px solid var(--line);
    background: transparent;
    color: var(--text);
    cursor: pointer;
    text-decoration: none;
    display: inline-flex;
    align-items: center;
    transition: background var(--fast) var(--ease);
  }

  .btn:hover {
    background: var(--surface);
  }

  .btn.primary {
    background: var(--signal);
    color: var(--on-signal);
    border-color: var(--signal);
  }

  .btn.primary:hover {
    filter: brightness(1.08);
    background: var(--signal);
  }

  .btn.ghost {
    border-color: transparent;
    color: var(--muted);
  }

  .devices {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
  }

  .dev {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding: 11px 13px;
    border: 1px solid var(--line);
    border-radius: var(--r);
    background: var(--surface);
  }

  .dev-glyph {
    font-size: 17px;
    opacity: 0.85;
    width: 20px;
    text-align: center;
  }

  .dev-info {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }

  .dev-name {
    font-size: var(--fs-sm);
    font-weight: 600;
  }

  .dev-addr {
    font-size: var(--fs-xs);
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .empty,
  .error {
    margin: 0;
    font-size: var(--fs-sm);
  }

  .empty {
    color: var(--muted);
  }

  .error {
    color: var(--fault);
  }

  .xfer {
    display: flex;
    align-items: center;
    gap: 13px;
    border: 1px solid var(--signal-line);
    background: var(--signal-soft);
    border-radius: var(--r);
    padding: var(--sp-3) 14px;
    flex-wrap: wrap;
  }

  .xfer-name {
    font-size: var(--fs-xs);
    color: var(--signal);
    letter-spacing: 0.05em;
  }

  .bar {
    flex: 1;
    min-width: 80px;
    height: 5px;
    border-radius: 3px;
    background: color-mix(in srgb, var(--signal) 18%, transparent);
    overflow: hidden;
  }

  .bar i {
    display: block;
    height: 100%;
    background: var(--signal);
    border-radius: 3px;
    transition: width var(--base) var(--ease);
  }

  .xfer-meta {
    font-size: var(--fs-xs);
    color: var(--muted);
  }

  @media (max-width: 720px) {
    .page {
      padding: var(--sp-4);
    }
    .kv {
      grid-template-columns: 1fr;
      gap: 2px;
    }
    .kv dt {
      margin-top: var(--sp-2);
    }
  }
</style>
