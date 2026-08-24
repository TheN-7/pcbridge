<script lang="ts">
  import { bridge } from "$lib/state/bridge.svelte";

  // One at a time, oldest first. Stacking several dialogs would make it
  // easy to approve the wrong one by reflex — and approving is the
  // decision that hands someone your files.
  const pending = $derived(bridge.pendingSessions);
  const current = $derived(pending[0] ?? null);

  let busy = $state(false);

  async function decide(approve: boolean, remember: boolean) {
    if (!current || busy) return;
    busy = true;
    await bridge.resolveSession(current.id, approve, remember);
    busy = false;
  }
</script>

{#if current}
  <div class="scrim" role="dialog" aria-modal="true" aria-labelledby="approval-title">
    <div class="panel">
      <span class="eyebrow">Device wants to connect</span>
      <h2 id="approval-title">{current.label}</h2>
      <p class="addr mono">{current.address}</p>

      <p class="body">
        It entered the correct PIN. If you allow it, it can browse and
        download everything in your shared folder, and send files to it.
      </p>

      {#if pending.length > 1}
        <p class="more">{pending.length - 1} more waiting behind this one.</p>
      {/if}

      <div class="actions">
        <button class="btn primary" disabled={busy} onclick={() => decide(true, false)}>
          Allow once
        </button>
        <button class="btn" disabled={busy} onclick={() => decide(true, true)}>
          Allow and remember
        </button>
        <button class="btn danger" disabled={busy} onclick={() => decide(false, false)}>
          Deny
        </button>
      </div>

      <p class="note">
        "Remember" skips this prompt for this device next time. You can
        undo it from Devices.
      </p>
    </div>
  </div>
{/if}

<style>
  .scrim {
    position: fixed;
    inset: 0;
    z-index: 50;
    display: grid;
    place-items: center;
    padding: var(--sp-4);
    background: color-mix(in srgb, var(--ink) 78%, transparent);
    backdrop-filter: blur(3px);
  }

  .panel {
    width: min(400px, 100%);
    border: 1px solid var(--signal-line);
    border-radius: var(--r-lg);
    background: var(--raised);
    padding: var(--sp-5);
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
  }

  .eyebrow {
    font-family: var(--mono);
    font-size: var(--fs-xs);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--signal);
  }

  h2 {
    font-size: var(--fs-lg);
    margin: 0;
  }

  .addr {
    margin: 0;
    font-size: var(--fs-xs);
    color: var(--dim);
  }

  .body {
    margin: var(--sp-2) 0 0;
    font-size: var(--fs-sm);
    color: var(--muted);
  }

  .more {
    margin: 0;
    font-size: var(--fs-xs);
    color: var(--warn);
  }

  .actions {
    display: flex;
    flex-direction: column;
    gap: var(--sp-2);
    margin-top: var(--sp-3);
  }

  .btn {
    border-radius: var(--r-sm);
    padding: 11px 14px;
    font-family: var(--sans);
    font-size: var(--fs-sm);
    font-weight: 600;
    border: 1px solid var(--line);
    background: transparent;
    color: var(--text);
    cursor: pointer;
  }

  .btn:hover:not(:disabled) {
    background: var(--surface);
  }

  .btn:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .btn.primary {
    background: var(--signal);
    color: var(--on-signal);
    border-color: var(--signal);
  }

  .btn.danger {
    color: var(--fault);
    border-color: color-mix(in srgb, var(--fault) 35%, transparent);
  }

  .note {
    margin: var(--sp-2) 0 0;
    font-size: var(--fs-xs);
    color: var(--dim);
  }
</style>
