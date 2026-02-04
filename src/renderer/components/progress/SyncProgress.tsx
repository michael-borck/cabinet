import { Loader2 } from 'lucide-react'
import { useSyncStatus } from '@/hooks/useSyncStatus'

export function SyncProgress() {
  const { data: jobs } = useSyncStatus()
  const activeJob = jobs?.find((j: { status: string }) => j.status === 'running' || j.status === 'pending')

  if (!activeJob) return null

  const pct = activeJob.total_files > 0
    ? Math.round((activeJob.processed_files / activeJob.total_files) * 100)
    : 0

  return (
    <div className="flex items-center gap-2 text-xs">
      <Loader2 className="h-3 w-3 animate-spin" />
      <span>Syncing {activeJob.processed_files}/{activeJob.total_files}</span>
      <div className="h-1.5 w-20 rounded-full bg-muted">
        <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
