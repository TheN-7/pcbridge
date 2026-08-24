<script lang="ts">
  import "$lib/styles/tokens.css";
  import { page } from "$app/state";
  import { onMount } from "svelte";
  import { bridge } from "$lib/state/bridge.svelte";
  import BrowserApp from "$lib/browser/BrowserApp.svelte";
  import ApprovalPrompt from "$lib/components/ApprovalPrompt.svelte";

  // The desktop window and a browser are different products, not the same
  // product with things hidden. Branching at the root keeps it that way:
  // a browser never even loads the screens it isn't allowed to use.
  const isDesktop =
    typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;

  let { children } = $props();

  // Opening the stream here, once, is what makes every screen live: the
  // store is shared, so a snapshot pushed while you're on Settings also
  // updates Overview behind it.
  onMount(() => {
    if (!isDesktop) return;
    bridge.connect();
    return () => bridge.disconnect();
  });

  // The theme is a setting like any other, so it arrives over the same
  // stream — change it on the PC and a connected phone re-themes too.
  $effect(() => {
    const theme = bridge.settings.theme;
    if (typeof document === "undefined") return;
    if (theme === "system") {
      document.documentElement.removeAttribute("data-theme");
    } else {
      document.documentElement.setAttribute("data-theme", theme);
    }
  });

  // One nav definition drives both the desktop rail and the mobile tab
  // bar — the same UI runs in the Tauri window, a phone browser, and the
  // Android WebView, so these must never drift apart.
  const nav = [
    { href: "/", label: "Overview", glyph: "◈" },
    { href: "/files", label: "My files", glyph: "▤" },
    { href: "/devices", label: "Devices", glyph: "◇" },
    { href: "/transfers", label: "Transfers", glyph: "⇅" },
    { href: "/settings", label: "Settings", glyph: "⚙" },
  ];

  const isActive = (href: string) =>
    href === "/" ? page.url.pathname === "/" : page.url.pathname.startsWith(href);
</script>

{#if !isDesktop}
  <BrowserApp />
{:else}
<div class="shell">
  <ApprovalPrompt />
  <nav class="rail" aria-label="Sections">
    <span class="rail-label">Bridge</span>
    {#each nav.slice(0, 4) as item (item.href)}
      <a href={item.href} class="nav" class:on={isActive(item.href)}>
        <span class="g" aria-hidden="true">{item.glyph}</span>
        <span class="t">{item.label}</span>
      </a>
    {/each}

    <span class="rail-label rail-label-gap">System</span>
    {#each nav.slice(4) as item (item.href)}
      <a href={item.href} class="nav" class:on={isActive(item.href)}>
        <span class="g" aria-hidden="true">{item.glyph}</span>
        <span class="t">{item.label}</span>
      </a>
    {/each}
  </nav>

  <main class="content">
    {#if bridge.connection !== "live"}
      <!-- Says plainly that the numbers aren't real yet, rather than
           showing stale values that look authoritative. -->
      <div class="banner" role="status">
        <span class="dot" aria-hidden="true"></span>
        {bridge.connection === "connecting"
          ? "Connecting to the PC Bridge service…"
          : "Not connected to the PC Bridge service — showing placeholder data."}
      </div>
    {/if}
    {@render children()}
  </main>

  <nav class="tabbar" aria-label="Sections">
    {#each nav as item (item.href)}
      <a href={item.href} class="tab" class:on={isActive(item.href)}>
        <span class="g" aria-hidden="true">{item.glyph}</span>
        <span class="t">{item.label}</span>
      </a>
    {/each}
  </nav>
</div>
{/if}

<style>
  .shell {
    display: grid;
    grid-template-columns: 186px 1fr;
    min-height: 100vh;
    min-height: 100dvh;
    background: var(--ink);
  }

  /* ---------- desktop rail ---------- */
  .rail {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: var(--sp-3) 10px;
    border-right: 1px solid var(--line);
    background: var(--ink);
  }

  .rail-label {
    font-family: var(--mono);
    font-size: 10px;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--dim);
    padding: var(--sp-2) 10px 6px;
  }

  .rail-label-gap {
    margin-top: 10px;
  }

  .nav {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: var(--sp-2) 10px;
    border-radius: var(--r-sm);
    color: var(--muted);
    font-size: var(--fs-sm);
    text-decoration: none;
    transition: background var(--fast) var(--ease),
                color var(--fast) var(--ease);
  }

  .nav:hover {
    background: var(--surface);
    color: var(--text);
  }

  /* Active state carries an inset amber edge rather than a filled
     block — it marks position without shouting over page content. */
  .nav.on {
    background: var(--signal-soft);
    color: var(--text);
    box-shadow: inset 2px 0 0 var(--signal);
  }

  .nav .g {
    width: 15px;
    text-align: center;
    opacity: 0.8;
  }

  .content {
    min-width: 0;
    background: var(--surface);
    overflow-y: auto;
  }

  .banner {
    display: flex;
    align-items: center;
    gap: var(--sp-2);
    padding: var(--sp-2) var(--sp-5);
    background: var(--warn-soft);
    color: var(--warn);
    border-bottom: 1px solid color-mix(in srgb, var(--warn) 30%, transparent);
    font-size: var(--fs-sm);
  }

  .banner .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: currentColor;
    flex: none;
  }

  /* ---------- mobile tab bar ---------- */
  .tabbar {
    display: none;
  }

  @media (max-width: 720px) {
    .shell {
      grid-template-columns: 1fr;
      grid-template-rows: 1fr auto;
    }

    .rail {
      display: none;
    }

    .tabbar {
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      border-top: 1px solid var(--line);
      background: var(--ink);
      /* Clears the iOS home indicator without a fixed magic number. */
      padding-bottom: env(safe-area-inset-bottom, 0);
    }

    .tab {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 3px;
      padding: var(--sp-2) 4px;
      color: var(--dim);
      text-decoration: none;
      font-size: 10px;
      transition: color var(--fast) var(--ease);
    }

    .tab .g {
      font-size: 17px;
      line-height: 1.2;
    }

    .tab.on {
      color: var(--signal);
    }
  }
</style>
