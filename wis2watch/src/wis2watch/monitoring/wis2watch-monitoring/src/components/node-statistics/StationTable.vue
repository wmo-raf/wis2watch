<template>
  <section class="stations">
    <h3 class="stations__heading">Stations, what is broken first</h3>

    <p class="stations__note">
      Every station this centre declares or has been heard transmitting for,
      sorted so that what has stopped is at the top &mdash; the default sort is
      a filter that hides nothing. Everything here is this centre's own
      observation: a station may transmit under more than one centre's topics,
      and what another centre heard is not on this page.
    </p>

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
          v-if="filtering"
          type="button"
          class="stations__clear"
          @click="choose({search: '', standing: ''})"
      >
        Clear filter
      </button>
    </div>

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
        All <strong>{{ count(stations.length) }}</strong> stations, none hidden.
      </template>
      <template v-else-if="hidden">
        <strong>{{ count(shown.length) }}</strong> of
        {{ count(stations.length) }} stations &mdash;
        {{ count(hidden) }} hidden by the filter.
      </template>
      <template v-else>
        All <strong>{{ count(stations.length) }}</strong> stations match the
        filter, so it is hiding none of them.
      </template>
    </p>

    <div v-if="stations.length" class="stations__scroll">
      <table class="stations__table">
        <thead>
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
            <th scope="col" class="stations__cell--spark">
              24h activity
              <span class="stations__column-note">shape, flat 24h</span>
            </th>
          </tr>
        </thead>

        <tbody>
          <tr v-for="row in shown" :key="row.station_id">
            <td class="stations__cell--text stations__id">{{ row.wigos_id }}</td>
            <td class="stations__cell--text stations__name">{{ displayName(row) }}</td>
            <td class="stations__cell--text">
              <span class="stations__standing" :class="standingClass(row.standing)">
                {{ label(row.standing) }}
              </span>
            </td>
            <td class="stations__cell--text">{{ formatInstant(row.last_heard) }}</td>
            <td class="stations__cell--number">{{ formatQuiet(row.hours_quiet) }}</td>
            <td class="stations__cell--number">{{ count(row.messages_in_window) }}</td>
            <td class="stations__cell--spark">
              <Sparkline
                  :values="row.sparkline"
                  :name="displayName(row)"
                  :standing="row.standing"
              />
            </td>
          </tr>
        </tbody>
      </table>

      <p v-if="!shown.length" class="stations__empty">
        No station matches this filter. All {{ count(stations.length) }} of them
        are still here &mdash; clear the filter to see them.
      </p>
    </div>

    <p class="stations__caveats">
      Quiet is judged against a flat {{ staleAfterHours }} hours, the same
      threshold the figures above use. The message column counts
      {{ windowLabel.toLowerCase() }} and moves with the window; the activity
      column is always the last 24 whole hours, and is drawn for shape rather
      than volume &mdash; a row's trace is scaled to its own busiest hour, so
      the comparable number is the message column beside it. A flat line on the
      baseline is a station the world heard nothing from.
    </p>
  </section>
</template>

<script setup>
/**
 * Every station of one centre, one row each.
 *
 * The aggregate above answers *whether* something is wrong. This answers
 * *which stations*, and it does it for the whole population at every node
 * size: **all rows, always**. There is no server paging behind this and no
 * client paging in it, because the availability matrix that lands on these
 * same rows next needs all of them, and a finding that only shows on the page
 * you happen to be looking at is not a finding.
 *
 * **Sort, filter and search are the reader's, and they are in the address
 * bar.** The rows are already here -- they arrived for the matrix -- so
 * asking the server to sort them would be a round trip to reorder a list in
 * memory. What the server does own is the *starting* order: RANK, then longest
 * quiet, then WIGOS id, which puts what is broken at the top. This component
 * never re-derives that; leaving the sort unchosen leaves the rows exactly as
 * they arrived.
 *
 * Any filter has to state what it hid. A count of rows on screen with nothing
 * saying how many there were is the number a reader mistakes for the
 * population -- and the degenerate case is worth saying out loud too: a filter
 * matching everything has hidden nothing, and looks identical to no filter at
 * all unless the page says so.
 */
import {computed} from 'vue'

import Sparkline from './Sparkline.vue'
import {formatCount, formatInstant, formatQuiet} from './charts/plot.js'
import {STANDINGS, STANDING_LABEL, STANDING_RANK} from './standings.js'

