<template>
  <section class="drilldown" aria-live="polite">
    <div class="drilldown__bar">
      <h3 class="drilldown__heading">
        <template v-if="station">{{ displayName(station) }}</template>
        <!-- Named as a failure rather than left saying "opening", which is
             what a heading over a refusal would go on claiming. -->
        <template v-else-if="error">This station could not be opened</template>
        <template v-else>Opening the station&hellip;</template>
      </h3>

      <!-- The way back out, and it is the only thing on this panel that is
           always here: a reader who arrived on a link carrying a station has
           no row to click a second time. -->
      <button type="button" class="drilldown__dismiss" @click="$emit('dismiss')">
        Close
      </button>
    </div>

    <Message v-if="error" severity="error" :closable="false">{{ error }}</Message>

    <p v-else-if="!detail" class="drilldown__state">
      Reading this station under {{ centreId }}&hellip;
    </p>

    <template v-else>
      <!-- Repeated rather than assumed from the row this was opened from,
           because `?station=` is a shareable link: whoever opens it may never
           have seen the table. -->
      <dl class="drilldown__identity">
        <div class="drilldown__fact">
          <dt>WIGOS id</dt>
          <dd>{{ station.wigos_id }}</dd>
        </div>
        <div class="drilldown__fact">
          <dt>Standing</dt>
          <dd>
            <span class="drilldown__standing" :class="`drilldown__standing--${station.standing}`">
              {{ standingLabel }}
            </span>
          </dd>
        </div>
        <div class="drilldown__fact">
          <dt>Last heard by this centre</dt>
          <dd>{{ formatInstant(station.last_heard) }}</dd>
        </div>
        <div class="drilldown__fact">
          <dt>Quiet</dt>
          <dd>{{ formatQuiet(station.hours_quiet) }}</dd>
        </div>
        <div class="drilldown__fact">
          <dt>Position</dt>
          <dd>{{ position }}</dd>
        </div>
      </dl>

      <p class="drilldown__note">
        Everything below is <strong>{{ centreId }}</strong>'s own observation of
        this station. A station may transmit under more than one centre's
        topics, and what another centre heard is not on this page.
      </p>

      <section class="drilldown__panel">
        <h4 class="drilldown__panel-heading">Messages, hour by hour</h4>
        <p class="drilldown__panel-note">
          One bar per whole UTC hour, in messages this centre published for this
          station. Flat 24 hours, whatever window is chosen &mdash; "is this
          station working now" is a question about now. An hour drawn as
          diagonals is one this centre published in and named no station at
          all: nothing can be said about this station there, which is not the
          same as its having been silent.
        </p>

        <StationHourlyChart :buckets="detail.now.buckets" :hourly="detail.now.hourly"/>
      </section>

      <section class="drilldown__panel">
        <h4 class="drilldown__panel-heading">
          Heard, day by day
        </h4>

        <template v-if="daily">
          <p class="drilldown__panel-note">
            One cell per UTC day over {{ detail.window.label.toLowerCase() }},
            oldest at the left, in the same three colours the matrix beside the
            rows uses. {{ grainWords.scale }} A day drawn as diagonals is one
            this centre published nothing naming any station on.
          </p>

          <div class="drilldown__strip">
            <PresenceCells
                :values="daily.hours"
                :station-less="daily.stationLess"
                :buckets="detail.buckets"
                :grain="detail.window.grain"
                :ceilings="ceilings"
                :cell-width="cellWidth"
                :height="STRIP_HEIGHT"
                :name="displayName(station)"
            />
          </div>

          <p class="drilldown__axis">
            <span>{{ axisStart }}</span>
            <span>{{ axisEnd }}</span>
          </p>

          <ul class="drilldown__legend">
            <li v-for="state in legend" :key="state.key" class="drilldown__key">
              <span class="drilldown__swatch" :class="`drilldown__swatch--${state.key}`"/>
              {{ state.label }}
            </li>
          </ul>
        </template>

        <p v-else class="drilldown__panel-empty">
          One day is one cell, so there is nothing to lay out over
          {{ detail.window.label.toLowerCase() }}. Choose a longer window above,
          or read the hours in the chart above this.
        </p>
      </section>

      <section class="drilldown__panel">
        <h4 class="drilldown__panel-heading">
          What it publishes under, {{ detail.window.label.toLowerCase() }}
        </h4>
        <p class="drilldown__panel-note">
          Which of this centre's datasets carried this station over the window,
          busiest first, each with the last hour it was heard under that one.
          A station that has stopped under one dataset and not another has
          stopped in a way the row above cannot say.
        </p>

        <table v-if="datasets.length" class="drilldown__datasets">
          <thead>
            <tr>
              <th scope="col">Dataset</th>
              <th scope="col" class="drilldown__number">Messages</th>
              <th scope="col">Last heard</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="entry in datasets" :key="entry.id ?? 'unclaimed'">
              <td>
                <template v-if="entry.id">
                  <span class="drilldown__dataset-title">{{ entry.title }}</span>
                  <span class="drilldown__dataset-id">{{ entry.identifier }}</span>
                </template>
                <!-- Kept rather than dropped: the breakdown is read as an
                     account of the total beside it, and one that does not add
                     up is one a reader has to reconcile by hand. -->
                <span v-else class="drilldown__dataset-title">
                  Traffic on a topic no dataset claims
                </span>
              </td>
              <td class="drilldown__number">{{ formatCount(entry.messages) }}</td>
              <td>{{ formatInstant(entry.last_heard) }}</td>
            </tr>
          </tbody>
        </table>

        <p v-else class="drilldown__panel-empty">
          This centre published nothing at all for this station over
          {{ detail.window.label.toLowerCase() }}, so there is no breakdown to
          give. The charts above are the same finding, drawn.
        </p>

        <!-- The two numbers are two questions, and the sentence keeps them
             apart on purpose. A centre whose traffic never reaches the Global
             Broker reads as "0 messages, heard in 20 of 24 hours" here, which
             is the propagation finding said plainly rather than a
             contradiction a reader has to resolve. -->
        <p class="drilldown__caveats">
          <strong>{{ formatCount(detail.window_stats.messages_total) }}</strong>
          messages for this station reached the Global Broker over the window,
          which is the world's view of it and the only vantage point volumes
          are counted from &mdash; the same publication observed at this
          centre's own broker as well is still one publication. Counting across
          <em>every</em> vantage point instead, this centre was heard
          publishing for it in
          <strong>{{ formatCount(detail.window_stats.active_buckets) }}</strong>
          of {{ formatCount(detail.buckets.length) }} {{ grainWords.period }}.
        </p>
      </section>
    </template>
  </section>
