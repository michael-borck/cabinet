import { useEffect, useMemo, useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import { X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ScrollArea } from '@/components/ui/scroll-area'
import { useCategories } from '@/hooks/useCategories'
import { useCreateView, usePreviewView } from '@/hooks/useViews'
import { useSelectionStore } from '@/stores/selectionStore'
import { getSources, type ViewQuery } from '@/services/api'
import { queryKeys } from '@/services/apiClient'
import { useQuery } from '@tanstack/react-query'

interface FlatCategory {
  id: number
  name: string
  parent_id: number | null
}

function splitValues(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function categoryPath(category: FlatCategory, byId: Map<number, FlatCategory>): string {
  const parts: string[] = []
  let current: FlatCategory | undefined = category
  const seen = new Set<number>()
  while (current && !seen.has(current.id)) {
    seen.add(current.id)
    parts.push(current.name)
    current = current.parent_id !== null ? byId.get(current.parent_id) : undefined
  }
  return parts.reverse().join(' > ')
}

interface ViewBuilderProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function ViewBuilder({ open, onOpenChange }: ViewBuilderProps) {
  const setSelectedView = useSelectionStore((state) => state.setSelectedView)
  const { data: categories } = useCategories('flat')
  const { data: sources } = useQuery({
    queryKey: queryKeys.sources,
    queryFn: getSources,
  })
  const createView = useCreateView()
  const preview = usePreviewView()

  const [name, setName] = useState('')
  const [conceptIds, setConceptIds] = useState<Set<number>>(new Set())
  const [includeDescendants, setIncludeDescendants] = useState(true)
  const [docTypes, setDocTypes] = useState('')
  const [topics, setTopics] = useState('')
  const [sourceId, setSourceId] = useState('')
  const [text, setText] = useState('')
  const [collapse, setCollapse] = useState(true)

  const flatCategories = useMemo(() => {
    const list = (categories ?? []) as FlatCategory[]
    const byId = new Map(list.map((category) => [category.id, category]))
    return list
      .map((category) => ({ ...category, path: categoryPath(category, byId) }))
      .sort((a, b) => a.path.localeCompare(b.path))
  }, [categories])

  const query = useMemo<ViewQuery>(() => {
    const value: ViewQuery = {
      clusters: collapse ? 'collapse' : 'separate',
    }
    if (conceptIds.size > 0) {
      value.concepts = {
        ids: Array.from(conceptIds),
        include_descendants: includeDescendants,
        match: 'any',
      }
    }
    const docTypeValues = splitValues(docTypes)
    if (docTypeValues.length > 0) value.doc_types = docTypeValues
    const topicValues = splitValues(topics)
    if (topicValues.length > 0) value.topics = topicValues
    if (sourceId) value.sources = [Number(sourceId)]
    if (text.trim()) value.text = text.trim()
    return value
  }, [conceptIds, includeDescendants, docTypes, topics, sourceId, text, collapse])

  useEffect(() => {
    if (!open) return
    setName('')
    setConceptIds(new Set())
    setIncludeDescendants(true)
    setDocTypes('')
    setTopics('')
    setSourceId('')
    setText('')
    setCollapse(true)
    preview.reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  const toggleConcept = (id: number) => {
    setConceptIds((current) => {
      const next = new Set(current)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const save = () => {
    const trimmed = name.trim()
    if (!trimmed) {
      toast.error('Give the view a name')
      return
    }
    createView.mutate(
      { name: trimmed, query },
      {
        onSuccess: (view: { id: number }) => {
          toast.success('View saved')
          setSelectedView(view.id)
          onOpenChange(false)
        },
        onError: () => toast.error('Failed to save view'),
      }
    )
  }

  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 max-h-[85vh] w-[540px] -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-lg border bg-background p-6 shadow-lg">
          <Dialog.Title className="text-lg font-semibold">New view</Dialog.Title>
          <p className="mt-1 text-xs text-muted-foreground">
            A view is a saved live filter over your documents. It never copies or moves files.
          </p>

          <div className="mt-4 space-y-3">
            <div>
              <label className="text-xs text-muted-foreground">Name</label>
              <Input value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. Q1 invoices" className="mt-1" />
            </div>

            <div>
              <div className="flex items-center justify-between">
                <label className="text-xs text-muted-foreground">Concepts</label>
                <label className="flex items-center gap-1 text-xs text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={includeDescendants}
                    onChange={(event) => setIncludeDescendants(event.target.checked)}
                  />
                  include sub-concepts
                </label>
              </div>
              <ScrollArea className="mt-1 h-32 rounded-md border">
                <div className="space-y-0.5 p-2">
                  {flatCategories.map((category) => (
                    <label
                      key={category.id}
                      className="flex cursor-pointer items-center gap-2 rounded px-1 py-0.5 text-xs hover:bg-accent"
                    >
                      <input
                        type="checkbox"
                        checked={conceptIds.has(category.id)}
                        onChange={() => toggleConcept(category.id)}
                      />
                      <span className="truncate">{category.path}</span>
                    </label>
                  ))}
                  {flatCategories.length === 0 && (
                    <p className="p-1 text-xs text-muted-foreground">No concepts yet.</p>
                  )}
                </div>
              </ScrollArea>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">Document types</label>
                <Input value={docTypes} onChange={(event) => setDocTypes(event.target.value)} placeholder="invoice, contract" className="mt-1" />
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Topics</label>
                <Input value={topics} onChange={(event) => setTopics(event.target.value)} placeholder="billing, travel" className="mt-1" />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="text-xs text-muted-foreground">Source</label>
                <select
                  value={sourceId}
                  onChange={(event) => setSourceId(event.target.value)}
                  className="mt-1 flex h-10 w-full rounded-md border bg-background px-3 text-sm"
                >
                  <option value="">Any source</option>
                  {(sources ?? []).map((source: { id: number; label: string | null; path: string }) => (
                    <option key={source.id} value={source.id}>
                      {source.label || source.path}
                    </option>
                  ))}
                </select>
              </div>
              <div>
                <label className="text-xs text-muted-foreground">Text contains</label>
                <Input value={text} onChange={(event) => setText(event.target.value)} placeholder="acme" className="mt-1" />
              </div>
            </div>

            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={collapse}
                onChange={(event) => setCollapse(event.target.checked)}
              />
              Collapse versions (one row per version cluster)
            </label>
          </div>

          <div className="mt-5 flex items-center justify-between gap-2">
            <div className="text-xs text-muted-foreground">
              {preview.data ? `${preview.data.total} matching document(s)` : ''}
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" disabled={preview.isPending} onClick={() => preview.mutate(query)}>
                Preview
              </Button>
              <Button size="sm" disabled={createView.isPending} onClick={save}>
                Save view
              </Button>
            </div>
          </div>

          <Dialog.Close asChild>
            <button className="absolute right-4 top-4" aria-label="Close">
              <X className="h-4 w-4" />
            </button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
