<template>
  <div class="nodes" :class="`nodes--${view}`">
    <!-- A fresh install before the first catalogue sync. Its own sentence
         rather than the population line's, and the controls are not drawn at
         all: there is nothing to filter, and two empty fields over an empty
         table invite a reader to conclude their filter is what hid the
         region. -->
    <p v-if="!rows.length" class="nodes__empty">
      No centres are registered yet. The catalogue sync populates them, and a
      fresh install has none until the first sync lands.
    </p>

    <template v-else>
      <div class="nodes__controls">
        <label class="nodes__field">
          <span class="nodes__field-label">Search</span>
          <input
              v-model="search"
              type="search"
              class="nodes__input nodes__input--search"
              placeholder="Centre ID or country"
          >
        </label>

        <!-- Offered on the detailed page and not on the glance, because on
             the glance the default sort already does its job: worst first puts
             every centre that is not transmitting in the first rows, and a
             control that repeats the ordering is chrome earning nothing.
             Search stays on both, because it answers the one thing sorting
             cannot -- how is *this* centre, right now. -->
        <label v-if="filterable" class="nodes__field">
          <span class="nodes__field-label">Standing</span>
          <select v-model="standing" class="nodes__input nodes__input--standing">
            <option value="">All standings</option>
            <option v-for="option in standings" :key="option.key" :value="option.key">
              {{ option.label }}
            </option>
          </select>
        </label>

        <button v-if="filtering" type="button" class="nodes__clear" @click="clear">
          Clear filter
        </button>

        <!-- What is on screen, and of how much, on the same line as the
             controls that decided it: cause and effect together, and the row
             is balanced by something worth reading rather than by stretching
             a text field across the panel.

             The degenerate case has its own sentence on purpose. A filter that
             matched everything is not the same as no filter, and a reader who
             cannot tell them apart reads a full table as the whole region. -->
        <p class="nodes__population" role="status">
          <template v-if="counted.state === 'all'">
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
      </div>

      <div v-if="shown.length" class="nodes__scroll">
        <table class="nodes__table">
          <colgroup>
            <col v-for="column in columns" :key="column.key" :style="{width: column.width}">
          </colgroup>

          <thead>
          <tr>
            <th
                v-for="column in columns"
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
            <td
                v-for="column in columns"
                :key="column.key"
                :class="[`nodes__cell--${column.align || 'text'}`]"
            >
              <!-- The code is the handle, and it leads to the centre's own
                   statistics tab, which is where going back in time lives --
                   24 hours to ninety days. That is the question both tables
                   leave a reader with: this centre looks wrong today, what has
                   it been doing? The whole row is deliberately not a link: this
                   table has sortable heads and controls directly above it, and
                   a full-row hit target on a surface people are fiddling with
                   is how a reader leaves the page by accident. -->
              <a v-if="column.key === 'centre_id'" :href="row.node_url" class="nodes__centre">
                <code>{{ row.centre_id }}</code>
              </a>

              <span v-else-if="column.key === 'country_name'">
                {{ row.country_name || '—' }}
              </span>

              <!-- Whichever verdict this view draws, marked the same way. -->
              <span
                  v-else-if="column.key === verdict"
                  class="nodes__standing"
                  :class="`nodes__standing--${row[verdict]}`"
              >
                {{ word(verdict, row[verdict]) }}
              </span>

              <span v-else-if="column.key === 'last_seen_at'">
                {{ formatInstant(row.last_seen_at) }}
              </span>

              <span v-else-if="column.key === 'hours_quiet'">
                {{ formatQuiet(row.hours_quiet) }}
              </span>

              <span v-else-if="column.key === 'messages_in_window'">
                {{ formatCount(row.messages_in_window) }}
              </span>

              <Sparkline
                  v-else-if="column.key === 'sparkline'"
                  :values="row.sparkline"
                  :name="row.centre_id"
                  :standing-label="word(verdict, row[verdict])"
                  :height="SPARK_HEIGHT"
              />

              <span v-else-if="COUNTS.includes(column.key)">
                {{ formatCount(row[column.key]) }}
              </span>

              <!-- The badges, with what the overview page used to say under
                   them carried as a tooltip instead of as a second and third
                   line. Rows that deep destroy the reading down a column that
                   a worst-first table exists for -- and the broker's error
                   arrives here whole, where that page cut it at sixty
                   characters. -->
              <span
                  v-else
                  class="nodes__badge"
                  :class="{'nodes__badge--worst': isWorst(column.key, row[column.key])}"
                  :title="badgeTitle(row, column.key, labels) || null"
              >
                {{ word(column.key, row[column.key]) }}
              </span>
            </td>
          </tr>
          </tbody>
        </table>
      </div>

      <p v-else class="nodes__empty">
        No centre matches this filter. All {{ formatCount(rows.length) }} of them are
        still here &mdash;
        <button type="button" class="nodes__clear" @click="clear">clear it</button>
        to see them.
      </p>
    </template>
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
import {computed, ref, watch} from 'vue'

