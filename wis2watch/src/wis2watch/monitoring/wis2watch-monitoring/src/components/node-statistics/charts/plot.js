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
        step,
        x: (index) => index * step,
        centre: (index) => (index + 0.5) * step,
        // Never below a hairline. At 90 buckets in a narrow panel a gap of
        // 0.6px is what turns a bar chart into a grey slab.
        barWidth: Math.max(1, step - (step > 6 ? 2 : step > 3 ? 1 : 0.4)),
    }
}

/**
 * Which buckets get a label, chosen from measured pixels.
 *
 * This is what the ResizeObserver buys. A ladder keyed off the bucket count
 * cannot know how wide it is, so it either crowds a narrow panel or leaves a
 * wide one nearly unlabelled.
 */
export function tickIndices(count, plotWidth, minLabelPx = 46) {
    const perLabel = Math.max(
        1,
        Math.ceil(minLabelPx / (plotWidth / Math.max(1, count)))
    )

    const indices = []
    for (let index = 0; index < count; index += perLabel) {
        indices.push(index)
    }

    return indices
}

//: The label steps an hourly axis is allowed to use, in hours. Every one of
//: them divides 24, so whichever fits, the synoptic hours are among the ones
//: labelled -- a reader looking for 06Z finds a tick there rather than at 05Z.
const HOUR_LABEL_STEPS = [1, 2, 3, 6, 12]

/**
 * Which buckets of an hourly axis get a label, anchored to round UTC hours.
 *
 * The plain pixel ladder above would step from wherever the window happens to
 * start, which is any hour at all, and put the labels at 05Z, 08Z, 11Z. The
 * hours a reader of WMO traffic is looking for are 00/06/12/18Z, so the step
 * is chosen to fit the measured width and then the labels are placed on the
 * hours that step lands on rather than on the buckets it counts to.
 *
 * @param {Date[]} starts - the start of each bucket, oldest first.
 * @param {number} plotWidth - the measured width of the plot, in pixels.
 * @param {number} minLabelPx - how much room one label needs.
 * @returns {number[]} the indices to label.
 */
export function roundHourTicks(starts, plotWidth, minLabelPx = 40) {
    if (!starts.length) {
        return []
    }

    const perBucket = plotWidth / starts.length
    const step =
        HOUR_LABEL_STEPS.find((hours) => hours * perBucket >= minLabelPx) ??
        HOUR_LABEL_STEPS[HOUR_LABEL_STEPS.length - 1]

    return starts.reduce((indices, start, index) => {
        if (start.getUTCHours() % step === 0) {
            indices.push(index)
        }

        return indices
    }, [])
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
