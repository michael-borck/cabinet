import { useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Cloud, FileIcon, FolderPlus, MoreHorizontal, Settings } from 'lucide-react'
import { toast } from 'sonner'
import { useAllFiles, useViewFiles } from '@/hooks/useFiles'
import { useViews } from '@/hooks/useViews'
import { useSelectionStore } from '@/stores/selectionStore'
import { useUIStore } from '@/stores/uiStore'
import { useViewBuilderStore } from '@/stores/viewBuilderStore'
import { Button } from '@/components/ui/button'
import { createSource, getLLMConfig, getSources, type SavedView } from '@/services/api'
import { queryKeys } from '@/services/apiClient'
import { cn } from '@/lib/utils'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

interface FileItem {
  id: number
  filename: string
  path: string
  extension: string | null
  size_bytes: number
  status: string
  availability?: string
  cluster_id?: number | null
  version_count?: number
  categories: { id: number; name: string; slug: string; color: string | null }[]
}

function ViewChips({ view, onEdit }: { view: SavedView; onEdit: () => void }) {
  const query = view.query
  const chips: string[] = []
  const conceptCount = query.concepts?.ids?.length ?? 0
  if (conceptCount > 0) {
    chips.push(
      `${conceptCount} concept${conceptCount === 1 ? '' : 's'}${
        query.concepts?.include_descendants === false ? '' : ' (incl. sub)'
      }`
    )
  }
  for (const docType of query.doc_types ?? []) chips.push(`type: ${docType}`)
  for (const topic of query.topics ?? []) chips.push(`topic: ${topic}`)
  if (query.sources?.length) {
    chips.push(`${query.sources.length} source${query.sources.length === 1 ? '' : 's'}`)
  }
  if (query.text) chips.push(`text: “${query.text}”`)
  if (query.clusters !== 'separate') chips.push('versions collapsed')

  return (
    <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
      {chips.length === 0 && (
        <span className="text-xs text-muted-foreground">All documents</span>
      )}
      {chips.map((chip) => (
        <span key={chip} className="rounded-full bg-muted px-2 py-0.5 text-xs">
          {chip}
        </span>
      ))}
      <Button variant="ghost" size="sm" className="h-6 px-2 text-xs" onClick={onEdit}>
        Edit
      </Button>
    </div>
  )
}

