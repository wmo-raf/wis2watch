/**
 * What one cell of the availability matrix means, and how wide it is drawn.
 *
 * Under the same rule `charts/plot.js` is under: ARITHMETIC AND TOKENS ONLY.
 * Nothing here returns markup. It is a separate file from that one because
 * the matrix is not a panelled chart -- it has no axis furniture, no measured
 * width and no hover layer -- and the two would only share a filename.
 *
 * **Three states, not two and not a ramp.** #48 drew all three: intensity
 * over `active_hours` is speckle whose light end collides with the silent
 * cell, and the eye loses the very structure the matrix exists for; binary is
 * crisp but blind to a station thinning from 22 hours a day to 2 before it
 * dies. Silent / thin / full keeps both -- a decaying cohort becomes a pale
 * wedge *before* it becomes silence -- and needs nothing new from the server,
 * because `active_hours` per bucket already carries it.
 *
 * The ceiling a cell is judged against is the *bucket's own*, which is the
 * part that is easy to get wrong. A UTC day's ceiling is 24 hours; the day in
 * progress has only had as many hours as have elapsed, and judging it against
 * 24 would draw every station on the page as thin every morning -- the exact
 * false alarm the open-bucket mark exists to prevent, reproduced in the fill.
 */
import {
    formatCount,
    formatDay,
    formatDayLong,
    formatHour,
    formatHourLong,
} from './charts/plot.js'

//: Below this much of a bucket, a station was heard *thinly* rather than
//: fully. **Provisional.** #48 drew it at 90 days against seeded data and it
//: looked right; #66 looked again in both themes and left it standing but
//: unmeasured. It is the one number on this tab still set against fake data,
//: and it is a single named constant so that real traffic can move it in one
//: place rather than in every call site.
export const THIN_FRACTION = 0.3

//: How many hours a whole UTC day holds, and how long an hour is. The daily
//: ceiling and the arithmetic that shortens it, each spelled once.
const HOURS_PER_DAY = 24
const MS_PER_HOUR = 3_600_000

/**
 * Everything about a matrix cell that follows from the size of its bucket.
 *
 * A map rather than a branch, in the same shape as the server's own
 * `PRESENCE_FOR_GRAIN` and for the same reason: the grain decides the
 * ceiling, the wording and the formatter, and three answers to one question
 * spelled in three `if`s is three places to forget one of them.
 *
 * `ceiling` is null where the grain has none. An hour carries messages, and
 * there is no number of messages an hour is "full" at, so the scale there is
 * the row's own busiest hour -- the convention the sparkline beside it
 * already uses, and for the same reason: station traffic is heavy-tailed, and
 * one dominant reporter scaled across the column would draw every other
 * station as thin.
 */
export const GRAINS = {
    day: {
        //: What a run of these buckets is called, for the label a screen
        //: reader is given.
        period: 'days',
        //: The words beside each colour, and the sentence that says what
        //: the pale one is measured against. Per grain rather than one
        //: template with the unit swapped in, because the middle state is not
        //: the same claim at both: a day is judged against the clock, and an
        //: hour against the station's own busiest.
        legend: {
            full: 'Heard for most of the day',
            thin: 'Heard, but for a small part of the day',
        },
        scale:
            'A cell is judged against the 24 hours of its day, so a pale one is'
            + ' a station that was heard for only a little of it.',
        long: formatDayLong,
        short: formatDay,
        ceiling: (bucket, now) => {
            if (!bucket.partial) {
                return HOURS_PER_DAY
            }

            // Never below one hour and never above a whole day: an axis read
            // a moment after midnight would otherwise divide by nearly zero
            // and call a station that has said nothing yet full.
            const elapsed = Math.floor((now - Date.parse(bucket.start)) / MS_PER_HOUR)

            return Math.min(HOURS_PER_DAY, Math.max(1, elapsed))
        },
        heard: (value, ceiling) => `heard in ${value} of ${ceiling} hours`,
        silent: 'silent all day: nothing heard from this station',
    },
    hour: {
        period: 'hours',
        bucket: 'the hour',
        legend: {
            full: "Heard at this station's own usual rate",
            thin: "Heard, but well below this station's busiest hour",
        },
        scale:
            "A cell is judged against this station's own busiest hour \u2014 the"
            + ' same scale the trace beside it is drawn to, and for the same'
            + ' reason: station traffic is heavy-tailed, and one dominant'
            + ' reporter scaled across the column would draw every other'
            + ' station as thin.',
        long: formatHourLong,
        short: formatHour,
        ceiling: null,
        heard: (value) => `${formatCount(value)} messages`,
        silent: 'silent: no messages this hour',
    },
}