import Sparkline from '@/components/node-statistics/Sparkline.vue'
// The three formatters the station rows are drawn with, borrowed rather than
// rewritten: a table with its own idea of how a quiet span is worded is a
// second answer to a question this tool already answers. They live under the
// statistics island because that is where they were first needed; if a shared
// format module is ever extracted, both callers move together.
import {formatCount, formatInstant, formatQuiet} from
    '@/components/node-statistics/charts/plot.js'
// The address bar, borrowed from the statistics tab rather than rewritten --
// its own docstring says it exists so that two components syncing state do not
// each invent a way to do it, and one of them start pushing history entries.
import {readParam, writeParams} from '@/components/node-statistics/querystring.js'

import {
    badgeTitle,
    columnsFor,
    labelsFrom,
    matches,
    narrowing,
    nextSort,
    population,
    ranksFrom,
    sortRows,
    verdictFor,
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
  /**
   * Which table this is: `glance` or `detail`.
   *
   * It decides three things together, which is why it is one name and not
   * three flags: which columns are drawn, which of the two verdicts goes in
   * the status slot, and whether the standing filter is offered at all.
   */
  view: {
    type: String,
    default: 'glance'
  },
})

//: How tall a sparkline is drawn, in real pixels. Handed to the component
//: rather than chosen by it, because the row height is this table's decision.
const SPARK_HEIGHT = 20

//: The columns that are plain numbers about how big a centre is rather than
//: how well it is. Drawn on the detailed page only.
const COUNTS = ['dataset_count', 'station_count']

//: Whether this table's state belongs in the address bar.
//:
//: The detailed page's is: "look at these centres" is a link worth sending,
//: and the page it replaces had shareable `?staleness=` links. The glance
//: panel's is not -- it lives on the admin home, and a homepage that
//: accumulated `?q=` on every keystroke is a homepage nobody can bookmark.
const linkable = props.view === 'detail'

const search = ref(linkable ? readParam('q') : '')
const standing = ref(linkable ? readParam('standing') : '')
const sort = ref(linkable ? readParam('sort') : '')
const direction = ref(linkable ? readParam('dir') || 'asc' : 'asc')

// Deliberately no reading of the page's old `?staleness=` and `?order=`. Two
// of the three staleness values map onto a standing and the third does not --
// "active" spans five of the seven -- and a mapping that silently drops a
// third of its inputs is worse than none. Nothing in the codebase ever linked
// here with parameters; only a reader's own copied link could carry them.
if (linkable) {
  watch([search, standing, sort, direction], () => {
    writeParams({
      q: search.value,
      standing: standing.value,
      sort: sort.value,
      // Only where there is a column to be a direction of. `?dir=asc` on its
      // own is a key a reader is asked to interpret and that means nothing.
      dir: sort.value ? direction.value : '',
    })
  })
}

const ranks = computed(() => ranksFrom(props.vocabularies))
const labels = computed(() => labelsFrom(props.vocabularies))

//: The columns this view draws, and which verdict sits in its status slot.
const columns = computed(() => columnsFor(props.view))
const verdict = computed(() => verdictFor(props.view))

//: Whether the standing filter is offered. The glance table does without it:
//: worst first already puts everything that is not transmitting at the top.
const filterable = computed(() => props.view === 'detail')

//: The standings a filter can be set to, in the server's own order, so the
//: control offers them worst first exactly as the rows arrive.
const standings = computed(() => props.vocabularies[verdict.value] || [])

const filtering = computed(() =>
    narrowing({search: search.value, standing: standing.value})
)

