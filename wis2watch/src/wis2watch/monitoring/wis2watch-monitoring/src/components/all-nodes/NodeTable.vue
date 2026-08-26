<template>
  <div class="nodes">
    <div class="nodes__controls">
      <label class="nodes__field">
        <span class="nodes__field-label">Search</span>
        <input
            v-model="search"
            type="search"
            class="nodes__input"
            placeholder="Centre ID or country"
        >
      </label>

      <label class="nodes__field">
        <span class="nodes__field-label">Standing</span>
        <select v-model="standing" class="nodes__input">
          <option value="">All standings</option>
          <option v-for="option in standings" :key="option.key" :value="option.key">
            {{ option.label }}
          </option>
        </select>
      </label>

      <button v-if="filtering" type="button" class="nodes__clear" @click="clear">
        Clear filter
      </button>
    </div>

    <!-- What is on screen, and of how much. A count of rows with nothing
         saying how many there were is the number a reader quietly mistakes
         for the region. The degenerate case has its own sentence on purpose:
         a filter that matched everything is not the same as no filter, and a
         reader who cannot tell them apart reads a full table as the whole
         region. -->
    <p class="nodes__population" role="status">
      <template v-if="counted.state === 'none'">
        No centres are registered yet. The catalogue sync populates them, and a
        fresh install has none until the first sync lands.
      </template>
      <template v-else-if="counted.state === 'all'">
        All <strong>{{ formatCount(counted.total) }}</strong> centres, none hidden.
      </template>
      <template v-else-if="counted.state === 'narrowed'">
        <strong>{{ formatCount(counted.shown) }}</strong> of
        {{ formatCount(counted.total) }} centres &mdash;
        {{ formatCount(counted.hidden) }} hidden by the filter.
      </template>
      <template v-else>
        All <strong>{{ formatCount(counted.total) }}</strong> centres match the
        filter, so it is hiding none of them.
      </template>
    </p>

    <div v-if="rows.length" class="nodes__scroll">
      <table class="nodes__table">
        <colgroup>
          <col v-for="column in COLUMNS" :key="column.key" :style="{width: column.width}">
        </colgroup>

        <thead>
        <tr>
          <th
              v-for="column in COLUMNS"
              :key="column.key"
              scope="col"
              :class="[`nodes__cell--${column.align || 'text'}`]"
              :aria-sort="ariaSort(column.key)"
          >
            <button
                v-if="column.value"
                type="button"
                class="nodes__sort"
                :class="{'nodes__sort--on': sort === column.key}"
                @click="sortBy(column.key)"
            >
              {{ column.label }}
              <span aria-hidden="true">{{ arrow(column.key) }}</span>
            </button>
            <span v-else>{{ column.label }}</span>
          </th>
        </tr>
        </thead>

        <tbody>
        <tr v-for="row in shown" :key="row.node_id">
          <td class="nodes__cell--text">
            <!-- The code is the handle, and it leads to the centre's own
                 diagnostic page -- the same landing the overview table has
                 always used, so the two send a reader to one place. The whole
                 row is deliberately not a link: this table has sortable heads
                 and two controls directly above it, and a full-row hit target
                 on a surface people are fiddling with is how a reader leaves
                 the homepage by accident. -->
            <a :href="row.detail_url" class="nodes__centre">
              <code>{{ row.centre_id }}</code>
            </a>
          </td>
          <td class="nodes__cell--text">{{ row.country_name || '—' }}</td>
          <td class="nodes__cell--text">
            <span class="nodes__standing" :class="`nodes__standing--${row.standing}`">
              {{ word('standing', row.standing) }}
            </span>
          </td>
          <td class="nodes__cell--text">{{ formatInstant(row.last_seen_at) }}</td>
          <td class="nodes__cell--number">{{ formatQuiet(row.hours_quiet) }}</td>
          <td class="nodes__cell--number">{{ formatCount(row.messages_in_window) }}</td>
          <td>
            <Sparkline
                :values="row.sparkline"
                :name="row.centre_id"
                :standing-label="word('standing', row.standing)"
                :height="SPARK_HEIGHT"
            />
          </td>
          <td v-for="field in BADGES" :key="field" class="nodes__cell--text">
            <span class="nodes__badge" :class="{'nodes__badge--worst': isWorst(field, row[field])}">
              {{ word(field, row[field]) }}
            </span>
          </td>
        </tr>
        </tbody>
      </table>
    </div>

    <p v-if="rows.length && !shown.length" class="nodes__empty">
      No centre matches this filter. All {{ formatCount(rows.length) }} of them are
      still here &mdash;
      <button type="button" class="nodes__clear" @click="clear">clear it</button>
      to see them.
    </p>
  </div>
