<template>
  <div class="node-statistics">
    <p v-if="loading && !summary" class="node-statistics__state">
      Reading {{ nodeName }}'s stations&hellip;
    </p>

    <Message v-if="error" severity="error" :closable="false" class="node-statistics__error">
      {{ error }}
    </Message>

    <Message
        v-if="summary && !summary.vantage.active"
        severity="warn"
        :closable="false"
        class="node-statistics__vantage"
    >
      No Global Broker connection is switched on, so nothing here has been
      counted from the world's view of this centre.
    </Message>

    <!-- The page's spine, and the reason it is furniture rather than a note:
         the window buttons govern four of the seven panels and nothing here
         used to say which four. Under two headed bands the layout carries the
         claim -- everything below the second band moves when the buttons do,
         and everything below the first one does not. -->
    <!-- Two readings rather than one control with a hole in it. `24h` was
         never a window among four: the server has no daily series for a
         single day, which is why three panels used to empty themselves when
         it was picked. Under tabs it is not a window at all -- it is the
         first tab -- and the switcher on the second governs everything under
         it, which is the only arrangement in which a reader can see what the
         buttons are for. -->
    <Tabs v-if="summary || rows" :value="tab" @update:value="chooseTab">
      <TabList>
        <Tab value="now">Current State</Tab>
        <Tab value="past">Previous Days</Tab>
      </TabList>

      <TabPanels>
        <TabPanel value="now">
          <p class="node-statistics__strip">
            <span class="node-statistics__strip-title">Last 24 hours</span>
          </p>

          <div v-if="summary" class="node-statistics__standing">
            <p class="node-statistics__eyebrow">
              Standing
              <InfoNote label="How standing right now is counted">
                Standing is judged over a flat {{ summary.stale_after_hours }}h,
                whatever window you choose: a station is transmitting if this
                centre published for it within the last
                {{ summary.stale_after_hours }} hours.
                <template v-if="declares">The headline counts only stations the
                  registry declares. The
                </template>
                <template v-else>The</template>
                figures cover every station, declared or not, so a station that was
                never declared and has since stopped counts as gone quiet rather
                than as undeclared.
              </InfoNote>
            </p>

            <p class="node-statistics__headline">
              <template v-if="declares">
                <strong>{{ counts.transmitting }}</strong>
                of {{ counts.declared_station_count }} declared stations transmitting
              </template>
              <template v-else>
                <strong>{{ live }}</strong>
                of {{ population }} stations transmitting
              </template>
            </p>

            <!-- Said once, at the top, and the charts below repeat it on their
                 own axes: with nothing declared there is no promise to measure
                 against, so every ratio on this page is of what has been heard
                 rather than of what was undertaken.

                 Two ways of having no promise, and they are different findings
                 about different people. A centre that was asked and named
                 nothing has a registry to populate; a centre with no address of
                 its own was never asked, and saying it declares nothing would
                 report this tool's blind spot as the centre's. -->
            <p v-if="!declares" class="node-statistics__caveats">
              <template v-if="asked">
                Nothing is declared for this centre, so the figures on this page
                are counted against the stations it has been heard transmitting
                for &mdash; not against a registered network.
              </template>
              <template v-else>
                This centre advertises no station registry, so nothing has ever
                asked it what it declares. The figures on this page are counted
                against the stations it has been heard transmitting for &mdash;
                not against a registered network, and not because it has none.
              </template>
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

            <p
                v-if="counts.unlocated_station_count"
                class="node-statistics__caveats"
            >
              {{ counts.unlocated_station_count }} of them carry no coordinates
              and cannot be put on a map.
            </p>
          </div>

          <section v-if="summary" class="node-statistics__panel">
            <h3 class="node-statistics__panel-heading">
              Hourly Stations Reporting
              <InfoNote label="How the hour-by-hour chart is read">
                This chart always shows the last 24 whole hours, whatever window
                you choose &mdash; a shorter span than the standing figures above,
                which are judged over a flat {{ summary.stale_after_hours }}h.
                Message counts are in the words below the chart.
              </InfoNote>
            </h3>
            <p class="node-statistics__panel-note">
              Stations reporting in a given hour.
            </p>

            <HourlyChart
                :buckets="summary.now.buckets"
                :hourly="summary.now.hourly"
                :axis="axis"
                :selected="view.bucket"
                :selectable="hourlyIsTheWindow"
                @select="refine({bucket: $event})"
            />
          </section>

          <!-- The rows again, on the ground. Above the band rather than beside
               the table, because standing here is the flat threshold and not the
               window: this panel answers the same question the headline above it
               does, in a different projection. -->
          <section v-if="rows" class="node-statistics__panel">
            <h3 class="node-statistics__panel-heading">
              Stations
              <InfoNote label="What the map can say that the table cannot">
                Standing here is right now, a flat {{ rows.stale_after_hours }}h.
                A block of silent pins close together is a regional outage, where
                the same number scattered across the country is stations failing
                one at a time.
              </InfoNote>
            </h3>
            <p class="node-statistics__panel-note">
              Click a station to show details
            </p>

            <StationMap
                :stations="rows.stations"
                :selected="view.station"
                :stale-after-hours="rows.stale_after_hours"
                @choose="refine"
            />
          </section>
        </TabPanel>

        <TabPanel value="past">
          <p class="node-statistics__strip">
            <span class="node-statistics__strip-title">
              Over {{ bandLabel.toLowerCase() }}
            </span>
            <span v-if="pastWindows.length" class="node-statistics__strip-control">
              <WindowControl
                  label="Period"
                  :model-value="windowKey"
                  :windows="pastWindows"
                  :busy="loading"
                  @update:model-value="choose"
              />
            </span>
          </p>

          <div v-if="summary" class="node-statistics__window">
            <p class="node-statistics__eyebrow">
              Coverage
              <InfoNote label="What counts as reporting inside the window">
                A station counts here if this centre published for it at least
                once during the window
                <template v-if="declares">. A station the
                  registry never declared can count here, but not in the
                  {{ windowStats.declared_station_count }} beside it
                </template>
                .
              </InfoNote>
            </p>

            <p class="node-statistics__headline">
              <strong>{{ windowStats.reported_station_count }}</strong>
              of {{ declares ? windowStats.declared_station_count : population }}
              reported at least once
            </p>

            <p class="node-statistics__population">
              {{ count(windowStats.messages_total) }} messages,
              {{ count(windowStats.unattributed_messages_total) }} of them naming
              no station.
            </p>

            <!-- Whether that share is bad is a question this tab cannot answer:
                 a share only reads as a verdict beside the centres carrying the
                 identifier throughout, and this page has one centre on it. The
                 report that lists them all is region-wide and the link is plain
                 -- it lands on the whole table, this centre somewhere in it.

                 The text names the report's window rather than the one the
                 control above is set to, because they are different periods and
                 the report will quote a different percentage for this centre
                 than the figures here do. Saying whose window it is, in the
                 report's own vocabulary of hours, is where that mismatch is
                 disarmed. -->
            <p class="node-statistics__compare">
              <a :href="unattributedReportUrl">
                Compare with other centres over the last
                {{ attributionHours }} hours
              </a>
            </p>

            <!-- The gap between the two bands, said here in full rather than left
                 to be found by comparing this figure with one a screen above it:
                 the bands are what separated them, so the sentence has to carry
                 both numbers itself. -->
            <p class="node-statistics__caveats">
              <template v-if="stoppedSince">
                <strong>{{ stoppedSince }}</strong> more stations reported inside
                this window than the {{ live }} transmitting now: they reported and
                have since stopped.
              </template>
              <template v-else>
                Every station that reported inside this window is still
                transmitting.
              </template>
            </p>
          </div>

          <template v-if="summary && windowStats.daily">
            <section class="node-statistics__panel">
              <h3 class="node-statistics__panel-heading">
                Stations reporting, day by day
                <InfoNote label="Why today's bar is drawn open">
                  Today's bar is dashed and open on the right because the day is
                  still being counted. It will look short in the morning. That is
                  not an outage.
                </InfoNote>
              </h3>

              <p class="node-statistics__panel-note">
                One bar per UTC day, on the same scale as the hours above. The
                height is how many stations reported that day.
              </p>

              <DailyChart
                  :buckets="summary.buckets"
                  :daily="windowStats.daily"
                  :axis="axis"
                  :as-of="summary.generated_at"
                  :selected="view.bucket"
                  selectable
                  @select="refine({bucket: $event})"
              />
            </section>

            <section class="node-statistics__panel">
              <h3 class="node-statistics__panel-heading">
                Messages per active station
                <InfoNote label="How messages per active station is read">
                  The line breaks on days when no station reported. There is no
                  per-station figure for a day like that, and a zero would wrongly
                  say every station was silent.
                </InfoNote>
              </h3>

              <p class="node-statistics__panel-note">
                One point per UTC day. The height is the average number of messages
                from each station that reported.
              </p>

              <RatioChart
                  :buckets="summary.buckets"
                  :daily="windowStats.daily"
                  :as-of="summary.generated_at"
                  :selected="view.bucket"
                  selectable
                  @select="refine({bucket: $event})"
              />
            </section>
          </template>

          <section
              v-if="summary && windowStats.hour_of_day"
              class="node-statistics__panel"
          >
            <h3 class="node-statistics__panel-heading">
              Message volume by hour of day, UTC
              <InfoNote label="How the hour-of-day profile is read">
                A flat profile is a centre publishing whenever observations happen
                to arrive. Today's hours so far are included, so over a short window
                the hours already past today are summed over one more day than the
                hours still to come.
              </InfoNote>
            </h3>

            <p class="node-statistics__panel-note">
              Messages received in each hour of the day, summed over the window.
            </p>

            <HourOfDayChart
                :hour-of-day="windowStats.hour_of_day"
                :window-label="summary.window.label"
            />
          </section>

        </TabPanel>
      </TabPanels>
    </Tabs>

    <!-- One station, opened. Below the tabs with the rows, because that is
         where one of the two gestures that open it is -- the other, a pin, is
         on the current-state tab. Inline rather than over the page:
         everything the reader chose is in the querystring, so dismissing it
         clears one key and the sort, the filter and the picked bucket are
         untouched, and the table is never unmounted and never loses its
         place. -->
    <StationDrilldown
        v-if="stationUrl"
        :url="stationUrl"
        :centre-id="centreId"
        @dismiss="refine({station: ''})"
    />

    <!-- The rows, under both tabs rather than inside either. They are the same
         stations whichever tab is open; only the grain of the matrix moves,
         which is the window's doing and not the tab's. Kept out of the panels
         so that switching tabs does not unmount a thousand rows and lose the
         reader's sort, filter and place in them.

         Drawn off `rows` rather than off the summary, and that is the whole
         point of it being a second request: the rows arrive on their own and
         are drawn whether or not the figures did. Everything this panel needs
         to label itself is echoed on the rows' own payload, so it never
         reaches into the summary and cannot be taken down with it. -->
    <section v-if="rows || stationsError" class="node-statistics__panel">
      <h3 class="node-statistics__panel-heading">
        Stations, what is broken first
        <InfoNote label="Whose observation the rows are">
          Everything here is what this centre published.
        </InfoNote>
      </h3>
      <p class="node-statistics__panel-note">
        One row per station this centre declares or has been heard transmitting for.
      </p>

      <StationTable
          v-if="rows"
          :stations="rows.stations"
          :buckets="rows.buckets"
          :grain="rows.window.grain"
          :as-of="rows.generated_at"
          :bucket="view.bucket"
          :station="view.station"
          :search="view.search"
          :standing="view.standing"
          :sort="view.sort"
          :direction="view.direction"
          :window-label="rows.window.label"
          :window-key="rows.window.key"
          :centre-name="nodeName"
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
 * **A bucket picked on a chart or a matrix column filters the rows to the
 * stations that were dark in it**, and it is the same querystring state as
 * the rest. The charts and the matrix are drawn from two payloads, so the
 * selection travels as the bucket's start rather than as a column index: an
 * index would mean "the third column of whichever list you happen to be
 * holding", and the two lists are not always the same length. A bucket the
 * rows' axis does not carry is dropped rather than guessed at.
 *
 * **The map above the rows is the same population placed on the ground**, and
 * it is bound to the *standing* rather than to the window: "reported in the
 * window" is degenerate at both ends of the range, drawing the identical
 * picture at 24 hours and painting a block dead for weeks green at 90 days.
 * So it is the one panel here a reader can move the control past without it
 * changing, and it says so on its legend. It reads the rows rather than a map
 * endpoint of its own, because a second source for the same stations is a
 * second population to disagree with the table.
 *
 * The station a reader picks is one piece of state for three surfaces: picked
 * on the map, it is highlighted in the rows; picked in the rows, it is ringed
 * on the map; and either way it opens the drilldown above them, which is the
 * last step of the journey the tab exists for -- the reader has found *which*
 * station stopped, and now opens it. It is not a filter -- it hides nothing on
 * either surface -- and where the filters above the rows have hidden the
 * station it names, the table says so rather than quietly widening itself.
 *
 * The drilldown is a third request and is reached by adding an id to the
 * stations URL rather than by a path of its own, so that everything this
 * bundle asks for was reversed on the Django side. It arrives and fails on its
 * own, like the rows: a station whose drilldown cannot be read leaves the
 * figures and the table standing.
 *
 * Both URLs are handed in as props rather than assembled here. The bundle is
 * built ahead of time, so a path composed inside it is a path nobody can
 * rename from the Django side.
 */
