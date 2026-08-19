/**
 * The arithmetic every panel on the statistics tab is drawn with.
 *
 * The rule this file is under: ARITHMETIC AND TOKENS ONLY. Nothing here
 * returns SVG markup and nothing takes an options object describing a chart.
 * The moment either happens this has become `<BarChart>` with extra steps,
 * which is the thing #51 decided not to build after measuring what a charting
 * library costs and finding that the two gestures the tab actually needs --
 * the hatched stub and bucket-select -- sit outside what a library draws.
 *
 * The function worth watching is `bucketAtX`. The hover layer resolves a
 * pointer through it, the keyboard route resolves a focused index through it,
 * and bucket-select will resolve a click through it. One arithmetic, written
 * once, is what stops a reader's pointer and a reader's arrow key landing on
 * different hours.
 *
 * Bucket starts arrive from the server and are read in UTC throughout. A
 * local-time formatter here would put the synoptic hours on the wrong labels
 * for everyone outside UTC, and no test would notice in a UTC test database.
 */
import {onBeforeUnmount, onMounted, ref} from 'vue'

//: The room every panel on the tab leaves for its axis furniture: the y
//: labels down the left and the bucket labels underneath, in real pixels
//: rather than scaled, so a 10px label stays 10px whatever Wagtail's layout
//: does around it. Shared rather than agreed by four copies, because four
//: panels stacked down one page have to start their plots at one x or they
//: cannot be read against each other -- which is the whole reason the daily
//: series is drawn on the hourly chart's axis.
export const PAD_LEFT = 30
export const PAD_BOTTOM = 14

//: How tall the station-less mark stands, wherever the tab draws it. Enough
//: to be unmistakable against a bucket that drew nothing, low enough that
//: nobody reads it off the y axis -- and one number, because the same mark at
//: two sizes is two marks to a reader.
export const STUB_HEIGHT = 7

/** A count in the reader's own locale, spelled in full. */
export function formatCount(value) {
    return value.toLocaleString()
}

/**
 * Value to pixel, y measured downwards, always anchored at zero.
 *
 * A chart whose baseline is not zero exaggerates every difference on it, and
 * this tab's charts are read for how far a bar is from full coverage.
 */
export function yScale(max, plotHeight) {
    const safeMax = max > 0 ? max : 1

    return (value) => plotHeight - (value / safeMax) * plotHeight
}

/** Bucket index to a pixel band: where it starts, its middle, and its width. */
export function bandScale(count, plotWidth) {
    const step = plotWidth / Math.max(1, count)

    return {
        x: (index) => index * step,
        centre: (index) => (index + 0.5) * step,
        // Never below a hairline. At 90 buckets in a narrow panel a gap of
        // 0.6px is what turns a bar chart into a grey slab.
        barWidth: Math.max(1, step - (step > 6 ? 2 : step > 3 ? 1 : 0.4)),
    }
}

//: The label steps an hourly axis is allowed to use, in hours. Every one of
//: them divides 24, so whichever fits, the synoptic hours are among the ones
//: labelled -- a reader looking for 06Z finds a tick there rather than at 05Z.
const HOUR_LABEL_STEPS = [1, 2, 3, 6, 12]

/**
 * Which buckets of an axis of hours get a label, anchored to round UTC hours.
 *
 * Two things at once, and both are needed. How many labels fit is measured in
 * real pixels -- which is what the ResizeObserver buys, since a ladder keyed
 * off the bucket count cannot know how wide it is. Where they land is then
 * chosen by the clock rather than by counting buckets: stepping from wherever
 * the window happens to start, which is any hour at all, puts the labels at
 * 05Z, 08Z, 11Z, and the hours a reader of WMO traffic is looking for are
 * 00/06/12/18Z.
 *
 * Takes the hours themselves rather than the bucket starts, so that the
 * hour-of-day profile -- whose buckets are the clock rather than a moment in
 * it -- is labelled by the same arithmetic as the hourly chart. Two charts on
 * one page labelling 06Z at two different spacings are two axes to read.
 *
 * @param {number[]} hours - the UTC hour each bucket falls on, in order.
 * @param {number} plotWidth - the measured width of the plot, in pixels.
 * @param {number} minLabelPx - how much room one label needs.
 * @returns {number[]} the indices to label.
 */