</template>

<script setup>
/**
 * Every centre of the region, worst first, on the page everybody lands on.
 *
 * The statistics tab's station table one level up, and deliberately the same
 * reading: one ranked standing to sort by, the shape of the last day beside
 * the number, and a population line that says what is hidden. What a reader
 * learns on one is what they can use on the other.
 *
 * **No virtualisation and no paging.** The population is the region -- tens of
 * rows, not thousands -- so the whole of it is drawn and the browser scrolls
 * it. The station table's virtual rows are machinery for a problem this table
 * does not have, and inheriting them would be inheriting the bugs without the
 * reason.
 *
 * **The state is local.** The station table's filters live in the address bar,
 * because a view of a node is a link worth sending. This is a panel on the
 * admin home; there is no page here to link to, and a homepage that
 * accumulated `?q=` on every keystroke would be a homepage nobody could
 * bookmark. The overview page's own migration onto this component is where
 * that question comes back.
 *
 * **The words are the server's.** Every label is looked up in the vocabularies
 * the payload carried; nothing here spells a standing. That is what keeps this
 * table and the overview page describing one centre in one vocabulary.
 */
import {computed, ref} from 'vue'

import Sparkline from '@/components/node-statistics/Sparkline.vue'
// The three formatters the station rows are drawn with, borrowed rather than
// rewritten: a table with its own idea of how a quiet span is worded is a
// second answer to a question this tool already answers. They live under the
// statistics island because that is where they were first needed; if a shared
// format module is ever extracted, both callers move together.
import {formatCount, formatInstant, formatQuiet} from
    '@/components/node-statistics/charts/plot.js'

import {
    COLUMNS,
    labelsFrom,
    matches,
    narrowing,
    nextSort,
    population,
    ranksFrom,
    sortRows,
} from './rows.js'

const props = defineProps({
  /** Every registered centre, in the server's reading order. */
  rows: {
    type: Array,
    required: true
  },
  /** The vocabularies each judgement is spelled in, worst first. */
  vocabularies: {
    type: Object,
    required: true
  },
})

//: How tall a sparkline is drawn, in real pixels. Handed to the component
//: rather than chosen by it, because the row height is this table's decision.
const SPARK_HEIGHT = 20

//: The three judgements that ride beside the standing as its evidence, in the
//: order they are drawn. Named once because the header, the cells and the
//: colouring rule all walk the same list.
const BADGES = ['origin_watch', 'cache_pickup', 'silence']

const search = ref('')
const standing = ref('')
const sort = ref('')
const direction = ref('asc')

const ranks = computed(() => ranksFrom(props.vocabularies))
const labels = computed(() => labelsFrom(props.vocabularies))

//: The standings a filter can be set to, in the server's own order, so the
//: control offers them worst first exactly as the rows arrive.
const standings = computed(() => props.vocabularies.standing || [])

const filtering = computed(() =>
    narrowing({search: search.value, standing: standing.value})
)

const matching = computed(() =>
    props.rows.filter((row) =>
        matches(row, {search: search.value, standing: standing.value})
    )
)

const shown = computed(() =>
    sortRows(matching.value, {
      sort: sort.value,
      direction: direction.value,
      ranks: ranks.value,
    })
)

const counted = computed(() =>
    population(props.rows.length, shown.value.length, filtering.value)
)

/** What one value of one judgement is called, in the server's words. */
function word(field, value) {
  return labels.value[field]?.[value] || value
}

/**
 * Whether this is the worst thing its vocabulary can say.
 *
 * Rank nought, which is the worst value in all three of these -- they are
 * declared worst-first on the Python side and the rank is the position. Only
 * that one is marked, so the colour on this table means a fault and the other
 * two thirds of every badge column stay quiet. What is *best* is deliberately
 * not marked: `Silence`'s last rank is "Not judged", which is an absence of a
 * verdict rather than a good one, and a rule that painted the end of a
 * vocabulary green would call it healthy.
 */
function isWorst(field, value) {
  return ranks.value[field]?.[value] === 0
}

