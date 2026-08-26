<template>
  <div class="all-nodes">
    <p v-if="loading" class="all-nodes__note" role="status">
      Reading the region…
    </p>

    <!-- A failed read is not a verdict, and the sentence says so. The
         dangerous failure of a health panel is silence: an operator who sees
         an empty box on the page they log in to reads it as "nothing is
         wrong", which is the one thing this cannot tell them. -->
    <Message v-else-if="error" severity="error" :closable="false">
      <p class="all-nodes__error">{{ error }}</p>
      <p class="all-nodes__error">
        This says nothing about the centres themselves &mdash; it is this panel
        that could not be read, not the region.
      </p>
      <button type="button" class="all-nodes__retry" @click="load">Try again</button>
    </Message>

    <template v-else>
      <NodeTable :rows="payload.rows" :vocabularies="payload.vocabularies"/>

      <!-- How old this is, and how to make it newer. The homepage is a tab
           people leave open for hours, and a triage table that is quietly
           ninety minutes stale is worse than none: it is read with exactly the
           confidence a fresh one earns. The stamp is the honest part; the
           button is what makes the stamp something a reader can act on. -->
      <p class="all-nodes__stamp" role="status">
        <span>Traffic over {{ payload.window.label.toLowerCase() }}, as of
          {{ formatInstant(payload.generated_at) }}.</span>
        <button
            type="button"
            class="all-nodes__retry"
            :disabled="refreshing"
            @click="load"
        >
          {{ refreshing ? 'Refreshing…' : 'Refresh' }}
        </button>
      </p>
    </template>
  </div>
</template>

<script setup>
/**
 * The region's health on the page everybody lands on.
 *
 * A frame and a fetch. The panel this mounts into is rendered with the rest of
 * the admin home, and everything in here arrives afterwards -- which is the
 * whole reason it is a fetch: `all_nodes_statistics()` is a handful of indexed
 * lookups and one rollup group-by, and that is fine behind a request and a tax
 * on the one page every reader loads if it sits on the critical path of a
 * login.
 *
 * Read once on mount and then only when asked. There is no timer: every admin
 * tab left open in the organisation would run the region's query on it
 * forever, and the rollups underneath only move once an hour.
 *
 * The gap reports are deliberately not here. They are static links, they are
 * rendered by the panel's own template, and they therefore survive this island
 * failing entirely -- which is exactly when a reader most needs the report
 * that says what is missing from the picture.
 */
import {onMounted, ref} from 'vue'
import Message from 'primevue/message'

import NodeTable from './NodeTable.vue'
import {formatInstant} from '@/components/node-statistics/charts/plot.js'

// The colour roles the sparkline is drawn from. Shared with the statistics
// tab rather than chosen again here: one decision about what silence looks
// like, in one file.
import '@/components/node-statistics/charts/roles.css'

const props = defineProps({
  /** Where the region's rows are read from, reversed on the Django side. */
  statisticsUrl: {
    type: String,
    required: true
  },
})

//: The endpoint answers JSON and nothing else, and asking for it by name is
//: what keeps DRF's browsable HTML out of a fetch that would then fail to
//: parse.
const JSON_ONLY = {headers: {'Accept': 'application/json'}}

const payload = ref(null)
const loading = ref(true)
const refreshing = ref(false)
const error = ref('')

async function load() {
  // The first read holds the panel open with a line of its own; a refresh
  // leaves the rows on screen and marks the button instead. A table that
  // blanked itself every time somebody checked whether it was current would
  // be a table nobody checks.
  if (payload.value) {
    refreshing.value = true
  } else {
    loading.value = true
  }

  error.value = ''

  try {
    const response = await fetch(props.statisticsUrl, JSON_ONLY)

    if (!response.ok) {
      throw new Error(`The region could not be read (${response.status}).`)
    }

    payload.value = await response.json()
  } catch (failure) {
    // Only where there is nothing on screen already. A refresh that fails
    // over a table that is merely a few minutes old should not throw the
    // table away -- the stale rows are worth more than an error, and the
    // stamp beside them already says how old they are.
    if (!payload.value) {
      error.value = failure.message || 'The region could not be read.'
    }
  } finally {
    loading.value = false
    refreshing.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.all-nodes__note {
  font-size: 0.85rem;
  color: var(--w-color-text-meta);
  margin: 0;
  /* Held open so the page does not jump between the panel's render and the
     island's first paint. */
  min-height: 12rem;
}

.all-nodes__error {
  margin: 0 0 0.35rem;
}

.all-nodes__stamp {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0.5rem 0 0;
}

.all-nodes__retry {
  background: none;
  border: 0;
  padding: 0;
  font: inherit;
  color: var(--w-color-text-link-default);
  text-decoration: underline;
  cursor: pointer;
}

.all-nodes__retry:disabled {
  color: var(--w-color-text-meta);
  text-decoration: none;
  cursor: default;
}
</style>
