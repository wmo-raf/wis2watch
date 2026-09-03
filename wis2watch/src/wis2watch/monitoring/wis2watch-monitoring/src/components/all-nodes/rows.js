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
        key: 'transmission',
        label: 'Status',
        width: '11rem',
        vocabulary: 'transmission',
        // The glance table's verdict: whether data is flowing, and nothing
        // about the plumbing that carried it.
        value: (row, ranks) => rankOf(ranks, 'transmission', row.transmission),
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
        key: 'last_observation_at',
        // "Last observation" and not "Last seen" -- key and label both --
        // because that is what the server measures: the verdict beside it is
        // anchored on observation traffic, and a column headed with the wider
        // word would have a centre read as heard-from six minutes ago and
        // gone quiet at once.
        label: 'Last observation',
        width: '11rem',
        // Never seen sorts before anything, exactly as the server's own
        // reading order does it: "nothing has ever arrived" is the extreme of
        // "a long time ago", not a missing value to be swept to the end.
        value: (row) =>
            row.last_observation_at ? Date.parse(row.last_observation_at) : -Infinity,
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
        key: 'dataset_count',
        // Every dataset the centre declares, of whatever kind. How big a
        // centre is rather than how well it is, which is why the verdict
        // does not read it and why it is not the count in the sentence under
        // the status.
        label: 'Datasets',
        width: '6rem',
        align: 'number',
        value: (row) => row.dataset_count,
    },
    {
        key: 'station_count',
        label: 'Stations',
        width: '6rem',
        align: 'number',
        value: (row) => row.station_count,
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
 * Which columns each surface draws, and in what order.
 *
 * Two tables from one component, because they are one table asking two
 * questions. The glance on the admin's front page asks whether data is
 * flowing; the overview page asks what is wrong. Neither is a subset of the
 * other by accident -- the glance drops the plumbing on purpose, and the
 * detailed page draws a different verdict in the same slot.
 *
 * Named lists rather than a boolean, and rather than a column list handed in
 * from a template. A boolean toggling five things is the union-of-two-feature-
 * sets prop this codebase already refused once; a list in a `data-` attribute
 * is a place to typo a key into a silently missing column. Two lists side by
 * side can be read against each other, which is the only way anybody will
 * notice the day they drift.
 */
export const VIEWS = {
    glance: [
        'centre_id',
        'country_name',
        'transmission',
        'last_observation_at',
        'hours_quiet',
        'messages_in_window',
        'sparkline',
    ],
    detail: [
        'centre_id',
        'country_name',
        'standing',
        'last_observation_at',
        'hours_quiet',
        'messages_in_window',
        'sparkline',
        'dataset_count',
        'station_count',
        'origin_watch',
        'cache_pickup',
        'silence',
    ],
}

/**
 * Which verdict each view draws, and therefore filters by.
 *
 * Beside `VIEWS` rather than worked out in the component, because it *is* the
 * views' own business: the two tables put a different verdict in the same slot,
 * and a filter narrowing by the column a table is not drawing is a filter that
 * hides rows for a reason nobody can see.
 */
export const VERDICT_FOR_VIEW = {
    glance: 'transmission',
    detail: 'standing',
}

/** Which verdict a view draws. */
export function verdictFor(view) {
    return VERDICT_FOR_VIEW[view] || VERDICT_FOR_VIEW.glance
}

/**
 * The columns one view draws.
 *
 * Falls back to the glance rather than to everything, because a view name
 * nobody recognises is a mount point somebody mistyped, and the smaller table
 * is the safer thing to render while they work out why.
 *
 * @param {string} view - `glance` or `detail`.
 * @returns {Array<object>} the column descriptors, in drawing order.
 */
export function columnsFor(view) {
    return (VIEWS[view] || VIEWS.glance).map((key) => COLUMN_BY_KEY[key])
}

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
 * @param {string} verdict - which verdict field the standing filter reads,
 *   since the two views put a different one in the same slot.
 * @returns {boolean} whether it is shown.
 */
export function matches(row, {search = '', standing = ''} = {}, verdict = 'standing') {
    if (standing && row[verdict] !== standing) {
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


/**
 * How much of a centre's output is past its own cadence, in words.
 *
 * One sentence, two callers, one spelling. The detailed table hangs it off
 * its Silence badge as a tooltip and the glance table draws it under the
 * status; written twice it would be two sentences within a release or two,
 * and a reader moving between the tables would be told the same fact in two
 * shapes.
 *
 * It is also where the word "dataset" is allowed to appear. The verdict above
 * it no longer says it -- see `TransmissionStanding` -- because a reader who
 * has never registered a WCMP2 record cannot act on the catalogue's noun as a
 * verdict. Here the count teaches it: "3 of 12" makes a dataset a countable
 * thing this centre has twelve of, which is as much as anybody needs to read
 * the row.
 *
 * "Observation datasets" and not "datasets", because the counts are the
 * centre's observation datasets alone -- the verdict is measured over those
 * (ADR-0017), and the Datasets column two along counts every kind. A reader
 * comparing "3 of 5 overdue" against a count of twelve is owed the word that
 * explains the difference.
 *
 * Empty where the centre has nothing that can be judged, which no `silent`
 * row is -- being silent requires a dataset with an expectation -- so this is
 * a guard rather than a case.
 *
 * @param {object} row - the centre.
 * @returns {string} the sentence, or an empty string.
 */
export function overdueSentence(row) {
    if (!row?.judged_dataset_count) {
        return ''
    }

    return `${row.silent_dataset_count} of ${row.judged_dataset_count} observation datasets overdue`
}

/**
 * What a badge says under itself, where it has more to say.
 *
 * The overview page carried these as extra lines under two of its badges --
 * what the centre's own broker last reported, and how many of its datasets
 * are overdue. They come back as the badge's own tooltip rather than as lines
 * of their own: rows two and three deep destroy the reading down a column
 * that a worst-first table exists for, and the broker's error arrives here
 * *whole*, where the page it replaces cut it at sixty characters.
 *
 * That argument is about *this* table, which is twelve columns wide and shows
 * the Silence badge and the dataset count as columns of their own. It does
 * not reach the glance table, which is seven columns and had nothing on the
 * row at all -- see {@link subline}.
 *
 * Empty where there is nothing to add, which the caller renders as no tooltip
 * at all rather than as an empty one.
 *
 * @param {object} row - the centre.
 * @param {string} field - which badge is asking.
 * @param {object} labels - every vocabulary's label map, by field.
 * @returns {string} the tooltip, or an empty string.
 */
export function badgeTitle(row, field, labels) {
    if (field === 'origin_watch') {
        const said = labels?.origin_broker_reachability?.[row.origin_broker_reachability]
        // The state above says whether the centre can be judged at all; this
        // says what its broker is doing about the obligation to be dialable,
        // which is a different conversation with a different person.
        const parts = said ? [`Broker: ${said}`] : []

        if (row.origin_last_error) {
            parts.push(row.origin_last_error)
        }

        return parts.join(' \u2014 ')
    }

    if (field === 'silence') {
        return overdueSentence(row)
    }

    return ''
}

/**
 * The second line under a centre's status, where it has earned one.
 *
 * The glance table shows one verdict and, until this, none of what it was
 * folded from: a reader met "Behind schedule" in a seven-column table with no
 * count, no dataset column, and a `Quiet` cell reading six minutes right
 * beside it. A verdict with its evidence nowhere on the row and its
 * contradiction next to it is worse than a quiet row.
 *
 * Only on the rows with a fault, and only on the glance table.
 *
 * Only `silent`, because it is the one verdict here that is about *part* of a
 * centre -- the others are whole-centre facts the `Last observation` and
 * `Quiet` columns already carry. That also keeps the cost where it belongs:
 * the seven rows in thirty-two that are the reason the page was opened grow a
 * line and the other twenty-five do not, which makes a worst-first scan
 * easier rather than flatter. It is the shape ADR-0009 rejected at twelve
 * columns and every badge, and the reason it rejected it does not survive at
 * one line under one badge on a minority of rows.
 *
 * Only the glance table, because the detailed one already draws the Silence
 * badge, the dataset count, and this very sentence as that badge's tooltip.
 * A second copy in the Standing cell would put one sentence twice on one row,
 * which teaches a reader the two cells might mean different things.
 *
 * Here rather than as a `v-if` in the template for the reason the view's
 * column lists are here: the component draws, this decides, and a decision
 * spelled in a template is a decision with no test.
 *
 * @param {object} row - the centre.
 * @param {string} view - `glance` or `detail`.
 * @param {string} verdict - which verdict this view draws.
 * @returns {string} the line, or an empty string for no line at all.
 */
export function subline(row, view, verdict) {
    if (view !== 'glance' || row?.[verdict] !== 'silent') {
        return ''
    }

    return overdueSentence(row)
}