export function clockTicks(hours, plotWidth, minLabelPx = 40) {
    if (!hours.length) {
        return []
    }

    const perBucket = plotWidth / hours.length
    const step =
        HOUR_LABEL_STEPS.find((every) => every * perBucket >= minLabelPx) ??
        HOUR_LABEL_STEPS[HOUR_LABEL_STEPS.length - 1]

    return hours.reduce((indices, hour, index) => {
        if (hour % step === 0) {
            indices.push(index)
        }

        return indices
    }, [])
}

//: The steps an axis top is allowed to be rounded up to, per decade. A top is
//: a number a reader can find a middle of by eye, rather than whatever the
//: tallest bar happened to be -- but the ladder has to be fine enough that a
//: peak fills its panel: [1, 2, 5] alone tops a 23,400 peak out at 50,000 and
//: draws the busiest hour of the window at less than half the plot.
const NICE_STEPS = [1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10]

/**
 * An axis top a reader can read a middle off, at or above the tallest value.
 *
 * Only for the axes whose top is not a population. Coverage charts top out at
 * the declared station count and must never be scaled to their own data --
 * the height of a bar there *is* the finding. A ratio and a message volume
 * have no such ceiling, so the alternative to rounding up is an axis topped
 * by an arbitrary number like 8,432.
 *
 * @param {number} max - the tallest value on the axis.
 * @returns {number} the top to draw to, never below 1.
 */
export function niceTop(max) {
    if (!(max > 0)) {
        return 1
    }

    const decade = 10 ** Math.floor(Math.log10(max))
    const step = NICE_STEPS.find((multiple) => max <= multiple * decade) ?? 10

    return step * decade
}

/**
 * A number short enough to sit beside an axis, without lying about its size.
 *
 * Message volumes over ninety days run to seven figures, and a y label of
 * "1,240,000" is wider than the room the left pad leaves for it. Only the two
 * steps above ten thousand are shortened: below that a count is short enough
 * to be worth stating exactly, and rounding it would lose the figure a reader
 * came for.
 *
 * @param {number} value - the number to label.
 * @returns {string} the label.
 */
export function compactCount(value) {
    if (value >= 1_000_000) {
        return `${Math.round(value / 100_000) / 10}M`
    }

    if (value >= 10_000) {
        return `${Math.round(value / 1_000)}k`
    }

    return value.toLocaleString()
}

/**
 * Which buckets of a daily axis get a label, counted back from the newest.
 *
 * Anchored at the newest bucket rather than the oldest, because that one is
 * today and is the label a reader looks for first -- and because a window is
 * a rolling one, so the oldest bucket is an arbitrary date and the newest is
 * never one.
 *
 * No clock arithmetic, unlike the hourly axis: there is nothing about a day
 * of the month a reader is hunting for the way they hunt for 06Z, so an even
 * spacing that always includes today is the whole of what is wanted.
 *
 * @param {number} count - how many buckets the axis holds.
 * @param {number} plotWidth - the measured width of the plot, in pixels.
 * @param {number} minLabelPx - how much room one label needs.
 * @returns {number[]} the indices to label, oldest first.
 */
export function spacedTicks(count, plotWidth, minLabelPx = 44) {
    if (count <= 0) {
        return []
    }

    const room = Math.max(1, Math.floor(plotWidth / minLabelPx))
    const step = Math.max(1, Math.ceil(count / room))

    const indices = []
    for (let index = count - 1; index >= 0; index -= step) {
        indices.unshift(index)
    }

    return indices
}

//: Half of the widest label a bucket axis puts under a tick. Enough for a
//: date or the word "today", which are what the daily axis carries.
const LABEL_HALF_PX = 18

