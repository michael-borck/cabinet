import { ChevronRight, ChevronDown, FolderOpen, Folder, Plus } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useCategories, useCreateCategory } from '@/hooks/useCategories'
import { useSelectionStore } from '@/stores/selectionStore'
import { cn } from '@/lib/utils'

interface CategoryNode {
  id: number
  name: string
  slug: string
  parent_id: number | null
  file_count: number
  children: CategoryNode[]
}

function TreeNode({ node, depth = 0 }: { node: CategoryNode; depth?: number }) {
  const [expanded, setExpanded] = useState(true)
  const { selectedCategoryId, setSelectedCategory } = useSelectionStore()
  const isSelected = selectedCategoryId === node.id
  const hasChildren = node.children && node.children.length > 0

  return (
    <div>
      <button
        className={cn(
          'flex w-full items-center gap-1 rounded-md px-2 py-1.5 text-sm hover:bg-accent',
          isSelected && 'bg-accent text-accent-foreground font-medium'
        )}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={() => setSelectedCategory(node.id)}
      >
        {hasChildren ? (
          <span
            onClick={(e) => { e.stopPropagation(); setExpanded(!expanded) }}
            className="shrink-0"
          >
            {expanded ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
          </span>
        ) : (
          <span className="w-4 shrink-0" />
        )}
        {expanded && hasChildren ? (
          <FolderOpen className="h-4 w-4 shrink-0 text-muted-foreground" />
        ) : (
          <Folder className="h-4 w-4 shrink-0 text-muted-foreground" />
        )}
        <span className="truncate">{node.name}</span>
        {node.file_count > 0 && (
          <span className="ml-auto text-xs text-muted-foreground">{node.file_count}</span>
        )}
      </button>
      {expanded && hasChildren && (
        <div>
          {node.children.map((child) => (
            <TreeNode key={child.id} node={child} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  )
}

export function CategoryTree() {
  const { data: categories, isLoading } = useCategories('tree')
  const { selectedCategoryId, setSelectedCategory } = useSelectionStore()
  const [adding, setAdding] = useState(false)
  const [newName, setNewName] = useState('')
  const createCat = useCreateCategory()

  const cancelAdd = () => {
    setAdding(false)
    setNewName('')
  }

  const submitAdd = () => {
    const name = newName.trim()
    if (!name) {
      cancelAdd()
      return
    }
    createCat.mutate(
      { name },
      {
        onSuccess: () => cancelAdd(),
        onError: () => toast.error('Failed to create category'),
      }
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="text-sm font-semibold">Categories</span>
        <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setAdding(true)}>
          <Plus className="h-4 w-4" />
        </Button>
      </div>
      {adding && (
        <div className="border-b px-3 py-2">
          <Input
            autoFocus
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submitAdd()
              if (e.key === 'Escape') cancelAdd()
            }}
            onBlur={cancelAdd}
            placeholder="New category name…"
            className="h-7 text-sm"
            disabled={createCat.isPending}
          />
        </div>
      )}
      <ScrollArea className="flex-1">
        <div className="p-2">
          <button
            className={cn(
              'flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm hover:bg-accent',
              selectedCategoryId === null && 'bg-accent text-accent-foreground font-medium'
            )}
            onClick={() => setSelectedCategory(null)}
          >
            <FolderOpen className="h-4 w-4 text-muted-foreground" />
            All Files
          </button>
          {isLoading && <div className="px-2 py-4 text-sm text-muted-foreground">Loading...</div>}
          {categories?.map((cat: CategoryNode) => (
            <TreeNode key={cat.id} node={cat} />
          ))}
        </div>
      </ScrollArea>
    </div>
  )
}
