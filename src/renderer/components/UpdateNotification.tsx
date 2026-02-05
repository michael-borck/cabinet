import { useEffect, useState } from 'react'
import { Download, RefreshCw, X } from 'lucide-react'
import { Button } from '@/components/ui/button'

type UpdateState = 'idle' | 'available' | 'downloading' | 'downloaded' | 'error'

export function UpdateNotification() {
  const [state, setState] = useState<UpdateState>('idle')
  const [version, setVersion] = useState('')
  const [progress, setProgress] = useState(0)
  const [dismissed, setDismissed] = useState(false)

  useEffect(() => {
    const unsubs = [
      window.api.onUpdateAvailable((info) => {
        setVersion(info.version)
        setState('available')
        setDismissed(false)
      }),
      window.api.onUpdateNotAvailable(() => {
        // No-op, stay idle
      }),
      window.api.onUpdateDownloadProgress((p) => {
        setProgress(Math.round(p.percent))
      }),
      window.api.onUpdateDownloaded(() => {
        setState('downloaded')
      }),
      window.api.onUpdateError(() => {
        setState('error')
      })
    ]

    return () => unsubs.forEach((unsub) => unsub())
  }, [])

  if (state === 'idle' || dismissed) return null

  return (
    <div className="flex items-center gap-2 text-xs">
      {state === 'available' && (
        <>
          <span>Update v{version} available</span>
          <Button
            variant="ghost"
            size="icon"
            className="h-5 w-5"
            onClick={() => {
              setState('downloading')
              window.api.downloadUpdate()
            }}
          >
            <Download className="h-3 w-3" />
          </Button>
        </>
      )}

      {state === 'downloading' && (
        <>
          <span>Downloading update...</span>
          <div className="h-1.5 w-20 rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
          <span>{progress}%</span>
        </>
      )}

      {state === 'downloaded' && (
        <>
          <span>Ready to install v{version}</span>
          <Button
            variant="ghost"
            size="icon"
            className="h-5 w-5"
            onClick={() => window.api.installUpdate()}
          >
            <RefreshCw className="h-3 w-3" />
          </Button>
        </>
      )}

      {state === 'error' && <span>Update failed</span>}

      <Button
        variant="ghost"
        size="icon"
        className="h-5 w-5"
        onClick={() => setDismissed(true)}
      >
        <X className="h-3 w-3" />
      </Button>
    </div>
  )
}
