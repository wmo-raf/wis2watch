/**
 * The station table, as a file somebody can send.
 *
 * Two formats and one rule: **both say the same thing about what they are.**
 * An export leaves the page and arrives somewhere with none of its context --
 * no filter above it, no window control beside it, nobody who remembers what
 * was on screen when the button was pressed -- so every provenance line the
 * page carries visually is written into the file itself. A picture of 47 rows
 * with nothing saying they were filtered out of 312 is a document that lies
 * about a centre, and it lies to whoever was *not* in the room.
 *
 * Neither route screenshots the DOM, and that is not a preference. The table
 * virtualises: about fifty rows exist at any moment and the rest is two
 * spacers holding the height open. Every screenshot library there is would
 * capture those fifty rows and two grey voids, and would do it silently.
 * The data is all here in memory, so both formats are drawn from it.
 */
import {presenceStates} from './presence.js'
import {displayName, formatInstant, formatQuiet} from './charts/plot.js'
import {STANDING_LABEL} from './standings.js'

//: The dot beside a standing, and the three ways it is drawn -- filled live,
//: filled alarm, or the live colour as a ring. Read off the same standings
//: the stylesheet keys its own rules off, because a picture whose dot
//: disagrees with the page's is worse than a picture with no dot at all.
const DOT = 8
const DOT_GAP = 6

const ALARMING = new Set(['gone_quiet', 'never_transmitted'])

//: Chrome refuses a canvas over 16384px on a side, and does it by handing
//: back a surface that draws nothing rather than by throwing. At a legible
//: row this is where that lands, with room left for the caption block.
const MAX_ROWS = 1100

//: A legible row, and the same one the table itself is read at plus a little:
//: the table has a hover state and a scrollbar to help a reader hold their
//: place, and a flat image has neither.
const ROW = 16
const HEADER = 34
const PAD = 16
const GAP = 12

const WIGOS_WIDTH = 152
const NAME_WIDTH = 186
const STANDING_WIDTH = 118
const HEARD_WIDTH = 128
const QUIET_WIDTH = 68

//: Every text column, in the order the table reads them, so that the heads,
//: the cells and the width the matrix starts at are worked out from one list
//: rather than from three additions that can drift apart.
const TEXT_COLUMNS = [
    {label: 'WIGOS id', width: WIGOS_WIDTH, ink: true,
        value: (row) => row.wigos_id || ''},
    {label: 'Name', width: NAME_WIDTH, ink: true,
        value: (row) => displayName(row)},
    {label: 'Standing', width: STANDING_WIDTH, dot: true,
        value: (row) => STANDING_LABEL[row.standing] || ''},
    {label: 'Last heard', width: HEARD_WIDTH,
        value: (row) => formatInstant(row.last_heard)},
    {label: 'Quiet', width: QUIET_WIDTH,
        value: (row) => formatQuiet(row.hours_quiet)},
]

const TEXT_WIDTH = TEXT_COLUMNS.reduce((total, column) => total + column.width, 0)

const FONT = '12px system-ui, -apple-system, "Segoe UI", sans-serif'
const FONT_SMALL = '11px system-ui, -apple-system, "Segoe UI", sans-serif'
const FONT_HEAD = '600 12px system-ui, -apple-system, "Segoe UI", sans-serif'

/**
 * What was on screen, in the words the file will carry.
 *
 * Built once and handed to both formats, so the CSV's comment rows and the
 * image's caption cannot come to disagree about which view was exported.
 *
 * @param {object} context - the centre, the window, the filter and the counts.
 * @returns {string[]} one line each, in reading order.
 */
export function provenance({centreName, windowLabel, generatedAt, filters,
                            showing, total, sortLabel}) {
    const lines = [
        `${centreName} — stations`,
        `Period: ${windowLabel}`,
        `Generated: ${generatedAt}`,
    ]

    // Said even when nothing is filtered, because "no filter" is a claim the
    // file has to make: a reader cannot tell the difference between a filter
    // line that was omitted and one that was never true.
    lines.push(filters.length
        ? `Filter: ${filters.join('; ')}`
        : 'Filter: none')

    lines.push(
        `Showing ${showing} of ${total} stations`
        + (sortLabel ? `, sorted by ${sortLabel}` : '')
    )

    return lines
}

