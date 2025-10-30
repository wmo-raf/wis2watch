import {onMounted, onUnmounted, ref} from 'vue'

export function useWebSocket() {
    const ws = ref(null)
    const isConnected = ref(false)
    const connectionStatus = ref('disconnected')
    const messageHandlers = ref([])

    const connect = () => {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
        const wsUrl = `${protocol}//${window.location.host}/ws/mqtt-status/`

        ws.value = new WebSocket(wsUrl)

        ws.value.onopen = () => {
            isConnected.value = true
            connectionStatus.value = 'connected'
            console.log('WebSocket connected')
        }

        ws.value.onmessage = (event) => {
            const message = JSON.parse(event.data)
            messageHandlers.value.forEach(handler => handler(message))
        }

        ws.value.onclose = () => {
            isConnected.value = false
            connectionStatus.value = 'disconnected'
            console.log('WebSocket disconnected, reconnecting...')
            setTimeout(connect, 3000)
        }

        ws.value.onerror = (error) => {
            connectionStatus.value = 'error'
            console.error('WebSocket error:', error)
        }
    }

    const disconnect = () => {
        if (ws.value) {
            ws.value.close()
        }
    }

    const sendMessage = (data) => {
        if (ws.value && isConnected.value) {
            ws.value.send(JSON.stringify(data))
        }
    }

    const onMessage = (handler) => {
        messageHandlers.value.push(handler)
    }

    onMounted(() => {
        connect()
    })

    onUnmounted(() => {
        disconnect()
    })

    return {
        isConnected,
        connectionStatus,
        sendMessage,
        onMessage
    }
}