export function FileTable() {
  const { selectedCategoryId, selectedViewId } = useSelectionStore()
  const { selectedFileId, setSelectedFile } = useSelectionStore()
  const setSettingsOpen = useUIStore((state) => state.setSettingsOpen)
  const openBuilder = useViewBuilderStore((state) => state.openBuilder)
  const qc = useQueryClient()

  const params: Record<string, unknown> = {}
  if (selectedCategoryId) params.category_id = selectedCategoryId
  const categoryFiles = useAllFiles(params, { enabled: selectedViewId === null })
  const viewFiles = useViewFiles(selectedViewId)
  const { files: loadedFiles, total, isLoading, isError, hasNextPage } =
    selectedViewId !== null ? viewFiles : categoryFiles
  const files = loadedFiles as unknown as FileItem[]

  const { data: views } = useViews()
  const { data: sources } = useQuery({ queryKey: queryKeys.sources, queryFn: getSources })
  const { data: llm } = useQuery({ queryKey: queryKeys.llmConfig, queryFn: getLLMConfig })

  const activeView = (views ?? []).find((view: SavedView) => view.id === selectedViewId)

  const addSource = useMutation({
    mutationFn: async () => {
      const path = await window.api.selectFolder()
      if (!path) return
      return createSource({ path })
    },
    onSuccess: (result) => {
      if (!result) return
      qc.invalidateQueries({ queryKey: queryKeys.sources })
      qc.invalidateQueries({ queryKey: ['files'] })
      toast.success('Folder added — run a sync from Settings → Sources')
    },
    onError: () => toast.error('Failed to add folder'),
  })

  const parentRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: files.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 40,
    overscan: 20,
  })

  if (isLoading) {
    return <div className="flex items-center justify-center p-8 text-muted-foreground">Loading files...</div>
  }

  if (isError && files.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-destructive">
        <AlertTriangle className="mb-2 h-8 w-8" />
        <p>Couldn&apos;t load files — the local engine isn&apos;t responding.</p>
      </div>
    )
  }

  if (files.length === 0) {
    if (selectedViewId !== null) {
      return (
        <div className="flex flex-col items-center justify-center gap-2 p-8 text-muted-foreground">
          <p>No documents match this view.</p>
          {activeView && (
            <Button size="sm" variant="outline" onClick={() => openBuilder(activeView)}>
              Edit filters
            </Button>
          )}
        </div>
      )
    }

    if (sources && sources.length === 0) {
      return (
        <div className="flex flex-col items-center justify-center gap-3 p-8 text-center">
          <div>
            <h2 className="text-lg font-semibold">Welcome to Cabinet</h2>
            <p className="mt-1 max-w-md text-sm text-muted-foreground">
              Add a folder to build a searchable, self-organising library. Files stay exactly
              where they are — Cabinet only keeps a catalogue.
            </p>
          </div>
          <div className="flex gap-2">
            <Button onClick={() => addSource.mutate()} disabled={addSource.isPending}>
              <FolderPlus className="mr-1 h-4 w-4" /> Add a folder
            </Button>
            <Button variant="outline" onClick={() => setSettingsOpen(true)}>
              <Settings className="mr-1 h-4 w-4" /> Settings
            </Button>
          </div>
        </div>
      )
    }

    return (
      <div className="flex flex-col items-center justify-center gap-2 p-8 text-center text-muted-foreground">
        <p>No documents yet.</p>
        <p className="max-w-md text-xs">
          Run a sync from Settings → Sources to index this folder.
          {llm === null && ' Configure an LLM in Settings to categorise files.'}
        </p>
        <Button size="sm" variant="outline" onClick={() => setSettingsOpen(true)}>
          Open settings
        </Button>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      {activeView && (
        <div className="flex items-center gap-2 border-b bg-muted/30 px-4 py-2">
          <span className="shrink-0 text-sm font-medium">{activeView.name}</span>
          <ViewChips view={activeView} onEdit={() => openBuilder(activeView)} />
        </div>
      )}

      {/* Header */}
      <div className="flex items-center border-b bg-muted/50 px-4 py-2 text-xs font-medium text-muted-foreground">
        <span className="flex-1">Name</span>
        <span className="w-24 text-right">Size</span>
        <span className="w-24 text-right">Status</span>
        <span className="w-8" />
      </div>
      {/* Virtual list */}
      <div ref={parentRef} className="flex-1 overflow-auto">
        <div style={{ height: `${virtualizer.getTotalSize()}px`, position: 'relative' }}>
          {virtualizer.getVirtualItems().map((vItem) => {
            const file = files[vItem.index]
            return (
              <div
                key={file.id}
                className={cn(
                  'absolute left-0 right-0 flex cursor-pointer items-center px-4 py-2 text-sm hover:bg-accent/50',
                  selectedFileId === file.id && 'bg-accent'
                )}
                style={{ top: `${vItem.start}px`, height: `${vItem.size}px` }}
                onClick={() => setSelectedFile(file.id)}
              >
                <span className={cn(
                  'flex flex-1 items-center gap-2 truncate',
                  file.availability && file.availability !== 'available' && 'opacity-50'
                )}>
                  <FileIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="truncate">{file.filename}</span>
                  {file.extension && (
                    <span className="text-xs text-muted-foreground">{file.extension}</span>
                  )}
                  {(file.version_count ?? 1) > 1 && (
                    <span className="text-xs text-muted-foreground">
                      {file.version_count} versions
                    </span>
                  )}
                  {file.availability === 'source_unavailable' && (
                    <span className="text-xs text-muted-foreground">(drive not connected)</span>
                  )}
                  {file.availability === 'cloud_placeholder' && (
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      <Cloud className="h-3 w-3" /> (in cloud)
                    </span>
                  )}
                  {file.availability === 'missing' && (
                    <span className="text-xs text-muted-foreground">(missing)</span>
                  )}
                </span>
                <span className="w-24 text-right text-xs text-muted-foreground">
                  {formatSize(file.size_bytes)}
                </span>
                <span className="w-24 text-right">
                  <span className={cn(
                    'inline-block rounded-full px-2 py-0.5 text-xs',
                    file.status === 'categorized' && 'bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300',
                    file.status === 'pending' && 'bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300',
                    file.status === 'error' && 'bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300',
                  )}>
                    {file.status}
                  </span>
                </span>
                <span className="w-8 text-center">
                  <MoreHorizontal className="h-4 w-4 text-muted-foreground" />
                </span>
              </div>
            )
          })}
        </div>
      </div>
      {/* Footer */}
      <div className="border-t bg-muted/50 px-4 py-1.5 text-xs text-muted-foreground">
        {hasNextPage ? `Loading… ${files.length} of ${total} files` : `${total} files`}
      </div>
    </div>
  )
}
