import { ChevronRight, ChevronDown, FolderOpen, Folder, FolderPlus, Pencil, Plus, Trash2 } from 'lucide-react'
import { useState } from 'react'
import { toast } from 'sonner'
import * as ContextMenu from '@radix-ui/react-context-menu'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  useCategories,
  useCreateCategory,
  useDeleteCategory,
  useUpdateCategory,
} from '@/hooks/useCategories'
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

const menuItemClass =
  'flex cursor-pointer items-center gap-2 rounded px-2 py-1.5 text-sm outline-none data-[highlighted]:bg-accent'

function InlineNameInput({
  initial,
  placeholder,
  onSubmit,
  onCancel,
}: {
  initial: string
  placeholder: string
  onSubmit: (name: string) => void
  onCancel: () => void
}) {
  const [name, setName] = useState(initial)
  return (
    <Input
      autoFocus
      value={name}
      onChange={(e) => setName(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          const trimmed = name.trim()
          if (trimmed && trimmed !== initial) onSubmit(trimmed)
          else onCancel()
        }
        if (e.key === 'Escape') onCancel()
      }}
      onBlur={onCancel}
      placeholder={placeholder}
      className="h-7 text-sm"
    />
  )
}

function TreeNode({ node, depth = 0 }: { node: CategoryNode; depth?: number }) {
  const [expanded, setExpanded] = useState(true)
  const [editing, setEditing] = useState(false)
  const [addingChild, setAddingChild] = useState(false)
  const { selectedCategoryId, setSelectedCategory } = useSelectionStore()
  const updateCat = useUpdateCategory()
  const deleteCat = useDeleteCategory()
  const createCat = useCreateCategory()
  const isSelected = selectedCategoryId === node.id
  const hasChildren = node.children && node.children.length > 0

  const handleDelete = () => {
    const detail = node.file_count > 0 ? `${node.file_count} file assignment(s) will be removed. ` : ''
    if (!window.confirm(`Delete "${node.name}"? ${detail}Subcategories move up one level.`)) return
    deleteCat.mutate(node.id, {
      onSuccess: () => {
        if (isSelected) setSelectedCategory(null)
      },
      onError: () => toast.error('Failed to delete category'),
    })
  }

  return (
    <div>
      {editing ? (
        <div className="py-0.5 pr-2" style={{ paddingLeft: `${depth * 16 + 8}px` }}>
          <InlineNameInput
            initial={node.name}
            placeholder="Category name…"
            onCancel={() => setEditing(false)}
            onSubmit={(name) => {
              setEditing(false)
              updateCat.mutate(
                { id: node.id, data: { name } },
                { onError: () => toast.error('Failed to rename category') }
              )
            }}
          />
        </div>
      ) : (
        <ContextMenu.Root>
          <ContextMenu.Trigger asChild>
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
          </ContextMenu.Trigger>
          <ContextMenu.Portal>
            <ContextMenu.Content className="z-50 min-w-[180px] rounded-md border bg-background p-1 shadow-md">
              <ContextMenu.Item className={menuItemClass} onSelect={() => setEditing(true)}>
                <Pencil className="h-3.5 w-3.5" /> Rename
              </ContextMenu.Item>
              <ContextMenu.Item
                className={menuItemClass}
                onSelect={() => { setAddingChild(true); setExpanded(true) }}
              >
                <FolderPlus className="h-3.5 w-3.5" /> New subcategory
              </ContextMenu.Item>
              <ContextMenu.Separator className="my-1 h-px bg-border" />
              <ContextMenu.Item
                className={cn(menuItemClass, 'text-destructive data-[highlighted]:text-destructive')}
                // Let the menu finish closing before the blocking confirm().
                onSelect={() => setTimeout(handleDelete, 0)}
              >
                <Trash2 className="h-3.5 w-3.5" /> Delete
              </ContextMenu.Item>
            </ContextMenu.Content>
          </ContextMenu.Portal>
        </ContextMenu.Root>
      )}
      {addingChild && (
        <div className="py-0.5 pr-2" style={{ paddingLeft: `${(depth + 1) * 16 + 8}px` }}>
          <InlineNameInput
            initial=""
            placeholder="New subcategory…"
            onCancel={() => setAddingChild(false)}
            onSubmit={(name) => {
              setAddingChild(false)
              createCat.mutate(
                { name, parent_id: node.id },
                { onError: () => toast.error('Failed to create category') }
              )
            }}
          />
        </div>
      )}
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
  const createCat = useCreateCategory()

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
          <InlineNameInput
            initial=""
            placeholder="New category name…"
            onCancel={() => setAdding(false)}
            onSubmit={(name) => {
              setAdding(false)
              createCat.mutate(
                { name },
                { onError: () => toast.error('Failed to create category') }
              )
            }}
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
