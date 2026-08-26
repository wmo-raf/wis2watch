import {describe, expect, it} from 'vitest'

import {
    COLUMN_BY_KEY,
    labelsFrom,
    matches,
    narrowing,
    nextSort,
    population,
    ranksFrom,
    sortRows,
} from './rows.js'

//: The payload's vocabularies, shortened to what these tests read. Worst
//: first, as the server declares them.
const VOCABULARIES = {
    standing: [
        {key: 'never_seen', label: 'Never heard from'},
        {key: 'stale', label: 'Gone quiet'},
        {key: 'silent', label: 'Datasets overdue'},
        {key: 'not_cached', label: 'Not reaching the caches'},
        {key: 'no_broker', label: 'Not watched'},
        {key: 'archive_only', label: 'Archive only'},
        {key: 'healthy', label: 'Healthy'},
    ],
    silence: [
        {key: 'silent', label: 'Silent'},
        {key: 'on_schedule', label: 'On schedule'},
        {key: 'unknown', label: 'Not judged'},
    ],
}

const RANKS = ranksFrom(VOCABULARIES)

function centre(overrides) {
    return {
        node_id: 1,
        centre_id: 'zz-test',
        country_name: 'Testland',
        standing: 'healthy',
        last_seen_at: '2026-08-26T10:00:00Z',
        hours_quiet: 1,
        messages_in_window: 10,
        sparkline: [],
        origin_watch: 'watched_at_broker',
        cache_pickup: 'picked_up',
        silence: 'on_schedule',
        ...overrides,
    }
}

const NEVER = centre({
    centre_id: 'bj-meteobenin',
    standing: 'never_seen',
    last_seen_at: null,
    hours_quiet: null,
    messages_in_window: 0,
})
const QUIET = centre({centre_id: 'bi-igebu', standing: 'stale', hours_quiet: 163})
const WELL = centre({centre_id: 'cg-met', standing: 'healthy', hours_quiet: 1})

/*
 * The pair of infinities, and the reason this file exists.
 *
 * A centre nothing has ever been heard from is the most concerning row in the
 * region. It has no timestamp to sort by and no elapsed hours to measure, so
 * both columns have to place it by hand -- at opposite ends, because they read
 * the same fact from opposite directions. Get one of them backwards and the
 * worst centre in the region sorts quietly to the bottom of a panel built to
 * put it at the top, and nothing anywhere throws.
 */
describe('a centre nothing has ever been heard from', () => {
    const LAST_SEEN = COLUMN_BY_KEY.last_seen_at.value
    const QUIET_FOR = COLUMN_BY_KEY.hours_quiet.value

    it('sorts before every timestamp on Last seen', () => {
        expect(LAST_SEEN(NEVER)).toBe(-Infinity)
        expect(LAST_SEEN(NEVER)).toBeLessThan(LAST_SEEN(WELL))
    })

    it('sorts after every span on Quiet, having been quiet for ever', () => {
        expect(QUIET_FOR(NEVER)).toBe(Infinity)
        expect(QUIET_FOR(NEVER)).toBeGreaterThan(QUIET_FOR(QUIET))
    })

    it('is first ascending by Last seen and first descending by Quiet', () => {
        const rows = [WELL, QUIET, NEVER]

        expect(
            sortRows(rows, {sort: 'last_seen_at', direction: 'asc', ranks: RANKS})[0]
        ).toBe(NEVER)
        expect(
            sortRows(rows, {sort: 'hours_quiet', direction: 'desc', ranks: RANKS})[0]
        ).toBe(NEVER)
    })
})

describe('sorting a badge column', () => {
    it('orders by the vocabulary rank, not by the label', () => {
        // Alphabetically "Gone quiet" precedes "Never heard from", which is
        // the order this column must not have.
        const sorted = sortRows([WELL, QUIET, NEVER], {
            sort: 'standing',
            direction: 'asc',
            ranks: RANKS,
        })

        expect(sorted.map((row) => row.standing)).toEqual([
            'never_seen',
            'stale',
            'healthy',
        ])
    })

    it('puts a standing the vocabulary has never heard of last', () => {
        const newer = centre({centre_id: 'aa-new', standing: 'invented_upstream'})
        const sorted = sortRows([newer, NEVER], {
            sort: 'standing',
            direction: 'asc',
            ranks: RANKS,
        })

        expect(sorted[0]).toBe(NEVER)
    })
})

