<template>
  <div ref="root" class="stations" :style="{'--stations-row': `${ROW_HEIGHT}px`}">
    <div class="stations__controls">
      <label class="stations__field">
        <span class="stations__field-label">Search</span>
        <input
            :value="search"
            type="search"
            class="stations__input"
            placeholder="WIGOS id or name"
            @input="choose({search: $event.target.value})"
        >
      </label>

      <label class="stations__field">
        <span class="stations__field-label">Standing</span>
        <select
            :value="standing"
            class="stations__input"
            @change="choose({standing: $event.target.value})"
        >
          <option value="">All standings</option>
          <option v-for="option in STANDINGS" :key="option.key" :value="option.key">
            {{ option.label }}
          </option>
        </select>
      </label>

      <button
          v-if="narrowedByFields"
          type="button"
          class="stations__clear"
          @click="choose({search: '', standing: ''})"
      >
        Clear filter
      </button>

      <!-- Both take what is on screen rather than the whole population, which
           is the rule the wall above follows for the same reason: a file and
           a page that disagree about which stations are in question are two
           answers to one question. What was filtered travels inside the file,
           because nothing else about the page does. -->
      <span v-if="stations.length" class="stations__exports">
        <button type="button" class="stations__export" @click="downloadCsv">
          <svg
              class="stations__export-glyph"
              viewBox="0 0 16 16"
              aria-hidden="true"
              focusable="false"
          >
            <path
                d="M8 2v7.4m0 0L5.1 6.6M8 9.4l2.9-2.8M2.6 11.4v1.4a1 1 0 0 0 1 1h8.8a1 1 0 0 0 1-1v-1.4"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
            />
          </svg>
          CSV
        </button>
        <button type="button" class="stations__export" @click="downloadImage">
          <svg
              class="stations__export-glyph"
              viewBox="0 0 16 16"
              aria-hidden="true"
              focusable="false"
          >
            <path
                d="M8 2v7.4m0 0L5.1 6.6M8 9.4l2.9-2.8M2.6 11.4v1.4a1 1 0 0 0 1 1h8.8a1 1 0 0 0 1-1v-1.4"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
            />
          </svg>
          Image
        </button>
      </span>
    </div>

    <p v-if="exportError" class="stations__export-error" role="status">
      {{ exportError }}
    </p>

    <!-- What the picked bucket did to this list, said out loud and on its
         own line. Its own numbers rather than the combined ones below,
         because a reader who has a search running too cannot otherwise tell
         which of the two hid what -- and because the degenerate case is a
         finding: a bucket in which every station was dark hides nobody, and a
         page that suppressed the sentence there would be a page that looks
         unfiltered on the worst day this centre has had. -->
    <p v-if="picked && stations.length" class="stations__picked" role="status">
      <span class="stations__picked-what">
        Dark {{ grainWords.preposition }} {{ pickedName }}
      </span>
      <template v-if="darkCount === stations.length">
        &mdash; every one of the {{ formatCount(stations.length) }} stations was,
        so this hides none of them.
      </template>
      <template v-else>
        &mdash; {{ formatCount(darkCount) }} of
        {{ formatCount(stations.length) }} stations were, so this hides
        {{ formatCount(stations.length - darkCount) }}.
      </template>
      <button type="button" class="stations__clear" @click="choose({bucket: ''})">
        Clear selection
      </button>
    </p>

    <!-- The station the reader picked, on the map or in these rows. It is
         said in words as well as drawn, because the row carrying the
         highlight can be anywhere in a thousand of them -- and because the
         filter above can hide it altogether, which is a state a highlight
         alone cannot report. -->
    <p v-if="pickedStation" class="stations__station" role="status">
      <span class="stations__picked-what">{{ displayName(pickedStation) }}</span>
      <template v-if="pickedRow !== -1">
        &mdash; picked, and highlighted in the rows below.
      </template>
      <template v-else>
        &mdash; picked, and hidden here by the filter above.
      </template>
      <button type="button" class="stations__clear" @click="choose({station: ''})">
        Clear station
      </button>
    </p>

    <!-- The population header. Whatever is on screen, this says what the whole
         of it is -- and where a filter is on, what it took away. A count of
         rows with nothing saying how many there were is the number a reader
         quietly mistakes for the population. -->
    <p class="stations__population" role="status">
      <template v-if="!stations.length">
        This centre declares no stations, and nothing has been heard
        transmitting under its topics.
      </template>
      <template v-else-if="!filtering">
        All <strong>{{ formatCount(stations.length) }}</strong> stations, none hidden.
      </template>
      <template v-else-if="hidden">
        <strong>{{ formatCount(shown.length) }}</strong> of
        {{ formatCount(stations.length) }} stations &mdash;
        {{ formatCount(hidden) }} hidden by the filter.
      </template>
      <template v-else>
        All <strong>{{ formatCount(stations.length) }}</strong> stations match the
        filter, so it is hiding none of them.
      </template>
    </p>


    <div v-if="stations.length" ref="viewport" class="stations__scroll" @scroll="onScroll">
      <table class="stations__table">
        <colgroup>
          <col v-for="column in COLUMNS" :key="column.key" :style="{width: column.width}">
          <col v-if="showsShape" :style="{width: SPARK_WIDTH}">
          <col :style="{width: `${matrixWidth + CELL_PADDING}px`}">
        </colgroup>

        <thead ref="header">
        <tr>
          <th
              v-for="column in COLUMNS"
              :key="column.key"
              scope="col"
              :class="[`stations__cell--${column.align || 'text'}`]"
              :aria-sort="ariaSort(column.key)"
          >
            <button
                type="button"
                class="stations__sort"
                :class="{'stations__sort--on': sort === column.key}"
                @click="sortBy(column.key)"
            >
              {{ column.label }}
              <span aria-hidden="true">{{ arrow(column.key) }}</span>
            </button>
          </th>
          <th v-if="showsShape" scope="col">
            24h activity
            <span class="stations__column-note">shape, flat 24h</span>
          </th>
          <!-- The matrix's own heading, and the only axis it has. Both ends
               are placed off the same cell width the cells are drawn at, so
               the labels cannot come to sit over the wrong column. -->
          <th scope="col">
            {{ windowLabel }}
            <span class="stations__column-note stations__axis" :style="{width: `${matrixWidth}px`}">
              <span>{{ axisStart }}</span>
              <span>{{ axisEnd }}</span>
            </span>
            <!-- One head per column, and picking one filters the rows below
                 to the stations that were dark in it. It sits in the header
                 because that is where a reader who has just read a band down
                 the matrix is already looking, and it is drawn at the cell
                 width the rows use, so a head cannot come to sit over the
                 wrong column. -->
            <BucketHeads
                v-if="buckets.length"
                :buckets="buckets"
                :grain="grain"
                :cell-width="cellWidth"
                :selected="bucket"
                :window-label="windowLabel"
                @select="choose({bucket: $event})"
            />
          </th>
        </tr>
        </thead>

        <tbody>
        <!-- The rows above the ones drawn, as height and nothing else. The
             scrollbar measures the whole population this way, so a reader
             is never told a thousand stations are a screenful. -->
        <tr v-if="topPad" class="stations__spacer" :style="{height: `${topPad}px`}">
          <td :colspan="COLUMN_COUNT"/>
        </tr>

        <tr
            v-for="row in drawn"
            :key="row.station_id"
            :class="{'stations__row--picked': isPicked(row)}"
        >
          <td class="stations__cell--text stations__id">
            <!-- The id is the handle rather than the whole row: a row-wide
                 click is unreachable from a keyboard, and a station picked
                 here is picked on the map beside it too. -->
            <button
                type="button"
                class="stations__pick"
                :aria-pressed="isPicked(row)"
                @click="pick(row)"
            >
              {{ row.wigos_id }}
            </button>
          </td>
          <td class="stations__cell--text" :title="displayName(row)">
            {{ displayName(row) }}
          </td>
          <td class="stations__cell--text">
            <span class="stations__standing" :class="`stations__standing--${row.standing}`">
              {{ label(row.standing) }}
            </span>
          </td>
          <td class="stations__cell--text">{{ formatInstant(row.last_heard) }}</td>
          <td class="stations__cell--number">{{ formatQuiet(row.hours_quiet) }}</td>
          <td class="stations__cell--number">
            {{ formatCount(row.messages_in_window) }}
          </td>
          <td v-if="showsShape">
            <Sparkline
                :values="row.sparkline"
                :name="displayName(row)"
                :standing-label="label(row.standing)"
                :height="SPARK_HEIGHT"
            />
          </td>
          <td>
            <PresenceCells
                :values="row.presence"
                :buckets="buckets"
                :grain="grain"
                :ceilings="ceilings"
                :baseline="row.baseline_hours"
                :cell-width="cellWidth"
                :height="ROW_HEIGHT"
                :name="displayName(row)"
            />
          </td>
        </tr>

        <tr v-if="bottomPad" class="stations__spacer" :style="{height: `${bottomPad}px`}">
          <td :colspan="COLUMN_COUNT"/>
        </tr>
        </tbody>
      </table>

      <p v-if="!shown.length" class="stations__empty">
        No station matches this filter. All {{ formatCount(stations.length) }} of them
        are still here &mdash; clear the filter to see them.
      </p>
    </div>

    <!-- Words beside every colour, because the cell states are the one thing
         on this page that cannot be read off a number. The same legend the
         drilldown carries, so one colour cannot come to be described two
         ways on two panels of one tab. -->
    <PresenceLegend v-if="buckets.length" :grain="grain" :unjudged="anyUnjudged"/>

    <!-- The whole of what is listed below, at half a pixel a station. It sits
         under the filter rather than above it because it is drawn from what
         the filter left: the rows the table is showing, in the order it is
         showing them. Above the controls it would look like a fixed picture
         of the centre, which is exactly the reading #66 recorded as a defect
         when the prototype's wall ignored the filter. -->
    <template v-if="shown.length && buckets.length">
      <NavigatorWall
          :stations="shown"
          :buckets="buckets"
          :grain="grain"
          :ceilings="ceilings"
          :window-label="windowLabel"
      />

      <!-- The wall's own two ends, in the same words the matrix's header
           carries and drawn over the wall's full width rather than the
           matrix's: the two surfaces run the same buckets in the same order,
           but the wall spreads them across the panel and the cells do not, so
           a reader locating a gap in time needs the ends said here too. -->
      <p class="stations__wall-axis">
        <span>{{ axisStart }}</span>
        <span>{{ axisEnd }}</span>
      </p>

      <p class="stations__wall-note">
      </p>
    </template>

  </div>
