import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { getJobs } from '@/services/api'
import { queryKeys } from '@/services/apiClient'

interface JobStatus {
  id: number
  status: string
  error_count: number
}

const TERMINAL_STATUSES = new Set(['completed', 'completed_with_errors', 'failed', 'cancelled'])

export function useSyncStatus() {
  const qc = useQueryClient()
  const query = useQuery({
    queryKey: queryKeys.jobs,
    queryFn: getJobs,
    refetchInterval: 3000,
  })

  // Track each job's last-seen status so we can invalidate files/categories
  // exactly once when a job transitions into a terminal state. null until the
  // first fetch: jobs in that snapshot are historical, not transitions.
  const prevStatuses = useRef<Map<number, string> | null>(null)

  const jobs = query.data as JobStatus[] | undefined
  useEffect(() => {
    if (!jobs) return
    const next = new Map<number, string>()
    for (const job of jobs) next.set(job.id, job.status)
    const prev = prevStatuses.current
    prevStatuses.current = next
    if (prev === null) return
    let jobFinished = false
    for (const job of jobs) {
      // A job that is terminal now and wasn't in the previous snapshot started
      // after mount and finished within one poll interval — still a transition.
      if (TERMINAL_STATUSES.has(job.status) && prev.get(job.id) !== job.status) {
        jobFinished = true
        if (job.status === 'failed') {
          toast.error('Sync failed — check your LLM settings and try again')
        } else if (job.status === 'completed_with_errors') {
          toast.warning(
            `Sync finished, but ${job.error_count} file${job.error_count === 1 ? '' : 's'} could not be processed`
          )
        }
      }
    }
    if (jobFinished) {
      qc.invalidateQueries({ queryKey: ['files'] })
      qc.invalidateQueries({ queryKey: ['categories'] })
      qc.invalidateQueries({ queryKey: queryKeys.jobs })
    }
  }, [jobs, qc])

  return query
}
