/**
 * What the reader has picked -- which bucket, and which station -- and what
 * "dark in it" means.
 *
 * Under the same rule `charts/plot.js` and `presence.js` are under:
 * ARITHMETIC AND TOKENS ONLY. Nothing here returns markup.
 *
 * **A selection is a bucket start, not an index.** The charts and the matrix
 * are drawn from two payloads that arrive on two requests -- the summary's
 * axis and the rows' axis -- and an index means "the third column of whatever
 * list you happen to be holding". The same instant spelled the way the server
 * spelled it means one bucket on every surface, survives a window change by
 * failing to resolve rather than by resolving to the wrong day, and is a
 * querystring value a reader can read in the address bar.
 *
 * **Dark is silence, and it is the matrix's own silence.** A station is dark
 * in a bucket when its presence vector carries nothing there -- the same zero
 * `presenceState` paints as `silent` -- so the rows the filter keeps are
 * exactly the rows whose cell in that column is empty. Two definitions of
 * darkness on one page is a filter that hides a station whose cell says it
 * was heard.
 */
import {grainOf} from './presence.js'

//: What a surface offering the gesture tells a reader it does, in one place
//: because four charts and a column strip saying it four ways is four
//: gestures to a reader listening rather than one. The keys are named rather
//: than the mouse: whoever is being read this sentence is on the keyboard.
export const SELECT_HINT =
    'Press Enter to filter the station list below to the stations that were'
    + ' dark in this bucket, and Escape to clear that filter.'

/**
 * Where a bucket sits on an axis, or -1 where that axis does not carry it.
 *
 * -1 is the ordinary case rather than an error: a link carrying a day of a
 * 90-day window, opened at the default 24 hours, names a bucket this axis
 * genuinely has no column for. The caller drops the selection; nothing here
 * guesses at a neighbour.
 *
 * @param {{start: string}[]} buckets - the axis, as the server drew it.
 * @param {string} key - the bucket's start, as the server spelled it.
 * @returns {number} the index, or -1.
 */
export function bucketIndexOf(buckets, key) {
    if (!key) {
        return -1
    }

    return buckets.findIndex((bucket) => bucket.start === key)
}

/**
 * Whether one station was heard nothing from in one bucket.
 *
 * A vector shorter than the axis counts as dark rather than as an error: the
 * rows and their axis travel together, so this can only happen mid-swap
 * between two windows, and a row that throws takes the table with it.
 *
 * @param {{presence: number[]}} station - the row.
 * @param {number} at - the bucket's index on the axis the row is drawn against.
 * @returns {boolean} true where the station was silent in that bucket.
 */
export function darkIn(station, at) {
    return !station.presence?.[at]
}

/**
 * The station the reader picked, as the rows spell its id.
 *
 * A selection reaches both surfaces through the address bar, which carries
 * strings, and a row carries the number the server sent. The conversion is
 * here rather than at each surface because the map matches it against a
 * feature property and the table against a row, and two spellings of one
 * comparison is how the ring and the highlighted row come to mark two
 * different stations.
 *
 * @param {string} picked - what the address bar says, or empty for none.
 * @returns {number|null} the station id, or null where nothing is picked.
 */
export function pickedStationId(picked) {
    const id = Number(picked)

    return picked && Number.isFinite(id) ? id : null
}

/**
 * Whether one station is the picked one.
 *
 * @param {{station_id: number}} station - the row.
 * @param {string} picked - what the address bar says, or empty for none.
 * @returns {boolean} true where this is the station the reader picked.
 */
export function isPickedStation(station, picked) {
    return station.station_id === pickedStationId(picked)
}

/**
 * What the selected bucket is called, in the words its grain uses.
 *
 * The long form, which is the one the charts' readouts use: this names the
 * bucket a whole table is being filtered by, and "14 Aug" without a year on a
 * station that stopped in 2024 is the one formatting mistake that would
 * matter here.
 *
 * @param {{start: string, partial: boolean}} bucket - the bucket picked.
 * @param {string} grain - `day` or `hour`, the server's own spelling.
 * @returns {string} the bucket, named.
 */
export function bucketName(bucket, grain) {
    if (!bucket) {
        return ''
    }

    return grainOf(grain).long(new Date(bucket.start))
}