</template>

<script setup>
/**
 * One station of this centre, opened.
 *
 * The last step of the journey the tab exists for. The aggregate says
 * *whether* something is wrong, the rows say *which stations*, and this says
 * what one of them has been doing: its own hours, its own days, and which of
 * the centre's datasets it publishes under.
 *
 * **Its identity and standing are repeated rather than assumed** from the row
 * it was opened from. `?station=<id>` on the page URL is a shareable link, and
 * a link that only makes sense to somebody who still has the table in front of
 * them is not a link.
 *
 * **A third request, on the same terms as the other two.** It arrives on its
 * own and fails on its own, so a station whose drilldown cannot be read leaves
 * the rows and the figures standing. The URL is handed in whole -- window and
 * all -- because the page owns the tab's vocabulary and this owns one request.
 *
 * The panel sits inline above the rows rather than over them. Everything the
 * reader chose lives in the querystring, so dismissing this is a matter of
 * clearing one key: the sort, the filter and the picked bucket are untouched
 * because nothing here ever touched them, and the table below is not unmounted
 * and does not lose its scroll.
 *
 * The same `now` / `window_stats` split the rest of the tab is built on. The
 * hourly chart is the fixed 24 hours and does not move with the control; the
 * day-grain strip and the dataset breakdown do.
 */
import {computed, ref, watch} from 'vue'
import Message from 'primevue/message'

import PresenceCells from './PresenceCells.vue'
import StationHourlyChart from './StationHourlyChart.vue'
import {
  displayName,
  formatCount,
  formatDayLong,
  formatInstant,
  formatQuiet,
} from './charts/plot.js'
import {bucketCeilings, cellWidthFor, grainOf} from './presence.js'
import {STANDING_LABEL} from './standings.js'

