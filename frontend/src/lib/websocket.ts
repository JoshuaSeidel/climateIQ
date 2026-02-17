/**
 * Reconnecting WebSocket client with exponential backoff.
 *
 * Ingress-aware: uses BASE_PATH from api.ts so connections route
 * correctly through Home Assistant's ingress proxy.
 *
 * Usage:
 *   const ws = new ReconnectingWebSocket('zones')
 *   ws.subscribe((data) => { ... })
 *   ws.connect()
 *   // later:
 *   ws.close()
 */

import { BASE_PATH } from './api'

export type MessageHandler<T = unknown> = (data: T) => void

/** Build the full ws:// or wss:// URL for a given channel. */
function buildWsUrl(channel: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return `${protocol}//${host}${BASE_PATH}/ws?channel=${channel}`
}

export class ReconnectingWebSocket<T = unknown> {
  private ws: WebSocket | null = null
  private subscribers: MessageHandler<T>[] = []
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private attempt = 0
  private stopped = false
  private readonly url: string
  private readonly maxDelay: number
  private readonly initialDelay: number

  constructor(
    channel: string,
    options: { initialDelay?: number; maxDelay?: number } = {},
  ) {
    this.url = buildWsUrl(channel)
    this.initialDelay = options.initialDelay ?? 1_000
    this.maxDelay = options.maxDelay ?? 30_000
  }

  /** Register a handler called for every parsed message. */
  subscribe(handler: MessageHandler<T>): () => void {
    this.subscribers.push(handler)
    return () => {
      this.subscribers = this.subscribers.filter((h) => h !== handler)
    }
  }

  /** Open (or re-open) the WebSocket connection. */
  connect(): void {
    this.stopped = false
    this.openConnection()
  }

  /** Permanently close the connection and stop reconnecting. */
  close(): void {
    this.stopped = true
    this.clearReconnect()
    if (this.ws) {
      this.ws.onclose = null // prevent reconnect on intentional close
      this.ws.close()
      this.ws = null
    }
  }

  /** Whether the underlying socket is currently open. */
  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN
  }

  // ---------------------------------------------------------------
  // Internal
  // ---------------------------------------------------------------

  private openConnection(): void {
    if (this.stopped) return

    try {
      this.ws = new WebSocket(this.url)
    } catch {
      this.scheduleReconnect()
      return
    }

    this.ws.onopen = () => {
      this.attempt = 0
    }

    this.ws.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as T
        for (const handler of this.subscribers) {
          handler(data)
        }
      } catch (e) {
        console.error('WebSocket parse error:', e)
      }
    }

    this.ws.onclose = () => {
      this.ws = null
      this.scheduleReconnect()
    }

    this.ws.onerror = () => {
      // onclose will fire after onerror, triggering reconnect
      this.ws?.close()
    }
  }

  private scheduleReconnect(): void {
    if (this.stopped) return
    this.clearReconnect()

    // Exponential backoff: 1s, 2s, 4s, 8s, ... capped at maxDelay
    const delay = Math.min(
      this.initialDelay * 2 ** this.attempt,
      this.maxDelay,
    )
    this.attempt += 1
    this.reconnectTimer = setTimeout(() => this.openConnection(), delay)
  }

  private clearReconnect(): void {
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }
}
