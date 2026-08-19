<template>
  <div class="node-statistics">
    <p v-if="loading && !summary" class="node-statistics__state">
      Reading {{ nodeName }}'s stations&hellip;
    </p>

    <Message v-if="error" severity="error" :closable="false" class="node-statistics__error">
      {{ error }}
    </Message>

    <template v-if="summary">
      <div class="node-statistics__bar">
        <WindowControl
            :model-value="windowKey"
            :windows="summary.windows"
            :busy="loading"
            @update:model-value="choose"
        />
      </div>

      <Message
          v-if="!summary.vantage.active"
          severity="warn"
          :closable="false"
          class="node-statistics__vantage"
      >
        No Global Broker connection is switched on, so nothing here has been
        counted from the world's view of this centre.
      </Message>

      <div class="node-statistics__blocks">
        <div class="node-statistics__standing">
          <p class="node-statistics__eyebrow">
            Right now &mdash; does not move with the window
          </p>

          <p class="node-statistics__headline">
            <strong>{{ counts.transmitting }}</strong>
            of {{ counts.declared_station_count }} declared stations transmitting
          </p>

          <p class="node-statistics__population">
            Of all {{ population }} stations this centre declares or has been
            heard transmitting for:
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
            The headline counts only stations the registry declares; the figures
            above cover every station, declared or not, so a station that was
            never declared and has since stopped is counted as gone quiet rather
            than as undeclared.
            <template v-if="counts.unlocated_station_count">
              {{ counts.unlocated_station_count }} of them carry no coordinates
              and cannot be put on a map.
            </template>
          </p>
        </div>

        <div class="node-statistics__window">
          <p class="node-statistics__eyebrow">
            {{ summary.window.label }} &mdash; moves with the window
          </p>

          <p class="node-statistics__headline">
            <strong>{{ window_stats.reported_station_count }}</strong>
            of {{ window_stats.declared_station_count }} reported at least once
          </p>

          <p class="node-statistics__population">
            {{ count(window_stats.messages_total) }} messages,
            {{ count(window_stats.unattributed_messages_total) }} of them naming
            no station.
          </p>

          <p class="node-statistics__caveats">
            <template v-if="stoppedSince">
              <strong>{{ stoppedSince }}</strong> more stations reported inside
              this window than are transmitting now: they reported and have
              since stopped.
            </template>
            <template v-else>
              Every station that reported inside this window is still
              transmitting.
            </template>
            A station counts here if this centre was heard publishing for it
            once, at any vantage point, so a station the registry never
            declared can be counted into this figure but not into the
            {{ window_stats.declared_station_count }} beside it.
          </p>
        </div>
      </div>

      <section class="node-statistics__panel">
        <h3 class="node-statistics__panel-heading">
          Stations reporting, hour by hour
        </h3>
        <p class="node-statistics__panel-note">
          One bar per whole UTC hour, against every station the registry
          declares &mdash; so the height of a bar is how much of this centre
          was reporting, not how busy it was. Flat 24 hours, whatever window is
          chosen. Message volume is in the words below the chart.
        </p>

        <HourlyChart
            :buckets="summary.now.buckets"
            :hourly="summary.now.hourly"
            :declared="counts.declared_station_count"
        />
      </section>

      <section class="node-statistics__panel">
        <h3 class="node-statistics__panel-heading">
          Stations reporting, day by day
        </h3>

        <template v-if="window_stats.daily">
          <p class="node-statistics__panel-note">
            One bar per UTC day, on the same axis as the hours above, so a
            node-wide outage is a gap the whole width of the panel rather than
            something to be found station by station. Today is included and
            drawn <em>open</em> &mdash; dashed, unclosed on its right &mdash;
            because it is still being counted: it is short every morning, and
            that is not a collapse.
          </p>

          <DailyChart
              :buckets="summary.buckets"
              :daily="window_stats.daily"
              :declared="counts.declared_station_count"
              :as-of="summary.generated_at"
          />
        </template>

        <p v-else class="node-statistics__panel-empty">
          One day is one bar, so there is no series to draw over
          {{ summary.window.label.toLowerCase() }}. Choose a longer window
          above.
        </p>
      </section>

      <Message severity="secondary" :closable="false">
        The station table, the matrix and the map are not drawn yet.
      </Message>
    </template>
  </div>