const props = defineProps({
  /** The rows, as the server ordered them. */
  stations: {
    type: Array,
    required: true
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
  /** What the message column counts, from the server's own label. */
  windowLabel: {
    type: String,
    default: 'the window'
  },
  /** How many hours of quiet is too many, echoed by the server. */
  staleAfterHours: {
    type: Number,
    default: 24
  },
})

const emit = defineEmits(['choose'])

//: The sortable columns, in the order they are drawn. `value` is what a row
//: sorts by rather than what it shows, which is the whole of the difference
//: between sorting a standing alphabetically and sorting it by how broken it
//: is. The sparkline is not among them: it is a shape, and there is no order
//: to put shapes in.
const COLUMNS = [
  {key: 'wigos_id', label: 'WIGOS id', value: (row) => row.wigos_id},
  {key: 'name', label: 'Name', value: (row) => displayName(row).toLowerCase()},
  {
    key: 'standing',
    label: 'Standing',
    // By RANK rather than by label, so this column sorts into the order the
    // rows arrive in rather than into alphabetical order, which would put
    // "Gone quiet" above "Never heard from" for no reason a reader can see.
    value: (row) => STANDING_RANK[row.standing] ?? STANDINGS.length,
  },
  {
    key: 'last_heard',
    label: 'Last heard',
    // Never heard from sorts before anything, exactly as the server's own
    // reading order does it: "no transmission ever" is the extreme of "a long
    // time ago", not a missing value to be swept to the end.
    value: (row) => (row.last_heard ? Date.parse(row.last_heard) : -Infinity),
    align: 'text',
  },
  {
    key: 'hours_quiet',
    label: 'Quiet',
    // The same fact from the other end, and so the opposite infinity. A
    // station nothing ever heard has been quiet for ever.
    value: (row) => (row.hours_quiet === null ? Infinity : row.hours_quiet),
    align: 'number',
  },
  {
    key: 'messages_in_window',
    label: 'Messages',
    value: (row) => row.messages_in_window,
    align: 'number',
  },
]

const COLUMN_BY_KEY = Object.fromEntries(COLUMNS.map((column) => [column.key, column]))

const filtering = computed(() => Boolean(props.search.trim() || props.standing))

const matching = computed(() => {
  const wanted = props.search.trim().toLowerCase()

  return props.stations.filter((row) => {
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

function count(value) {
  return formatCount(value)
}

function label(standing) {
  return STANDING_LABEL[standing] || standing
}

/** What to call the station: the operator's own name, where there is one. */
function displayName(row) {
  return row.local_name || row.name || row.wigos_id
}

function standingClass(standing) {
  return `stations__standing--${standing}`
}

function choose(chosen) {
  emit('choose', chosen)
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
.stations__heading {
  font-size: 0.95rem;
  margin: 0 0 0.25rem;
  color: var(--w-color-text-label);
}

.stations__note,
.stations__caveats {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0 0 0.75rem;
  max-width: 70ch;
}

.stations__caveats {
  margin: 0.75rem 0 0;
}

.stations__controls {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 0.75rem;
  margin-bottom: 0.5rem;
}

.stations__field {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
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
  padding: 0.2rem 0.4rem;
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 3px;
  background: transparent;
  color: var(--w-color-text-label);
}

/* Bottom-aligned rather than top: the fields carry a label above them, so a
   button at the top of the row sits beside their labels rather than beside
   the controls it belongs with. */
.stations__clear {
  align-self: flex-end;
  font: inherit;
  font-size: 0.78rem;
  padding: 0.25rem 0.6rem;
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 3px;
  background: transparent;
  color: var(--w-color-text-label);
  cursor: pointer;
}

.stations__population {
  font-size: 0.8rem;
  color: var(--w-color-text-meta);
  margin: 0 0 0.5rem;
}

/* The table scrolls inside its own panel rather than down the page. Every row
   is still here -- this is not paging, and nothing is hidden from a search or
   a sort -- but a thousand of them are not a thing to put between a reader and
   the bottom of the page.

   It is also the only place the column headings can stay put. A sticky header
   sticks to its nearest scrolling ancestor, and Wagtail's own chrome is
   between this table and the page, so a header that sticks to the page is a
   header that scrolls away or hides behind the admin's slim bar. Bounded
   here, it sticks where a reader needs it.

   Sideways for the same container, so a WIGOS id never has to wrap to fit. */
.stations__scroll {
  max-height: 34rem;
  overflow: auto;
}

/* Separated rather than collapsed, which is not a cosmetic choice: a
   collapsed border belongs to the table rather than to the cell, and a sticky
   header painted over a scrolling row then shows a hairline of that row
   through the seam. Spacing of zero keeps it looking collapsed. */
.stations__table {
  width: 100%;
  border-collapse: separate;
  border-spacing: 0;
  font-size: 0.8rem;
}

.stations__table th,
.stations__table td {
  padding: 0.3rem 0.5rem;
  box-shadow: inset 0 -1px 0 var(--w-color-border-furniture);
  white-space: nowrap;
  vertical-align: middle;
}

.stations__name {
  min-width: 12rem;
}

.stations__table th {
  text-align: left;
  font-weight: 600;
  color: var(--w-color-text-meta);
  font-size: 0.72rem;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  /* The header stays while a thousand rows go past it, which is what makes
     "all rows, always" readable rather than merely honest. It sticks to the
     panel above, which is the scrolling ancestor. */
  position: sticky;
  top: 0;
  background: var(--w-color-surface-page);
  z-index: 1;
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

.stations__cell--number {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.stations__cell--spark {
  width: 7rem;
  min-width: 7rem;
}

.stations__id {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
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
