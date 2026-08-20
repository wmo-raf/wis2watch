/**
 * Which of a centre's stations can be put on a map, and what is left off.
 *
 * Under the same rule `charts/plot.js`, `presence.js` and `selection.js` are
 * under: ARITHMETIC AND TOKENS ONLY. Nothing here touches MapLibre, and
 * nothing here returns markup.
 *
 * **The map is a projection of the station rows.** There is no map endpoint
 * and no GeoJSON API: the rows already carry `latitude` and `longitude`, and
 * a second source for the same stations is a second population to disagree
 * with the table beside it.
 *
 * **A drawn feature carries what is drawn and nothing else** -- an id and
 * whether the station is silent. Everything the popup says is looked up on
 * the row at the moment it is clicked, which is what keeps the source stable
 * while the reader moves the window control: `hours_quiet` is re-derived
 * against the clock on every request and would make the feature collection
 * different every time while the picture on screen is identical. The map is
 * the one panel a reader can move a control past without it changing, and
 * that promise is kept here rather than by hoping nothing re-renders.
 *
 * The residue matters as much as the plot. A station with no coordinates
 * inside an outage region is silent for exactly the same reason as the ones
 * drawn red and cannot be seen to be, so "375 not plotted" invites a reader
 * to assume the rest is uninteresting where "375 not plotted, 80 of them
 * silent" does not.
 */
import {isSilent} from './standings.js'

/**
 * The stations as a GeoJSON feature collection, ready for a MapLibre source.
 *
 * A station with either coordinate missing is left out rather than placed at
 * a default: null island is a real place on the map and a centre whose
 * registry is thin would draw a cluster in the Gulf of Guinea.
 *
 * @param {{station_id: number, latitude: number|null, longitude: number|null,
 *     standing: string}[]} stations - the rows, as the server sent them.
 * @returns {{type: string, features: object[]}} the located stations.
 */
export function stationFeatures(stations) {
    const features = stations
        .filter(isPlottable)
        .map((station) => ({
            type: 'Feature',
            geometry: {
                type: 'Point',
                coordinates: [station.longitude, station.latitude],
            },
            properties: {
                //: What a click and the picked layer's filter match on. The
                //: row is looked up by it, so nothing else has to ride here.
                id: station.station_id,
                //: Two colours, one boolean. Which standing it is stays on
                //: the row, for the popup and the table to say in words.
                silent: isSilent(station.standing),
            },
        }))

    return {type: 'FeatureCollection', features}
}

/**
 * What the map cannot show, and how much of it is a failure.
 *
 * @param {{latitude: number|null, longitude: number|null, standing: string}[]}
 *     stations - the rows, as the server sent them.
 * @returns {{total: number, silent: number}} how many carry no coordinates,
 *     and how many of those have not been heard from.
 */
export function unplottable(stations) {
    const missing = stations.filter((station) => !isPlottable(station))

    return {
        total: missing.length,
        silent: missing.filter((station) => isSilent(station.standing)).length,
    }
}

/**
 * The furthest corners of what is drawn, or null where nothing is.
 *
 * @param {{features: object[]}} collection - as `stationFeatures` built it.
 * @returns {[[number, number], [number, number]]|null} south-west, north-east.
 */
export function boundsOf(collection) {
    if (!collection.features.length) {
        return null
    }

    const longitudes = collection.features.map((f) => f.geometry.coordinates[0])
    const latitudes = collection.features.map((f) => f.geometry.coordinates[1])

    return [
        [Math.min(...longitudes), Math.min(...latitudes)],
        [Math.max(...longitudes), Math.max(...latitudes)],
    ]
}

/** Whether a station can be drawn at all. */
function isPlottable(station) {
    return (
        typeof station.latitude === 'number'
        && typeof station.longitude === 'number'
    )
}
