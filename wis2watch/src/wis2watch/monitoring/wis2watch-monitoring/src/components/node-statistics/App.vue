<template>
  <div class="node-statistics">
    <p v-if="loading" class="node-statistics__state">
      Reading {{ nodeName }}'s stations&hellip;
    </p>

    <Message v-else-if="error" severity="error" :closable="false">
      {{ error }}
    </Message>

    <template v-else-if="summary">
      <Message
          v-if="!summary.vantage.active"
          severity="warn"
          :closable="false"
          class="node-statistics__vantage"
      >
        No Global Broker connection is switched on, so nothing here has been
        counted from the world's view of this centre.
      </Message>

      <div class="node-statistics__standing">
        <p class="node-statistics__headline">
          <strong>{{ counts.transmitting }}</strong>
          of {{ counts.declared_station_count }} declared stations transmitting
        </p>

        <dl class="node-statistics__counts">
          <div
              v-for="figure in figures"
              :key="figure.key"
              class="node-statistics__count"
          >
            <dt>{{ figure.label }}</dt>
            <dd>{{ figure.value }}</dd>
          </div>
        </dl>

        <p class="node-statistics__caveats">
          Standing is flat {{ summary.stale_after_hours }}h, whatever window is
          chosen: a station is transmitting if this centre has been heard
          publishing for it within {{ summary.stale_after_hours }} hours.
          <template v-if="counts.unlocated_station_count">
            {{ counts.unlocated_station_count }} of these stations carry no
            coordinates and cannot be put on a map.
          </template>
        </p>
      </div>

      <Message severity="secondary" :closable="false">
        The charts, the station table and the map are not drawn yet.
      </Message>
    </template>
  </div>
</template>

<script setup>
/**
 * The node statistics dashboard.
 *
 * What is drawn so far is the standing block: how many of the centre's
 * stations are working, how many have stopped, and how many it transmits for
 * without declaring them. Every moving view -- the hourly chart, the daily
 * series, the station table, the availability matrix, the map -- arrives on
 * its own ticket and reads the same endpoint over a window this page does not
 * offer a control for yet.
 *
 * Two things about these numbers that the labels alone do not carry.
 *
 * The standing is *now*-anchored and stays that way. When the window control
 * arrives it will move everything on the tab except this block, because "is
 * this station working" is a question about now rather than about a span.
 *
 * The undeclared figure is deliberately outside the denominator. A station
 * transmitting under this centre's topics that nothing declares is a
 * registration gap, not a shortfall against what the centre promised, and
 * counting it into "412 of 500" would have the two numbers describing
 * different populations.
 *
 * The summary URL is handed in as a prop rather than assembled here. The
 * bundle is built ahead of time, so a path composed inside it is a path
 * nobody can rename from the Django side.
 */
import {computed, onMounted, ref} from 'vue'
import Message from 'primevue/message'

const props = defineProps({
  nodeId: {
    type: Number,
    required: true
  },
  nodeName: {
    type: String,
    required: true
  },
  centreId: {
    type: String,
    required: false,
    default: ''
  },
  summaryUrl: {
    type: String,
    required: true
  },
})

const summary = ref(null)
const loading = ref(true)
const error = ref('')

const counts = computed(() => summary.value.now)

const figures = computed(() => [
  {key: 'gone_quiet', label: 'Gone quiet', value: counts.value.gone_quiet},
  {
    key: 'never_transmitted',
    label: 'Declared, never heard from',
    value: counts.value.never_transmitted,
  },
  {
    key: 'undeclared_transmitting',
    label: 'Transmitting, not declared',
    value: counts.value.undeclared_transmitting,
  },
])

onMounted(async () => {
  try {
    const response = await fetch(props.summaryUrl, {
      headers: {'Accept': 'application/json'},
    })

    if (!response.ok) {
      throw new Error(`The statistics could not be read (${response.status}).`)
    }

    summary.value = await response.json()
  } catch (failure) {
    // Said on the page rather than only in the console: a tab that renders
    // nothing at all is indistinguishable from a centre with no stations,
    // which is a real state this dashboard is meant to report.
    error.value = failure.message || 'The statistics could not be read.'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.node-statistics__state {
  color: var(--w-color-text-meta);
}

.node-statistics__vantage {
  margin-bottom: 1rem;
}

.node-statistics__standing {
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 0.3rem;
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}

.node-statistics__headline {
  font-size: 1.15rem;
  margin: 0 0 0.75rem;
  color: var(--w-color-text-label);
}

.node-statistics__headline strong {
  font-size: 1.6rem;
}

.node-statistics__counts {
  display: flex;
  flex-wrap: wrap;
  gap: 1.5rem;
  margin: 0;
}

.node-statistics__count dt {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
}

.node-statistics__count dd {
  font-size: 1.3rem;
  margin: 0;
  color: var(--w-color-text-label);
}

.node-statistics__caveats {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 1rem 0 0;
  max-width: 60ch;
}
</style>
