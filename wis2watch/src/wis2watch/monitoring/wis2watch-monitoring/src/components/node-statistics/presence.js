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

//: Below this much of what a bucket was expected to carry, a station was
//: heard *thinly* rather than fully.
//:
//: The number survived #112 unchanged; what it is a share *of* did not. It was
//: a share of the clock's 24 hours, and #110 measured that cut at every
//: position on the axis: the least-bad place was half the day, because the
//: distribution of `active_hours/24` is bimodal and 0.5 falls in the trough.
//: The reason it is bimodal is that it was measuring reporting *cadence* --
//: two thirds of every pale cell was a station sitting at its own normal
//: level, and the commonest healthy cadence on the network (three-hourly, 8
//: hours of every day) sat 0.8 hours from the old 0.3 line.
//:
//: Against the station's own expectation the shape is different and much
//: kinder to a threshold: one mode at 0.8-1.1 of its usual, a thin tail below
//: 0.6, and nothing bimodal about it, because "a station against itself" is
//: one population where "a station against the clock" was several. 0.5 sits in
//: sparse ground there too, so the number stays where it was and now means
//: something a reader can act on: **half of what this station normally does**.
//:
//: Measured over the same six clean days: judged this way 4.5% of station-days
//: draw pale against 32.1% before, catching every one of the real drops the
//: clock caught.
export const THIN_FRACTION = 0.5

//: How many hours a whole UTC day holds, and how long an hour is. The daily
//: ceiling and the arithmetic that shortens it, each spelled once.
const HOURS_PER_DAY = 24
const MS_PER_HOUR = 3_600_000

