import { useQuery } from '@tanstack/react-query'
import { getJobs } from '@/services/api'
import { queryKeys } from '@/services/apiClient'

export function useSyncStatus() {
  return useQuery({
    queryKey: queryKeys.jobs,
    queryFn: getJobs,
    refetchInterval: 3000,
  })
}