</template>

<script setup>
/**
 * The node statistics dashboard.
 *
 * Two blocks of figures side by side, and the difference between them is the
 * finding the whole tab exists for. The **standing** ("412 of 500
 * transmitting") is *now*-anchored by definition and does not move when the
 * reader moves the control. The **window coverage** ("478 of 500 reported at
 * least once") does. The gap between them is the 66 stations that reported
 * this month and have since stopped -- which is why they are labelled
 * distinctly, and why they are bound to two different blocks of the payload
 * rather than to two fields of one.
 *
 * The undeclared figure is deliberately outside the denominator. A station
 * transmitting under this centre's topics that nothing declares is a
 * registration gap, not a shortfall against what the centre promised, and
 * counting it into "412 of 500" would have the two numbers describing
 * different populations.
 *
 * **The window lives in the page's querystring**, under the same key and the
 * same values the API takes -- so the link a reader copies out of the address
 * bar reproduces the view they were looking at, and there is no second
 * vocabulary to keep in step with the first. Loading a URL cold reads the
 * window off it before the first request is made.
 *
 * The summary URL is handed in as a prop rather than assembled here. The
 * bundle is built ahead of time, so a path composed inside it is a path
 * nobody can rename from the Django side.
 */
import {computed, onMounted, ref} from 'vue'
import Message from 'primevue/message'

import DailyChart from './DailyChart.vue'
import HourlyChart from './HourlyChart.vue'
import WindowControl from './WindowControl.vue'
// The tab's colour vocabulary, loaded once for the island. Unscoped on
// purpose: a role is not one component's styling, and every surface added
// after this one is bound by the same names.
import './charts/roles.css'

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

//: The querystring key, which is also the API's parameter name and the
//: server's own vocabulary for the values. One string, three places.
const WINDOW_PARAM = 'window'

//: What a reader who has chosen nothing is shown. The server has the same
//: default, so this is only what the first request asks for -- the resolved
//: window is always read back off the response.
const DEFAULT_WINDOW = '24h'

const summary = ref(null)
const loading = ref(true)
const error = ref('')
const windowKey = ref(
    new URLSearchParams(window.location.search).get(WINDOW_PARAM) || DEFAULT_WINDOW
)

const counts = computed(() => summary.value.now)
const window_stats = computed(() => summary.value.window_stats)

function count(value) {
  return value.toLocaleString()
}

// Deliberately not labelled "declared, never heard from" and the like. The
// standings do not partition the declared population -- a station nothing
// declares that stopped months ago is `gone_quiet`, not `undeclared` -- so a
// label naming the registry on a figure that does not filter by it would be a
// wrong number rather than a terse one. The population line above says which
// population these cover, and the headline ratio says which one it counts.
const figures = computed(() => [
  {key: 'transmitting', label: 'Transmitting', value: counts.value.transmitting},
  {key: 'gone_quiet', label: 'Gone quiet', value: counts.value.gone_quiet},
  {
    key: 'never_transmitted',
    label: 'Never heard from',
    value: counts.value.never_transmitted,
  },
  {
    key: 'undeclared_transmitting',
    label: 'Transmitting, undeclared',
    value: counts.value.undeclared_transmitting,
  },
])

// Every station the centre declares or has been heard transmitting for. The
// four standings are exhaustive over exactly this set, which is what makes
// summing them the honest way to state the scale.
const population = computed(() =>
    counts.value.transmitting +
    counts.value.gone_quiet +
    counts.value.never_transmitted +
    counts.value.undeclared_transmitting
)

// The gap the two blocks exist to show, stated rather than left to be worked
// out by subtracting one headline from the other. Never negative: at the
// default window the two count the same day and the coverage can be the
// smaller of the pair, which is not a finding about anything.
const stoppedSince = computed(() =>
    Math.max(
        0,
        window_stats.value.reported_station_count - counts.value.transmitting
    )
)