</template>

<script setup>
/**
 * Every station of one centre, one row each, and every row's availability.
 *
 * The aggregate above answers *whether* something is wrong. This answers
 * *which stations*, and it does it for the whole population at every node
 * size: **all rows, always**. There is no server paging behind this and no
 * client paging in it, because the availability matrix on the right of these
 * rows needs all of them, and a finding that only shows on the page you
 * happen to be looking at is not a finding. What makes that affordable is the
 * row height being a constant: only the rows in view are drawn, and the rest
 * of the population is held open as height.
 *
 * **The matrix is this table.** It is not a second view with its own axis, its
 * own sort or its own idea of which row is which -- it is the `presence`
 * vector each row already carries, drawn as the trailing cells of that row.
 * Two components with two orderings is how a stripe comes to be read against
 * the wrong station, and there is no toggle here that could put them out of
 * step. What the matrix says that nothing else on the tab can: a horizontal
 * stripe is one station stopping and when, and a contiguous band of them with
 * aligned stripes is a subset failing together -- a sub-network, a path, a
 * dataset -- rather than a scatter of unrelated hiccups.
 *
 * **Sort, filter and search are the reader's, and they are in the address
 * bar.** The rows are already here -- they arrived for the matrix -- so
 * asking the server to sort them would be a round trip to reorder a list in
 * memory. What the server does own is the *starting* order: RANK, then longest
 * quiet, then WIGOS id, which puts what is broken at the top. This component
 * never re-derives that; leaving the sort unchosen leaves the rows exactly as
 * they arrived, which is also the order the matrix is worth reading in.
 *
 * **A picked bucket is a filter like the others, and it is the only one that
 * is about a moment.** Selecting a column of the matrix -- or a bar of any
 * chart drawn over this window -- keeps the stations that were dark in it.
 * That is not a convenience: a subset still dark forms a band anyone can see,
 * but a subset that has already recovered is invisible under every sort here,
 * because RANK and last-heard both describe where a station stands *now*. The
 * selection travels as the bucket's start rather than as a column index, so
 * it survives the trip through the address bar and either names a column of
 * this axis or names none of it.
 *
 * **The navigator wall is the same list at a size no list can be read at.**
 * The matrix can only ever have about fifty rows on screen, and a centre with
 * a thousand stations has a finding that lives in all of them at once. So the
 * wall above the rows draws every station the table is showing at half a pixel
 * each -- the same vectors, the same order, the same colours -- and it is
 * drawn from the *filtered* list rather than from the population, because a
 * wall showing a thousand stations above a table filtered to forty-seven is
 * two answers to one question. It carries no names and no gestures: it says
 * where in the list to look, and the rows say what is there.
 *
 * Any filter has to state what it hid. A count of rows on screen with nothing
 * saying how many there were is the number a reader mistakes for the
 * population -- and the degenerate case is worth saying out loud too: a filter
 * matching everything has hidden nothing, and looks identical to no filter at
 * all unless the page says so.
 */
