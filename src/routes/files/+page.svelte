<script lang="ts">
  import { onMount } from "svelte";
  import { bridge, formatBytes } from "$lib/state/bridge.svelte";
  import { browser, formatModified, glyphFor, type Entry } from "$lib/state/files.svelte";

  let fileInput: HTMLInputElement;
  let folderInput: HTMLInputElement;

  onMount(() => browser.open(""));

  // Re-open the root when the shared folder changes — including when it
  // was changed from Settings, or from another device entirely. Without
  // this the screen would keep listing a folder that is no longer shared.
  let lastRoot = $state(bridge.settings.sharedFolder);
  $effect(() => {
    const root = bridge.settings.sharedFolder;
    if (root !== lastRoot) {
      lastRoot = root;
      browser.open("");
    }
  });

  const crumbs = $derived.by(() => {
    const parts = browser.path.split("/").filter(Boolean);
    const acc: { label: string; path: string }[] = [{ label: "Shared", path: "" }];
    let running = "";
    for (const part of parts) {
      running = running ? `${running}/${part}` : part;
      acc.push({ label: part, path: running });
    }
    return acc;
  });

  async function activate(entry: Entry) {
    if (entry.isDir) {
      await browser.enter(entry);
    } else {
      window.location.href = await browser.downloadUrl(entry.name);
    }
  }

  async function newFolder() {
    const name = prompt("Name for the new folder");
    if (name?.trim()) await browser.mkdir(name.trim());
  }

  async function renameEntry(entry: Entry) {
    const next = prompt("New name", entry.name);
    if (next?.trim() && next !== entry.name) await browser.rename(entry.name, next.trim());
  }

  async function deleteEntry(entry: Entry) {
    const what = entry.isDir ? "folder and everything in it" : "file";
    if (confirm(`Delete "${entry.name}"? This removes the ${what} permanently.`)) {
      await browser.remove(entry.name);
    }
  }

  async function deleteSelected() {
    const n = browser.selected.size;
    if (n && confirm(`Delete ${n} item${n === 1 ? "" : "s"} permanently?`)) {
      await browser.removeSelected();
    }
  }
</script>

