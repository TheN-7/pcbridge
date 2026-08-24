<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import {
    session,
    formatBytes,
    formatModified,
    glyphFor,
    type Entry,
  } from "./session.svelte";

  let pin = $state("");
  let pinInput: HTMLInputElement | undefined = $state();
  let fileInput: HTMLInputElement | undefined = $state();

  onMount(() => session.start());
  onDestroy(() => session.stop());

  $effect(() => {
    if (session.phase === "pin") pinInput?.focus();
  });

  const crumbs = $derived.by(() => {
    const parts = session.path.split("/").filter(Boolean);
    const acc: { label: string; path: string }[] = [{ label: "Shared", path: "" }];
    let running = "";
    for (const part of parts) {
      running = running ? `${running}/${part}` : part;
      acc.push({ label: part, path: running });
    }
    return acc;
  });

  const allSelected = $derived(
    session.entries.length > 0 && session.selected.size === session.entries.length,
  );

  function submit(event: Event) {
    event.preventDefault();
    const value = pin.trim();
    if (value && !session.checking) {
      session.submitPin(value);
      pin = "";
    }
  }

  function activate(entry: Entry) {
    if (entry.isDir) session.enter(entry);
    else session.downloadOne(entry.name);
  }
</script>

{#if session.phase === "pin"}
  <div class="center">
    <form class="card" onsubmit={submit}>
      <h1>pc<span class="slash">/</span>bridge</h1>
      <p class="help">Enter the PIN shown on the PC.</p>
      <input
        bind:this={pinInput}
        bind:value={pin}
        class="pin"
        type="password"
        inputmode="numeric"
        autocomplete="off"
        aria-label="PIN"
        placeholder="••••••"
        disabled={session.checking}
      />
      {#if session.error}<p class="err" role="alert">{session.error}</p>{/if}
      <button class="primary" type="submit" disabled={session.checking || !pin.trim()}>
        {session.checking ? "Checking…" : "Connect"}
      </button>
    </form>
  </div>

{:else if session.phase === "waiting"}
  <div class="center">
    <div class="card">
      <div class="pulse" aria-hidden="true"></div>
      <h1 class="waiting-title">Waiting for approval</h1>
      <p class="help">
        The PIN was correct. Someone at the PC needs to allow this device
        before you can browse. This screen updates on its own.
      </p>
    </div>
  </div>

{:else if session.phase === "denied"}
  <div class="center">
    <div class="card">
      <h1 class="waiting-title">Not allowed</h1>
      <p class="help">
        This device wasn't allowed to connect. Ask whoever is at the PC,
        then try again.
      </p>
      <button class="primary" onclick={() => (session.phase = "pin")}>
        Try again
      </button>
    </div>
  </div>

{:else}
  <div class="browser">
    <header class="top">
      <span class="brand">pc<span class="slash">/</span>bridge</span>
      <button class="ghost" onclick={() => fileInput?.click()}>Upload</button>
    </header>

    <nav class="crumbs" aria-label="Folder path">
      {#each crumbs as crumb, i (crumb.path)}
        {#if i > 0}<span class="sep" aria-hidden="true">›</span>{/if}
        {#if i === crumbs.length - 1}
          <span class="cur">{crumb.label}</span>
        {:else}
          <button class="crumb" onclick={() => session.open(crumb.path)}>
            {crumb.label}
          </button>
        {/if}
      {/each}
    </nav>

    {#if session.error}
      <p class="err bar" role="alert">{session.error}</p>
    {/if}

    <div class="tools">
      <button
        class="ghost sm"
        onclick={() => (allSelected ? session.clearSelection() : session.selectAll())}
      >
        {allSelected ? "Clear" : "Select all"}
      </button>
      <span class="count">
        {session.selected.size > 0
          ? `${session.selected.size} selected · ${formatBytes(session.selectedSize)}`
          : `${session.entries.length} item(s)`}
      </span>
    </div>

    <ul class="list">
      {#if session.parent !== null}
        <li class="row up">
          <button class="rowmain" onclick={() => session.goUp()}>
            <span class="g" aria-hidden="true">⬑</span>
            <span class="nm">..</span>
          </button>
        </li>
      {/if}

      {#each session.entries as entry (entry.name)}
        <li class="row" class:sel={session.selected.has(entry.name)}>
          <input
            type="checkbox"
            class="pick"
            checked={session.selected.has(entry.name)}
            onchange={() => session.toggle(entry.name)}
            aria-label="Select {entry.name}"
          />
          <button class="rowmain" onclick={() => activate(entry)}>
            <span class="g" aria-hidden="true">{glyphFor(entry)}</span>
            <span class="nm">{entry.name}</span>
            <span class="meta">
              {entry.isDir ? "" : formatBytes(entry.size)}
              {#if !entry.isDir}<span class="when">{formatModified(entry.modified)}</span>{/if}
            </span>
          </button>
          {#if entry.isDir}
            <button
              class="zip"
              onclick={() => session.downloadFolder(entry.name)}
              title="Download this folder as a zip"
            >
              ⬇
            </button>
          {/if}
        </li>
      {/each}

      {#if session.entries.length === 0 && !session.loading}
        <li class="empty">This folder is empty.</li>
      {/if}
    </ul>

    <input
      bind:this={fileInput}
      type="file"
      multiple
      class="sr"
      onchange={(e) => {
        const f = e.currentTarget.files;
        if (f?.length) session.upload(f);
        e.currentTarget.value = "";
      }}
    />

    <!-- Sits above the content so a long list never buries the action. -->
    {#if session.selected.size > 0}
      <div class="dock">
        <span class="dockinfo">
          {session.selected.size} selected
        </span>
        <button class="primary" onclick={() => session.downloadSelected()}>
          Download as zip
        </button>
      </div>
    {/if}
  </div>
{/if}

<style>
  .center {
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
    text-align: left;
  }

  h1 {
    font-family: var(--mono);
    font-size: 30px;
    font-weight: 700;
    letter-spacing: -0.045em;
    margin: 0;
  }

  .waiting-title {
    font-size: 22px;
  }

  .slash {
    color: var(--signal);
  }

  .help {
    margin: 0;
    color: var(--muted);
    font-size: var(--fs-sm);
  }

  .pin {
    background: var(--surface);
    border: 1px solid var(--line);
    border-radius: var(--r);
    color: var(--text);
    padding: var(--sp-3);
    font-family: var(--mono);
    font-size: 22px;
    letter-spacing: 0.35em;
    text-align: center;
    width: 100%;
  }

  .pin:focus {
    border-color: var(--signal);
    outline: none;
  }

  /* A slow pulse rather than a spinner: this wait depends on a person
     noticing a prompt, not on a request completing. */
  .pulse {
    width: 44px;
    height: 44px;
    border-radius: 50%;
    background: var(--signal-soft);
    border: 1px solid var(--signal-line);
    animation: breathe 2s var(--ease) infinite;
  }

  @keyframes breathe {
    0%, 100% { opacity: 0.35; transform: scale(0.94); }
    50% { opacity: 1; transform: scale(1); }
  }

  @media (prefers-reduced-motion: reduce) {
    .pulse { animation: none; opacity: 0.8; }
  }

  .primary {
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

  .primary:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .err {
    margin: 0;
    color: var(--fault);
    font-size: var(--fs-sm);
  }

  .err.bar {
    padding: var(--sp-2) var(--sp-4);
    background: var(--fault-soft);
  }

  /* ---------- file browser ---------- */

  .browser {
    min-height: 100vh;
    min-height: 100dvh;
    background: var(--ink);
    display: flex;
    flex-direction: column;
    padding-bottom: 84px;
  }

  .top {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-3);
    padding: var(--sp-3) var(--sp-4);
    border-bottom: 1px solid var(--line);
    position: sticky;
    top: 0;
    background: var(--ink);
    z-index: 2;
  }

  .brand {
    font-family: var(--mono);
    font-weight: 700;
    letter-spacing: -0.03em;
  }

  .ghost {
    background: none;
    border: 1px solid var(--line);
    border-radius: var(--r-sm);
    color: var(--text);
    padding: 6px 12px;
    font-family: var(--sans);
    font-size: var(--fs-sm);
    cursor: pointer;
  }

  .ghost.sm {
    padding: 4px 10px;
    font-size: var(--fs-xs);
    color: var(--muted);
  }

  .crumbs {
    display: flex;
    align-items: center;
    gap: 6px;
    flex-wrap: wrap;
    padding: var(--sp-3) var(--sp-4) var(--sp-2);
    font-family: var(--mono);
    font-size: var(--fs-sm);
  }

  .crumb {
    background: none;
    border: 0;
    padding: 0;
    color: var(--signal);
    font: inherit;
    cursor: pointer;
  }

  .sep { color: var(--dim); }
  .cur { color: var(--text); font-weight: 700; }

  .tools {
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding: 0 var(--sp-4) var(--sp-2);
  }

  .count {
    font-family: var(--mono);
    font-size: var(--fs-xs);
    color: var(--dim);
  }

  .list {
    list-style: none;
    margin: 0;
    padding: 0 var(--sp-2);
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .row {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    border-radius: var(--r-sm);
    padding: 0 var(--sp-2);
  }

  .row.sel {
    background: var(--signal-soft);
  }

  .pick {
    flex: none;
    width: 18px;
    height: 18px;
  }

  /* Generous vertical padding: this is a touch target first and a table
     row second. */
  .rowmain {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    background: none;
    border: 0;
    padding: 13px 4px;
    color: var(--text);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }

  .rowmain .g {
    opacity: 0.8;
    width: 16px;
    text-align: center;
    flex: none;
  }

  .nm {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--fs-sm);
  }

  .meta {
    flex: none;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    font-family: var(--mono);
    font-size: var(--fs-xs);
    color: var(--muted);
  }

  .when {
    color: var(--dim);
    font-size: 10px;
  }

  .zip {
    flex: none;
    background: none;
    border: 1px solid transparent;
    border-radius: var(--r-sm);
    color: var(--muted);
    padding: 6px 9px;
    cursor: pointer;
    font-size: var(--fs-sm);
  }

  .zip:hover {
    border-color: var(--signal-line);
    color: var(--signal);
  }

  .empty {
    padding: var(--sp-6) var(--sp-4);
    text-align: center;
    color: var(--dim);
    font-size: var(--fs-sm);
  }

  .dock {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    display: flex;
    align-items: center;
    gap: var(--sp-3);
    padding: var(--sp-3) var(--sp-4);
    padding-bottom: calc(var(--sp-3) + env(safe-area-inset-bottom, 0px));
    background: var(--surface);
    border-top: 1px solid var(--line);
  }

  .dockinfo {
    flex: 1;
    font-family: var(--mono);
    font-size: var(--fs-xs);
    color: var(--muted);
  }

  .dock .primary {
    padding: 11px 18px;
    font-size: var(--fs-sm);
  }

  .sr {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    border: 0;
  }
</style>