/**
 * What a grain means, defaulting to the day.
 *
 * The default is not defensive tidiness: the rows and the summary arrive on
 * separate requests, so this is asked the question before the window has been
 * echoed back, and a matrix that throws in that moment takes the table with
 * it.
 *
 * @param {string} grain - the server's own spelling, `day` or `hour`.
 * @returns {object} the entry of `GRAINS` that grain names.
 */
export function grainOf(grain) {
    return GRAINS[grain] || GRAINS.day
}

/**
 * What a cell says, given what was heard in it and what the bucket could hold.
 *
 * @param {number} value - what the station was heard doing in this bucket.
 * @param {number} ceiling - the most this bucket could have carried.
 * @returns {string} `silent`, `thin` or `full`.
 */
export function presenceState(value, ceiling) {
    if (!value) {
        return 'silent'
    }

    return value < ceiling * THIN_FRACTION ? 'thin' : 'full'
}

/**
 * The ceiling each bucket of a window is judged against, or null at hourly grain.
 *
 * A daily bucket's ceiling is a fact about the clock, so it is the same for
 * every row and is worked out once for the whole table rather than a thousand
 * times. Null says "each row against its own busiest bucket", which is a
 * per-row answer and cannot be given by this function.
 *
 * @param {{start: string, partial: boolean}[]} buckets - the window's axis.
 * @param {string} grain - `day` or `hour`, the server's own spelling.
 * @param {string} asOf - when the payload was read, for the open bucket.
 * @returns {number[]|null} a ceiling per bucket, or null at hourly grain.
 */
export function bucketCeilings(buckets, grain, asOf) {
    const ceiling = grainOf(grain).ceiling

    if (!ceiling) {
        return null
    }

    const now = asOf ? Date.parse(asOf) : Date.now()

    return buckets.map((bucket) => ceiling(bucket, now))
}

//: How wide one cell is drawn, by how many of them there are. Fixed pixels
//: rather than a share of the column, because a cell that shrinks with the
//: panel is a matrix that stops being readable on a narrow window -- the row
//: height is fixed for the same reason. The table scrolls sideways instead.
const CELL_WIDTHS = [
    {upTo: 24, width: 12},
    {upTo: 31, width: 10},
]

//: What is left for a window nothing above covers: ninety days at 5px is a
//: 450px grid, which is a band a reader can take in without scrolling.
const NARROW_CELL = 5

/**
 * How wide one bucket is drawn, in real pixels.
 *
 * @param {number} count - how many buckets the window carries.
 * @returns {number} the width of one cell.
 */
export function cellWidthFor(count) {
    return (CELL_WIDTHS.find(({upTo}) => count <= upTo) || {width: NARROW_CELL}).width
}

/**
 * What one cell says in words, for the native tooltip it carries.
 *
 * Every cell carries one. There are thousands of them on screen, which is
 * exactly why this is the browser's own `<title>` rather than the shared
 * hover card the charts use: two tooltip idioms on one page, split by the
 * density of the surface they sit on.
 *
 * @param {{start: string, partial: boolean}} bucket - the bucket drawn.
 * @param {number} value - what the station was heard doing in it.
 * @param {number} ceiling - the most that bucket could have carried.
 * @param {string} grain - `day` or `hour`.
 * @returns {string} the sentence.
 */
export function presenceTitle(bucket, value, ceiling, grain) {
    const words = grainOf(grain)
    // Said in the tooltip as well as drawn, because the three states are a
    // colour, and a colour is never the only carrier of a finding here.
    const thin = presenceState(value, ceiling) === 'thin' ? ', thinly' : ''
    const heard = value ? `${words.heard(value, ceiling)}${thin}` : words.silent
    // Driven by the bucket rather than by the grain: an hourly axis never
    // carries an unfinished bucket, because the hour in progress is left out
    // of the window rather than served half-counted.
    const counting = bucket.partial
        ? ` — the day is still being counted, ${ceiling}h of it so far`
        : ''

    return `${words.long(new Date(bucket.start))} — ${heard}${counting}`
}