import {computed, nextTick, onMounted, ref, watch} from 'vue'

import BucketHeads from './BucketHeads.vue'
import NavigatorWall from './NavigatorWall.vue'
import PresenceCells from './PresenceCells.vue'
import PresenceLegend from './PresenceLegend.vue'
import Sparkline from './Sparkline.vue'
import {displayName, formatCount, formatInstant, formatQuiet} from './charts/plot.js'
import {axisEnds, bucketCeilings, cellWidthFor, grainOf} from './presence.js'
import {bucketIndexOf, bucketName, darkIn, isPickedStation} from './selection.js'
import {STANDING_LABEL, STANDING_RANK, STANDINGS} from './standings.js'
import {useVirtualRows} from './useVirtualRows.js'
import {useRoles} from './charts/useRoles.js'
import {
  filenameFor,
  handOver,
  provenance,
  stationsCsv,
  stationsImage,
} from './exports.js'

const props = defineProps({
  /** The rows, as the server ordered them. */
  stations: {
    type: Array,
    required: true
  },
  /** The window's axis, which every row's presence vector is indexed by. */
  buckets: {
    type: Array,
    default: () => []
  },
  /** The size of one bucket: `day` or `hour`, the server's own spelling. */
  grain: {
    type: String,
    default: 'day'
  },
  /** When the rows were read, which is what makes today a part-day. */
  asOf: {
    type: String,
    default: ''
  },
  /**
   * The bucket the reader picked, as the server spelled its start, or empty.
   * A start rather than an index, so that a link carrying one either names a
   * column of this window's axis or names none of it -- never the wrong one.
   */
  bucket: {
    type: String,
    default: ''
  },
  /** What the reader chose to search for. */
  search: {
    type: String,
    default: ''
  },
  /** The standing the rows are filtered to, or empty for all of them. */
  standing: {
    type: String,
    default: ''
  },
  /** Which column the rows are sorted by, or empty for the server's order. */
  sort: {
    type: String,
    default: ''
  },
  /** Which way that column runs: `asc` or `desc`. */
  direction: {
    type: String,
    default: 'asc'
  },
  /**
   * The station the reader picked, as a string because it arrives from the
   * address bar, or empty for none. The map beside these rows is filtered to
   * the same one.
   */
  station: {
    type: String,
    default: ''
  },
  /** What the message column counts, from the server's own label. */
  windowLabel: {
    type: String,
    default: 'the window'
  },
  /**
   * What this centre is called, carried for one reason: a file that leaves
   * the page has to name the centre it is about, and nothing else in this
   * component ever says it.
   */
  centreName: {
    type: String,
    default: 'This centre'
  },
  /** The window's own key, for naming the file after it. */
  windowKey: {
    type: String,
    default: ''
  },
  /** How many hours of quiet is too many, echoed by the server. */
  staleAfterHours: {
    type: Number,
    default: 24
  },
})

