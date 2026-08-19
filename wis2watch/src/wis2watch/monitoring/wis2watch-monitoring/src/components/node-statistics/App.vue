<template>
  <div class="node-statistics">
    <p v-if="loading && !summary" class="node-statistics__state">
      Reading {{ nodeName }}'s stations&hellip;
    </p>

    <Message v-if="error" severity="error" :closable="false" class="node-statistics__error">
      {{ error }}
    </Message>

    <div v-if="windows.length" class="node-statistics__bar">
      <WindowControl
          :model-value="windowKey"
          :windows="windows"
          :busy="loading"
          @update:model-value="choose"
      />
    </div>

    <template v-if="summary">

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
            <strong>{{ windowStats.reported_station_count }}</strong>
            of {{ windowStats.declared_station_count }} reported at least once
          </p>

          <p class="node-statistics__population">
            {{ count(windowStats.messages_total) }} messages,
            {{ count(windowStats.unattributed_messages_total) }} of them naming
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
            {{ windowStats.declared_station_count }} beside it.
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

        <template v-if="windowStats.daily">
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
              :daily="windowStats.daily"
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

      <section class="node-statistics__panel">
        <h3 class="node-statistics__panel-heading">
          Messages per active station
        </h3>

        <template v-if="windowStats.daily">
          <p class="node-statistics__panel-note">
            How much each station that reported was heard saying, per UTC day.
            Read against the chart above it: a centre whose station count holds
            steady while this climbs is publishing more from the same network,
            which is a different thing from a network that has grown. The line
            <em>breaks</em> where no station reported &mdash; there is no
            per-station figure for a day like that, and a zero would read as
            "every station said nothing".
          </p>

          <RatioChart
              :buckets="summary.buckets"
              :daily="windowStats.daily"
              :as-of="summary.generated_at"
          />
        </template>

        <p v-else class="node-statistics__panel-empty">
          One day is one point, so there is no line to draw over
          {{ summary.window.label.toLowerCase() }}. Choose a longer window
          above.
        </p>
      </section>

      <section class="node-statistics__panel">
        <h3 class="node-statistics__panel-heading">
          Message volume by hour of day, UTC
        </h3>

        <template v-if="windowStats.hour_of_day">
          <p class="node-statistics__panel-note">
            Every 00Z of the window added together, every 01Z, and so on: the
            centre's daily rhythm, and the one chart here that plots messages
            rather than stations. Peaks on the synoptic hours are a centre
            reporting to schedule; a flat profile is one publishing whenever
            observations happen to arrive. Today's hours so far are in the sum,
            so over a short window the hours already past today are summed over
            one day more than the hours still to come.
          </p>

          <HourOfDayChart
              :hour-of-day="windowStats.hour_of_day"
              :window-label="summary.window.label"
          />
        </template>

        <p v-else class="node-statistics__panel-empty">
          Over {{ summary.window.label.toLowerCase() }} this would be the hourly
          chart above, drawn again in messages rather than stations. Choose a
          longer window above to see the rhythm across days.
        </p>
      </section>

    </template>

    <!-- Outside the block above, and that is the whole point of it being a
         second request: the rows arrive on their own and are drawn whether or
         not the figures did. Everything this panel needs to label itself --
         the window it was read over, the threshold quiet is judged by -- is
         echoed on the rows' own payload, so it never reaches into the
         summary and cannot be taken down with it. -->
    <section v-if="rows || stationsError" class="node-statistics__panel">
      <h3 class="node-statistics__panel-heading">
        Stations, what is broken first
      </h3>
      <p class="node-statistics__panel-note">
        Every station this centre declares or has been heard transmitting for,
        sorted so that what has stopped is at the top &mdash; the default sort
        is a filter that hides nothing. Everything here is this centre's own
        observation: a station may transmit under more than one centre's
        topics, and what another centre heard is not on this page.
        The cells trailing each row are that station's availability over the
        window, and they are the rows themselves rather than a second view:
        read across one for when a station stopped, and down a column for
        whether the same stations keep stopping together.
      </p>

      <StationTable
          v-if="rows"
          :stations="rows.stations"
          :buckets="rows.buckets"
          :grain="rows.window.grain"
          :as-of="rows.generated_at"
          :search="table.search"
          :standing="table.standing"
          :sort="table.sort"
          :direction="table.direction"
          :window-label="rows.window.label"
          :stale-after-hours="rows.stale_after_hours"
          @choose="refine"
      />

      <Message v-else severity="error" :closable="false">
        {{ stationsError }}
      </Message>
    </section>

    <!-- Only once the figures are up: before that the line at the top of the
         page is already saying the tab is being read, and two of them is one
         page reporting the same wait twice. -->
    <p v-else-if="loading && summary" class="node-statistics__state">
      Reading {{ nodeName }}'s stations one row at a time&hellip;
    </p>

    <Message v-if="summary" severity="secondary" :closable="false">
      The map is not drawn yet.
    </Message>
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
 * The table below the charts answers the other half of the question. The
 * aggregate says *whether* something is wrong; the rows say *which stations*,
 * all of them at every node size. They arrive on their own request, because
 * the two payloads are differently shaped -- a handful of numbers against a
 * matrix's worth of per-station vectors -- and the figures should not wait for
 * the rows. Sorting, filtering and searching them is done here rather than by
 * the server, and lands in the same querystring the window does.
 *
 * Both URLs are handed in as props rather than assembled here. The bundle is
 * built ahead of time, so a path composed inside it is a path nobody can
 * rename from the Django side.
 */
