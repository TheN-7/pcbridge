<script lang="ts">
  let { title, note }: { title: string; note: string } = $props();
</script>

<div class="page">
  <h1>{title}</h1>
  <p class="note">{note}</p>
</div>

<style>
  .page {
    display: flex;
    flex-direction: column;
    gap: var(--sp-3);
    padding: var(--sp-5);
    max-width: 780px;
  }

  h1 {
    font-size: var(--fs-xl);
  }

  .note {
    color: var(--muted);
    margin: 0;
    max-width: 60ch;
  }
</style>