const emit = defineEmits(['choose'])

//: How tall every row is, in pixels, and it is fixed rather than measured.
//: #48 drew the alternative: labels are legible to about 12px and dead below
//: 10, and at 4px a matrix has a wide empty gutter beside it where the names
//: used to be. So the row does not shrink to fit a bigger population -- the
//: answer to scale is a second view, not a shorter row -- and holding it
//: constant is also what makes the list virtualisable at all.
const ROW_HEIGHT = 14

//: The sparkline, one pixel inside the row top and bottom so a trace at full
//: height does not touch the row above it.
const SPARK_HEIGHT = ROW_HEIGHT - 2
const SPARK_WIDTH = '5rem'

//: The horizontal padding of a cell, which the matrix column has to carry on
//: top of its grid -- the columns are laid out fixed, so a width that forgets
//: the padding clips the newest bucket.
const CELL_PADDING = 16

//: The sortable columns, in the order they are drawn. `value` is what a row
//: sorts by rather than what it shows, which is the whole of the difference
//: between sorting a standing alphabetically and sorting it by how broken it
//: is. Neither the sparkline nor the matrix is among them: both are shapes,
//: and there is no order to put shapes in.
const COLUMNS = [
  {key: 'wigos_id', label: 'WIGOS id', value: (row) => row.wigos_id, width: '9.5rem'},
  {
    key: 'name',
    label: 'Name',
    value: (row) => displayName(row).toLowerCase(),
    width: '13rem',
  },
  {
    key: 'standing',
    label: 'Standing',
    // By RANK rather than by label, so this column sorts into the order the
    // rows arrive in rather than into alphabetical order, which would put
    // "Gone quiet" above "Never heard from" for no reason a reader can see.
    value: (row) => STANDING_RANK[row.standing] ?? STANDINGS.length,
    width: '9rem',
  },
  {
    key: 'last_heard',
    label: 'Last heard',
    // Never heard from sorts before anything, exactly as the server's own
    // reading order does it: "no transmission ever" is the extreme of "a long
    // time ago", not a missing value to be swept to the end.
    value: (row) => (row.last_heard ? Date.parse(row.last_heard) : -Infinity),
    align: 'text',
    width: '10.5rem',
  },
  {
    key: 'hours_quiet',
    label: 'Quiet',
    // The same fact from the other end, and so the opposite infinity. A
    // station nothing ever heard has been quiet for ever.
    value: (row) => (row.hours_quiet === null ? Infinity : row.hours_quiet),
    align: 'number',
    width: '5rem',
  },
  {
    key: 'messages_in_window',
    label: 'Messages',
    value: (row) => row.messages_in_window,
    align: 'number',
    width: '6rem',
  },
]

const COLUMN_BY_KEY = Object.fromEntries(COLUMNS.map((column) => [column.key, column]))

//: Every column the table is actually drawing, for the spacers that stand in
//: for the rows not drawn. The matrix always, the 24h shape only where it is
//: on the same clock as the matrix beside it -- a spacer counting a column
//: that is not there pads the wrong width and the scroll comes apart.
const COLUMN_COUNT = computed(() => COLUMNS.length + (showsShape.value ? 2 : 1))

//: Whether the 24h shape is drawn at all.
//:
//: It is a flat 24 hours whatever else is on screen, so it belongs beside a
//: matrix of 24 hourly buckets and nowhere else: over a window of days it
//: would be the one column in the row reading a different clock, which is the
//: thing the page's two tabs exist to stop. Read off the grain the rows
//: themselves were drawn at rather than off a prop, because that grain *is*
//: which tab this is.
const showsShape = computed(() => props.grain === 'hour')

//: Whether the two fields above the table are narrowing anything, which is
//: what their own clear button is offered for. The picked bucket is not among
//: them: it has its own line and its own button, because one control that
//: clears three things is a control a reader presses to drop a search and
//: loses the day they were reading.
const narrowedByFields = computed(
    () => Boolean(props.search.trim() || props.standing)
)

//: Whether anything at all is being kept off the page, which is the question
//: the population count answers.
const filtering = computed(() => narrowedByFields.value || picked.value)

//: Which column of this window's axis the selection names, or -1 where it
//: names none of it. A link carrying a day of a 90-day window, opened at the
//: default 24 hours, lands here: the selection is ignored rather than
//: resolved to whichever column happens to sit at that index.
const pickedAt = computed(() => bucketIndexOf(props.buckets, props.bucket))

const picked = computed(() => pickedAt.value !== -1)