const props = defineProps({
  /**
   * The endpoint for this station over the window on screen, reversed by the
   * page and handed here whole. Changing it is what re-reads the panel: a new
   * station and a new window are the same event to this component.
   */
  url: {
    type: String,
    required: true
  },
  /** The centre whose observation this is, for the sentences that name it. */
  centreId: {
    type: String,
    default: ''
  },
})

defineEmits(['dismiss'])

//: Both endpoints on this tab answer JSON and nothing else, and asking for it
//: by name is what keeps DRF's browsable HTML out of a fetch that would then
//: fail to parse.
const JSON_ONLY = {headers: {'Accept': 'application/json'}}

//: How tall the day strip is drawn. Taller than a table row, because this one
//: is not sitting in a row -- it is the panel's own picture, and at row height
//: a 90-day strip is a hairline.
const STRIP_HEIGHT = 26

//: How much wider than the matrix's cells these are drawn. The strip has a
//: whole panel rather than a table column, and the widths `cellWidthFor`
//: gives are chosen for a matrix a thousand rows tall.
const CELL_SCALE = 2

const detail = ref(null)
const error = ref('')

const station = computed(() => detail.value?.station || null)
const standingLabel = computed(
    () => STANDING_LABEL[station.value.standing] || station.value.standing
)

//: What this grain calls its buckets and its scale, from the one place that
//: decides them -- so the sentence under this strip and the sentence under
//: the matrix are the same sentence.
const grainWords = computed(() => grainOf(detail.value?.window.grain))

// The two vectors the strip is drawn from, unpicked from the series here
// rather than in the template: `PresenceCells` takes the positional vectors
// the rows hand it, and the drilldown's series is a list of objects because
// it carries the volume the strip's tooltips say out loud.
const daily = computed(() => {
  const series = detail.value?.window_stats.daily

  if (!series) {
    return null
  }

  return {
    hours: series.map((day) => day.active_hours),
    stationLess: series.map((day) => day.station_less),
  }
})

const ceilings = computed(() =>
    bucketCeilings(
        detail.value.buckets, detail.value.window.grain, detail.value.generated_at
    )
)

const cellWidth = computed(
    () => cellWidthFor(detail.value.buckets.length) * CELL_SCALE
)

const datasets = computed(() => detail.value?.window_stats.datasets || [])

// The strip's two ends in words, for the same reason the matrix carries them:
// a run of cells says when something stopped only if the axis is named.
const axisStart = computed(() => axisLabel(detail.value.buckets[0]))
const axisEnd = computed(
    () => axisLabel(detail.value.buckets[detail.value.buckets.length - 1])
)

const legend = computed(() => [
  {key: 'full', label: grainWords.value.legend.full},
  {key: 'thin', label: grainWords.value.legend.thin},
  {key: 'silent', label: 'Silent: nothing heard from this station'},
  {key: 'nameless', label: 'This centre published, naming no station at all'},
])

const position = computed(() => {
  if (station.value.latitude === null || station.value.longitude === null) {
    return 'No coordinates: this station cannot be put on a map'
  }

  return `${station.value.latitude.toFixed(4)}, ${station.value.longitude.toFixed(4)}`
})

function axisLabel(bucket) {
  return bucket ? formatDayLong(new Date(bucket.start)) : ''
}

/**
 * Read this station.
 *
 * The panel is emptied first rather than left showing the last station under
 * the new one's heading, which is the one state here that would be a lie
 * rather than a wait.
 */
async function load() {
  detail.value = null
  error.value = ''

  try {
    const response = await fetch(props.url, JSON_ONLY)

    if (response.status === 404) {
      // The one refusal a reader can act on, and it comes of a stale or
      // hand-edited link: the station is real and is not this centre's, or is
      // nothing at all. Said in those terms rather than as a bare 404.
      throw new Error(
          'This centre neither declares that station nor has been heard ' +
          'transmitting for it. It may belong to another centre.'
      )
    }

    if (!response.ok) {
      throw new Error(`This station could not be read (${response.status}).`)
    }

    detail.value = await response.json()
  } catch (failure) {
    error.value = failure.message || 'This station could not be read.'
  }
}

