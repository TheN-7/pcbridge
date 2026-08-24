<script lang="ts">
  import { bridge, formatBytes, formatRate, type Transfer } from "$lib/state/bridge.svelte";

  const active = $derived(
    bridge.transfers.filter((t) => t.state === "active" || t.state === "queued"),
  );
  const finished = $derived(
    bridge.transfers.filter((t) => t.state !== "active" && t.state !== "queued"),
  );

  const totals = $derived.by(() => {
    const done = active.reduce((sum, t) => sum + t.bytesDone, 0);
    const all = active.reduce((sum, t) => sum + t.bytesTotal, 0);
    const rate = active.reduce((sum, t) => sum + (t.rate ?? 0), 0);
    return { done, all, rate };
  });

  function percent(t: Transfer) {
    return t.bytesTotal > 0 ? Math.min(100, (t.bytesDone / t.bytesTotal) * 100) : 0;
  }

  // Worded from this PC's point of view, because that's whose screen
  // this is: a device uploading is something arriving here, and a device
  // downloading is something leaving.
  function stateLabel(t: Transfer) {
    const incoming = t.direction === "upload";
    switch (t.state) {
      case "queued": return "Waiting";
      case "active": return incoming ? "Receiving" : "Sending";
      case "done": return incoming ? "Received" : "Sent";
      case "failed": return "Failed";
      case "cancelled": return "Cancelled";
    }
  }

  function preposition(t: Transfer) {
    return t.direction === "upload" ? "from" : "to";
  }

  /** Remaining time from the current rate. Hidden when it'd be a guess. */
  function eta(t: Transfer) {
    if (t.state !== "active" || !t.rate || t.rate < 1) return "";
    const left = Math.max(0, t.bytesTotal - t.bytesDone);
    const secs = Math.round(left / t.rate);
    if (secs < 60) return `${secs}s left`;
    if (secs < 3600) return `${Math.round(secs / 60)} min left`;
    return `${(secs / 3600).toFixed(1)} h left`;
  }
</script>

<div class="page">
  <header class="head">
    <div>
      <h1>Transfers</h1>
      <p class="lede">
        {#if active.length > 0}
          {formatBytes(totals.done)} of {formatBytes(totals.all)}
          {#if totals.rate > 0}· {formatRate(totals.rate)}{/if}
        {:else}
          Files moving between this PC and the devices connected to it.
        {/if}
      </p>
    </div>
    {#if finished.length > 0}
      <button class="btn ghost" onclick={() => bridge.clearFinishedTransfers()}>
        Clear finished
      </button>
    {/if}
  </header>

  {#if bridge.transfers.length === 0}
    <div class="empty">
      <p class="empty-title">Nothing transferred yet</p>
      <p>
        Uploads and downloads appear here with live progress as they
        happen. <a href="/devices">Devices</a> shows the address to
        connect to.
      </p>
    </div>
  {/if}

  {#if active.length > 0}
    <section>
      <h2 class="label">In progress</h2>
      <ul class="list">
        {#each active as t (t.id)}
          <li class="item active">
            <div class="row">
              <span class="arrow" aria-hidden="true">
                {t.direction === "upload" ? "↑" : "↓"}
              </span>
              <span class="name">{t.name}</span>
              <span class="state mono">{stateLabel(t)}</span>
              <button class="mini" onclick={() => bridge.cancelTransfer(t.id)}>
                Cancel
              </button>
            </div>

            <div
              class="bar"
              role="progressbar"
              aria-valuenow={Math.round(percent(t))}
              aria-valuemin="0"
              aria-valuemax="100"
            >
              <i style="width:{percent(t)}%"></i>
            </div>

            <div class="meta mono">
              <span>{formatBytes(t.bytesDone)} / {formatBytes(t.bytesTotal)}</span>
              <span>{preposition(t)} {t.deviceName}</span>
              {#if t.rate}<span>{formatRate(t.rate)}</span>{/if}
              {#if eta(t)}<span>{eta(t)}</span>{/if}
            </div>
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  {#if finished.length > 0}
    <section>
      <h2 class="label">Finished</h2>
      <ul class="list">
        {#each finished as t (t.id)}
          <li class="item {t.state}">
            <div class="row">
              <span class="arrow" aria-hidden="true">
                {t.direction === "upload" ? "↑" : "↓"}
              </span>
              <span class="name">{t.name}</span>
              <span class="state mono {t.state}">{stateLabel(t)}</span>
            </div>
            <div class="meta mono">
              <span>{formatBytes(t.bytesTotal)}</span>
              <span>{preposition(t)} {t.deviceName}</span>
            </div>
            {#if t.error}
              <p class="err">{t.error}</p>
            {/if}
          </li>
        {/each}
      </ul>
    </section>
  {/if}
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
    font-variant-numeric: tabular-nums;
  }

  .label {
    font-family: var(--mono);
    font-size: var(--fs-xs);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--dim);
    font-weight: 500;
    margin: 0 0 var(--sp-2);
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
    border: 1px solid var(--line);
    border-radius: var(--r);
    background: var(--raised);
    backdrop-filter: var(--blur);
    -webkit-backdrop-filter: var(--blur);
    box-shadow: var(--shadow-sm);
    padding: var(--sp-3) var(--sp-4);
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
  }

  .item.active {
    border-color: var(--signal-line);
    background: var(--signal-soft);
  }

  .item.failed {
    border-color: color-mix(in srgb, var(--fault) 40%, transparent);
  }

  .row {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
  }

  .arrow {
    color: var(--signal);
    font-family: var(--mono);
    flex: none;
  }

  .name {
    flex: 1;
    min-width: 0;
    font-size: var(--fs-sm);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .state {
    font-size: var(--fs-xs);
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    flex: none;
  }

  .state.done { color: var(--live); }
  .state.failed { color: var(--fault); }
  .state.cancelled { color: var(--dim); }

  .bar {
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

  .meta {
    display: flex;
    gap: var(--sp-3);
    flex-wrap: wrap;
    font-size: var(--fs-xs);
    color: var(--muted);
  }

  .err {
    margin: 0;
    font-size: var(--fs-sm);
    color: var(--fault);
  }

  .mini {
    background: none;
    border: 1px solid transparent;
    border-radius: var(--r-sm);
    padding: 3px 9px;
    color: var(--muted);
    font-family: var(--sans);
    font-size: var(--fs-xs);
    cursor: pointer;
    flex: none;
  }

  .mini:hover {
    border-color: var(--line);
    color: var(--text);
  }

  .btn {
    border-radius: var(--r-sm);
    padding: 9px 16px;
    font-size: var(--fs-sm);
    font-weight: 600;
    border: 1px solid var(--line);
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
    transition: background var(--fast) var(--ease);
  }

  .btn.ghost {
    background: transparent;
    border-color: transparent;
    color: var(--muted);
  }

  .btn.ghost:hover {
    background: var(--raised);
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
    max-width: 44ch;
    font-size: var(--fs-sm);
  }

  @media (max-width: 720px) {
    .page { padding: var(--sp-4); }
  }
</style>
