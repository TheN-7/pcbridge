<script lang="ts">
  /**
   * The signature component: two endpoints and the channel between
   * them. This is the whole product in one element — idle when quiet,
   * flowing amber when bytes are actually moving.
   *
   * It appears in the desktop Overview, the phone header, and the tray
   * flyout, which is what makes those three surfaces feel like one app.
   */
  let {
    from,
    to,
    active = false,
    fromGlyph = "▪",
    toGlyph = "▫",
  }: {
    from: string;
    to: string;
    active?: boolean;
    fromGlyph?: string;
    toGlyph?: string;
  } = $props();
</script>

<div
  class="channel"
  role="img"
  aria-label="{from} linked to {to}{active ? ', transfer in progress' : ''}"
>
  <span class="node">
    <span class="glyph" aria-hidden="true">{fromGlyph}</span>{from}
  </span>

  <span class="wire" class:active></span>

  <span class="node">
    <span class="glyph" aria-hidden="true">{toGlyph}</span>{to}
  </span>
</div>

<style>
  .channel {
    display: flex;
    align-items: center;
    gap: 14px;
    flex-wrap: wrap;
  }

  .node {
    display: flex;
    align-items: center;
    gap: 9px;
    border: 1px solid var(--line);
    background: var(--surface);
    border-radius: var(--r);
    padding: 9px 13px;
    font-family: var(--mono);
    font-size: var(--fs-sm);
    white-space: nowrap;
  }

  .glyph {
    font-size: 15px;
    line-height: 1;
    opacity: 0.85;
  }

  .wire {
    flex: 1;
    min-width: 60px;
    height: 2px;
    border-radius: 2px;
    background: var(--line);
    position: relative;
    overflow: hidden;
  }

  .wire.active::after {
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(
      90deg,
      transparent 0%,
      transparent 32%,
      var(--signal) 50%,
      transparent 68%,
      transparent 100%
    );
    background-size: 220% 100%;
    animation: flow 2.6s linear infinite;
  }

  @keyframes flow {
    from { background-position: 120% 0; }
    to { background-position: -120% 0; }
  }

  /* With motion reduced, an active channel still reads as different
     from an idle one — it just holds the accent instead of animating. */
  @media (prefers-reduced-motion: reduce) {
    .wire.active {
      background: var(--signal-line);
    }
    .wire.active::after {
      animation: none;
      background: none;
    }
  }
</style>
