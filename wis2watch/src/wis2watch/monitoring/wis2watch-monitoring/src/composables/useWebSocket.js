import {onMounted, onUnmounted, ref} from 'vue'

/**
 * The ingest feed, as a connection that comes back.
 *
 * The feed is read-only: it says what the ingestion process is seeing, and
 * there is nothing on it to start or stop. A broker is connected because it
 * is in the registry, and the way to change that is to change the registry.
 * The one thing this end may say is `{action: 'get_status'}`, which asks for
 * the state of the connections again.
 *
 * What arrives:
 * - 'status'        the state of every broker connection, keyed by source id
 * - 'status_update' the same, pushed rather than asked for
 * - 'message'       a message the ingest just stored: {centre_id, topic,
 *                   timestamp, geometry}
 * - 'error'         something this end asked for that the feed will not do
 */
export function useWebSocket() {
    const ws = ref(null)
    const isConnected = ref(false)
    const connectionStatus = ref('disconnected')
    const messageHandlers = ref([])
    const reconnectAttempts = ref(0)
    const maxReconnectAttempts = 10
    const baseReconnectDelay = 1000 // 1 second

    /**
     * Calculate exponential backoff delay for reconnection attempts
     */
    const getReconnectDelay = () => {
        return Math.min(
            baseReconnectDelay * Math.pow(2, reconnectAttempts.value),
            30000 // Max 30 seconds
        )
    }

    /**
     * Connect to the WebSocket endpoint
     */
    const connect = () => {
        // Construct WebSocket URL based on current protocol and host
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const wsUrl = `${protocol}//${window.location.host}/ws/ingest-feed/`

        console.log(`🔌 Connecting to WebSocket: ${wsUrl}`)
        connectionStatus.value = 'connecting'

        try {
            ws.value = new WebSocket(wsUrl)

            ws.value.onopen = () => {
                isConnected.value = true
                connectionStatus.value = 'connected'
                reconnectAttempts.value = 0
                console.log('✅ WebSocket connected')

                // Backend sends initial status on connect, no need to request it
            }

            ws.value.onmessage = (event) => {
                try {
                    const message = JSON.parse(event.data)
                    console.log('📨 WebSocket message received:', message.type, message)

                    // Notify all registered message handlers
                    messageHandlers.value.forEach(handler => {
                        try {
                            handler(message)
                        } catch (error) {
                            console.error('❌ Error in message handler:', error)
                        }
                    })
                } catch (error) {
                    console.error('❌ Error parsing WebSocket message:', error)
                }
            }

            ws.value.onclose = (event) => {
                isConnected.value = false
                connectionStatus.value = 'disconnected'

                console.log(`🔌 WebSocket disconnected (code: ${event.code}, reason: ${event.reason || 'unknown'})`)

                // Attempt reconnection with exponential backoff
                if (reconnectAttempts.value < maxReconnectAttempts) {
                    const delay = getReconnectDelay()
                    reconnectAttempts.value++

                    console.log(
                        `🔄 Reconnecting in ${delay}ms (attempt ${reconnectAttempts.value}/${maxReconnectAttempts})...`
                    )

                    setTimeout(connect, delay)
                } else {
                    console.error('❌ Max reconnection attempts reached. Please refresh the page.')
                    connectionStatus.value = 'error'
                }
            }

            ws.value.onerror = (error) => {
                connectionStatus.value = 'error'
                console.error('❌ WebSocket error:', error)
            }
        } catch (error) {
            console.error('❌ Error creating WebSocket connection:', error)
            connectionStatus.value = 'error'
        }
    }

    /**
     * Disconnect from WebSocket
     */
    const disconnect = () => {
        if (ws.value) {
            // Set to max attempts to prevent auto-reconnect
            reconnectAttempts.value = maxReconnectAttempts
            ws.value.close(1000, 'Client disconnect')
            ws.value = null
        }
    }

    /**
     * Send a message through the WebSocket
     * @param {Object} data - Message data to send
     *
     * The feed answers one thing: `{action: 'get_status'}`, for the state of
     * the broker connections. Anything else comes back as an error.
     */
    const sendMessage = (data) => {
        if (ws.value && isConnected.value) {
            try {
                const message = JSON.stringify(data)
                ws.value.send(message)
                console.log('📤 Sent WebSocket message:', data)
            } catch (error) {
                console.error('❌ Error sending WebSocket message:', error)
            }
        } else {
            console.warn('⚠️ Cannot send message: WebSocket not connected')
        }
    }

    /**
     * Register a message handler
     * @param {Function} handler - Function to handle incoming messages
     *
     * Handler receives messages with the following types:
     * - type: 'status' / 'status_update' - the state of every broker
     *   connection, keyed by message source id
     *   data: {source_id: {source_id, name, source_type, centre_id,
     *          is_reachable, last_connected_at, last_error}}
     *
     * - type: 'message' - a message the ingest just stored
     *   data: {centre_id, topic, timestamp, geometry}
     *
     * - type: 'error' - the feed refusing what this end asked for
     *   error: string
     */
    const onMessage = (handler) => {
        if (typeof handler === 'function') {
            messageHandlers.value.push(handler)
        } else {
            console.error('❌ onMessage handler must be a function')
        }
    }

    /**
     * Remove a message handler
     * @param {Function} handler - Handler function to remove
     */
    const offMessage = (handler) => {
        const index = messageHandlers.value.indexOf(handler)
        if (index > -1) {
            messageHandlers.value.splice(index, 1)
        }
    }

    // Lifecycle hooks
    onMounted(() => {
        connect()
    })

    onUnmounted(() => {
        disconnect()
    })

    return {
        // State
        isConnected,
        connectionStatus,
        reconnectAttempts,

        // Methods
        sendMessage,
        onMessage,
        offMessage,
        connect,
        disconnect
    }
}