import { useEffect } from 'react'
import { Command } from 'cmdk'
import { Search, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { useSearchStore } from '@/stores/searchStore'
import { useSearch } from '@/hooks/useSearch'
import { useSelectionStore } from '@/stores/selectionStore'

export function SearchBar() {
  const { query, isOpen, setQuery, setOpen } = useSearchStore()
  const { setSelectedFile } = useSelectionStore()
  const { results } = useSearch(query)

  useEffect(() => {
    const down = (e: KeyboardEvent) => {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault()
        setOpen(!isOpen)
      }
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', down)
    return () => document.removeEventListener('keydown', down)
  }, [isOpen, setOpen])

  if (!isOpen) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex h-9 w-full items-center gap-2 rounded-md border bg-muted/50 px-3 text-sm text-muted-foreground hover:bg-muted"
      >
        <Search className="h-4 w-4" />
        <span>Search files...</span>
        <kbd className="ml-auto rounded bg-muted px-1.5 py-0.5 text-xs">&#x2318;K</kbd>
      </button>
    )
  }

  return (
    <div className="relative">
      <Command className="rounded-md border shadow-md" shouldFilter={false}>
        <div className="flex items-center border-b px-3">
          <Search className="mr-2 h-4 w-4 shrink-0 text-muted-foreground" />
          <Command.Input
            value={query}
            onValueChange={setQuery}
            placeholder="Search files..."
            className="flex h-10 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            autoFocus
          />
          <button onClick={() => setOpen(false)}>
            <X className="h-4 w-4 text-muted-foreground" />
          </button>
        </div>
        <Command.List className="max-h-64 overflow-auto p-1">
          {query && results.length === 0 && (
            <Command.Empty className="p-4 text-center text-sm text-muted-foreground">
              No results found.
            </Command.Empty>
          )}
          {results.map((file: { id: number; filename: string; path: string; categories?: { id: number; name: string; color: string | null }[] }) => (
            <Command.Item
              key={file.id}
              value={String(file.id)}
              onSelect={() => {
                setSelectedFile(file.id)
                setOpen(false)
              }}
              className="flex cursor-pointer flex-col gap-0.5 rounded-md px-3 py-2 text-sm hover:bg-accent"
            >
              <span className="font-medium">{file.filename}</span>
              {file.categories && file.categories.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {file.categories.map((cat) => (
                    <Badge key={cat.id} variant="secondary" className="text-xs">
                      {cat.name}
                    </Badge>
                  ))}
                </div>
              )}
            </Command.Item>
          ))}
        </Command.List>
      </Command>
    </div>
  )
}
