/**
 * The one hover layer of #51, and the keyboard route, as a single thing.
 *
 * A pointer position and a focused index both resolve to a bucket through
 * `bucketAtX`, and both set the same `index`. Everything a chart shows about
 * "the bucket the reader is on" reads that one ref, so a reader arrowing
 * along the axis and a reader moving a mouse cannot be shown different hours.
 * If they ever are, this file is what is wrong.
 *
 * The accessibility story here is the gesture rather than the decoration:
 * the buckets are a list a keyboard can walk, and the words the chart puts
 * beside them are the same words the pointer produces. Colour is never the
 * only carrier of anything.
 */
import {ref} from 'vue'

import {bucketAtX} from './plot.js'

/**
 * Track which bucket a reader is on.
 *
 * @param {() => number} bucketCount - how many buckets the axis holds.
 * @param {() => number} plotWidth - the measured width the buckets span.
 */
export function useBucketHover(bucketCount, plotWidth) {
    const index = ref(null)
    const focused = ref(false)

    /** Follow the pointer, in coordinates relative to the plot's left edge. */
    function onPointerMove(event, padLeft = 0) {
        const bounds = event.currentTarget.getBoundingClientRect()

        index.value = bucketAtX(
            event.clientX - bounds.left - padLeft,
            bucketCount(),
            plotWidth()
        )
    }

    function clear() {
        index.value = null
    }

    /** Move to a bucket by index, clamped to the axis, or leave it entirely. */
    function moveTo(wanted) {
        if (wanted === null) {
            index.value = null

            return
        }

        index.value = Math.max(0, Math.min(bucketCount() - 1, wanted))
    }

    /** Step along the axis, entering it at the newest bucket, which is the one
     * a reader arriving at this tab has come for. */
    function step(by) {
        moveTo(index.value === null ? bucketCount() - 1 : index.value + by)
    }

    /** Enter the axis, landing on the newest bucket if nothing is chosen. */
    function onFocus() {
        focused.value = true

        if (index.value === null) {
            moveTo(bucketCount() - 1)
        }
    }

    function onBlur() {
        focused.value = false
        clear()
    }

    /**
     * Walk the axis by key.
     *
     * The same keys on every chart on the tab, which is the reason this lives
     * beside the pointer route rather than in each of them: two charts on one
     * page whose arrow keys step differently are two charts a reader has to
     * learn separately.
     */
    function onKeydown(event) {
        if (event.key === 'ArrowRight') {
            step(1)
        } else if (event.key === 'ArrowLeft') {
            step(-1)
        } else if (event.key === 'Home') {
            moveTo(0)
        } else if (event.key === 'End') {
            moveTo(bucketCount() - 1)
        } else if (event.key === 'Escape') {
            clear()
        } else {
            return
        }

        event.preventDefault()
    }

    return {index, focused, onPointerMove, clear, moveTo, step, onFocus, onBlur, onKeydown}
}
