import { ExternalLink, FolderOpen, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useFile, useRemoveFileCategory } from '@/hooks/useFiles'
import { useSelectionStore } from '@/stores/selectionStore'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

export function FileDetailsPanel() {
  const { selectedFileId, setSelectedFile } = useSelectionStore()
  const { data: file } = useFile(selectedFileId)
  const removeCat = useRemoveFileCategory()

  if (!selectedFileId || !file) {
    return (
      <div className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
        Select a file to view details
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="text-sm font-semibold truncate">{file.filename}</span>
        <Button variant="ghost" size="icon" className="h-7 w-7 shrink-0" onClick={() => setSelectedFile(null)}>
          <X className="h-4 w-4" />
        </Button>
      </div>
      <ScrollArea className="flex-1">
        <div className="space-y-4 p-3">
          {/* Metadata */}
          <div className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Size</span>
              <span>{formatSize(file.size_bytes)}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Type</span>
              <span>{file.extension || 'Unknown'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Status</span>
              <span>{file.status}</span>
            </div>
          </div>

          {/* Actions */}
          <div className="flex gap-2">
            <Button variant="outline" size="sm" onClick={() => window.api.revealInFinder(file.path)}>
              <FolderOpen className="mr-1 h-3 w-3" /> Reveal
            </Button>
            <Button variant="outline" size="sm" onClick={() => window.api.openFile(file.path)}>
              <ExternalLink className="mr-1 h-3 w-3" /> Open
            </Button>
          </div>

          {/* Path */}
          <div>
            <span className="text-xs font-medium text-muted-foreground">Path</span>
            <p className="mt-1 break-all text-xs">{file.path}</p>
          </div>

          {/* Categories */}
          <div>
            <span className="text-xs font-medium text-muted-foreground">Categories</span>
            <div className="mt-1 flex flex-wrap gap-1">
              {file.categories?.map((cat: { id: number; name: string }) => (
                <Badge key={cat.id} variant="secondary" className="gap-1">
                  {cat.name}
                  <button
                    className="ml-0.5 hover:text-destructive"
                    onClick={() => removeCat.mutate({ fileId: file.id, categoryId: cat.id })}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
              {(!file.categories || file.categories.length === 0) && (
                <span className="text-xs text-muted-foreground">No categories</span>
              )}
            </div>
          </div>

        </div>
      </ScrollArea>
    </div>
  )
}
