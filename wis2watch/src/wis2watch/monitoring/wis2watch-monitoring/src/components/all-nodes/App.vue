<template>
  <div class="all-nodes">
    <!-- The refresh control, in the panel header where Wagtail puts a panel's
         actions, rather than under the table where it started. It is rendered
         here rather than in the Django template because its state is the
         island's: it spins while a request is in flight and is disabled for
         the duration, and reflecting that onto a server-rendered node would
         mean poking attributes at foreign DOM.

         Present from mount and never removed, including while the first read
         is still in the air and after one has failed. The error below keeps a
         "Try again" of its own: the two are one action at two moments -- this
         is the standing control a reader reaches for on seeing the stamp is
         old, that one appears where their eye already is when it broke. -->
    <Teleport to="#all-nodes-refresh">
      <button
          type="button"
          class="button button--icon text-replace all-nodes__refresh"
          :aria-label="inFlight ? 'Refreshing' : 'Refresh'"
          :disabled="inFlight"
          @click="load"
      >
        <svg
            class="icon"
            :class="inFlight ? 'icon-spinner' : 'icon-rotate'"
            aria-hidden="true"
        >
          <use :href="inFlight ? '#icon-spinner' : '#icon-rotate'"/>
        </svg>
      </button>
    </Teleport>

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

      <!-- How old this is. The button that acts on it is in the header; this
           stays here because it is not an action -- it is a fact about the
           rows, and it belongs against the thing it describes. The homepage is
           a tab people leave open for hours, and a triage table that is
           quietly ninety minutes stale is worse than none: it is read with
           exactly the confidence a fresh one earns. -->
      <p class="all-nodes__stamp" role="status">
        Traffic over {{ payload.window.label.toLowerCase() }}, as of
        {{ formatInstant(payload.generated_at) }}.
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
 * This root carries the panel's own gutter, because Wagtail's dashboard panel
 * pads its *header* and leaves its content flush to the border. `--nodes-
 * gutter` is published here rather than written twice, so the table below can
 * escape it by exactly as much as it was inset.
 */
import {computed, onMounted, ref} from 'vue'
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

//: Whether a request is in the air at all, first read or later one. The two
//: are separate states below because they do different things to the body --
//: one holds it open, the other leaves the rows alone -- but the header
//: button makes no such distinction: something is loading, so it spins.
const inFlight = computed(() => loading.value || refreshing.value)

async function load() {
  // The first read holds the panel open with a line of its own; a refresh
  // leaves the rows on screen and spins the header button instead. A table
  // that blanked itself every time somebody checked whether it was current
  // would be a table nobody checks.
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
/* Wagtail's dashboard panel pads its header by 1.25rem and gives its content
   no padding at all, so an island dropped into it sits flush against the
   border while the heading above is inset. The gutter is put back here, and
   published as a custom property so the table can escape it by exactly the
   amount it was inset by rather than by a number written twice. */
.all-nodes {
  --nodes-gutter: 1.25rem;
  padding: var(--nodes-gutter);
}

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
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: var(--nodes-gutter) 0 0;
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

/* Teleported into the panel header, so it is styled by Wagtail's own
   `.w-panel__controls .button--icon` rule and needs only what that rule does
   not say: what a disabled one looks like, and that the spinner turns. */
.all-nodes__refresh:disabled {
  color: var(--w-color-text-meta);
  cursor: default;
}

.all-nodes__refresh .icon-spinner {
  animation: all-nodes-spin 1s linear infinite;
}

@media (prefers-reduced-motion: reduce) {
  .all-nodes__refresh .icon-spinner {
    animation: none;
  }
}

@keyframes all-nodes-spin {
  to {
    transform: rotate(1turn);
  }
}
</style>