//: The bucket the reader picked, in the words its own grain uses.
const pickedName = computed(
    () => bucketName(props.buckets[pickedAt.value], props.grain)
)

//: How many of the whole population were dark in it -- the selection's own
//: figure, counted over every station rather than over what the search and
//: the standing filter have left, so that the sentence stating it is true
//: whatever else is on.
const darkCount = computed(() => {
  if (!picked.value) {
    return 0
  }

  return props.stations.filter((row) => darkIn(row, pickedAt.value)).length
})

const matching = computed(() => {
  const wanted = props.search.trim().toLowerCase()

  return props.stations.filter((row) => {
    // The selection first, because it is the one filter that is about a
    // moment rather than about the station as it stands now -- and it is the
    // only route to a cohort that has already recovered, which no sort of
    // this table can show.
    if (picked.value && !darkIn(row, pickedAt.value)) {
      return false
    }

    if (props.standing && row.standing !== props.standing) {
      return false
    }

    if (!wanted) {
      return true
    }

    // Both names and the id, because a station is looked up by whichever of
    // them the reader happens to have in front of them -- an operator's own
    // name off a roster, or a WIGOS id out of a topic.
    return [row.wigos_id, row.name, row.local_name]
        .some((field) => (field || '').toLowerCase().includes(wanted))
  })
})

const shown = computed(() => {
  const column = COLUMN_BY_KEY[props.sort]

  if (!column) {
    // The server's order, untouched. Not a sort this component happens to
    // agree with today: re-deriving RANK here is how the table and the rows
    // it was sent come to disagree about what is broken.
    return matching.value
  }

  const way = props.direction === 'desc' ? -1 : 1

  return [...matching.value].sort((left, right) => {
    const a = column.value(left)
    const b = column.value(right)

    if (a === b) {
      // The server's order as the tie-break, so a column of equal values --
      // a hundred stations with no messages at all -- stays in the order the
      // page arrived in rather than shuffling on every re-render.
      return 0
    }

    return (a < b ? -1 : 1) * way
  })
})

const hidden = computed(() => props.stations.length - shown.value.length)

const {viewport, header, first, end, onScroll, reset, scrollToRow, topPad, bottomPad} =
    useVirtualRows(computed(() => shown.value.length), ROW_HEIGHT)

//: The station the reader picked, as the rows spell its id. The address bar
//: carries a string and a row carries the number the server sent, so the two
//: are compared in one place rather than at every row.
const pickedStation = computed(
    () => props.stations.find((row) => isPicked(row)) || null
)

//: Where that station is in the list as it is being shown, or -1 where the
//: filter above has hidden it. A pick made on the map is about a station
//: rather than about the filter, so it is never allowed to widen one: the
//: line above the rows says the row is hidden instead.
const pickedRow = computed(() => shown.value.findIndex((row) => isPicked(row)))

/**
 * Bring the picked row into view, for the pick that was made on the map: a
 * highlight four hundred rows down is not a highlight. Nothing moves where
 * the row is already on screen, and nothing moves where the filter has
 * hidden it -- `scrollToRow` is given -1 and does nothing with it.
 *
 * After the render, because the row it is scrolling to may not be drawn yet.
 */
function revealPicked() {
  nextTick(() => scrollToRow(pickedRow.value))
}

// The pick and nothing else. A filter change resets the list to the top on
// purpose, and scrolling back to a station picked before it would take that
// away from the reader who just narrowed the list.
watch(() => props.station, revealPicked)

// And on the way in, for the link that arrives carrying one: the row would
// otherwise be highlighted somewhere nobody can see.
onMounted(revealPicked)

//: The rows on screen. Keyed by station id in the template, so scrolling
//: moves the rows that stayed rather than repainting a screenful of matrix
//: cells that have not changed.
const drawn = computed(() => shown.value.slice(first.value, end.value))

// Back to the top whenever the list underneath changes shape. Staying at
// pixel 4,000 of a list that just became forty rows long is a panel that
// looks empty, and the reader who narrowed it has not moved.
// The rows themselves are in that list: a window change re-reads every one
// of them, and pixel 4,000 of the last window's population is not a place a
// reader chose to be in this one.
watch(
    () => [
      props.stations,
      props.search,
      props.standing,
      props.bucket,
      props.sort,
      props.direction,
    ],
    () => reset()
)

//: How wide one cell is drawn, decided once here rather than by each row, so
//: that a thousand rows cannot disagree about where a column is.
const cellWidth = computed(() => cellWidthFor(props.buckets.length))

const matrixWidth = computed(() => cellWidth.value * props.buckets.length)

//: What each bucket could have carried, worked out once for the whole table.
//: Null at hourly grain, where the scale is each row's own busiest hour.
const ceilings = computed(() => bucketCeilings(props.buckets, props.grain, props.asOf))

// Whether the hatch is on the page at all, which is what decides if the legend
// names it. Read off the rows actually shown rather than the whole population:
// a legend explaining a mark the current filter has hidden is a reader hunting
// for something that is not there.
const anyUnjudged = computed(
    () => Boolean(ceilings.value)
        && shown.value.some((row) => row.baseline_hours == null)
)

const openBucket = computed(() => props.buckets.some((bucket) => bucket.partial))

//: What this grain calls its buckets, its scale and its legend, from the one
//: map the cells and their tooltips are drawn from.
const grainWords = computed(() => grainOf(props.grain))

