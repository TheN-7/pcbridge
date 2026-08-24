<script lang="ts">
  /**
   * State encoded in shape and color together — an LED inside a pill —
   * so a glance answers "what's happening" without reading the label.
   */
  export type PillState = "live" | "busy" | "off" | "fault";

  let {
    state = "off" as PillState,
    label,
  }: { state?: PillState; label: string } = $props();
</script>

<span class="pill {state}">
  <span class="led" aria-hidden="true"></span>{label}
</span>

<style>
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 4px 11px;
    border-radius: 999px;
    border: 1px solid transparent;
    font-family: var(--mono);
    font-size: var(--fs-xs);
    letter-spacing: 0.05em;
    text-transform: uppercase;
    white-space: nowrap;
  }

  .led {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: currentColor;
    box-shadow: 0 0 0 3px color-mix(in srgb, currentColor 18%, transparent);
  }

  .live {
    background: var(--live-soft);
    color: var(--live);
    border-color: color-mix(in srgb, var(--live) 32%, transparent);
  }

  .busy {
    background: var(--signal-soft);
    color: var(--signal);
    border-color: var(--signal-line);
  }

  .fault {
    background: var(--fault-soft);
    color: var(--fault);
    border-color: color-mix(in srgb, var(--fault) 38%, transparent);
  }

  .off {
    background: transparent;
    color: var(--dim);
    border-color: var(--line);
  }

  /* The busy state is the only one that moves, because it's the only
     one describing something actively in progress. */
  .busy .led {
    animation: pulse 1.4s var(--ease) infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.45; }
  }
</style>