/** Read the tab over a window, and say so in the address bar. */
async function choose(key) {
  windowKey.value = key
  syncQuerystring(key)

  await load()
}

/**
 * Put the chosen window in the page's URL.
 *
 * Replaced rather than pushed: a reader flipping through four windows to find
 * the one they want has not made four navigations, and a back button that
 * walks back through them is a back button that never leaves the tab. What
 * matters is that the address bar always shows the view on screen, so the
 * link is copyable at any moment.
 */
function syncQuerystring(key) {
  const url = new URL(window.location.href)

  url.searchParams.set(WINDOW_PARAM, key)
  window.history.replaceState(null, '', url)
}

async function load() {
  loading.value = true
  error.value = ''

  try {
    const url = new URL(props.summaryUrl, window.location.origin)
    url.searchParams.set(WINDOW_PARAM, windowKey.value)

    const response = await fetch(url, {headers: {'Accept': 'application/json'}})

    if (!response.ok) {
      throw new Error(await refusal(response))
    }

    summary.value = await response.json()
    // From the response rather than from what was asked for: the server
    // resolves the window, and a page labelling its charts from the request
    // is a page that can disagree with the numbers on them.
    windowKey.value = summary.value.window.key
  } catch (failure) {
    // Said on the page rather than only in the console: a tab that renders
    // nothing at all is indistinguishable from a centre with no stations,
    // which is a real state this dashboard is meant to report.
    error.value = failure.message || 'The statistics could not be read.'
  } finally {
    loading.value = false
  }
}

/**
 * What a refusal says, in the reader's terms.
 *
 * A window nothing offers is the one refusal a reader can act on -- it comes
 * of a hand-edited or a stale link -- and the server names the ones that
 * exist, so the page repeats them rather than reporting a bare 400.
 */
async function refusal(response) {
  const generic = `The statistics could not be read (${response.status}).`

  try {
    const body = await response.json()

    if (body.valid_windows) {
      // The server's own message names the window and the alternatives, but
      // it is written for whoever is holding the API. This is the same
      // refusal in the reader's terms, since a stale bookmark is how they
      // got here.
      return (
          `There is no window called "${windowKey.value}". ` +
          `This page can be read over ${body.valid_windows.join(', ')}.`
      )
    }
  } catch {
    return generic
  }

  return generic
}

onMounted(load)
</script>

<style scoped>
.node-statistics__state {
  color: var(--w-color-text-meta);
}

.node-statistics__bar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 0.75rem;
}

.node-statistics__error,
.node-statistics__vantage {
  margin-bottom: 1rem;
}

/* Side by side where there is room, because the pair is the finding: the
   standing and the window coverage are read against each other or not at
   all. They stack on a narrow page rather than shrinking. */
.node-statistics__blocks {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(22rem, 1fr));
  gap: 1rem;
  margin-bottom: 1rem;
}

.node-statistics__standing,
.node-statistics__window {
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 0.3rem;
  padding: 1rem 1.25rem;
}

.node-statistics__eyebrow {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--w-color-text-meta);
  margin: 0 0 0.5rem;
}

.node-statistics__headline {
  font-size: 1.15rem;
  margin: 0 0 0.75rem;
  color: var(--w-color-text-label);
}

.node-statistics__headline strong {
  font-size: 1.6rem;
}

.node-statistics__population {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0 0 0.5rem;
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

.node-statistics__panel {
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 0.3rem;
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}

.node-statistics__panel-heading {
  font-size: 0.95rem;
  margin: 0 0 0.25rem;
  color: var(--w-color-text-label);
}

.node-statistics__panel-note {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0 0 0.75rem;
  max-width: 70ch;
}

/* A line of text where the chart would be, and nothing else. A dashed box of
   chart height is as visually heavy as a chart, so the default view of the
   tab would open on a page of "not available" rectangles. */
.node-statistics__panel-empty {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0;
  max-width: 70ch;
}

.node-statistics__caveats {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 1rem 0 0;
  max-width: 60ch;
}
</style>
