<script lang="ts">
  /**
   * The transfer bar: what's moving right now, wherever you are.
   *
   * Lives in the layout rather than inside a page, because a transfer
   * outlives the screen that started it. Downloading a folder from
   * Overview and then opening Settings used to hide the transfer
   * completely, which reads as "it stopped" when nothing of the sort has
   * happened.
   *
   * Renders the shared store and owns nothing: it is a view of
   * bridge.activeTransfers, not a second copy of it.
   */
  import { bridge, formatBytes, formatRate } from "$lib/state/bridge.svelte";

  const active = $derived(bridge.activeTransfers);

  // One bar for everything in flight. Percentages are summed by bytes,
  // not averaged across transfers, so a 4 GB file and a 40 KB one don't
  // count equally towards "how far along is this".
  const totals = $derived({
    done: active.reduce((sum, t) => sum + t.bytesDone, 0),
    total: active.reduce((sum, t) => sum + t.bytesTotal, 0),
    rate: active.reduce((sum, t) => sum + (t.rate ?? 0), 0),
  });

  const percent = $derived(
    totals.total > 0 ? Math.min(100, (totals.done / totals.total) * 100) : 0,
  );

  // The name is only useful when there is one thing to name.
  const label = $derived(
    active.length === 1
      ? `${active[0].direction === "upload" ? "↑" : "↓"} ${active[0].name}`
      : `${active.length} transfers`,
  );

  /** Remaining time across everything in flight. Hidden when it would be
   *  a guess: with no measured rate, or nothing known to be left. */
  const eta = $derived.by(() => {
    if (totals.rate < 1 || totals.total <= totals.done) return "";
    const secs = Math.round((totals.total - totals.done) / totals.rate);
    if (secs < 60) return `${secs}s left`;
    if (secs < 3600) return `${Math.round(secs / 60)} min left`;
    return `${(secs / 3600).toFixed(1)} h left`;
  });
</script>

{#if active.length > 0}
  <a class="bar" href="/transfers" aria-label="Transfers in progress">
    <span class="name mono">{label}</span>

    <span
      class="track"
      role="progressbar"
      aria-valuenow={Math.round(percent)}
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <i style="width:{percent}%"></i>
    </span>

    <span class="meta mono">
      {formatBytes(totals.done)}{#if totals.total > 0} / {formatBytes(totals.total)}{/if}
      {#if totals.rate > 0}· {formatRate(totals.rate)}{/if}
      {#if eta}· {eta}{/if}
    </span>
  </a>
{/if}

<style>
  /* Sticky rather than fixed: `.content` is the scroll container, so
     this pins to the bottom of the pane the pages scroll inside without
     needing to know the rail's width or the tab bar's height.

     Opaque, not the usual translucent --signal-soft. Content scrolls
     underneath a sticky element, and a see-through bar with text sliding
     behind it is unreadable exactly while something is transferring. */
  .bar {
    position: sticky;
    bottom: 0;
    z-index: 3;
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding: var(--sp-2) var(--sp-5);
    border-top: 1px solid var(--signal-line);
    background: color-mix(in srgb, var(--signal) 14%, var(--surface));
    color: var(--text);
    text-decoration: none;
    font-size: var(--fs-sm);
  }

  .name {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--signal);
    font-weight: 600;
  }

  .track {
    flex: 1 1 120px;
    min-width: 60px;
    height: 5px;
    border-radius: 3px;
    background: color-mix(in srgb, var(--signal) 18%, transparent);
    overflow: hidden;
  }

  .track i {
    display: block;
    height: 100%;
    background: var(--signal);
    border-radius: 3px;
    transition: width var(--base) var(--ease);
  }

  .meta {
    flex: none;
    font-size: var(--fs-xs);
    color: var(--muted);
    white-space: nowrap;
  }

  @media (max-width: 720px) {
    .bar {
      padding: var(--sp-2) var(--sp-4);
    }
    /* The numbers are the first thing to go when there's no room; the
       name and the bar still say what's happening. */
    .meta {
      display: none;
    }
  }
</style>
