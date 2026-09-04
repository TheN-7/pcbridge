<script lang="ts">
  import { bridge, type ConnectedClient } from "$lib/state/bridge.svelte";
  import PairQr from "$lib/components/PairQr.svelte";
  import StatusPill from "$lib/components/StatusPill.svelte";

  const live = $derived(bridge.clients.filter((c) => c.streams > 0));
  const recent = $derived(bridge.clients.filter((c) => c.streams === 0));

  const scheme = $derived(bridge.settings.networkMode === "http" ? "http" : "https");
  const address = $derived(bridge.server.lanAddress ?? "");

  // Times arrive as unix seconds so the server needs no date library.
  function ago(unixSeconds: string): string {
    const then = Number(unixSeconds);
    if (!Number.isFinite(then) || then === 0) return "";
    const secs = Math.max(0, Math.floor(Date.now() / 1000) - then);
    if (secs < 10) return "just now";
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.floor(secs / 60)} min ago`;
    if (secs < 86400) return `${Math.floor(secs / 3600)} h ago`;
    return `${Math.floor(secs / 86400)} d ago`;
  }

  function glyphFor(client: ConnectedClient): string {
    const l = client.label.toLowerCase();
    if (l.includes("android") || l.includes("iphone")) return "▫";
    if (l.includes("ipad")) return "▭";
    return "▪";
  }

  let copied = $state(false);
  async function copyAddress() {
    if (!address) return;
    await navigator.clipboard.writeText(`${scheme}://${address}`);
    copied = true;
    setTimeout(() => (copied = false), 1800);
  }
</script>

<div class="page">
  <header class="head">
    <div>
      <h1>Devices</h1>
      <p class="lede">
        Everyone connected to this PC right now. Devices appear when they
        open the app and drop off shortly after they leave.
      </p>
    </div>
    <StatusPill
      state={live.length > 0 ? "live" : "off"}
      label={live.length === 1 ? "1 connected" : `${live.length} connected`}
    />
  </header>

  <!-- How to connect -->
  <section class="card join">
    <span class="label">To connect a device</span>
    <p class="joinhelp">
      Open this address in any browser on the same network, then enter the
      PIN from Overview.
    </p>
    <button class="joinaddr mono" onclick={copyAddress} disabled={!address}>
      {address ? `${scheme}://${address}` : "Not on a network"}
      <span class="hint">{copied ? "copied" : "copy"}</span>
    </button>
    {#if scheme === "https"}
      <p class="joinnote">
        The first visit from each device shows a security warning — tap
        Advanced, then Proceed. It's remembered after that.
      </p>
    {/if}

    <PairQr />
  </section>

  <!-- Connected now -->
  <section>
    <h2 class="label">Connected now</h2>
    {#if live.length === 0}
      <div class="empty">
        <p class="empty-title">Nobody's connected</p>
        <p>
          Open the address above on a phone or another computer and it'll
          appear here.
        </p>
      </div>
    {:else}
      <ul class="list">
        {#each live as c (c.id)}
          <li class="item">
            <span class="glyph" aria-hidden="true">{glyphFor(c)}</span>
            <span class="info">
              <span class="name">{c.label}</span>
              <span class="meta mono">
                {c.address} · connected {ago(c.connectedAt)}
              </span>
            </span>
            <StatusPill state="live" label="Viewing" />
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <!-- Recently seen -->
  {#if recent.length > 0}
    <section>
      <h2 class="label">Recently here</h2>
      <ul class="list">
        {#each recent as c (c.id)}
          <li class="item quiet">
            <span class="glyph" aria-hidden="true">{glyphFor(c)}</span>
            <span class="info">
              <span class="name">{c.label}</span>
              <span class="meta mono">{c.address} · last seen {ago(c.lastSeen)}</span>
            </span>
            <StatusPill state="off" label="Idle" />
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  <p class="foot">
    Anyone with the PIN and this address can browse and change your shared
    folder. Change the PIN on Overview to cut off every device at once.
  </p>
</div>

<style>
  .page {
    display: flex;
    flex-direction: column;
    gap: var(--sp-4);
    padding: var(--sp-6) var(--sp-5);
  }

  .head {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--sp-4);
    flex-wrap: wrap;
  }

  h1 {
    font-size: var(--fs-xl);
  }

  .lede {
    color: var(--muted);
    margin: var(--sp-1) 0 0;
    max-width: 56ch;
  }

  .label {
    font-family: var(--mono);
    font-size: var(--fs-xs);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--dim);
    font-weight: 500;
    margin: 0 0 var(--sp-2);
    display: block;
  }

  .card {
    border: 1px solid var(--line);
    border-radius: var(--r-lg);
    background: var(--raised);
    backdrop-filter: var(--blur);
    -webkit-backdrop-filter: var(--blur);
    box-shadow: var(--shadow-md);
    padding: var(--sp-5);
  }

  .join {
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
  }

  .joinhelp,
  .joinnote {
    margin: 0;
    font-size: var(--fs-sm);
    color: var(--muted);
    max-width: 58ch;
  }

  .joinnote {
    color: var(--dim);
    font-size: var(--fs-xs);
  }

  /* The address is the whole point of this card, so it's typographically
     the loudest thing in it — and the control you press to copy. */
  .joinaddr {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: var(--sp-3);
    background: var(--signal-soft);
    border: 1px solid var(--signal-line);
    border-radius: var(--r-sm);
    color: var(--signal);
    padding: var(--sp-3);
    font-size: var(--fs-md);
    font-weight: 600;
    font-family: var(--mono);
    cursor: pointer;
    text-align: left;
    word-break: break-all;
    transition: background var(--fast) var(--ease);
  }

  .joinaddr:hover:not(:disabled) {
    background: color-mix(in srgb, var(--signal) 22%, var(--signal-soft));
  }

  .joinaddr:disabled {
    color: var(--dim);
    border-color: var(--line);
    cursor: default;
  }









  .hint {
    font-size: 10px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--dim);
    flex: none;
  }

  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
  }

  .item {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding: 12px 14px;
    border: 1px solid var(--line);
    border-radius: var(--r);
    background: var(--raised);
    backdrop-filter: var(--blur);
    -webkit-backdrop-filter: var(--blur);
    transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease);
  }

  .item:hover {
    background: var(--raised-hover);
    border-color: color-mix(in srgb, var(--text) 14%, var(--line));
  }

  .item.quiet {
    opacity: 0.72;
  }

  .glyph {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border-radius: var(--r-sm);
    background: var(--signal-soft);
    color: var(--signal);
    font-size: 15px;
    flex: none;
  }

  .info {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
  }

  .name {
    font-size: var(--fs-sm);
    font-weight: 600;
  }

  .meta {
    font-size: var(--fs-xs);
    color: var(--muted);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .empty {
    border: 1px dashed var(--line);
    border-radius: var(--r-lg);
    background: rgba(233, 240, 247, 0.015);
    padding: var(--sp-6) var(--sp-4);
    text-align: center;
    color: var(--muted);
  }

  .empty-title {
    color: var(--text);
    font-weight: 600;
    margin: 0 0 var(--sp-2);
  }

  .empty p {
    margin: 0 auto;
    max-width: 42ch;
    font-size: var(--fs-sm);
  }

  .foot {
    margin: 0;
    font-size: var(--fs-sm);
    color: var(--dim);
    max-width: 60ch;
  }

  @media (max-width: 720px) {
    .page { padding: var(--sp-4); }
  }
</style>