function clear() {
  search.value = ''
  standing.value = ''
}

function sortBy(key) {
  const next = nextSort({sort: sort.value, direction: direction.value}, key)

  sort.value = next.sort
  direction.value = next.direction
}

function ariaSort(key) {
  if (sort.value !== key) {
    return 'none'
  }

  return direction.value === 'desc' ? 'descending' : 'ascending'
}

function arrow(key) {
  if (sort.value !== key) {
    return ''
  }

  return direction.value === 'desc' ? '▾' : '▴'
}
</script>

<style scoped>
/* The admin's own form styles reach these -- Wagtail sets margins on labels
   and a height on selects that inputs do not get -- so every one of them is
   stated here rather than inherited. Two controls side by side whose baselines
   disagree by a few pixels is the kind of thing a reader does not name and
   does not trust. */
.nodes__controls {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 0.75rem;
  margin: 0 0 0.75rem;
}

.nodes__field {
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
  margin: 0;
}

.nodes__field-label {
  font-size: 0.75rem;
  line-height: 1.2;
  font-weight: 400;
  color: var(--w-color-text-meta);
  margin: 0;
  padding: 0;
}

.nodes__input {
  box-sizing: border-box;
  height: 2.25rem;
  margin: 0;
  font-size: 0.85rem;
  font-family: inherit;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--w-color-border-field-default);
  border-radius: 0.25rem;
  background: var(--w-color-surface-page);
  color: var(--w-color-text-context);
  min-width: 12rem;
}

.nodes__clear {
  background: none;
  border: 0;
  padding: 0.3rem 0;
  font-size: 0.8rem;
  color: var(--w-color-text-link-default);
  text-decoration: underline;
  cursor: pointer;
}

.nodes__population {
  font-size: 0.85rem;
  color: var(--w-color-text-context);
  margin: 0 0 0.5rem;
}

/* Bounded on purpose. Every centre is in here and nothing is hidden, but a
   region of forty centres drawn at full height is a homepage that is nothing
   but this table. */
.nodes__scroll {
  max-height: 26rem;
  overflow: auto;
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 0.25rem;
}

.nodes__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.nodes__table th,
.nodes__table td {
  padding: 0.35rem 0.6rem;
  text-align: left;
  white-space: nowrap;
  border-bottom: 1px solid var(--w-color-border-furniture);
}

/* The heads stay put while the region scrolls under them. On a table sorted
   worst-first, a reader who has scrolled to the quiet end and cannot see
   which column is which has lost the thing they scrolled for. */
.nodes__table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--w-color-surface-page);
  font-weight: 600;
  color: var(--w-color-text-meta);
}

.nodes__cell--number {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.nodes__sort {
  background: none;
  border: 0;
  padding: 0;
  font: inherit;
  color: inherit;
  cursor: pointer;
}

.nodes__sort--on {
  color: var(--w-color-text-context);
}

.nodes__centre {
  text-decoration: none;
}

/* The same mark the station rows carry, on the same two colours, because a
   reader scanning this column has already learned this vocabulary one page
   over. Four marks from two colours: filled red is nothing arriving, ringed
   red is arriving and faulty, ringed teal is well but not compliant, filled
   teal is well. */
.nodes__standing::before,
.nodes__badge--worst::before {
  content: '';
  display: inline-block;
  width: 0.5rem;
  height: 0.5rem;
  border-radius: 50%;
  margin-right: 0.35rem;
  background: var(--stat-live);
}

.nodes__standing--never_seen::before,
.nodes__standing--stale::before,
.nodes__badge--worst::before {
  background: var(--stat-silent);
}

.nodes__standing--silent::before,
.nodes__standing--not_cached::before,
.nodes__standing--no_broker::before {
  background: transparent;
  box-shadow: inset 0 0 0 2px var(--stat-silent);
}

/* Publishing perfectly well over a transport it is not obliged to offer.
   Outlined rather than filled, so it reads as "on, with a question about it"
   without spending a third colour -- the same device the station table uses
   for a station nothing declares. */
.nodes__standing--archive_only::before {
  background: transparent;
  box-shadow: inset 0 0 0 2px var(--stat-live);
}

.nodes__empty {
  font-size: 0.85rem;
  color: var(--w-color-text-meta);
  margin: 0.75rem 0 0;
}
</style>