/** One CSV field, quoted only where it has to be. */
function field(value) {
    const text = value === null || value === undefined ? '' : String(value)

    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

/**
 * The rows as a spreadsheet.
 *
 * The bucket columns carry the **raw count** rather than the three-state word
 * the matrix paints. The states are thresholds applied to these numbers, and
 * anyone opening a CSV is someone who may want to apply their own -- so the
 * file carries what was measured and the page keeps the reading of it.
 *
 * @returns {string} the whole file.
 */
export function stationsCsv({rows, buckets, lines}) {
    const out = lines.map((line) => `# ${line}`)

    out.push([
        'WIGOS id',
        'Name',
        'Standing',
        'Last heard',
        'Hours quiet',
        'Messages',
        ...buckets.map((bucket) => bucket.start),
    ].map(field).join(','))

    rows.forEach((row) => {
        out.push([
            row.wigos_id,
            displayName(row),
            STANDING_LABEL[row.standing] || row.standing,
            row.last_heard || '',
            row.hours_quiet === null ? '' : row.hours_quiet,
            row.messages_in_window,
            ...buckets.map((_, at) => (row.presence || [])[at] ?? 0),
        ].map(field).join(','))
    })

    return out.join('\n')
}

/**
 * The rows as a picture: names, standing, how long quiet, and the matrix.
 *
 * Drawn rather than captured, and drawn from the same `presenceStates` the
 * cells on the page are drawn from, so a band here is the band that was on
 * screen. Every column the table carries except the message count and the
 * 24h shape: the count is a figure to sort by rather than to read off a
 * picture, and the shape is a flat 24 hours that has no business beside a
 * matrix of days.
 *
 * @returns {HTMLCanvasElement|null} null where there are too many rows to
 *     draw, which the caller reports rather than swallows.
 */
export function stationsImage({rows, buckets, ceilings, roles, lines, axis}) {
    if (!rows.length || rows.length > MAX_ROWS) {
        return null
    }

    const cell = Math.max(3, Math.min(9, Math.round(760 / buckets.length)))
    const matrixWidth = cell * buckets.length
    const left = PAD + TEXT_WIDTH
    const width = left + matrixWidth + PAD
    const captionTop = PAD + HEADER + rows.length * ROW + GAP
    const height = captionTop + lines.length * 16 + GAP + 18 + PAD

    const canvas = document.createElement('canvas')
    const ratio = window.devicePixelRatio || 1

    canvas.width = Math.round(width * ratio)
    canvas.height = Math.round(height * ratio)

    const context = canvas.getContext('2d')

    context.scale(ratio, ratio)
    context.textBaseline = 'middle'

    context.fillStyle = roles.page
    context.fillRect(0, 0, width, height)

    // The column heads, and the axis over the matrix. Both ends of the axis
    // rather than one: a run of cells says *when* something stopped only if
    // the axis is named, which is the rule every other surface here follows.
    context.fillStyle = roles.ink
    context.font = FONT_HEAD

    let head = PAD

    TEXT_COLUMNS.forEach((column) => {
        context.fillText(column.label, head, PAD + 10)
        head += column.width
    })

    context.font = FONT_SMALL
    context.fillStyle = roles.meta
    context.fillText(axis.start, left, PAD + 10)
    context.textAlign = 'right'
    context.fillText(axis.end, left + matrixWidth, PAD + 10)
    context.textAlign = 'left'

    context.strokeStyle = roles.grid
    context.lineWidth = 1
    context.beginPath()
    context.moveTo(PAD, PAD + HEADER - 8.5)
    context.lineTo(width - PAD, PAD + HEADER - 8.5)
    context.stroke()

    rows.forEach((row, at) => {
        const top = PAD + HEADER + at * ROW
        const middle = top + ROW / 2

        // Every other row washed, which is the only thing standing in for the
        // hover the page has and the picture does not: at sixteen pixels a
        // row, an eye tracking a name across to its cells needs a rail.
        if (at % 2) {
            context.fillStyle = roles.zebra
            context.fillRect(PAD, top, width - PAD * 2, ROW)
        }

        context.font = FONT

        let x = PAD

        TEXT_COLUMNS.forEach((column) => {
            const inset = column.dot ? drawDot(context, roles, row.standing, x, middle) : 0

            // The identifying two in the ink the names are in, the rest in the
            // meta colour: on a sheet six columns wide the eye needs to be
            // told which of them name the station and which describe it.
            context.fillStyle = column.ink ? roles.ink : roles.meta
            context.fillText(
                clip(context, column.value(row), column.width - inset - 8),
                x + inset,
                middle
            )
            x += column.width
        })

        presenceStates(
            row.presence || [], ceilings, buckets.length, row.baseline_hours ?? null
        )
            .forEach((state, column) => {
                context.fillStyle = roles[state] || roles.empty
                context.fillRect(left + column * cell, top + 2, cell - 0.5, ROW - 4)
            })
    })

    // The legend, and then what this file is. Under the picture rather than
    // over it: the picture is what a reader looks at first, and a caption
    // above delays that by a paragraph.
    let y = captionTop + 8
    let x = PAD

    context.font = FONT_SMALL

    // Every state the picture can contain, named. A legend that lists three
    // colours over a picture drawn in four is the same lie as a filtered
    // export that does not say it was filtered.
    ;[
        ['full', 'Heard'],
        ['thin', 'Heard thinly'],
        ['silent', 'Silent'],
        ['unjudged', 'Not enough history to judge'],
    ]
        .forEach(([state, word]) => {
            context.fillStyle = roles[state]
            context.fillRect(x, y - 5, 16, 10)
            context.fillStyle = roles.meta
            context.fillText(word, x + 22, y)
            x += 22 + context.measureText(word).width + 18
        })

    y += 18

    lines.forEach((line) => {
        context.fillStyle = roles.meta
        context.fillText(line, PAD, y)
        y += 16
    })

    return canvas
}

/**
 * One standing's dot, and how much room it took.
 *
 * Undeclared is a *ring* rather than a third colour, exactly as the table
 * paints it: transmitting, with a question about it, said without spending
 * another hue on a page already carrying three.
 *
 * @returns {number} the width to indent the label by.
 */
function drawDot(context, roles, standing, x, middle) {
    if (!standing) {
        return 0
    }

    const centre = x + DOT / 2
    const alarming = ALARMING.has(standing)

    context.beginPath()
    context.arc(centre, middle, DOT / 2 - (standing === 'undeclared' ? 1 : 0), 0, Math.PI * 2)

    if (standing === 'undeclared') {
        context.strokeStyle = roles.live
        context.lineWidth = 2
        context.stroke()
    } else {
        context.fillStyle = alarming ? roles.alarm : roles.live
        context.fill()
    }

    return DOT + DOT_GAP
}

/** As much of a word as fits, with an ellipsis where it does not. */
function clip(context, text, room) {
    if (context.measureText(text).width <= room) {
        return text
    }

    let cut = text

    while (cut.length > 1 && context.measureText(`${cut}…`).width > room) {
        cut = cut.slice(0, -1)
    }

    return `${cut}…`
}

/**
 * Hand a file to the reader.
 *
 * A synthetic anchor rather than anything cleverer: this runs inside the
 * Wagtail admin, where an object URL and a `download` attribute are the whole
 * of what a download needs. Revoked on the next frame, because a URL revoked
 * in the same tick is one the browser has not finished reading.
 */
export function handOver(blob, filename) {
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')

    anchor.href = url
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()

    requestAnimationFrame(() => URL.revokeObjectURL(url))
}

/** A filename nobody has to rename: the centre, the window, the day. */
export function filenameFor(centreName, windowKey, extension) {
    const slug = (centreName || 'stations')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-|-$/g, '')
        .slice(0, 48)

    return `${slug || 'stations'}-${windowKey || 'window'}.${extension}`
}
