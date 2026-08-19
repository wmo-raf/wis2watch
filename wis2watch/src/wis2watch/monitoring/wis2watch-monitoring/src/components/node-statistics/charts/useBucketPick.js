/**
 * The select gesture of #74, written once for every surface that offers it.
 *
 * Four surfaces carry it -- three charts and the matrix's column heads -- and
 * this is what stops them agreeing about it in four places. What a pick means
 * is not obvious enough to be repeated: a bucket already picked is dropped
 * rather than picked again, Escape says nothing when there is nothing to
 * drop, and a surface whose axis is not the station rows' axis offers no pick
 * at all. Four copies of that is four chances for one of them to be a
 * gesture that quietly behaves differently from the others.
 *
 * It wraps `useBucketHover` rather than sitting beside it, because the pick
 * is the hover layer's own bucket: pointer, keyboard focus and click all
 * resolve through one `bucketAtX`, and a surface that composed the two
 * separately could pick a bucket other than the one it is describing.
 *
 * The surface it is handed is expected to carry `buckets`, `selected` and
 * `selectable`, which is the whole of the vocabulary a pick is spelled in.
 */
import {computed} from 'vue'

import {bucketIndexOf} from '../selection.js'
import {useBucketHover} from './useBucketHover.js'

/**
 * Track which bucket a reader is on, and let them pick it.
 *
 * @param {object} props - the surface's own props: `buckets`, `selected` and
 *     `selectable`.
 * @param {(event: string, start: string) => void} emit - the surface's
 *     emitter, which is sent `select` with a bucket start or an empty string.
 * @param {() => number} bucketCount - how many buckets the axis holds.
 * @param {() => number} plotWidth - the measured width the buckets span.
 * @returns everything `useBucketHover` returns, plus where the pick sits on
 *     this axis and what one option should say about it.
 */
export function useBucketPick(props, emit, bucketCount, plotWidth) {
    //: Where the pick sits on this axis, or -1 for none of it. Gated on
    //: `selectable` rather than merely resolved: the flat-24h chart under a
    //: daily window can hold an hour whose start is spelled exactly like the
    //: picked *day*, and marking it would be this chart claiming a pick it
    //: does not offer.
    const pickedAt = computed(() =>
        props.selectable ? bucketIndexOf(props.buckets, props.selected) : -1
    )

    /**
     * Say which bucket the reader picked, or that they dropped the pick.
     *
     * The bucket's start rather than its index, which is the one thing this
     * surface and the station rows can both be sure means the same bucket.
     * Picking what is already picked drops it, so the gesture that made the
     * filter is also the one that undoes it -- the pointer's way out, beside
     * Escape's. Saying nothing where nothing would change is what keeps
     * Escape on an unfiltered page out of the address bar.
     */
    function pick(at) {
        if (!props.selectable) {
            return
        }

        const start = at === null ? '' : props.buckets[at].start
        const next = start === props.selected ? '' : start

        if (next === props.selected) {
            return
        }

        emit('select', next)
    }

    const hover = useBucketHover(bucketCount, plotWidth, {onSelect: pick})

    /**
     * What one option's `aria-selected` says.
     *
     * The pick where there is one to make, and where the reader is where
     * there is not: on a surface that offers no pick, this list's only notion
     * of a chosen option is the one being walked, and saying nothing there
     * would leave a keyboard reader with an axis on which nothing is ever
     * selected.
     */
    function isPicked(at) {
        return props.selectable ? pickedAt.value === at : hover.index.value === at
    }

    return {...hover, pickedAt, pick, isPicked}
}