//: The two ends of the matrix's axis, worded by the one place that words
//: them: the drilldown's strip names its own ends too, and the newest column
//: being "today" on one surface and a date on the other is two axes to read.
const ends = computed(() => axisEnds(props.buckets, props.grain))
const axisStart = computed(() => ends.value.start)
const axisEnd = computed(() => ends.value.end)

//: The element the image's colours are inherited through. A canvas cannot
//: read a custom property, so the roles are resolved to strings the same way
//: the navigator wall resolves its three -- and against the island, because
//: outside it every one of them is the empty string.
const root = ref(null)

const exportRoles = useRoles(root, [
  'live', 'thin', 'empty', 'silent', 'ink', 'ink-muted', 'grid', 'band',
  'hatch-ground',
])

//: The palette the picture is painted with, named for what each role *is* in
//: the drawing rather than for what it is on the page. The export follows
//: the reader's theme rather than forcing one: a dark page exporting a white
//: sheet would hand back an image in colours the reader has never seen and
//: cannot check against what is on screen.
const imageRoles = computed(() => ({
  page: exportRoles.value['hatch-ground'],
  zebra: exportRoles.value.band,
  ink: exportRoles.value.ink,
  meta: exportRoles.value['ink-muted'],
  grid: exportRoles.value.grid,
  //: The standing dot's two fills. `live` is the same colour the matrix's
  //: `full` is painted in and is deliberately not a second role: the table
  //: paints the dot and the cells from one token, and so does this.
  live: exportRoles.value.live,
  alarm: exportRoles.value.silent,
  full: exportRoles.value.live,
  thin: exportRoles.value.thin,
  //: Flat, for the same reason the navigator wall paints it flat: a canvas
  //: cell four pixels wide cannot carry the hatch the matrix draws, and what
  //: has to survive the export is only that an unjudged cell is not a silent
  //: one.
  unjudged: exportRoles.value['hatch-line'],
  silent: exportRoles.value.empty,
  empty: exportRoles.value.empty,
}))

//: Said above where it happened rather than in an alert: the one refusal
//: here is a population too tall to draw, and the answer to it is a filter or
//: the other button, both of which are on this line.
const exportError = ref('')

//: Every narrowing in force, in the words the file will carry. The picked
//: bucket is described rather than named: it filters *rows* -- to the
//: stations that were dark in it -- and a file saying "bucket: 2026-08-14"
//: would read as a matrix cropped to one day, which is not what happened.
const activeFilters = computed(() => {
  const said = []

  if (props.search.trim()) {
    said.push(`search "${props.search.trim()}"`)
  }

  if (props.standing) {
    said.push(`standing = ${label(props.standing)}`)
  }

  if (picked.value) {
    said.push(`only stations dark ${grainWords.value.preposition} ${pickedName.value}`)
  }

  return said
})

const sortLabel = computed(() => {
  const column = COLUMN_BY_KEY[props.sort]

  return column
      ? `${column.label} (${props.direction === 'desc' ? 'desc' : 'asc'})`
      : ''
})

/** What both files say about themselves. */
function exportLines() {
  return provenance({
    centreName: props.centreName,
    windowLabel: props.windowLabel,
    generatedAt: props.asOf,
    filters: activeFilters.value,
    showing: formatCount(shown.value.length),
    total: formatCount(props.stations.length),
    sortLabel: sortLabel.value,
  })
}

function downloadCsv() {
  exportError.value = ''

  const text = stationsCsv({
    rows: shown.value,
    buckets: props.buckets,
    lines: exportLines(),
  })

  handOver(
      // The BOM, and it is not decoration: Excel reads a CSV without one as
      // the local codepage, and a station name with an accent in it arrives
      // mangled on exactly the desks this file is for.
      new Blob([`\ufeff${text}`], {type: 'text/csv;charset=utf-8'}),
      filenameFor(props.centreName, props.windowKey, 'csv')
  )
}

function downloadImage() {
  exportError.value = ''

  const canvas = stationsImage({
    rows: shown.value,
    buckets: props.buckets,
    ceilings: ceilings.value,
    roles: imageRoles.value,
    lines: exportLines(),
    axis: {start: axisStart.value, end: axisEnd.value},
  })

  if (!canvas) {
    exportError.value =
        `An image of ${formatCount(shown.value.length)} stations is taller than`
        + ` a browser will draw. Narrow the list with the filters above, or`
        + ` download the CSV, which has no limit.`

    return
  }

  canvas.toBlob((blob) => {
    if (blob) {
      handOver(blob, filenameFor(props.centreName, props.windowKey, 'png'))
    }
  }, 'image/png')
}

function label(standing) {
  return STANDING_LABEL[standing] || standing
}

function choose(chosen) {
  emit('choose', chosen)
}

/** Whether this row is the station the reader picked. */
function isPicked(row) {
  return isPickedStation(row, props.station)
}

/**
 * Pick this station, or let go of it where it is already picked.
 *
 * The same gesture the map's own ground click makes, and it has to be here
 * too: a reader who picked a row has no other way back out of it.
 */
function pick(row) {
  choose({station: isPicked(row) ? '' : String(row.station_id)})
}

