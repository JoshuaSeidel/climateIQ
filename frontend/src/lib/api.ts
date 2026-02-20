import { QueryClient } from '@tanstack/react-query'

// ============================================================================
// Ingress-aware base path detection
// ============================================================================

/**
 * Detect the HA ingress base path from the current URL.
 *
 * When accessed through Home Assistant ingress, the URL looks like:
 *   http://ha-host:8123/api/hassio_ingress/<token>/
 *
 * We detect this pattern and use it as the base path for all API calls
 * and WebSocket connections. For standalone mode, the base path is empty.
 */
function detectBasePath(): string {
  const path = window.location.pathname
  // Match HA ingress pattern: /api/hassio_ingress/<token>
  const ingressMatch = path.match(/^(\/api\/hassio_ingress\/[^/]+)/)
  if (ingressMatch) {
    return ingressMatch[1]
  }
  // Also check for a custom base path set via <base> tag or meta tag
  const baseMeta = document.querySelector('meta[name="x-ingress-path"]')
  if (baseMeta) {
    const content = baseMeta.getAttribute('content')
    if (content && content !== '/') {
      return content.replace(/\/$/, '')
    }
  }
  return ''
}

/** The detected base path (empty string for standalone, ingress prefix for HA) */
export const BASE_PATH = detectBasePath()

const API_BASE = `${BASE_PATH}/api/v1`

type ParamValue = string | number | boolean | undefined
type FetchOptions = RequestInit & {
  params?: Record<string, ParamValue | ParamValue[]>
}

const buildUrl = (path: string, params?: FetchOptions['params']) => {
  const url = new URL(`${API_BASE}${path}`, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([key, value]) => {
      if (value === undefined) return
      if (Array.isArray(value)) {
        // Join array values as comma-separated string
        const filtered = value.filter((v) => v !== undefined)
        if (filtered.length > 0) {
          url.searchParams.set(key, filtered.map(String).join(','))
        }
      } else {
        url.searchParams.set(key, String(value))
      }
    })
  }
  return url.toString()
}

export async function apiFetch<T>(path: string, options: FetchOptions = {}): Promise<T> {
  const { params, headers, ...rest } = options
  const response = await fetch(buildUrl(path, params), {
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
    ...rest,
  })

  if (!response.ok) {
    const message = await response.text()
    throw new Error(message || `API request failed: ${response.status}`)
  }

  // 204 No Content — nothing to parse
  if (response.status === 204) {
    return null as T
  }

  const contentType = response.headers.get('content-type')
  if (!contentType?.includes('application/json')) {
    // Non-JSON success response (e.g. plain text or empty body)
    const text = await response.text()
    return (text || null) as T
  }
  return response.json() as Promise<T>
}

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
})

export const api = {
  get: <T>(path: string, params?: FetchOptions['params']) => apiFetch<T>(path, { params }),
  post: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: 'PUT', body: body ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    apiFetch<T>(path, { method: 'PATCH', body: body ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => apiFetch<T>(path, { method: 'DELETE' }),
}