import {computed, onMounted, ref} from 'vue'
import Message from 'primevue/message'

import DailyChart from './DailyChart.vue'
import HourlyChart from './HourlyChart.vue'
import HourOfDayChart from './HourOfDayChart.vue'
import RatioChart from './RatioChart.vue'
import StationTable from './StationTable.vue'
import WindowControl from './WindowControl.vue'
import {readParam, writeParams} from './querystring.js'
import {STANDING_LABEL} from './standings.js'
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
  stationsUrl: {
    type: String,
    required: true
  },
})

//: The querystring key, which is also the API's parameter name and the
//: server's own vocabulary for the values. One string, three places.
const WINDOW_PARAM = 'window'

//: Both endpoints answer JSON and nothing else, and asking for it by name is
//: what keeps DRF's browsable HTML out of a fetch that would then fail to
//: parse.
const JSON_ONLY = {headers: {'Accept': 'application/json'}}

const summary = ref(null)
// The rows' whole payload rather than just its list, because the panel labels
// itself from the frame around them -- the window they were read over, the
// threshold quiet is judged by. Kept apart from the summary because the two
// arrive separately and on purpose: the headline figures are read long before
// a thousand rows and their vectors have crossed the wire, and a page that
// waits for the rows before drawing the numbers is a page that shows nothing
// while the numbers are already known. Reading the labels off the summary
// instead would put that independence back: one failure, both panels gone.
const rows = ref(null)
const loading = ref(true)
const error = ref('')
const stationsError = ref('')

// Empty until the URL or the reader says otherwise. There is no default
// spelled here: the server owns the list of windows and which of them a
// reader who has chosen nothing is shown, and a second copy of that on this
// side is a page that can offer a window the API would refuse.
const windowKey = ref(readParam(WINDOW_PARAM))

//: What the table is showing, under the same rule the window is: in the
//: address bar, so the link a reader copies reproduces the rows they were
//: looking at and not merely the node they were on. Read off the URL cold,
//: before any request is made.
const table = ref({
  search: readParam('q'),
  standing: readParam('standing'),
  sort: readParam('sort'),
  direction: readParam('dir') || 'asc',
})

// The windows on offer, kept beside the summary rather than read out of it,
// so that a refusal can seed them too: a reader who arrives on a stale link
// needs the control more than anyone, and a page with only an error on it is
// a dead end.
const windows = ref([])