/**
 * Where a tick label sits and which way it runs, kept inside the plot.
 *
 * The newest bucket of a rolling window is always worth labelling and always
 * sits against the right edge, so a centred label there is half outside the
 * plot -- which is how "today" comes out reading "toda". Turning the end
 * labels inwards costs nothing and is the only alternative to dropping them.
 *
 * @param {number} centre - the bucket's centre, in plot coordinates.
 * @param {number} padLeft - where the plot starts.
 * @param {number} width - the full measured width of the chart.
 * @returns {{x: number, anchor: string}} the label's position and anchor.
 */
export function tickPlacement(centre, padLeft, width) {
    if (centre + LABEL_HALF_PX > width) {
        return {x: width, anchor: 'end'}
    }

    if (centre - LABEL_HALF_PX < padLeft) {
        return {x: padLeft, anchor: 'start'}
    }

    return {x: centre, anchor: 'middle'}
}

/** Pixel to bucket index, or null outside the plot. Hover, focus and select share it. */
export function bucketAtX(px, count, plotWidth) {
    if (plotWidth <= 0 || count <= 0) {
        return null
    }

    const index = Math.floor((px / plotWidth) * count)

    return index < 0 || index >= count ? null : index
}

/** An axis label for one bucket: the UTC hour, on a chart that is all UTC. */
export function formatHour(start) {
    return `${String(start.getUTCHours()).padStart(2, '0')}Z`
}

/** One bucket named in full, for a readout that has room to say the day too. */
export function formatHourLong(start) {
    const day = start.toLocaleDateString('en-GB', {
        day: 'numeric',
        month: 'short',
        timeZone: 'UTC',
    })

    return `${day}, ${formatHour(start)}`
}

/** An axis label for one UTC day, on a chart that is all UTC. */
export function formatDay(start) {
    return start.toLocaleDateString('en-GB', {
        day: 'numeric',
        month: 'short',
        timeZone: 'UTC',
    })
}

/** One day named in full, for a readout that has room to say the weekday too. */
export function formatDayLong(start) {
    return start.toLocaleDateString('en-GB', {
        weekday: 'short',
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        timeZone: 'UTC',
    })
}

/**
 * One instant named in full, in UTC, for a table cell rather than an axis.
 *
 * The year is in it, unlike every axis label on the tab: a station that
 * stopped is the row a reader is hunting for, and "14 Aug" on a station last
 * heard from in 2024 is the one formatting mistake that would matter here.
 *
 * @param {string} iso - the instant, as the server sent it.
 * @returns {string} the instant in UTC, or an em dash where there is none.
 */
export function formatInstant(iso) {
    if (!iso) {
        return '\u2014'
    }

    const named = new Date(iso).toLocaleString('en-GB', {
        day: 'numeric',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        timeZone: 'UTC',
    })

    return `${named}Z`
}

/**
 * How long something has been quiet, in the largest unit that is still exact
 * enough to act on.
 *
 * Hours up to two days, because "quiet 31 hours" is the sentence that decides
 * whether a station has crossed the staleness threshold. Days after that,
 * because nobody reads 2,184 hours as three months.
 *
 * @param {number|null} hours - how long, or null where nothing was ever heard.
 * @returns {string} the span, or "never" where there is nothing to measure.
 */
export function formatQuiet(hours) {
    if (hours === null || hours === undefined) {
        return 'never heard'
    }

    if (hours < 48) {
        return `${Math.round(hours)}h`
    }

    return `${Math.round(hours / 24)}d`
}

/**
 * The measured width of an element, live, in real pixels.
 *
 * Panelled charts measure rather than scale a viewBox, so that a 10px label
 * stays 10px whatever Wagtail's layout does around it. Sparklines will do the
 * opposite for the opposite reason: inside a table cell, scaling is the point.
 */
export function useMeasuredWidth(fallback = 480) {
    const el = ref(null)
    const width = ref(fallback)
    let observer = null

    onMounted(() => {
        if (!el.value) {
            return
        }

        observer = new ResizeObserver((entries) => {
            const measured = entries[0]?.contentRect?.width

            if (measured) {
                width.value = Math.max(120, Math.round(measured))
            }
        })

        observer.observe(el.value)
        width.value = Math.max(120, Math.round(el.value.clientWidth || fallback))
    })

    onBeforeUnmount(() => observer?.disconnect())

    return {el, width}
}