/** Sort by a column, turning it around where it is already the one sorted. */
function sortBy(key) {
  if (props.sort !== key) {
    emit('choose', {sort: key, direction: 'asc'})

    return
  }

  // Third click on one column goes back to the server's order rather than
  // cycling forever between two sorts the reader may not have wanted. The
  // starting order is a finding, so there has to be a way back to it.
  if (props.direction === 'asc') {
    emit('choose', {sort: key, direction: 'desc'})
  } else {
    emit('choose', {sort: '', direction: 'asc'})
  }
}

function ariaSort(key) {
  if (props.sort !== key) {
    return 'none'
  }

  return props.direction === 'desc' ? 'descending' : 'ascending'
}

function arrow(key) {
  if (props.sort !== key) {
    return ''
  }

  return props.direction === 'desc' ? '▾' : '▴'
}
</script>

<style scoped>
.stations__caveats {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0.75rem 0 0;
  max-width: 70ch;
}

.stations__controls {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}

.stations__field {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

/* One height for both, stated rather than inherited. The admin styles a
   search input and a select from two different rules with two different
   min-heights and two different line-heights, so a pair that shares a class
   here still arrives on the page as two boxes of different sizes sitting on
   different baselines. Scoped under `.stations` for the specificity to beat
   them, and `border-box` so the height is the height rather than the height
   plus whatever padding each control brought with it. */
.stations .stations__input {
  box-sizing: border-box;
  height: 2rem;
  min-height: 0;
  line-height: 1.2;
}

.stations__field-label {
  font-size: 0.7rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--w-color-text-meta);
}

.stations__input {
  font: inherit;
  font-size: 0.8rem;
  padding: 0 0.45rem;
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 3px;
  background: transparent;
  color: var(--w-color-text-label);
}

/* Bottom-aligned rather than top: the fields carry a label above them, so a
   button at the top of the row sits beside their labels rather than beside
   the controls it belongs with. */
.stations__clear {
  box-sizing: border-box;
  align-self: flex-end;
  height: 2rem;
  font: inherit;
  font-size: 0.78rem;
  padding: 0 0.6rem;
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 3px;
  background: transparent;
  color: var(--w-color-text-label);
  cursor: pointer;
}

/* The picked bucket, on its own line above the population count and looking
   like the control it is: a reader who arrived on a link has to be able to
   see at a glance that the list in front of them is a filtered one. */
.stations__picked {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0 0 0.35rem;
}

/* The button belongs on the line rather than at the bottom of it: this row
   has no field labels above it for it to clear. */
.stations__picked .stations__clear {
  align-self: auto;
  height: auto;
  padding: 0.25rem 0.6rem;
}

.stations__picked-what {
  font-weight: 600;
  color: var(--w-color-text-label);
}

/* The picked station, on its own line for the same reason the picked bucket
   has one: a reader who arrived on a link has to be able to see what the rows
   in front of them are marked by. */
.stations__station {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0 0 0.35rem;
}

.stations__station .stations__clear {
  align-self: auto;
  height: auto;
  padding: 0.25rem 0.6rem;
}

/* Pushed to the far end of the controls: they act on what the fields to
   their left have left, and reading them in that order is the whole of what
   a reader needs to know about what comes out. */
.stations__exports {
  display: flex;
  gap: 0.4rem;
  margin-left: auto;
}

/* Filled rather than outlined, and that is the whole of what separates them
   from `Clear filter` sitting a few pixels to their left. Every other button
   on this line *narrows what is on screen*; these two take a copy of it away
   with the reader. Two gestures that different should not be one shape --
   and the arrow says which way the thing is going before the word does. */
.stations__export {
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  height: 2rem;
  gap: 0.35rem;
  font: inherit;
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0 0.7rem;
  border: 1px solid transparent;
  border-radius: 3px;
  background: var(--stat-band);
  color: var(--w-color-text-label);
  cursor: pointer;
}

.stations__export:hover {
  border-color: var(--w-color-border-furniture);
}

.stations__export-glyph {
  display: block;
  width: 0.95rem;
  height: 0.95rem;
  color: var(--stat-live);
}

.stations__export:focus-visible {
  outline: 2px solid var(--stat-focus);
  outline-offset: 1px;
}

.stations__export-error {
  font-size: 0.8rem;
  color: var(--w-color-text-error, var(--w-color-text-label));
  margin: 0 0 0.5rem;
  max-width: 70ch;
}

.stations__population {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0 0 0.5rem;
}

/* The wall's two ends, in the type the matrix's own end labels use. */
.stations__wall-axis {
  display: flex;
  justify-content: space-between;
  font-size: 0.7rem;
  font-weight: 400;
  color: var(--w-color-text-meta);
  margin: 0.15rem 0 0.35rem;
}

/* What the wall is, under it rather than over it: the picture is the thing a
   reader looks at first, and a caption above it delays that by a sentence. */
.stations__wall-note {
  font-size: 0.75rem;
  color: var(--w-color-text-meta);
  margin: 0 0 0.75rem;
  max-width: 70ch;
}

