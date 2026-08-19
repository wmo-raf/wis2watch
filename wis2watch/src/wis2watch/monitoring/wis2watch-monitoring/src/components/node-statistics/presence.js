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
import {formatDayLong, formatHourLong} from './charts/plot.js'

//: Below this much of a bucket, a station was heard *thinly* rather than
//: fully. **Provisional.** #48 drew it at 90 days against seeded data and it
//: looked right; #66 looked again in both themes and left it standing but
//: unmeasured. It is the one number on this tab still set against fake data,
//: and it is a single named constant so that real traffic can move it in one
//: place rather than in every call site.
export const THIN_FRACTION = 0.3

//: How many hours a whole UTC day holds. The daily ceiling, spelled once.
const HOURS_PER_DAY = 24

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
 * A daily bucket has a ceiling that is a fact about the clock -- 24 hours,
 * and fewer for the day still in progress -- so it is the same for every row
 * and is worked out once for the whole table rather than a thousand times.
 *
 * An hourly bucket has no such ceiling: it carries messages, and there is no
 * number of messages an hour is "full" at. So the scale is the row's own
 * busiest hour, which is the convention the sparkline beside it already uses
 * and for the same reason -- station traffic is heavy-tailed, and one
 * dominant reporter scaled across the column would draw every other station
 * as thin. Null here says "each row against itself", which is a per-row
 * answer and cannot be given by this function.
 *
 * @param {{start: string, partial: boolean}[]} buckets - the window's axis.
 * @param {string} grain - `day` or `hour`, the server's own spelling.
 * @param {string} asOf - when the payload was read, for the open bucket.
 * @returns {number[]|null} a ceiling per bucket, or null at hourly grain.
 */
export function bucketCeilings(buckets, grain, asOf) {
    if (grain !== 'day') {
        return null
    }

    const now = asOf ? Date.parse(asOf) : Date.now()

    return buckets.map((bucket) => {
        if (!bucket.partial) {
            return HOURS_PER_DAY
        }

        // Never below one hour, and never above a whole day: an axis read a
        // moment after midnight would otherwise divide by nearly zero and
        // call a station that has said nothing yet "full".
        const elapsed = (now - Date.parse(bucket.start)) / 3_600_000

        return Math.min(HOURS_PER_DAY, Math.max(1, Math.floor(elapsed) || 1))
    })
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

/** How wide one bucket is drawn, in real pixels. */
export function cellWidth(count) {
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
    const start = new Date(bucket.start)
    const when = grain === 'day' ? formatDayLong(start) : formatHourLong(start)
    const state = presenceState(value, ceiling)
    // Said in the tooltip as well as drawn, because the three states are a
    // colour, and a colour is never the only carrier of a finding here.
    const thin = state === 'thin' ? ', thinly' : ''

    if (grain === 'day') {
        const heard = value
            ? `heard in ${value} of ${ceiling} hours${thin}`
            : 'silent all day: nothing heard from this station'
        const counting = bucket.partial
            ? ` — the day is still being counted, ${ceiling}h of it so far`
            : ''

        return `${when} — ${heard}${counting}`
    }

    const heard = value
        ? `${value.toLocaleString()} messages${thin}`
        : 'silent: no messages this hour'

    return `${when} — ${heard}`
}
