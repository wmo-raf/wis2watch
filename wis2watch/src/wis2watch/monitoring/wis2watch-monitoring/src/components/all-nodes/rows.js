/**
 * What a centre's row is worth on each column, and what narrows the list.
 *
 * The component draws; this decides. Split out for the reason every tested
 * module in this tree is split out: the mistakes that matter here are the
 * silent ones. A comparator that sends never-seen to the wrong end returns a
 * table rather than an exception, and the table it returns has the worst
 * centre in the region at the bottom of a panel built to put it at the top.
 *
 * **Keys, never words.** Rows carry the server's own strings and the labels
 * ride once in the payload's `vocabularies`, so nothing here spells a
 * standing. That is what lets the overview page and this table describe one
 * centre in one vocabulary, and it is why the badge columns sort by looking a
 * rank up rather than by comparing text.
 *
 * **A vocabulary is already in reading order.** `NodeStanding.CHOICES` and
 * the three beside it are declared worst-first on the Python side, so a
 * badge's rank is just its position and there is no second ordering here to
 * fall out of step with the first.
 */

//: Where a value sorts that its own vocabulary has never heard of.
//:
//: Last, and deliberately not first: an unrecognised standing is a client
//: reading a payload from a newer server than itself, and the safe reading of
//: a word you do not know is "not the most urgent thing on this screen"
//: rather than "worse than everything".
const UNKNOWN_RANK = Number.MAX_SAFE_INTEGER

/**
 * Where one value sits in its vocabulary.
 *
 * @param {object} ranks - every vocabulary's rank map, by field.
 * @param {string} field - which vocabulary to read.
 * @param {string} value - the server's own string for the value.
 * @returns {number} its position, or {@link UNKNOWN_RANK}.
 */
function rankOf(ranks, field, value) {
    const rank = ranks?.[field]?.[value]

    return rank === undefined ? UNKNOWN_RANK : rank
}

/**
 * The table's columns, left to right, and what each one sorts by.
 *
 * The first six and the shape beside them are the station table's own
 * columns mapped onto a centre; the last three are the judgements the
 * standing is folded from, kept as its evidence.
 *
 * `value` is the sort key rather than the thing drawn -- a date as a number, a
 * badge as a rank -- because what a column *reads as* and what it *orders by*
 * are different questions and only one of them belongs in a comparator. A
 * column without one is a column that cannot be sorted, which the shape is:
 * ordering rows by a picture is not a thing a reader means.
 */
export const COLUMNS = [
    {
        key: 'centre_id',
        label: 'Centre',
        width: '9.5rem',
        value: (row) => row.centre_id,
    },
    {
        key: 'country_name',
        label: 'Country',
        width: '10rem',
        // Lowercased so the order is the one a reader expects rather than the
        // one ASCII has, where every capital sorts above every lower case.
        value: (row) => (row.country_name || '').toLowerCase(),
    },
    {
        key: 'standing',
        label: 'Standing',
        width: '11rem',
        vocabulary: 'standing',
        // By rank rather than by label, so this column sorts into the order
        // the rows arrived in rather than into alphabetical order -- which
        // would put "Gone quiet" above "Never heard from" for no reason a
        // reader can see.
        value: (row, ranks) => rankOf(ranks, 'standing', row.standing),
    },
    {
        key: 'last_seen_at',
        label: 'Last seen',
        width: '11rem',
        // Never seen sorts before anything, exactly as the server's own
        // reading order does it: "nothing has ever arrived" is the extreme of
        // "a long time ago", not a missing value to be swept to the end.
        value: (row) => (row.last_seen_at ? Date.parse(row.last_seen_at) : -Infinity),
    },
    {
        key: 'hours_quiet',
        label: 'Quiet',
        width: '5.5rem',
        align: 'number',
        // The same fact from the other end, and so the opposite infinity. A
        // centre nothing has ever been heard from has been quiet for ever.
        value: (row) =>
            row.hours_quiet === null || row.hours_quiet === undefined
                ? Infinity
                : row.hours_quiet,
    },
    {
        key: 'messages_in_window',
        label: 'Messages (24h)',
        width: '8rem',
        align: 'number',
        value: (row) => row.messages_in_window,
    },
    {
        key: 'sparkline',
        label: '24h shape',
        width: '8rem',
    },
    {
        key: 'origin_watch',
        label: 'Origin',
        width: '11rem',
        vocabulary: 'origin_watch',
        value: (row, ranks) => rankOf(ranks, 'origin_watch', row.origin_watch),
    },
    {
        key: 'cache_pickup',
        label: 'Global Cache',
        width: '9.5rem',
        vocabulary: 'cache_pickup',
        value: (row, ranks) => rankOf(ranks, 'cache_pickup', row.cache_pickup),
    },
    {
        key: 'silence',
        label: 'Silence',
        width: '9rem',
        vocabulary: 'silence',
        value: (row, ranks) => rankOf(ranks, 'silence', row.silence),
    },
]