watch(() => props.url, load, {immediate: true})
</script>

<style scoped>
/* Marked off from the panels around it, because it is a different thing from
   them: those are the centre, and this is one station of it. The focus colour
   down its left edge is the same mark the tab uses for what the reader
   picked, which is what this panel is. */
.drilldown {
  border: 1px solid var(--w-color-border-furniture);
  border-left: 3px solid var(--stat-focus);
  border-radius: 0.3rem;
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}

.drilldown__bar {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 1rem;
}

.drilldown__heading {
  font-size: 1.05rem;
  margin: 0 0 0.5rem;
  color: var(--w-color-text-label);
}

.drilldown__dismiss {
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 0.2rem;
  background: none;
  color: var(--w-color-text-label);
  font-size: 0.75rem;
  padding: 0.2rem 0.7rem;
  cursor: pointer;
}

.drilldown__state,
.drilldown__note,
.drilldown__panel-note,
.drilldown__panel-empty,
.drilldown__caveats {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0 0 0.75rem;
  max-width: 70ch;
}

.drilldown__caveats {
  margin: 0.75rem 0 0;
}

.drilldown__identity {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1.5rem;
  margin: 0 0 0.75rem;
}

.drilldown__fact dt {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--w-color-text-meta);
}

.drilldown__fact dd {
  margin: 0;
  font-size: 0.9rem;
  color: var(--w-color-text-label);
}

.drilldown__standing {
  font-size: 0.8rem;
}

/* The two standings that mean nothing has been heard lately, in the colour
   the whole tab calls silence. The other two say what they are in words and
   spend no colour on it. */
.drilldown__standing--never_transmitted,
.drilldown__standing--gone_quiet {
  color: var(--stat-silent);
}

.drilldown__panel {
  margin-top: 1rem;
}

.drilldown__panel-heading {
  font-size: 0.9rem;
  margin: 0 0 0.25rem;
  color: var(--w-color-text-label);
}

/* Sideways rather than shrinking: a 90-day strip squeezed into a narrow
   panel is a picture nobody can read a run of days off. */
.drilldown__strip {
  overflow-x: auto;
}

.drilldown__axis {
  display: flex;
  justify-content: space-between;
  font-size: 0.7rem;
  color: var(--w-color-text-meta);
  margin: 0.25rem 0 0;
}

.drilldown__legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.25rem 1.25rem;
  margin: 0.5rem 0 0;
  padding: 0;
  list-style: none;
  font-size: 0.75rem;
  color: var(--w-color-text-meta);
}

.drilldown__key {
  display: flex;
  align-items: center;
  gap: 0.35rem;
}

.drilldown__swatch {
  display: inline-block;
  width: 0.75rem;
  height: 0.6rem;
  border-radius: 1px;
}

.drilldown__swatch--full {
  background: var(--stat-live);
}

.drilldown__swatch--thin {
  background: var(--stat-thin);
}

.drilldown__swatch--silent {
  background: var(--stat-empty);
}

/* The hatch as a swatch, in the same two roles the pattern is drawn from, so
   the legend follows the theme with the cells it explains. */
.drilldown__swatch--nameless {
  background: repeating-linear-gradient(
      45deg,
      var(--stat-hatch-ground) 0 2px,
      var(--stat-hatch-line) 2px 3.5px
  );
}

.drilldown__datasets {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8rem;
}

.drilldown__datasets th,
.drilldown__datasets td {
  text-align: left;
  padding: 0.3rem 0.5rem 0.3rem 0;
  border-bottom: 1px solid var(--w-color-border-furniture);
  vertical-align: top;
}

.drilldown__datasets th {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--w-color-text-meta);
}

.drilldown__number {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.drilldown__dataset-title {
  display: block;
  color: var(--w-color-text-label);
}

.drilldown__dataset-id {
  display: block;
  font-size: 0.7rem;
  color: var(--w-color-text-meta);
  word-break: break-all;
}
</style>
