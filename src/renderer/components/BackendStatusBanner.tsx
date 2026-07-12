import { AlertTriangle } from 'lucide-react'
import { useBackendHealth } from '@/hooks/useBackendHealth'

export function BackendStatusBanner() {
  const { isDown } = useBackendHealth()

  if (!isDown) return null

  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-destructive/50 bg-destructive/10 px-4 py-2 text-sm text-destructive">
      <AlertTriangle className="h-4 w-4 shrink-0" />
      <span>
        Can&apos;t reach Cabinet&apos;s local engine. Data shown may be stale — try restarting the
        app if this persists.
      </span>
    </div>
  )
}