/** One column by its key. */
export const COLUMN_BY_KEY = Object.fromEntries(
    COLUMNS.map((column) => [column.key, column])
)

/**
 * Every vocabulary as a rank map, so a badge can be sorted by position.
 *
 * @param {object} vocabularies - the payload's own, by field.
 * @returns {object} `{field: {key: rank}}`.
 */
export function ranksFrom(vocabularies) {
    return Object.fromEntries(
        Object.entries(vocabularies || {}).map(([field, entries]) => [
            field,
            Object.fromEntries(entries.map(({key}, rank) => [key, rank])),
        ])
    )
}

/**
 * Every vocabulary as a label map, so a cell can be worded.
 *
 * @param {object} vocabularies - the payload's own, by field.
 * @returns {object} `{field: {key: label}}`.
 */
export function labelsFrom(vocabularies) {
    return Object.fromEntries(
        Object.entries(vocabularies || {}).map(([field, entries]) => [
            field,
            Object.fromEntries(entries.map(({key, label}) => [key, label])),
        ])
    )
}

/**
 * The rows in the order asked for, or in the order they arrived.
 *
 * **No sort is the server's sort**, untouched. Not an order this module
 * happens to agree with today: re-deriving the standing rank and the
 * tiebreakers here is exactly how a table and the rows it was sent come to
 * disagree about what is broken. The starting order is a finding, and the only
 * safe way to keep showing it is to not touch it.
 *
 * Equal values compare equal rather than falling through to a tiebreaker, so
 * a column where everything matches -- twenty centres with no messages at all
 * -- keeps the server's order underneath rather than shuffling on every
 * redraw.
 *
 * @param {Array<object>} rows - the rows to order.
 * @param {{sort: string, direction: string, ranks: object}} how - the column,
 *   the way, and the vocabularies the badge columns are ranked by.
 * @returns {Array<object>} a new array, or `rows` itself where nothing sorts.
 */
export function sortRows(rows, {sort, direction, ranks} = {}) {
    const column = COLUMN_BY_KEY[sort]

    if (!column?.value) {
        return rows
    }

    const way = direction === 'desc' ? -1 : 1

    return [...rows].sort((left, right) => {
        const a = column.value(left, ranks)
        const b = column.value(right, ranks)

        if (a === b) {
            return 0
        }

        return (a < b ? -1 : 1) * way
    })
}

/**
 * Whether one centre survives the controls above the table.
 *
 * Searched over the centre ID and the country, which are the two things on
 * the row a reader would type. The centre's *name* is deliberately not
 * searched: it is not drawn anywhere on this table, and a filter that hides
 * rows on evidence the reader cannot see is a filter that looks broken.
 *
 * @param {object} row - the centre.
 * @param {{search: string, standing: string}} narrowing - what is set.
 * @returns {boolean} whether it is shown.
 */
export function matches(row, {search = '', standing = ''} = {}) {
    if (standing && row.standing !== standing) {
        return false
    }

    const looked = search.trim().toLowerCase()

    if (!looked) {
        return true
    }

    return (
        (row.centre_id || '').toLowerCase().includes(looked) ||
        (row.country_name || '').toLowerCase().includes(looked)
    )
}

/** Whether either control is narrowing anything. */
export function narrowing({search = '', standing = ''} = {}) {
    return Boolean(search.trim() || standing)
}

/**
 * What the table is showing, and of how much.
 *
 * Four states rather than a count, because the sentence a reader needs is
 * different in each and the degenerate ones are findings in their own right.
 * A filter that matches everything is worth saying out loud -- a reader who
 * cannot tell "no rows were hidden" from "the filter is off" will read a full
 * table as an unfiltered one.
 *
 * @param {number} total - every centre the server sent.
 * @param {number} shown - how many survived the controls.
 * @param {boolean} filtering - whether anything is narrowing.
 * @returns {{state: string, total: number, shown: number, hidden: number}}
 *   one of `none`, `all`, `narrowed`, `matched`.
 */
export function population(total, shown, filtering) {
    if (!total) {
        return {state: 'none', total: 0, shown: 0, hidden: 0}
    }

    if (!filtering) {
        return {state: 'all', total, shown, hidden: 0}
    }

    const hidden = total - shown

    return {
        state: hidden ? 'narrowed' : 'matched',
        total,
        shown,
        hidden,
    }
}

/**
 * Where a click on a column head leaves the sort.
 *
 * Three states per column and not two: ascending, descending, and back to the
 * order the server sent. The starting order is a finding, so there has to be a
 * way back to it that is not reloading the page.
 *
 * @param {{sort: string, direction: string}} current - what is set now.
 * @param {string} key - the column that was clicked.
 * @returns {{sort: string, direction: string}} what to set.
 */
export function nextSort({sort, direction} = {}, key) {
    if (sort !== key) {
        return {sort: key, direction: 'asc'}
    }

    if (direction === 'asc') {
        return {sort: key, direction: 'desc'}
    }

    return {sort: '', direction: 'asc'}
}