const counts = computed(() => summary.value.now)
const windowStats = computed(() => summary.value.window_stats)

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
  {
    key: 'transmitting',
    label: STANDING_LABEL.transmitting,
    value: counts.value.transmitting,
  },
  {
    key: 'gone_quiet',
    label: STANDING_LABEL.gone_quiet,
    value: counts.value.gone_quiet,
  },
  {
    key: 'never_transmitted',
    label: STANDING_LABEL.never_transmitted,
    value: counts.value.never_transmitted,
  },
  {
    key: 'undeclared_transmitting',
    label: STANDING_LABEL.undeclared,
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
        windowStats.value.reported_station_count - counts.value.transmitting
    )
)

/** Read the tab over a window, and say so in the address bar. */
async function choose(key) {
  windowKey.value = key
  writeParams({[WINDOW_PARAM]: key})

  await load()
}

/**
 * Narrow the table, and say so in the address bar.
 *
 * No request goes out. Every row is already here -- they arrived for the
 * matrix -- so sorting, searching and filtering are a re-render, and asking
 * the server to do any of it would be a round trip to reorder a list in
 * memory. What is worth persisting is the *view*, which is why it goes in the
 * querystring under the same rule the window does.
 */
function refine(chosen) {
  table.value = {...table.value, ...chosen}

  writeParams({
    q: table.value.search,
    standing: table.value.standing,
    sort: table.value.sort,
    // Never on its own: a direction with nothing sorted by it is a link that
    // says something about a sort that is not happening.
    dir: table.value.sort ? table.value.direction : '',
  })
}

/**
 * Read the tab, both requests at once.
 *
 * Two requests rather than one, split by the shape of what comes back: the
 * headline figures are a handful of numbers and the rows are a matrix's worth
 * of vectors. Started together and drawn as each arrives, so the numbers are
 * on the page while the rows are still crossing the wire -- and a failure of
 * one leaves the other standing, which matters most for the rows: a centre
 * with a thousand stations is where the slow request is, and losing the whole
 * tab to it would be the worst trade on the page.
 */
async function load() {
  loading.value = true

  await Promise.all([loadSummary(), loadStations()])

  loading.value = false
}

async function loadSummary() {
  error.value = ''

  try {
    const response = await fetch(windowed(props.summaryUrl), JSON_ONLY)

    if (!response.ok) {
      throw new Error(await refusal(response))
    }

    summary.value = await response.json()
    windows.value = summary.value.windows
    // From the response rather than from what was asked for: the server
    // resolves the window, and a page labelling its charts from the request
    // is a page that can disagree with the numbers on them.
    windowKey.value = summary.value.window.key
  } catch (failure) {
    // Said on the page rather than only in the console: a tab that renders
    // nothing at all is indistinguishable from a centre with no stations,
    // which is a real state this dashboard is meant to report.
    error.value = failure.message || 'The statistics could not be read.'
  }
}

async function loadStations() {
  stationsError.value = ''

  try {
    const response = await fetch(windowed(props.stationsUrl), JSON_ONLY)

    if (!response.ok) {
      throw new Error(`The stations could not be read (${response.status}).`)
    }

    // All of them, and never merged into what is already on screen: a window
    // change re-reads every row, and rows left over from the last window
    // would carry its message counts under this window's label.
    rows.value = await response.json()
  } catch (failure) {
    stationsError.value = failure.message || 'The stations could not be read.'
  }
}

/** One of the tab's endpoints, asked over the window the reader chose. */
function windowed(endpoint) {
  const url = new URL(endpoint, window.location.origin)

  if (windowKey.value) {
    url.searchParams.set(WINDOW_PARAM, windowKey.value)
  }

  return url
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
      // The control is rendered from these, so the reader can choose their
      // way out of a stale link rather than being left with the refusal.
      windows.value = body.valid_windows.map((key) => ({key, label: key}))

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
