import { useQuery } from '@tanstack/react-query'
import { getHealth } from '@/services/api'

/**
 * Polls GET /health so the UI can tell "backend is down" apart from
 * "no data". `isDown` only flips after the retries are exhausted, so a
 * single dropped request doesn't flash the banner.
 */
export function useBackendHealth() {
  const query = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    refetchInterval: 5000,
    retry: 2,
    retryDelay: 1000,
    refetchIntervalInBackground: true,
  })
  return { isDown: query.isError }
}
