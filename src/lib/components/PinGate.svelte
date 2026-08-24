<script lang="ts">
  import { bridge } from "$lib/state/bridge.svelte";

  let pin = $state("");
  let checking = $state(false);
  let wrong = $state(false);
  let input: HTMLInputElement | undefined = $state();

  $effect(() => {
    input?.focus();
  });

  async function submit(event?: Event) {
    event?.preventDefault();
    const value = pin.trim();
    if (!value || checking) return;

    checking = true;
    wrong = false;
    const ok = await bridge.unlock(value);
    checking = false;

    if (!ok) {
      wrong = true;
      pin = "";
      input?.focus();
    }
  }
</script>

<div class="gate">
  <form class="card" onsubmit={submit}>
    <h1>pc<span class="slash">/</span>bridge</h1>
    <p class="help">
      Enter the PIN shown on the PC's Overview screen.
    </p>

    <input
      bind:this={input}
      bind:value={pin}
      class="pin mono"
      type="password"
      inputmode="numeric"
      autocomplete="off"
      aria-label="PIN"
      aria-invalid={wrong}
      placeholder="••••••"
      disabled={checking}
    />

    {#if wrong}
      <p class="error" role="alert">
        That PIN wasn't accepted. Check the PC's Overview screen — it may
        have been changed.
      </p>
    {/if}

    <button class="btn" type="submit" disabled={checking || !pin.trim()}>
      {checking ? "Checking…" : "Connect"}
    </button>
  </form>
</div>

<style>
  .gate {
    min-height: 100vh;
    min-height: 100dvh;
    display: grid;
    place-items: center;
    padding: var(--sp-4);
    background: var(--ink);
  }

  .card {
    width: min(340px, 100%);
    display: flex;
    flex-direction: column;
    gap: var(--sp-3);
  }

  h1 {
    font-family: var(--mono);
    font-size: 30px;
    font-weight: 700;
    letter-spacing: -0.045em;
    margin: 0;
  }

  .slash {
    color: var(--signal);
  }

  .help {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-sm);
  }

  /* Large and widely spaced: this is typed on a phone, often one-handed,
     from a number read off another screen across the room. */
  .pin {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--r);
    color: var(--text);
    padding: var(--sp-3);
    font-size: 22px;
    letter-spacing: 0.35em;
    text-align: center;
    width: 100%;
  }

  .pin:focus {
    border-color: var(--signal);
    outline: none;
  }

  .pin[aria-invalid="true"] {
    border-color: var(--fault);
  }

  .btn {
    border-radius: var(--r);
    padding: var(--sp-3);
    font-family: var(--sans);
    font-size: var(--fs-base);
    font-weight: 600;
    border: 1px solid var(--signal);
    background: var(--signal);
    color: var(--on-signal);
    cursor: pointer;
  }

  .btn:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .error {
    margin: 0;
    color: var(--fault);
    font-size: var(--fs-sm);
  }
</style>