<div class="page">
  <header class="head">
    <div>
      <h1>My files</h1>
      <p class="sub mono">{bridge.settings.sharedFolder}</p>
    </div>
    <div class="actions">
      <button class="btn" onclick={() => fileInput.click()}>Upload files</button>
      <button class="btn" onclick={() => folderInput.click()}>Upload folder</button>
      <button class="btn" onclick={newFolder}>New folder</button>
    </div>
  </header>

  <nav class="crumbs" aria-label="Folder path">
    {#each crumbs as crumb, i (crumb.path)}
      {#if i > 0}<span class="sep" aria-hidden="true">›</span>{/if}
      {#if i === crumbs.length - 1}
        <span class="cur">{crumb.label}</span>
      {:else}
        <button class="crumb" onclick={() => browser.open(crumb.path)}>{crumb.label}</button>
      {/if}
    {/each}
  </nav>

  {#if browser.error}
    <p class="error" role="alert">{browser.error}</p>
  {/if}

  {#if browser.selected.size > 0}
    <div class="selbar">
      <span class="mono">{browser.selected.size} selected</span>
      <div class="selbar-actions">
        <button class="btn danger" onclick={deleteSelected}>Delete</button>
        <button class="btn ghost" onclick={() => browser.clearSelection()}>Clear</button>
      </div>
    </div>
  {/if}

  <div class="tablewrap">
    <table>
      <thead>
        <tr>
          <th class="pick"><span class="sr">Select</span></th>
          <th>Name</th>
          <th class="right">Size</th>
          <th>Modified</th>
          <th class="right"><span class="sr">Actions</span></th>
        </tr>
      </thead>
      <tbody>
        {#if browser.parent !== null}
          <tr class="up">
            <td></td>
            <td colspan="4">
              <button class="rowbtn" onclick={() => browser.goUp()}>
                <span class="g" aria-hidden="true">⬑</span> ..
              </button>
            </td>
          </tr>
        {/if}

        {#each browser.entries as entry (entry.name)}
          <tr class:selected={browser.selected.has(entry.name)}>
            <td class="pick">
              <input
                type="checkbox"
                checked={browser.selected.has(entry.name)}
                onchange={() => browser.toggle(entry.name)}
                aria-label="Select {entry.name}"
              />
            </td>
            <td>
              <button class="rowbtn" onclick={() => activate(entry)}>
                <span class="g" aria-hidden="true">{glyphFor(entry)}</span>
                <span class="nm">{entry.name}</span>
              </button>
            </td>
            <td class="right mono">{entry.isDir ? "—" : formatBytes(entry.size)}</td>
            <td class="when mono">{formatModified(entry.modified)}</td>
            <td class="right nowrap">
              <button class="mini" onclick={() => renameEntry(entry)}>Rename</button>
              <button class="mini danger" onclick={() => deleteEntry(entry)}>Delete</button>
            </td>
          </tr>
        {/each}

        {#if browser.entries.length === 0 && !browser.loading}
          <tr>
            <td colspan="5" class="empty">
              This folder is empty. Upload something, or drop files here from
              another device.
            </td>
          </tr>
        {/if}
      </tbody>
    </table>
  </div>

  <p class="foot mono">
    {browser.loading ? "Loading…" : `${browser.entries.length} item(s)`}
  </p>

  <input
    bind:this={fileInput}
    type="file"
    multiple
    class="sr"
    onchange={(e) => {
      const f = e.currentTarget.files;
      if (f?.length) browser.upload(f);
      e.currentTarget.value = "";
    }}
  />
  <input
    bind:this={folderInput}
    type="file"
    multiple
    webkitdirectory
    class="sr"
    onchange={(e) => {
      const f = e.currentTarget.files;
      if (f?.length) browser.upload(f);
      e.currentTarget.value = "";
    }}
  />
</div>

<style>
  .page {
    display: flex;
    flex-direction: column;
    gap: var(--sp-3);
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

  .sub {
    margin: 4px 0 0;
    font-size: var(--fs-xs);
    color: var(--dim);
    word-break: break-all;
  }

  .actions {
    display: flex;
    gap: var(--sp-2);
    flex-wrap: wrap;
  }

  .crumbs {
    display: flex;
    align-items: center;
    gap: 7px;
    flex-wrap: wrap;
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

  .crumb:hover {
    text-decoration: underline;
  }

  .sep {
    color: var(--dim);
  }

  .cur {
    color: var(--text);
    font-weight: 700;
  }

  .tablewrap {
    overflow-x: auto;
    border: 1px solid var(--line);
    border-radius: var(--r-lg);
    background: var(--raised);
    backdrop-filter: var(--blur);
    -webkit-backdrop-filter: var(--blur);
    box-shadow: var(--shadow-md);
  }

  table {
    border-collapse: collapse;
    width: 100%;
    min-width: 560px;
  }

  th {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--dim);
    text-align: left;
    padding: 11px 14px;
    background: rgba(233, 240, 247, 0.02);
    border-bottom: 1px solid var(--line);
    font-weight: 500;
  }

  td {
    padding: 9px 14px;
    font-size: var(--fs-sm);
    border-bottom: 1px solid var(--line-soft);
    vertical-align: middle;
  }

  tr:last-child td {
    border-bottom: none;
  }

  tbody tr {
    transition: background var(--fast) var(--ease);
  }

  tbody tr:hover td {
    background: rgba(233, 240, 247, 0.025);
  }

  tr.selected td {
    background: var(--signal-soft);
  }

  .right {
    text-align: right;
  }

  .nowrap {
    white-space: nowrap;
  }

  .pick {
    width: 34px;
  }

  .when {
    font-size: var(--fs-xs);
    color: var(--dim);
    white-space: nowrap;
  }

  /* The row itself is the control — the whole name is clickable rather
     than a small link inside a cell you have to aim at. */
  .rowbtn {
    display: flex;
    align-items: center;
    gap: 10px;
    background: none;
    border: 0;
    padding: 2px 0;
    color: var(--text);
    font: inherit;
    cursor: pointer;
    text-align: left;
    width: 100%;
  }

  .rowbtn:hover .nm {
    color: var(--signal);
  }

  .rowbtn .g {
    opacity: 0.8;
    width: 16px;
    text-align: center;
    flex: none;
  }

  .mini {
    background: none;
    border: 1px solid transparent;
    border-radius: var(--r-sm);
    padding: 3px 8px;
    color: var(--muted);
    font-family: var(--sans);
    font-size: var(--fs-xs);
    cursor: pointer;
  }

  .mini:hover {
    border-color: var(--line);
    color: var(--text);
  }

  .mini.danger:hover {
    color: var(--fault);
    border-color: color-mix(in srgb, var(--fault) 40%, transparent);
  }

  .btn {
    border-radius: var(--r-sm);
    padding: var(--sp-2) 12px;
    font-family: var(--sans);
    font-size: var(--fs-sm);
    font-weight: 600;
    border: 1px solid var(--line);
    background: var(--surface);
    color: var(--text);
    cursor: pointer;
    transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease);
  }

  .btn:hover {
    background: var(--raised-hover);
    border-color: color-mix(in srgb, var(--text) 18%, var(--line));
  }

  .btn.ghost {
    border-color: transparent;
    color: var(--muted);
  }

  .btn.danger {
    color: var(--fault);
    border-color: color-mix(in srgb, var(--fault) 40%, transparent);
  }

  .selbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--sp-3);
    padding: 10px var(--sp-3);
    border: 1px solid var(--signal-line);
    background: var(--signal-soft);
    backdrop-filter: var(--blur);
    -webkit-backdrop-filter: var(--blur);
    border-radius: var(--r);
    font-size: var(--fs-sm);
    font-weight: 600;
  }

  .selbar-actions {
    display: flex;
    gap: var(--sp-2);
  }

  .empty,
  .foot {
    color: var(--dim);
    font-size: var(--fs-sm);
  }

  .empty {
    text-align: center;
    padding: var(--sp-5);
  }

  .foot {
    margin: 0;
    font-size: var(--fs-xs);
  }

  .error {
    margin: 0;
    color: var(--fault);
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

  @media (max-width: 720px) {
    .page {
      padding: var(--sp-4);
    }
  }
</style>