describe('the order the server sent', () => {
    it('is returned untouched when no column is sorted', () => {
        const rows = [WELL, NEVER, QUIET]

        expect(sortRows(rows, {sort: '', direction: 'asc', ranks: RANKS})).toBe(rows)
    })

    it('is returned untouched for a column that cannot be sorted', () => {
        const rows = [WELL, NEVER, QUIET]

        expect(sortRows(rows, {sort: 'sparkline', direction: 'asc', ranks: RANKS}))
            .toBe(rows)
    })

    it('survives underneath a column where every value is equal', () => {
        const first = centre({centre_id: 'aa-one', messages_in_window: 0})
        const second = centre({centre_id: 'bb-two', messages_in_window: 0})

        expect(
            sortRows([second, first], {
                sort: 'messages_in_window',
                direction: 'asc',
                ranks: RANKS,
            })
        ).toEqual([second, first])
    })
})

describe('what the controls hide', () => {
    it('keeps everything when nothing is set', () => {
        expect(matches(WELL, {})).toBe(true)
        expect(narrowing({})).toBe(false)
    })

    it('matches a centre ID however it was typed', () => {
        expect(matches(QUIET, {search: 'IGEBU'})).toBe(true)
        // Trimmed at both ends, so a trailing space from a paste still finds
        // the row it was pasted to find.
        expect(matches(QUIET, {search: '  bi-ige  '})).toBe(true)
        // A substring and not a fuzzy match: two fragments that both appear
        // somewhere is not a hit, or a search would find most of the region.
        expect(matches(QUIET, {search: 'bi ige'})).toBe(false)
    })

    it('matches the country, which is the other thing on the row', () => {
        expect(matches(WELL, {search: 'testl'})).toBe(true)
    })

    it('does not match a name the table never draws', () => {
        const named = centre({name: 'Congo Meteorological Agency'})

        expect(matches(named, {search: 'congo meteorological'})).toBe(false)
    })

    it('narrows to one standing exactly', () => {
        expect(matches(NEVER, {standing: 'never_seen'})).toBe(true)
        expect(matches(WELL, {standing: 'never_seen'})).toBe(false)
    })

    it('treats whitespace alone as no search at all', () => {
        expect(matches(WELL, {search: '   '})).toBe(true)
        expect(narrowing({search: '   '})).toBe(false)
    })
})

describe('what the population line has to say', () => {
    it('says there is no region yet when nothing was sent', () => {
        expect(population(0, 0, false).state).toBe('none')
    })

    it('says how many there are when nothing is narrowing', () => {
        expect(population(32, 32, false)).toEqual({
            state: 'all',
            total: 32,
            shown: 32,
            hidden: 0,
        })
    })

    it('says what the filter took away', () => {
        expect(population(32, 9, true)).toEqual({
            state: 'narrowed',
            total: 32,
            shown: 9,
            hidden: 23,
        })
    })

    it('says a filter that matched everything is hiding nothing', () => {
        // Its own state, because a reader who cannot tell this from an
        // unfiltered table will read a full one as the whole region.
        expect(population(32, 32, true).state).toBe('matched')
    })
})

describe('clicking a column head', () => {
    it('starts a new column ascending', () => {
        expect(nextSort({sort: '', direction: 'asc'}, 'hours_quiet'))
            .toEqual({sort: 'hours_quiet', direction: 'asc'})
    })

    it('turns the same column around', () => {
        expect(nextSort({sort: 'hours_quiet', direction: 'asc'}, 'hours_quiet'))
            .toEqual({sort: 'hours_quiet', direction: 'desc'})
    })

    it('gives the server order back on the third click', () => {
        expect(nextSort({sort: 'hours_quiet', direction: 'desc'}, 'hours_quiet'))
            .toEqual({sort: '', direction: 'asc'})
    })
})

describe('the vocabularies the payload carries', () => {
    it('ranks each value by its position, worst first', () => {
        expect(RANKS.standing.never_seen).toBe(0)
        expect(RANKS.standing.healthy).toBe(6)
        expect(RANKS.silence.silent).toBe(0)
    })

    it('words each value from the server rather than from here', () => {
        expect(labelsFrom(VOCABULARIES).standing.archive_only).toBe('Archive only')
    })

    it('copes with a payload that carried none', () => {
        expect(ranksFrom(undefined)).toEqual({})
        expect(labelsFrom(undefined)).toEqual({})
    })
})