import {computed, onMounted, ref} from 'vue'
import Message from 'primevue/message'
import Tab from 'primevue/tab'
import TabList from 'primevue/tablist'
import TabPanel from 'primevue/tabpanel'
import TabPanels from 'primevue/tabpanels'
import Tabs from 'primevue/tabs'

import DailyChart from './DailyChart.vue'
import HourlyChart from './HourlyChart.vue'
import HourOfDayChart from './HourOfDayChart.vue'
import InfoNote from './InfoNote.vue'
import RatioChart from './RatioChart.vue'
import StationDrilldown from './StationDrilldown.vue'
import StationMap from './StationMap.vue'
import StationTable from './StationTable.vue'
import WindowControl from './WindowControl.vue'
import {populationAxis} from './charts/plot.js'
import {readParam, writeParams} from './querystring.js'
import {pickedStationId} from './selection.js'
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
  //: Where the comparison this tab declines to make is actually made.
  //: Reversed on the server like every other path here: the bundle is built
  //: ahead of time, so a URL assembled inside it is a URL nobody can rename
  //: from the Python side.
  unattributedReportUrl: {
    type: String,
    required: true
  },
  //: Over how many hours that report works its share out. Off the server for
  //: the same reason the windows are: it is a setting, and a page that spelled
  //: its own copy would go on advertising the old period after it was widened.
  attributionHours: {
    type: Number,
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

//: What the reader has narrowed the stations to, under the same rule the
//: window is: in the address bar, so the link a reader copies reproduces the
//: rows they were looking at and not merely the node they were on. Read off
//: the URL cold, before any request is made.
//:
//: The rows and the map read one of these rather than one each. A station
//: picked on the map is the station highlighted in the rows, and two pieces
//: of state saying so is how the two panels come to disagree about which
//: station the reader is looking at.
const view = ref({
  search: readParam('q'),
  standing: readParam('standing'),
  // The bucket a reader picked on a chart or a column head, as the server
  // spelled its start. Read off the URL cold like the rest of them, which is
  // what makes a filtered view a link somebody can send.
  bucket: readParam('bucket'),
  // The one station the reader picked, on the map or in the rows. Not a
  // filter: it hides nothing on either surface, and the rows say so where
  // the filters above them have hidden the station it names.
  station: readParam('station'),
  sort: readParam('sort'),
  direction: readParam('dir') || 'asc',
})

// The windows on offer, kept beside the summary rather than read out of it,
// so that a refusal can seed them too: a reader who arrives on a stale link
// needs the control more than anyone, and a page with only an error on it is
// a dead end.
const windows = ref([])

//: Whether the hourly chart is drawn on the axis the station rows are drawn
//: against, which is true only while the window is the hourly one. It is the
//: flat-24h chart at every other window, and a bucket picked on it there
//: would name an hour the rows have no column for -- so the gesture is
//: offered where it means something and withheld where it would filter the
//: table to nothing. Read off the *rows'* payload rather than the summary's,
//: because the axis being asked about is theirs: with no rows on the page
//: there is nothing for a pick to filter either.
const hourlyIsTheWindow = computed(() => rows.value?.window.grain === 'hour')

//: The picked station's own endpoint, over the window on screen. Derived
//: from the stations URL rather than composed out of a path of its own: that
//: URL was reversed on the Django side, and the drilldown is the same
//: collection with an id on the end -- which is what keeps this bundle from
//: carrying a path nobody can rename from Python. Empty where nothing is
//: picked, and where the address bar carries something that is not an id.
//:
//: The window is the one the rows settled on where they have arrived, and
//: the control's before that. Deliberately not gated on the rows: a link
//: carrying a station is the case this panel exists for, and one that drew
//: nothing until a thousand rows had crossed the wire -- and nothing at all
//: if they failed -- would be a link that does not stand on its own, which is
//: the whole point of putting the station in the address bar.
//:
//: The cost is one extra read, and only on a link that names a station and no
//: window: the control is empty until the summary answers, so the first ask
//: takes the server's default and the second names it. Asking the server what
//: its default is, is cheaper than a panel that waits for the rows.
const stationUrl = computed(() => {
  const id = pickedStationId(view.value.station)

  if (id === null) {
    return ''
  }

  const url = new URL(`${props.stationsUrl}${id}/`, window.location.origin)
  const key = rows.value?.window.key || windowKey.value

  if (key) {
    url.searchParams.set(WINDOW_PARAM, key)
  }

  return String(url)
})

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

// Whether there is a promise on this page at all. Where a centre declares
// nothing -- which on this region is every centre, because the declarations
// have never been populated -- "412 of 500" has no 500 in it, and every
// surface that would have said one has to say which population it is counting
// against instead.
const declares = computed(() => counts.value.declared_station_count > 0)

// Why there is no promise, where there is none. A centre whose catalogue
// records advertise no address for it has no station registry to read, so its
// declared count is nought for a reason that is nothing to do with the centre
// -- and the caveat above has to say which of the two it is looking at.
const asked = computed(() => counts.value.advertises_station_registry)

// What is transmitting right now, counted the way the headline beside it is
// read. `TRANSMITTING` is declared-only by construction -- an undeclared
// station heard a minute ago is `UNDECLARED`, not transmitting -- so on a
// centre that declares nothing the standing figure is 0 however much traffic
// is arriving, and a headline of "0 of 57" would be the page's most confident
// wrong sentence. The two standings are disjoint, so this is a sum rather
// than a wider query.
const live = computed(() =>
    counts.value.transmitting + counts.value.undeclared_transmitting
)

//: The two readings the page offers, and they are spelled here rather than
//: read off anything: they are this component's own vocabulary and the API
//: has never heard of them.
const CURRENT = 'now'
const PAST = 'past'

//: The hourly window, which is the current-state tab rather than an option in
//: the switcher. Found by its grain rather than by its key, for the reason
//: every other window fact here is read off the server: the page must not be
//: the second place that knows what the windows are called.
const hourWindow = computed(
    () => windows.value.find((option) => option.grain !== 'day') || null
)

//: The windows the switcher offers, which is every window that is not the
//: hourly one. A control that still offered `24h` would be offering the tab
//: the reader is not on.
const pastWindows = computed(
    () => windows.value.filter((option) => option.grain === 'day')
)

//: Which tab is open, derived from the window rather than stored beside it.
//: The window is already in the address bar and already the thing the two
//: tabs differ by, so a second key saying the same thing is a link that can
//: contradict itself: `window=90d` on the current-state tab is a state with
//: no meaning and no way to draw it.
const tab = computed(
    () => (summary.value?.window.grain === 'day' ? PAST : CURRENT)
)

//: The period to come back to. A reader who was reading 90 days, looked at
//: the current state and went back should find 90 days rather than the
//: default -- and on a first visit there is nothing to remember, so the
//: switcher's own first option stands in.
const lastPast = ref(readParam(WINDOW_PARAM))

// What the switcher's band is headed with. Off the summary where there is one
// and off the rows otherwise, because the rows arrive on their own and the
// band has to name its window even on a page whose figures failed.
const bandLabel = computed(() => {
  const chosen = pastWindows.value.find((option) => option.key === windowKey.value)

  return chosen?.label || pastWindows.value[0]?.label || 'the window'
})

// The axis the two coverage charts are drawn on, derived once here rather
// than twice in them. They sit above each other on one page and are read
// against each other, so a second derivation is not a duplication but a way
// for the two panels to end up on different tops.
const axis = computed(() =>
    populationAxis(counts.value.declared_station_count, population.value)
)

// The gap the two blocks exist to show, stated rather than left to be worked
// out by subtracting one headline from the other -- from `live` rather than
// from `transmitting`, or a centre declaring nothing reports its whole
// population as having stopped. Never negative: at the
// default window the two count the same day and the coverage can be the
// smaller of the pair, which is not a finding about anything.
const stoppedSince = computed(() =>
    Math.max(
        0,
        windowStats.value.reported_station_count - live.value
    )
)

/** Read the page over a window, and say so in the address bar. */
async function choose(key) {
  windowKey.value = key
  writeParams({[WINDOW_PARAM]: key})

  await load()
}

/**
 * Open a tab, which is to say: read the page over that tab's window.
 *
 * The switch is a window change and nothing else, which is what keeps the two
 * tabs from needing state of their own -- and what makes a tab a link. The
 * period tab comes back to whatever period was last read rather than to a
 * default, because a reader who stepped away from 90 days to check the
 * current state did not thereby ask for 7.
 */
async function chooseTab(key) {
  const wanted = key === CURRENT
      ? hourWindow.value?.key
      : (pastWindows.value.some((option) => option.key === lastPast.value)
          ? lastPast.value
          : pastWindows.value[0]?.key)

  if (wanted && wanted !== windowKey.value) {
    await choose(wanted)
  }
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
  view.value = {...view.value, ...chosen}

  writeParams({
    q: view.value.search,
    standing: view.value.standing,
    bucket: view.value.bucket,
    station: view.value.station,
    sort: view.value.sort,
    // Never on its own: a direction with nothing sorted by it is a link that
    // says something about a sort that is not happening.
    dir: view.value.sort ? view.value.direction : '',
  })

  dropSelectionOffTheAxis()
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

    // Remembered here rather than in `choose`, because this is where the
    // window is *settled*: a stale link and a refused key both land here.
    if (summary.value.window.grain === 'day') {
      lastPast.value = summary.value.window.key
    }
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

    dropSelectionOffTheAxis()
  } catch (failure) {
    stationsError.value = failure.message || 'The stations could not be read.'
  }
}

