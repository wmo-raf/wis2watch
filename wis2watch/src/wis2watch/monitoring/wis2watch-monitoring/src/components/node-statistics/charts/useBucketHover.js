/**
 * The one hover layer of #51, the keyboard route, and the select gesture of
 * #74, as a single thing.
 *
 * A pointer position, a focused index and a click all resolve to a bucket
 * through `bucketAtX`, and all set the same `index`. Everything a chart shows
 * about "the bucket the reader is on" reads that one ref, so a reader
 * arrowing along the axis and a reader moving a mouse cannot be shown
 * different hours -- and the bucket a click selects is the bucket the reader
 * was already being told about. If they ever disagree, this file is what is
 * wrong.
 *
 * The accessibility story here is the gesture rather than the decoration:
 * the buckets are a list a keyboard can walk, the words the chart puts beside
 * them are the same words the pointer produces, and the selection is made and
 * dismissed on the same keys wherever it is offered. Colour is never the only
 * carrier of anything.
 */
import {ref} from 'vue'

import {bucketAtX} from './plot.js'

/**
 * Track which bucket a reader is on, and let them pick it.
 *
 * @param {() => number} bucketCount - how many buckets the axis holds.
 * @param {() => number} plotWidth - the measured width the buckets span.
 * @param {{onSelect?: (index: number|null) => void}} gesture - what a click,
 *     Enter or Space does with a bucket, and what Escape does with none.
 *     Left out on a surface that offers no selection, which is what keeps the
 *     keys on those surfaces doing nothing rather than doing something
 *     invisible.
 */
export function useBucketHover(bucketCount, plotWidth, {onSelect} = {}) {
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
     * Pick the bucket under the pointer.
     *
     * The position is read again rather than trusted from the last move: a
     * touch or a pen produces a click without ever having hovered, and a
     * gesture that only works after a mouse has been over the chart is a
     * gesture half the readers do not have.
     */
    function onClick(event, padLeft = 0) {
        onPointerMove(event, padLeft)

        if (index.value !== null) {
            onSelect?.(index.value)
        }
    }

    /**
     * Walk the axis by key, and pick from it.
     *
     * The same keys on every chart on the tab, which is the reason this lives
     * beside the pointer route rather than in each of them: two charts on one
     * page whose arrow keys step differently are two charts a reader has to
     * learn separately. Enter and Space select, because that is what they do
     * in every list; Escape leaves the axis *and* drops the selection, so the
     * way out of a filtered page is the key a reader already tries.
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
        } else if (event.key === 'Enter' || event.key === ' ') {
            if (index.value === null) {
                return
            }

            onSelect?.(index.value)
        } else if (event.key === 'Escape') {
            clear()
            onSelect?.(null)
        } else {
            return
        }

        event.preventDefault()
    }

    return {
        index,
        focused,
        onPointerMove,
        onClick,
        clear,
        moveTo,
        step,
        onFocus,
        onBlur,
        onKeydown,
    }
}