const matching = computed(() =>
    props.rows.filter((row) =>
        matches(row, {search: search.value, standing: standing.value}, verdict.value)
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
/* One row's height, stated rather than left to the content, because the
   scroll region's own height is counted in rows: twelve and a half of them,
   so the clipped row at the bottom is a deliberate "there is more below"
   rather than wherever a round number of rem happened to land. The half row
   and the scrollbar are the only two things telling a reader the region
   scrolls at all. */
.nodes {
  --nodes-row: 2.25rem;
  --nodes-visible-rows: 12.5;
}

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
  margin: 0 0 var(--nodes-gutter);
}

.nodes__field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin: 0;
}

/* Wagtail's own label values -- .875rem, 600, the label colour -- read off its
   tokens rather than borrowed from `w-field__label`. This codebase reads
   tokens and never Wagtail's component classes: a token survives a refactor,
   a class that gets renamed drops silently to browser default. */
.nodes__field-label {
  font-size: 0.875rem;
  font-weight: 600;
  line-height: 1.3;
  color: var(--w-color-text-label);
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
}

/* Sized to their jobs rather than to each other. Free text gets room to type
   in; the dropdown gets enough for "Not reaching the caches", because a select
   that truncates its own values is worse than a wide one. */
.nodes__input--search {
  width: 18rem;
  max-width: 100%;
}

.nodes__input--standing {
  width: 14rem;
  max-width: 100%;
}

.nodes__clear {
  background: none;
  border: 0;
  padding: 0 0 0.5rem;
  font-size: 0.8rem;
  color: var(--w-color-text-link-default);
  text-decoration: underline;
  cursor: pointer;
}

/* Pushed to the far end of the controls row, which is what balances it: the
   dead space to the right of two filters is filled by the sentence saying what
   those filters just did. */
.nodes__population {
  margin: 0 0 0.35rem auto;
  font-size: 0.85rem;
  color: var(--w-color-text-context);
  text-align: end;
}

/* Full bleed, escaping the gutter this island's root put back. Wagtail's own
   dashboard listings run edge to edge and inset their text instead, and the
   doubled frame -- a bordered box inside a bordered panel -- is what made this
   read as a table dumped in a panel rather than as a panel.

   The rules above and below are the region's own. The header has Wagtail's
   `w-panel__divider` doing that job already, so there is none under it. */
.nodes__scroll {
  max-height: calc(var(--nodes-row) * (var(--nodes-visible-rows) + 1));
  overflow: auto;
  margin-inline: calc(var(--nodes-gutter) * -1);
  border-block: 1px solid var(--w-color-border-furniture);
}

/* A page has no reason to clip. The panel bounds itself because it is a panel
   on a homepage and a region of forty centres drawn at full height would be
   the whole page; here the region *is* the page, and a reader who came looking
   for the twenty-ninth centre should not have to find it through a scrollbar
   inside a scrollbar. The columns still scroll sideways -- twelve of them do
   not fit a laptop -- so the rule above stays for that axis. */
.nodes--detail .nodes__scroll {
  max-height: none;
}

.nodes__table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.85rem;
}

.nodes__table th,
.nodes__table td {
  height: var(--nodes-row);
  padding: 0 0.6rem;
  text-align: left;
  white-space: nowrap;
  border-bottom: 1px solid var(--w-color-border-furniture);
}

/* The text lines up with the heading above and the stamp below; the rules run
   past it to the panel's own border. */
.nodes__table th:first-child,
.nodes__table td:first-child {
  padding-inline-start: var(--nodes-gutter);
}

.nodes__table th:last-child,
.nodes__table td:last-child {
  padding-inline-end: var(--nodes-gutter);
}

.nodes__table tbody tr:last-child td {
  border-bottom: 0;
}

/* The heads stay put while the region scrolls under them. On a table sorted
   worst-first, a reader who has scrolled to the quiet end and cannot see which
   column is which has lost the thing they scrolled for. The background is
   opaque and spans the whole cell, or rows show through as they pass under. */
.nodes__table thead th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--w-color-surface-dashboard-panel, var(--w-color-surface-page));
  font-weight: 600;
  color: var(--w-color-text-meta);
  /* The rule under the heads, drawn as an inset shadow rather than left to
     the shared `border-bottom` above. Under `border-collapse: collapse` a
     cell's borders are painted with the *table*, not with the cell, so a
     sticky head keeps its background and its text and leaves its border
     behind at the top of the scroll -- every row has a line under it except
     the one row that most needs separating from the data. A shadow is painted
     with the element, so it travels. */
  box-shadow: inset 0 -1px 0 var(--w-color-border-furniture);
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
  margin: 0;
}
</style>