/** A learned figure as a number of hours, for a sentence rather than a sum. */
function hours(value) {
    // Rounded, and never below one: a baseline of 0.4 hours is a station heard
    // in about one hour a day, and "against the 0 it is normally heard in" is
    // arithmetic showing through the words.
    return `${Math.max(1, Math.round(value))}h`
}

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
        //: The word a sentence naming one of these buckets takes before it:
        //: dark *on* a day, dark *at* an hour. In the map rather than in a
        //: ternary at the one call site, because the next surface to name a
        //: bucket in a sentence would write the ternary again.
        preposition: 'on',
        //: The words beside each colour, and the sentence that says what
        //: the pale one is measured against. Per grain rather than one
        //: template with the unit swapped in, because the middle state is not
        //: the same claim at both: a day is judged against the clock, and an
        //: hour against the station's own busiest.
        legend: {
            full: 'Heard about as much as it usually is',
            thin: 'Heard, but for less than half its usual day',
            unjudged: 'Heard, with too little history to say whether that is usual',
        },
        scale:
            "A cell is judged against how much of a day this centre normally"
            + ' hears this station in, not against the clock: a station'
            + ' reporting three-hourly is heard in 8 hours of every day and is'
            + ' perfectly well. So a pale cell is a station heard for less than'
            + ' half of its own usual day.',
        long: formatDayLong,
        short: formatDay,
        //: What the cell is judged against, said out loud. The figure it names
        //: is the station's own, not the clock's, so the sentence has to carry
        //: the comparison -- "4 of 24 hours" was true of the old ceiling and
        //: says nothing about whether 4 is a lot for this station.
        heard: (value, ceiling, partial) =>
            partial
                ? `heard in ${value}h so far, against the ${hours(ceiling)} it is`
                  + ' normally heard in by this point of a day'
                : `heard in ${value}h, against the ${hours(ceiling)} it is`
                  + ' normally heard in',
        //: The sentence for a station nothing has been learned about yet. It
        //: says what was heard -- which is a fact, and known -- and then says
        //: plainly that the comparison is missing, rather than leaving a
        //: reader to work out what the hatch means.
        unjudged: (value, partial) =>
            `heard in ${value}h${partial ? ' so far' : ''} — too little`
            + ' history from this station to say whether that is usual for it',
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
        silent: 'silent all day: nothing heard from this station',
        //: The sentence for a bucket the centre published in and named
        //: nobody in. Here rather than at the two surfaces that say it,
        //: because a tooltip and a legend saying it differently are two
        //: claims about one mark.
        stationLess:
            'this centre published on this day, and none of it named any'
            + ' station at all',
    },
    hour: {
        period: 'hours',
        preposition: 'at',
        bucket: 'the hour',
        legend: {
            full: "Heard at this station's own usual rate",
            thin: "Heard, but well below this station's busiest hour",
            // Never reached at this grain -- an hourly cell is judged against
            // the row's own busiest bucket, which every row has. Present so
            // that one legend component can read either grain without asking
            // which one it is holding.
            unjudged: null,
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
        //: Never reached: an hourly cell is judged against the row's own
        //: busiest bucket, which every row has. Null so that one tooltip
        //: function can read either grain without asking which it holds.
        unjudged: null,
        silent: 'silent: no messages this hour',
        stationLess:
            'this centre published in this hour, and none of it named any'
            + ' station at all',
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
 * What each bucket of *one station's* vector is judged against.
 *
 * The axis's own ceilings where the window has them. Where it has not -- an
 * hourly axis, there being no number of messages an hour is full at -- the
 * row's own busiest bucket, which is the convention the sparkline beside it
 * already uses: station traffic is heavy-tailed, and one dominant reporter
 * scaled across the column would draw every other station as thin.
 *
 * That fallback is spelled here and nowhere else. Two surfaces draw this
 * vector and both need it -- the cells on the row for the figures in their
 * tooltips, the navigator wall for its states -- and a second copy of it is
 * how one surface comes to call a station thin while the other calls it full.
 *
 * @param {number[]} values - what the station was heard doing per bucket.
 * @param {number[]|null} ceilings - the axis's own, or null for the row's.
 * @param {number} count - how many buckets the axis carries.
 * @param {number|null} baseline - how much of a whole bucket this centre
 *     normally hears this station in, or null where too little history exists
 *     to say. Ignored where the axis has no ceilings of its own.
 * @returns {number[]|null} a ceiling per bucket, or null where unjudged.
 */
export function rowCeilings(values, ceilings, count, baseline) {
    if (ceilings) {
        // Unjudged: nothing has been learned about this station yet, so there
        // is no scale to draw it against and none is invented. `null` here is
        // what `presenceStates` reads to mark the row rather than shade it.
        if (baseline == null) {
            return null
        }

        // The axis says how much of each bucket has *happened*; the baseline
        // says how much of a whole one this station is normally heard in. A
        // day in progress is judged against the share of its expectation that
        // has come due, so a station expected eight hours a day is not called
        // thin at 06:00 for having managed two.
        return ceilings.map((ceiling) => baseline * (ceiling / HOURS_PER_DAY))
    }

    // Never zero: a row that was heard nothing from divides by one and draws
    // silent all the way across, which is the truth about it.
    const peak = Math.max(1, ...values)

    return Array.from({length: count}, () => peak)
}

/**
 * One station's whole vector, said as states, against the axis it is drawn on.
 *
 * Two surfaces draw this vector -- the matrix cell strip on the row itself and
 * the navigator wall above the table -- and they are two renderings of one
 * finding rather than two findings. A band on the wall and a band in the
 * matrix are the same band only if the *states* come from here: a wall that
 * re-derived "thin" from `active_hours` would eventually be a wall drawn to a
 * threshold the cells beneath it no longer use.
 *
 * Read against the axis rather than against the vector, because the two can
 * disagree for a moment mid-swap between windows: a vector shorter than the
 * axis carries nothing at the columns it does not reach, which is what a
 * missing bucket means anyway.
 *
 * @param {number[]} values - what the station was heard doing per bucket.
 * @param {number[]|null} ceilings - the axis's own ceilings, or null for the
 *     row's own busiest bucket.
 * @param {number} count - how many buckets the axis carries.
 * @param {number|null} baseline - what this station is normally heard for.
 * @returns {string[]} `silent`, `thin`, `full` or `unjudged`, one per bucket.
 */
export function presenceStates(values, ceilings, count, baseline) {
    const against = rowCeilings(values, ceilings, count, baseline)

    return Array.from({length: count}, (_, at) => {
        const value = values[at] || 0

        // Silence needs no expectation. Whether a station was heard at all is
        // the same fact whatever it normally does, and it is the finding the
        // matrix exists for -- so an unjudged row keeps it and gives up only
        // the distinction between a full day and a thin one.
        if (!value) {
            return 'silent'
        }

        return against ? presenceState(value, against[at]) : 'unjudged'
    })
}

/**
 * The two ends of an axis of cells, in the words its own grain uses.
 *
 * Both surfaces that draw these cells name their ends -- a run of them says
 * *when* something stopped only if the axis is named -- and this is what stops
 * the matrix calling the newest column "today" while the drilldown's strip
 * calls it a date.
 *
 * The word rather than the date on the unfinished bucket, because that is the
 * one a reader looks for first and is what the charts' own axes call it.
 *
 * @param {{start: string, partial: boolean}[]} buckets - the axis.
 * @param {string} grain - `day` or `hour`, the server's own spelling.
 * @returns {{start: string, end: string}} the two ends, named.
 */
export function axisEnds(buckets, grain) {
    const words = grainOf(grain)

    const name = (bucket) => {
        if (!bucket) {
            return ''
        }

        return bucket.partial ? 'today' : words.short(new Date(bucket.start))
    }

    return {start: name(buckets[0]), end: name(buckets[buckets.length - 1])}
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

//: How much of the navigator wall one station gets. Half a pixel is #48's
//: figure and it is the point of the thing rather than a detail: it is what
//: puts a thousand stations in one viewport, and it is far too little to
//: label -- which is why the wall is a navigator and the table is the view.
const WALL_PX_PER_STATION = 0.5

//: The wall's floor and ceiling in height. The floor is for the small centre,
//: where twenty stations at half a pixel would be a ten-pixel smear rather
//: than a picture; the ceiling is what keeps the promise of "no scrolling" at
//: a centre big enough to break it, at the price of squeezing the lines below
//: half a pixel -- which `pixelBand` below is written to survive.
const WALL_MIN_HEIGHT = 44
const WALL_MAX_HEIGHT = 500

/**
 * How tall the navigator wall is drawn, for a list of this many stations.
 *
 * Here beside `cellWidthFor` rather than in the component, for the reason
 * that one is here: how big a mark is drawn is a decision about what can be
 * read, and the tab keeps those together.
 *
 * @param {number} count - how many stations are listed.
 * @returns {number} the wall's height in CSS pixels.
 */
export function wallHeightFor(count) {
    return Math.max(
        WALL_MIN_HEIGHT,
        Math.min(WALL_MAX_HEIGHT, Math.round(count * WALL_PX_PER_STATION))
    )
}

/**
 * Where one of `count` lines falls on a surface `total` pixels across.
 *
 * The navigator wall's arithmetic, and it is here beside `cellWidthFor` for
 * the same reason that one is: how big a mark is drawn is a decision about
 * what can be read, and it belongs with the rest of them rather than inside a
 * paint function.
 *
 * Whole pixels, and never fewer than one. A thousand stations in five hundred
 * pixels is half a pixel each, and a station drawn 0.4 pixels tall is an
 * antialiased smudge that reads as "thin" whatever state it is in -- which
 * would have the wall inventing a fourth colour out of rounding. So the bands
 * overlap where they must, and the caller decides who wins the shared pixel:
 * the wall paints the worse news last, so an outage cannot be covered by a
 * healthy neighbour it happens to be squeezed against.
 *
 * @param {number} index - which line, from zero.
 * @param {number} count - how many there are in all.
 * @param {number} total - how many pixels they share.
 * @returns {number[]} the first pixel of the line, and one past its last.
 */
export function pixelBand(index, count, total) {
    const start = Math.floor((index * total) / count)

    return [start, Math.max(start + 1, Math.floor(((index + 1) * total) / count))]
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
 * @param {boolean} stationLess - whether the centre published in this bucket
 *     and named no station at all in it.
 * @returns {string} the sentence.
 */
export function presenceTitle(bucket, value, ceiling, grain, stationLess = false) {
    const words = grainOf(grain)
    // Said in the tooltip as well as drawn, because the states are a colour,
    // and a colour is never the only carrier of a finding here -- the hatch
    // least of all: "no value on this axis" is the most a mark can say, and
    // which of the cases it is is only said here.
    const thin =
        ceiling != null && presenceState(value, ceiling) === 'thin' ? ', thinly' : ''

    let heard
    if (stationLess) {
        heard = words.stationLess
    } else if (!value) {
        // Silence needs no expectation, and says the same thing on an unjudged
        // row as on any other.
        heard = words.silent
    } else if (ceiling == null) {
        heard = words.unjudged(value, bucket.partial)
    } else {
        heard = `${words.heard(value, ceiling, bucket.partial)}${thin}`
    }

    return `${words.long(new Date(bucket.start))} — ${heard}`
}