/* The table scrolls inside its own panel rather than down the page. Every row
   is still here -- this is not paging, and nothing is hidden from a search or
   a sort -- but a thousand of them at a legible row height are sixteen
   thousand pixels, and that is not a thing to put between a reader and the
   bottom of the page.

   It is also the only place the column headings can stay put. A sticky header
   sticks to its nearest scrolling ancestor, and Wagtail's own chrome is
   between this table and the page, so a header that sticks to the page is a
   header that scrolls away or hides behind the admin's slim bar. Bounded
   here, it sticks where a reader needs it.

   Sideways for the same container: a WIGOS id never has to wrap to fit, and
   the matrix is as wide as its window needs. */
.stations__scroll {
  max-height: 34rem;
  overflow: auto;
}

/* Separated rather than collapsed, which is not a cosmetic choice: a
   collapsed border belongs to the table rather than to the cell, and a sticky
   header painted over a scrolling row then shows a hairline of that row
   through the seam. Spacing of zero keeps it looking collapsed.

   Fixed layout, which is what the virtualised rows require rather than
   prefer: with automatic layout the columns are measured from the rows that
   happen to be drawn, so every scroll that swaps a long name for a short one
   would resize the whole table under the reader. */
.stations__table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  table-layout: fixed;
  font-size: 0.7rem;
}

.stations__table th,
.stations__table td {
  padding: 0 0.5rem;
  box-shadow: inset 0 -1px 0 var(--w-color-border-furniture);
  white-space: nowrap;
  vertical-align: middle;
}

/* The fixed row, and every rule here exists to hold it there: the height is
   what the virtual list counts in, so a cell that grows by a padding or a
   line-height puts every row on screen at the wrong offset. */
.stations__table tbody td {
  height: var(--stations-row);
  line-height: var(--stations-row);
  overflow: hidden;
  text-overflow: ellipsis;
}

/* The rows that are scrolled past, as height and nothing else. No hairline
   and no padding: a spacer that draws anything is a stripe across the panel
   wherever the reader happens to be. */
.stations__spacer td {
  padding: 0;
  box-shadow: none;
}

.stations__table th {
  text-align: left;
  font-weight: 600;
  color: var(--w-color-text-meta);
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  padding-top: 0.3rem;
  padding-bottom: 0.3rem;
  /* The header stays while a thousand rows go past it, which is what makes
     "all rows, always" readable rather than merely honest. It sticks to the
     panel above, which is the scrolling ancestor. */
  position: sticky;
  top: 0;
  background: var(--w-color-surface-page);
  z-index: 1;
}

/* Identity stays put while the reader scrolls sideways to the far end of a
   ninety-day matrix. A cell nobody can put a name to is the one way this
   table could still mislead after all the work above it. */
.stations__table th:first-child,
.stations__table tbody td:first-child {
  position: sticky;
  left: 0;
  background: var(--w-color-surface-page);
  z-index: 1;
}

.stations__table th:first-child {
  z-index: 2;
}

.stations__sort {
  font: inherit;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-transform: inherit;
  letter-spacing: inherit;
}

.stations__sort--on {
  color: var(--w-color-text-label);
}

.stations__sort:focus-visible {
  outline: 2px solid var(--stat-focus);
  outline-offset: 1px;
}

.stations__column-note {
  display: block;
  font-weight: 400;
  text-transform: none;
  letter-spacing: 0;
  font-size: 0.68rem;
}

/* The matrix's two end labels, spread over exactly the width the cells are
   drawn at. Not an axis: two ends of one, which is as much as fits over a
   5px column and as much as a reader needs to know which way time runs. */
.stations__axis {
  display: flex;
  justify-content: space-between;
}

.stations__cell--number {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.stations__id {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

/* The id, still reading as the id: a button here is the keyboard's way to the
   same pick the map offers, not a second thing to look at. */
.stations__pick {
  font: inherit;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 2px;
}

.stations__pick:focus-visible {
  outline: 2px solid var(--stat-focus);
  outline-offset: 1px;
}

/* The picked row, in the focus colour the rest of the tab marks a pick with,
   and as a wash rather than a fill: every other column of the row has to stay
   readable under it. The sticky first cell is painted separately because it
   carries its own background to cover the rows sliding under it. */
.stations__row--picked td {
  background: color-mix(in srgb, var(--stat-focus) 14%, transparent);
}

.stations__row--picked td:first-child {
  background: linear-gradient(
      color-mix(in srgb, var(--stat-focus) 14%, transparent),
      color-mix(in srgb, var(--stat-focus) 14%, transparent)
  ),
  var(--w-color-surface-page);
  box-shadow: inset 2px 0 0 var(--stat-focus), inset 0 -1px 0 var(--w-color-border-furniture);
}

/* A dot rather than a coloured row: the standing is one fact about the
   station, and a whole row painted red is a row whose other columns are hard
   to read. Colour is never the only carrier -- the words are right beside
   it. */
.stations__standing::before {
  content: '';
  display: inline-block;
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  margin-right: 0.35rem;
  background: var(--stat-live);
}

.stations__standing--gone_quiet::before,
.stations__standing--never_transmitted::before {
  background: var(--stat-silent);
}

/* Transmitting, but nothing declares it: neither a failure nor a clean bill.
   Drawn as the live colour outlined rather than filled, so it reads as "on,
   with a question about it" without spending a third colour on the tab. */
.stations__standing--undeclared::before {
  background: transparent;
  box-shadow: inset 0 0 0 2px var(--stat-live);
}


.stations__empty {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0.75rem 0 0;
}
</style>
