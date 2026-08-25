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

    it('splits the rest at half of what was expected', () => {
        expect(presenceState(11, DAY)).toBe('thin')
        expect(presenceState(12, DAY)).toBe('full')
    })

    it('judges a value against whatever ceiling it is handed', () => {
        // Six hours into a day, a station heard in four of them is doing well,
        // not badly -- the false alarm the elapsed ceiling exists to prevent.
        expect(presenceState(4, 6)).toBe('full')
        expect(presenceState(4, DAY)).toBe('thin')
    })
})

/*
 * The measurement behind all of this, pinned because it is the thing that
 * would silently undo it. #110 found the commonest healthy cadence on the
 * network -- a three-hourly synoptic station, heard in 8 hours of every day --
 * sitting 0.8 hours from the old 0.3 cut against the clock, crossing into
 * `thin` by missing one slot of eight. #112 found the cause: the clock was the
 * wrong yardstick, and two thirds of every pale cell was a station at its own
 * normal level. Judged against itself, this station is simply full.
 */
describe('the three-hourly synoptic station', () => {
    const EVERY_DAY = [8, 8, 8, 8]
    const WHOLE_DAYS = [DAY, DAY, DAY, DAY]

    it('is full on its own eight hours, not thin on the clock\'s twenty-four', () => {
        expect(presenceStates(EVERY_DAY, WHOLE_DAYS, 4, 8))
            .toEqual(['full', 'full', 'full', 'full'])
    })

    it('is still full on the day it misses one of its eight slots', () => {
        expect(presenceStates([8, 8, 7, 8], WHOLE_DAYS, 4, 8))
            .toEqual(['full', 'full', 'full', 'full'])
    })

    it('is thin only when it loses more than half of what it does', () => {
        expect(presenceStates([8, 8, 3, 8], WHOLE_DAYS, 4, 8))
            .toEqual(['full', 'full', 'thin', 'full'])
    })

    it('would have been pale every single day against the clock', () => {
        expect(presenceStates(EVERY_DAY, WHOLE_DAYS, 4, DAY))
            .toEqual(['thin', 'thin', 'thin', 'thin'])
    })
})

describe('a station nothing has been learned about', () => {
    it('is unjudged rather than guessed at', () => {
        expect(presenceStates([8, 4], [DAY, DAY], 2, null))
            .toEqual(['unjudged', 'unjudged'])
    })

    it('still says plainly when it was not heard at all', () => {
        // Silence needs no expectation, and it is the finding the matrix
        // exists for -- an unjudged row gives up the thin/full distinction
        // and nothing else.
        expect(presenceStates([8, 0], [DAY, DAY], 2, null))
            .toEqual(['unjudged', 'silent'])
    })

    it('has no ceilings to be judged against', () => {
        expect(rowCeilings([8], [DAY], 1, null)).toBeNull()
    })
})

describe('the day in progress', () => {
    it('is judged against the share of its expectation that has come due', () => {
        // Halfway through a day, a station expected 8 hours has had 4 of them
        // due. Three is not thin; one is.
        const half = [DAY / 2]

        expect(presenceStates([3], half, 1, 8)).toEqual(['full'])
        expect(presenceStates([1], half, 1, 8)).toEqual(['thin'])
    })
})

describe('what a whole row is judged against', () => {
    it("scales the axis ceilings to the station's own expectation", () => {
        expect(rowCeilings([1, 2], [DAY, DAY], 2, 12)).toEqual([12, 12])
    })

    it("falls back to the row's own busiest bucket where it has none", () => {
        expect(rowCeilings([2, 8, 4], null, 3)).toEqual([8, 8, 8])
    })

    it('never divides by zero on a row that was heard nothing from', () => {
        expect(rowCeilings([0, 0], null, 2)).toEqual([1, 1])
        expect(presenceStates([0, 0], null, 2)).toEqual(['silent', 'silent'])
    })

    it('reads short vectors as missing rather than throwing', () => {
        expect(presenceStates([24], [DAY, DAY], 2, DAY)).toEqual(['full', 'silent'])
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
    it('is half of what was expected, and the legend says so', () => {
        expect(THIN_FRACTION).toBe(0.5)
        expect(grainOf('day').legend.thin).toContain('less than half its usual')
    })

    it('names the station rather than the clock in its scale sentence', () => {
        expect(grainOf('day').scale).toContain('normally hears this station')
        expect(grainOf('day').scale).not.toContain('24 hours')
    })
})
