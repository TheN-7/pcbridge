<script lang="ts">
  /**
   * The "point a phone at this" control.
   *
   * Lives in one place because it appears in two — Overview, where you
   * land, and Devices, where connecting is the subject. Two copies of
   * something holding a credential is two chances for one of them to
   * keep showing a code the other has already replaced.
   *
   * Two kinds, and the difference in what they grant is the whole point:
   *
   *   pairing   a one-time code, three minutes, connects outright
   *   standing  the PIN itself, never expires, still needs approval
   */
  import { bridge } from "$lib/state/bridge.svelte";

  /** Nothing to encode without an address, so the buttons are disabled
   *  rather than producing a code that leads nowhere. */
  const reachable = $derived(Boolean(bridge.server.lanAddress));

  let showing = $state<"none" | "pairing" | "standing">("none");
  let pairNonce = $state(0);
  let secondsLeft = $state(0);
  let remember = $state(true);
  let timer: ReturnType<typeof setInterval> | null = null;

  // Changes whenever the PIN does, so the standing image is re-fetched
  // instead of showing a code for the old one. Derived from the PIN
  // rather than being it — the credential has no business in a URL here.
  const standingNonce = $derived(
    Array.from(bridge.settings.pin).reduce(
      (hash, ch) => (hash * 31 + ch.charCodeAt(0)) | 0,
      7,
    ),
  );

  async function showPairing() {
    const issued = await bridge.newPairCode(remember);
    // No URL means the PC has no address a device could reach it on.
    // Bail rather than counting down against a code that can't be shown.
    if (!issued?.url) return;

    pairNonce = Date.now();
    secondsLeft = issued.expiresInSeconds;
    showing = "pairing";

    if (timer) clearInterval(timer);
    timer = setInterval(() => {
      secondsLeft -= 1;
      if (secondsLeft <= 0) hide();
    }, 1000);
  }

  function hide() {
    if (timer) clearInterval(timer);
    timer = null;
    secondsLeft = 0;
    showing = "none";
  }

  // Navigating away must not leave the countdown running.
  $effect(() => () => {
    if (timer) clearInterval(timer);
  });
</script>

<div class="qrblock">
  {#if showing === "pairing"}
    <img class="qr" src={bridge.pairQrUrl(pairNonce)} alt="Pairing code" />
    <p class="note">
      Point a phone's camera at this. It connects straight away — no PIN,
      no prompt. Expires in {secondsLeft}s.
    </p>
    <button class="qrbtn ghost" onclick={hide}>Hide</button>
  {:else if showing === "standing"}
    <img
      class="qr"
      src={bridge.standingQrUrl(standingNonce)}
      alt="Standing code"
    />
    <p class="note">
      Carries the address and the PIN, and never expires — safe to leave
      on screen or print. You still approve the device here, which is what
      makes that safe.
    </p>
    <button class="qrbtn ghost" onclick={hide}>Hide</button>
  {:else}
    <label class="opt">
      <input type="checkbox" bind:checked={remember} />
      <span>Remember whichever device scans it</span>
    </label>
    <div class="row">
      <button class="qrbtn" onclick={showPairing} disabled={!reachable}>
        Show QR code
      </button>
      <button
        class="qrbtn ghost"
        onclick={() => (showing = "standing")}
        disabled={!reachable}
      >
        Standing code
      </button>
    </div>
    <p class="note">
      {#if reachable}
        A QR code connects a phone with no PIN and no prompt, and lasts
        three minutes. A standing code never changes: it fills the PIN in
        for them, and you still approve the device here.
      {:else}
        This PC isn't on a network a device could reach it on.
      {/if}
    </p>
  {/if}
</div>

<style>
  .qrblock {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--sp-2);
  }

  /* A white plate regardless of theme. A QR inverted by a dark
     background is one many scanners refuse outright, and the quiet zone
     only does its job against light. */
  .qr {
    width: 176px;
    height: 176px;
    align-self: center;
    padding: var(--sp-2);
    background: #fff;
    border-radius: var(--r-sm);
  }

  .note {
    margin: 0;
    font-size: var(--fs-sm);
    color: var(--muted);
    max-width: 56ch;
  }

  .row {
    display: flex;
    gap: var(--sp-2);
    flex-wrap: wrap;
  }

  .opt {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    font-size: var(--fs-sm);
    color: var(--muted);
    cursor: pointer;
  }

  .qrbtn {
    border-radius: var(--r-sm);
    padding: var(--sp-2) 14px;
    font-family: var(--sans);
    font-size: var(--fs-sm);
    font-weight: 600;
    border: 1px solid var(--line);
    background: transparent;
    color: var(--text);
    cursor: pointer;
    transition: background var(--fast) var(--ease);
  }

  .qrbtn:hover:not(:disabled) {
    background: var(--surface);
  }

  .qrbtn:disabled {
    opacity: 0.45;
    cursor: default;
  }

  .qrbtn.ghost {
    border-color: transparent;
    color: var(--muted);
  }
</style>
