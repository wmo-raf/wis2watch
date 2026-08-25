import {describe, expect, it} from 'vitest'

import {
    THIN_FRACTION,
    grainOf,
    presenceState,
    presenceStates,
    rowCeilings,
} from './presence.js'

const DAY = 24

describe('what a matrix cell says it saw', () => {
    it('calls a bucket it heard nothing in silent', () => {
        expect(presenceState(0, DAY)).toBe('silent')
    })

    it('splits the rest at half the bucket', () => {
        expect(presenceState(11, DAY)).toBe('thin')
        expect(presenceState(12, DAY)).toBe('full')
    })

    /*
     * The measurement behind the threshold, pinned as a test because it is the
     * one thing that would silently undo it. #110 read six clean days of real
     * traffic and found the commonest healthy cadence on the network -- a
     * three-hourly synoptic station reporting all eight of its slots -- sitting
     * 0.8 hours above the old 0.3 cut, so it crossed into `thin` by missing a
     * single slot. It must now be nowhere near the line.
     */
    it('keeps a three-hourly synoptic station clear of the line', () => {
        expect(presenceState(8, DAY)).toBe('thin')
        expect(presenceState(7, DAY)).toBe('thin')
    })

    it('judges a partial day against the hours it has actually had', () => {
        // Six hours into a day, a station heard in four of them is doing well,
        // not badly -- the false alarm the elapsed ceiling exists to prevent.
        expect(presenceState(4, 6)).toBe('full')
        expect(presenceState(4, DAY)).toBe('thin')
    })
})

describe('what a whole row is judged against', () => {
    it('takes the axis ceilings where the grain has them', () => {
        expect(rowCeilings([1, 2], [24, 24], 2)).toEqual([24, 24])
    })

    it("falls back to the row's own busiest bucket where it has none", () => {
        expect(rowCeilings([2, 8, 4], null, 3)).toEqual([8, 8, 8])
    })

    it('never divides by zero on a row that was heard nothing from', () => {
        expect(rowCeilings([0, 0], null, 2)).toEqual([1, 1])
        expect(presenceStates([0, 0], null, 2)).toEqual(['silent', 'silent'])
    })

    it('reads short vectors as missing rather than throwing', () => {
        expect(presenceStates([24], [24, 24], 2)).toEqual(['full', 'silent'])
    })
})

describe('the day ceiling', () => {
    const day = grainOf('day')
    const start = '2026-08-20T00:00:00Z'

    it('is the whole clock for a day that has finished', () => {
        expect(day.ceiling({start, partial: false}, Date.parse(start))).toBe(24)
    })

    it('is the hours elapsed for the day in progress', () => {
        const now = Date.parse('2026-08-20T09:30:00Z')

        expect(day.ceiling({start, partial: true}, now)).toBe(9)
    })

    it('never falls below one hour just after midnight', () => {
        const now = Date.parse('2026-08-20T00:10:00Z')

        expect(day.ceiling({start, partial: true}, now)).toBe(1)
    })
})

describe('the threshold itself', () => {
    it('is half the bucket, and the legend says so', () => {
        expect(THIN_FRACTION).toBe(0.5)
        expect(grainOf('day').legend.thin).toContain('less than half')
    })
})
