import { useEffect, type PropsWithChildren } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { api, queryClient, BASE_PATH } from '@/lib/api'
import type { OverrideStatus, SystemSettings } from '@/types'

/**
 * Kick off prefetches for the most latency-sensitive Dashboard queries the
 * moment the app boots — before the router has even decided which page to
 * render.  React Query deduplicates in-flight requests, so if the Dashboard
 * mounts before these finish it will simply wait on the same promise rather
 * than issuing a second request.
 */
function usePrefetchCriticalQueries() {
  useEffect(() => {
    // Settings — used to hydrate the settings store on every page
    queryClient.prefetchQuery({
      queryKey: ['settings'],
      queryFn: () => api.get<SystemSettings>('/settings'),
    })

    // Override status — drives the Manual Override card (10s poll)
    queryClient.prefetchQuery({
      queryKey: ['override-status'],
      queryFn: () => api.get<OverrideStatus>('/system/override'),
    })

    // Zones — warm the HTTP connection; Dashboard will map and cache the result
    queryClient.prefetchQuery({
      queryKey: ['zones-raw-prefetch'],
      queryFn: () => fetch(`${BASE_PATH}/api/v1/zones`).then((r) => r.json()),
      staleTime: 14_000, // slightly less than the 15s poll interval
    })
  }, [])
}

function PrefetchGate({ children }: PropsWithChildren) {
  usePrefetchCriticalQueries()
  return <>{children}</>
}

export const AppProviders = ({ children }: PropsWithChildren) => {
  return (
    <QueryClientProvider client={queryClient}>
      <PrefetchGate>{children}</PrefetchGate>
    </QueryClientProvider>
  )
}
