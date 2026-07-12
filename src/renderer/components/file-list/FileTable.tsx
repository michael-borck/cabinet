import { useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { FileIcon, MoreHorizontal } from 'lucide-react'
import { useAllFiles } from '@/hooks/useFiles'
import { useSelectionStore } from '@/stores/selectionStore'
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
  categories: { id: number; name: string; slug: string; color: string | null }[]
}

export function FileTable() {
  const { selectedCategoryId } = useSelectionStore()
  const { selectedFileId, setSelectedFile } = useSelectionStore()
  const params: Record<string, unknown> = {}
  if (selectedCategoryId) params.category_id = selectedCategoryId
  const { files: loadedFiles, total, isLoading, hasNextPage } = useAllFiles(params)
  const files = loadedFiles as unknown as FileItem[]

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

  if (files.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center p-8 text-muted-foreground">
        <FileIcon className="mb-2 h-8 w-8" />
        <p>No files found</p>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
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
                <span className="flex flex-1 items-center gap-2 truncate">
                  <FileIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="truncate">{file.filename}</span>
                  {file.extension && (
                    <span className="text-xs text-muted-foreground">{file.extension}</span>
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
