/**
 * Only the rows a reader can see, out of however many there are.
 *
 * All rows, always, is the rule the station table is built on -- and at a
 * legible row height a thousand stations is a sixteen-thousand-pixel surface
 * carrying a hundred thousand matrix cells. Both statements are kept true by
 * drawing only the fifty rows the panel has room for and holding the rest of
 * the height open with a pair of spacers. Nothing is dropped: a row that is
 * not drawn is a row that is scrolled past, and the scrollbar is the size of
 * the whole population.
 *
 * The row height is a **constant, not a measurement**. That is the whole of
 * why this is simple: with a fixed height, which rows are in view is
 * arithmetic on the scroll offset, and there is no measuring pass, no
 * observer per row and no reflow when a name is longer than its column.
 *
 * The header is measured, though, and that is not a detail. The rows start
 * below a sticky header inside the same scrolling box, so the first pixel of
 * the first row is at the header's height rather than at zero -- and a
 * virtual list that forgets it draws blank space where the top rows should
 * be, off by however tall the header happens to be that day.
 */
import {computed, onBeforeUnmount, onMounted, ref} from 'vue'

/**
 * The window of rows to draw, and the padding that stands in for the rest.
 *
 * @param {import('vue').Ref<number>} count - how many rows there are in all.
 * @param {number} rowHeight - how tall every one of them is, in pixels.
 * @param {number} overscan - how many rows to draw beyond each edge.
 * @returns {{viewport: object, header: object, first: object, end: object,
 *     onScroll: function, reset: function, scrollToRow: function,
 *     topPad: object, bottomPad: object}} the element refs to bind, the slice
 *     to draw, the two spacers, and a way to bring one row into view.
 */
export function useVirtualRows(count, rowHeight, overscan = 8) {
    //: The scrolling box, and the header inside it whose height the rows
    //: start below.
    const viewport = ref(null)
    const header = ref(null)

    const scrollTop = ref(0)
    const viewportHeight = ref(400)
    const headerHeight = ref(0)

    let observer = null
    let queued = false

    /**
     * Follow the scroll, at most once a frame.
     *
     * A scroll event can arrive several times per frame, and each one that
     * changes the slice re-renders every drawn row and its matrix cells with
     * it. Coalescing to one read per frame is the difference between a table
     * that scrolls and one that stutters at a thousand rows.
     */
    function onScroll() {
        if (queued) {
            return
        }

        queued = true
        requestAnimationFrame(() => {
            queued = false
            scrollTop.value = viewport.value?.scrollTop || 0
        })
    }

    /**
     * Bring one row into view, and move no further than that.
     *
     * The map beside this table can pick a station that is four hundred rows
     * down, and a highlight nobody can see is not a highlight. Nothing moves
     * where the row is already on screen: a reader who picked a dot next to
     * the row they were reading should not have the list jump under them.
     *
     * The sticky header covers the top of the scrolling box, so the row has
     * to clear it as well as the box's own edge -- a row scrolled to the very
     * top of the viewport is a row underneath the column headings.
     *
     * @param {number} index - which row, in the order they are drawn.
     */
    function scrollToRow(index) {
        const box = viewport.value

        if (!box || index < 0) {
            return
        }

        //: The furthest down the box can be scrolled with this row still
        //: clear of the header, and the least it can be scrolled with the row
        //: still above the bottom edge.
        const highest = index * rowHeight
        const lowest = headerHeight.value + (index + 1) * rowHeight - box.clientHeight

        if (box.scrollTop > highest) {
            box.scrollTop = highest
        } else if (box.scrollTop < lowest) {
            box.scrollTop = lowest
        }

        scrollTop.value = box.scrollTop
    }

    /** Back to the top, for when the rows underneath have changed. */
    function reset() {
        scrollTop.value = 0

        if (viewport.value) {
            viewport.value.scrollTop = 0
        }
    }

    function measure() {
        viewportHeight.value = viewport.value?.clientHeight || viewportHeight.value
        headerHeight.value = header.value?.offsetHeight || 0
    }

    onMounted(() => {
        measure()

        // The panel is inside Wagtail's own layout and the header wraps at a
        // narrow width, so neither height is a constant. Both are watched
        // rather than read once.
        observer = new ResizeObserver(measure)

        if (viewport.value) {
            observer.observe(viewport.value)
        }

        if (header.value) {
            observer.observe(header.value)
        }
    })

    onBeforeUnmount(() => observer?.disconnect())

    const first = computed(() => {
        const above = (scrollTop.value - headerHeight.value) / rowHeight

        return Math.max(0, Math.min(count.value, Math.floor(above) - overscan))
    })

    //: One past the last row drawn, so that it slices.
    const end = computed(() => {
        const fits = Math.ceil(viewportHeight.value / rowHeight) + overscan * 2

        return Math.min(count.value, first.value + fits)
    })

    return {
        viewport,
        header,
        first,
        end,
        onScroll,
        reset,
        scrollToRow,
        //: The height of everything above the drawn rows, and below them.
        //: Held open by a spacer each, so the scrollbar measures the whole
        //: population rather than the handful of rows on screen.
        topPad: computed(() => first.value * rowHeight),
        bottomPad: computed(() => Math.max(0, count.value - end.value) * rowHeight),
    }
}