/**
 * Forget a picked bucket the rows have no column for.
 *
 * A selection is a bucket start rather than a column index precisely so that
 * this case can be *detected* -- a link carrying a day of a 90-day window,
 * opened at the default 24 hours, names an instant this axis does not carry.
 * It is dropped rather than left in the address bar, because a querystring
 * naming a filter that is not running is a link that reproduces the wrong
 * view the next time it is opened.
 *
 * A window the reader moves to that still carries the bucket keeps it: the
 * same day is the same day on a 30-day axis and a 90-day one.
 *
 * Asked on every refinement as well as on every load, because the two
 * payloads are read at two instants: an hour the charts still carry can have
 * fallen off the rows' axis already, and a table that quietly ignores the
 * bucket in its own address bar is the one state here nobody could explain.
 * It settles in one step -- clearing the bucket is a refinement whose own
 * check returns immediately.
 */
function dropSelectionOffTheAxis() {
  if (!view.value.bucket || !rows.value) {
    return
  }

  const carried = rows.value.buckets.some(
      (bucket) => bucket.start === view.value.bucket
  )

  if (!carried) {
    refine({bucket: ''})
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

/* Each tab's own band, and the whole of what says which clock the panels
   under it are on. A heading with a ground rather than a control in a corner:
   the reader has to be able to see, without reading anything, that the
   buttons belong to what is beneath them. */
.node-statistics__strip {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.5rem 0.85rem;
  margin: 0 0 1rem;
  padding: 0.55rem 0.9rem;
  border: 1px solid var(--w-color-border-furniture);
  border-radius: 0.3rem;
  background: var(--stat-band);
}

.node-statistics__strip-title {
  font-size: 0.85rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--w-color-text-label);
}

.node-statistics__strip-note {
  font-size: 0.75rem;
  color: var(--w-color-text-meta);
}

/* Hard right, so the buttons are in the same place on both bands' worth of
   scrolling and are never mistaken for part of the heading's words. */
.node-statistics__strip-control {
  margin-left: auto;
}

.node-statistics__error,
.node-statistics__vantage {
  margin-bottom: 1rem;
}

/* One under each band rather than side by side. The pair used to be read
   against each other in the layout; the bands separated them, so the
   comparison is carried in the coverage block's own words instead. */
.node-statistics__standing,
.node-statistics__window {
  margin-bottom: 1rem;
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

/* Set apart from the figure above it rather than run on from it: the link
   leaves the page, and a destination that reads as the tail of a sentence
   about this centre is one a reader follows without noticing they have gone
   region-wide. */
.node-statistics__compare {
  font-size: 0.8rem;
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
