import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import * as Tabs from '@radix-ui/react-tabs'
import { Settings, X, Folder, Brain, RefreshCw, ListFilter, ListRestart, Eye, EyeOff, Sparkles, FileText, Inbox, BarChart3, Download, Upload, FolderOutput } from 'lucide-react'
import { isAxiosError } from 'axios'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getSources, createSource, updateSource, deleteSource, scanSource, getLLMConfig, setLLMConfig, testLLMConfig, getEmbeddingConfig, setEmbeddingConfig, testEmbeddingConfig, getCategoryReview, approveCategory, rejectCategory, mergeCategory, consolidateCategories, updateCategory, proposeTaxonomy, applyTaxonomyProposal, getTaxonomyInsights, exportCuration, importCuration, planSymlinkExport, applySymlinkExport, planFinderTags, applyFinderTags, planObsidianExport, applyObsidianExport, type SymlinkTreePlan, type SymlinkTreeResult, type SymlinkTreeRequest, type FinderTagsPlan, type FinderTagsResult, type VaultExportPlan, type VaultExportResult, type VaultExportRequest } from '@/services/api'
import { useViews } from '@/hooks/useViews'
import { queryKeys } from '@/services/apiClient'
import { useSelectionStore } from '@/stores/selectionStore'
import { useUIStore } from '@/stores/uiStore'
import { toast } from 'sonner'

function errorDetail(err: unknown): string {
  if (isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    return err.message
  }
  return err instanceof Error ? err.message : 'Unknown error'
}

interface SourceRow {
  id: number
  path: string
  label: string | null
  exclude_patterns: string[]
  watch_enabled: boolean
  store_facts: boolean
  store_text_index: boolean
  availability: string
}

function SourcesTab() {
  const qc = useQueryClient()
  const setSelectedFile = useSelectionStore((s) => s.setSelectedFile)
  const { data: sources } = useQuery({ queryKey: queryKeys.sources, queryFn: getSources })
  // Which source's exclusion editor is open, and the in-progress text.
  const [editingId, setEditingId] = useState<number | null>(null)
  const [patternsText, setPatternsText] = useState('')
  const addSource = useMutation({
    mutationFn: async () => {
      const path = await window.api.selectFolder()
      if (!path) return
      return createSource({ path })
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: queryKeys.sources }) },
    onError: (err) => { toast.error(`Failed to add folder: ${errorDetail(err)}`) },
  })
  const rmSource = useMutation({
    mutationFn: deleteSource,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.sources })
      qc.invalidateQueries({ queryKey: ['files'] })
      qc.invalidateQueries({ queryKey: ['categories'] })
      // The selected file may belong to the deleted source; clear it.
      setSelectedFile(null)
    },
    onError: (err) => { toast.error(`Failed to remove folder: ${errorDetail(err)}`) },
  })
  const scanMut = useMutation({
    mutationFn: ({ id, mode }: { id: number; mode?: string }) => scanSource(id, mode),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: queryKeys.jobs })
      toast.success(vars.mode === 'full_reindex' ? 'Full reindex started' : 'Scan started')
    },
    onError: (err) => { toast.error(`Failed to start scan: ${errorDetail(err)}`) },
  })
  const fullReindex = (s: SourceRow) => {
    const ok = window.confirm(
      `Re-extract and re-categorize every file in "${s.label || s.path}"?\n\n` +
        'This re-runs the AI over all files (including ones that previously errored) ' +
        'and may take a while. Manually added categories are kept.'
    )
    if (ok) scanMut.mutate({ id: s.id, mode: 'full_reindex' })
  }
  const watchMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      updateSource(id, { watch_enabled: enabled }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: queryKeys.sources }) },
    onError: (err) => { toast.error(`Failed to update watching: ${errorDetail(err)}`) },
  })
  const factsMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      updateSource(id, { store_facts: enabled }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: queryKeys.sources }) },
    onError: (err) => { toast.error(`Failed to update facts storage: ${errorDetail(err)}`) },
  })
  const textIndexMut = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      updateSource(id, { store_text_index: enabled }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: queryKeys.sources }) },
    onError: (err) => { toast.error(`Failed to update full-text index: ${errorDetail(err)}`) },
  })
  const excludesMut = useMutation({
    mutationFn: ({ id, patterns }: { id: number; patterns: string[] }) =>
      updateSource(id, { exclude_patterns: patterns }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.sources })
      setEditingId(null)
      toast.success('Exclusions saved — applied on next scan')
    },
    onError: (err) => { toast.error(`Failed to save exclusions: ${errorDetail(err)}`) },
  })

  const toggleEditor = (s: SourceRow) => {
    if (editingId === s.id) {
      setEditingId(null)
    } else {
      setEditingId(s.id)
      setPatternsText(s.exclude_patterns.join(', '))
    }
  }
  const saveExcludes = (id: number) => {
    const patterns = patternsText.split(/[,\n]/).map((p) => p.trim()).filter(Boolean)
    excludesMut.mutate({ id, patterns })
  }

  const handleExportCuration = async () => {
    try {
      const data = await exportCuration()
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `cabinet-curation-${new Date().toISOString().slice(0, 10)}.json`
      anchor.click()
      URL.revokeObjectURL(url)
      toast.success('Curation exported')
    } catch (err) {
      toast.error(`Failed to export curation: ${errorDetail(err)}`)
    }
  }

  const handleImportCuration = async (file: File) => {
    try {
      const payload = JSON.parse(await file.text())
      const result = await importCuration(payload)
      toast.success(
        `Imported ${result.assignments_applied} assignments, ${result.concepts_created} concepts, ${result.views_created} views`
      )
      qc.invalidateQueries({ queryKey: queryKeys.sources })
      qc.invalidateQueries({ queryKey: ['files'] })
      qc.invalidateQueries({ queryKey: ['categories'] })
      qc.invalidateQueries({ queryKey: ['views'] })
    } catch (err) {
      toast.error(`Failed to import curation: ${errorDetail(err)}`)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Watched Folders</h3>
        <Button size="sm" onClick={() => addSource.mutate()}>
          <Folder className="mr-1 h-3 w-3" /> Add Folder
        </Button>
      </div>
      <div className="space-y-2">
        {sources?.map((s: SourceRow) => (
          <div key={s.id} className="rounded-md border p-2 text-sm">
            <div className="flex items-center justify-between">
              <span className="truncate">
                {s.label || s.path}
                {s.availability === 'unavailable' && (
                  <span className="ml-2 text-xs text-muted-foreground">(not connected)</span>
                )}
              </span>
              <div className="flex shrink-0 gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  title={s.watch_enabled ? 'Stop watching for changes' : 'Watch for changes'}
                  className={s.watch_enabled ? 'text-primary' : undefined}
                  disabled={watchMut.isPending}
                  onClick={() => watchMut.mutate({ id: s.id, enabled: !s.watch_enabled })}
                >
                  {s.watch_enabled ? <Eye className="h-3 w-3" /> : <EyeOff className="h-3 w-3" />}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  title={s.store_facts
                    ? 'Stop storing AI summaries/facts for this folder (purges unreferenced facts)'
                    : 'Store AI summaries/facts for this folder'}
                  className={s.store_facts ? 'text-primary' : undefined}
                  disabled={factsMut.isPending}
                  onClick={() => factsMut.mutate({ id: s.id, enabled: !s.store_facts })}
                >
                  <Sparkles className="h-3 w-3" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  title={s.store_text_index
                    ? 'Stop indexing full text for this folder (purges unreferenced text)'
                    : 'Index full text for search (stores extracted text locally)'}
                  className={s.store_text_index ? 'text-primary' : undefined}
                  disabled={textIndexMut.isPending}
                  onClick={() => textIndexMut.mutate({ id: s.id, enabled: !s.store_text_index })}
                >
                  <FileText className="h-3 w-3" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  title="Edit exclusions"
                  className={s.exclude_patterns.length > 0 ? 'text-primary' : undefined}
                  onClick={() => toggleEditor(s)}
                >
                  <ListFilter className="h-3 w-3" />
                </Button>
                <Button variant="ghost" size="sm" title="Rescan" disabled={scanMut.isPending} onClick={() => scanMut.mutate({ id: s.id })}>
                  <RefreshCw className="h-3 w-3" />
                </Button>
                <Button variant="ghost" size="sm" title="Full reindex (re-categorize everything)" disabled={scanMut.isPending} onClick={() => fullReindex(s)}>
                  <ListRestart className="h-3 w-3" />
                </Button>
                <Button variant="ghost" size="sm" title="Remove folder" onClick={() => rmSource.mutate(s.id)}>
                  <X className="h-3 w-3" />
                </Button>
              </div>
            </div>
            {editingId === s.id && (
              <div className="mt-2 space-y-1">
                <div className="flex gap-2">
                  <Input
                    value={patternsText}
                    onChange={(e) => setPatternsText(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') saveExcludes(s.id) }}
                    placeholder="e.g. *.log, drafts, projects/old*"
                    className="h-8 text-xs"
                  />
                  <Button size="sm" disabled={excludesMut.isPending} onClick={() => saveExcludes(s.id)}>
                    Save
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground">
                  Comma-separated glob patterns matched against names and paths relative to this
                  folder. Hidden files and common development folders (node_modules, __pycache__,
                  venv, …) are always skipped.
                </p>
              </div>
            )}
          </div>
        ))}
        {(!sources || sources.length === 0) && (
          <p className="text-sm text-muted-foreground">No folders added yet.</p>
        )}
      </div>

      <div className="space-y-2 border-t pt-4">
        <h3 className="text-sm font-medium">Curation backup</h3>
        <p className="text-xs text-muted-foreground">
          Export manual assignments, concepts, aliases, merges, and saved views to a
          portable JSON file. Cabinet also writes one next to its database when it
          exits, so curation can be restored after a rebuild.
        </p>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={handleExportCuration}>
            <Download className="mr-1 h-3 w-3" /> Export JSON
          </Button>
          <label className="inline-flex h-8 cursor-pointer items-center rounded-md border px-3 text-xs hover:bg-accent">
            <Upload className="mr-1 h-3 w-3" /> Import JSON
            <input
              type="file"
              accept="application/json,.json"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) void handleImportCuration(file)
                event.target.value = ''
              }}
            />
          </label>
        </div>
      </div>
    </div>
  )
}

interface LLMConfig {
  id: number
  provider: string
  model: string
  base_url: string | null
  api_key_set: boolean
}

function LLMForm({ config }: { config: LLMConfig | null }) {
  const qc = useQueryClient()
  const [provider, setProvider] = useState(config?.provider || 'openai')
  const [model, setModel] = useState(config?.model || 'gpt-4o-mini')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState(config?.base_url || '')

  // The backend only carries the stored key over when the provider is
  // unchanged; if the user switches provider, a blank key really means "no key".
  const keyIsSaved = (config?.api_key_set ?? false) && provider === config?.provider

  const saveMut = useMutation({
    mutationFn: () => setLLMConfig({ provider, model, api_key: apiKey || undefined, base_url: baseUrl || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.llmConfig })
      toast.success('LLM config saved')
    },
    onError: (err) => { toast.error(`Failed to save config: ${errorDetail(err)}`) },
  })
  const testMut = useMutation({
    mutationFn: () => testLLMConfig({ provider, model, api_key: apiKey || undefined, base_url: baseUrl || undefined }),
    onSuccess: (r) => { r.success ? toast.success('Connection successful') : toast.error(r.message) },
    onError: (err) => { toast.error(`Connection test failed: ${errorDetail(err)}`) },
  })

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-medium">LLM Provider</h3>
      <div className="space-y-3">
        <div>
          <label className="text-xs text-muted-foreground">Provider</label>
          <select value={provider} onChange={(e) => setProvider(e.target.value)}
            className="mt-1 flex h-10 w-full rounded-md border bg-background px-3 text-sm">
            <option value="openai">OpenAI</option>
            <option value="anthropic">Anthropic</option>
            <option value="gemini">Google Gemini</option>
            <option value="ollama">Ollama (Local)</option>
          </select>
        </div>
        <div>
          <label className="text-xs text-muted-foreground">Model</label>
          <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. gpt-4o-mini" className="mt-1" />
        </div>
        <div>
          <label className="text-xs text-muted-foreground">API Key</label>
          <Input
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={keyIsSaved ? '••••••••  (saved — leave blank to keep)' : 'sk-...'}
            className="mt-1"
          />
          <p className="mt-1 text-[10px] text-muted-foreground">
            Stored locally in Cabinet&apos;s database; OS keychain storage is pending.
          </p>
        </div>
        {(provider === 'ollama' || provider === 'openai') && (
          <div>
            <label className="text-xs text-muted-foreground">Base URL</label>
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://localhost:11434/v1" className="mt-1" />
          </div>
        )}
        <div className="flex gap-2">
          <Button size="sm" disabled={saveMut.isPending} onClick={() => saveMut.mutate()}>Save</Button>
          <Button size="sm" variant="outline" disabled={testMut.isPending} onClick={() => testMut.mutate()}>
            Test Connection
          </Button>
        </div>
      </div>
    </div>
  )
}

function EmbeddingForm({ config }: { config: LLMConfig | null }) {
  const qc = useQueryClient()
  const [provider, setProvider] = useState(config?.provider || 'ollama')
  const [model, setModel] = useState(config?.model || 'nomic-embed-text')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState(config?.base_url || 'http://localhost:11434/v1')

  const keyIsSaved = (config?.api_key_set ?? false) && provider === config?.provider

  const saveMut = useMutation({
    mutationFn: () => setEmbeddingConfig({ provider, model, api_key: apiKey || undefined, base_url: baseUrl || undefined }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.embeddingConfig })
      toast.success('Embedding config saved')
    },
    onError: (err) => { toast.error(`Failed to save embedding config: ${errorDetail(err)}`) },
  })
  const testMut = useMutation({
    mutationFn: () => testEmbeddingConfig({ provider, model, api_key: apiKey || undefined, base_url: baseUrl || undefined }),
    onSuccess: (r: { success: boolean; message: string }) => {
      r.success ? toast.success(r.message) : toast.error(r.message)
    },
    onError: (err) => { toast.error(`Embedding test failed: ${errorDetail(err)}`) },
  })

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-medium">Embeddings (optional)</h3>
        <p className="text-xs text-muted-foreground">
          One vector per document for organisation and similar-file grouping. Anthropic has no
          embeddings API, so it is configured separately — local Ollama with nomic-embed-text is
          the default.
        </p>
      </div>
      <div>
        <label className="text-xs text-muted-foreground">Provider</label>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}
          className="mt-1 flex h-10 w-full rounded-md border bg-background px-3 text-sm">
          <option value="ollama">Ollama (Local)</option>
          <option value="openai">OpenAI</option>
          <option value="gemini">Google Gemini</option>
        </select>
      </div>
      <div>
        <label className="text-xs text-muted-foreground">Embedding model</label>
        <Input value={model} onChange={(e) => setModel(e.target.value)} placeholder="nomic-embed-text" className="mt-1" />
      </div>
      <div>
        <label className="text-xs text-muted-foreground">API Key</label>
        <Input
          type="password"
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          placeholder={keyIsSaved ? '••••••••  (saved — leave blank to keep)' : 'optional for Ollama'}
          className="mt-1"
        />
      </div>
      {(provider === 'ollama' || provider === 'openai') && (
        <div>
          <label className="text-xs text-muted-foreground">Base URL</label>
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://localhost:11434/v1" className="mt-1" />
        </div>
      )}
      <div className="flex gap-2">
        <Button size="sm" disabled={saveMut.isPending} onClick={() => saveMut.mutate()}>Save</Button>
        <Button size="sm" variant="outline" disabled={testMut.isPending} onClick={() => testMut.mutate()}>
          Test Embedding
        </Button>
      </div>
    </div>
  )
}

function EmbeddingSection() {
  const { data: config, isPending, isError } = useQuery({ queryKey: queryKeys.embeddingConfig, queryFn: getEmbeddingConfig })

  if (isPending) {
    return <p className="text-sm text-muted-foreground">Loading embedding config…</p>
  }
  if (isError) {
    return <p className="text-sm text-destructive">Failed to load embedding configuration.</p>
  }
  return <EmbeddingForm key={config ? `cfg-${config.id}` : 'new'} config={config ?? null} />
}

function LLMTab() {
  const { data: config, isPending, isError } = useQuery({ queryKey: queryKeys.llmConfig, queryFn: getLLMConfig })

  // Don't render the form until the saved config has loaded, otherwise the
  // form state would be initialized from hardcoded defaults and Save could
  // clobber a working config. GET /settings/llm returns null when unset.
  if (isPending) {
    return <p className="text-sm text-muted-foreground">Loading configuration…</p>
  }
  if (isError) {
    return <p className="text-sm text-destructive">Failed to load LLM configuration.</p>
  }

  // Key the form on the config identity so it re-initializes when a new
  // config is saved/loaded, while leaving in-progress edits alone otherwise.
  return (
    <div className="space-y-6">
      <LLMForm key={config ? `cfg-${config.id}` : 'new'} config={config ?? null} />
      <div className="border-t pt-4">
        <EmbeddingSection />
      </div>
    </div>
  )
}

interface ReviewItem {
  id: number
  name: string
  path: string
  definition: string | null
  origin: string
  document_count: number
  samples: { file_id: number; filename: string; summary: string | null }[]
  possible_duplicates: {
    category_id: number
    name: string
    path: string
    similarity: number
  }[]
}

interface MergeProposal {
  source_id: number
  source_name: string
  target_id: number
  target_name: string
  similarity: number | null
  reason: string | null
  origin: string
}

interface RenameProposal {
  category_id: number
  current_name: string
  proposed_name: string
  reason: string | null
  origin: string
}

interface ConsolidationResult {
  merges: MergeProposal[]
  renames: RenameProposal[]
}

function ReviewTab() {
  const qc = useQueryClient()
  const [proposals, setProposals] = useState<ConsolidationResult | null>(null)
  const { data, isPending, isError } = useQuery({
    queryKey: queryKeys.categoryReview,
    queryFn: getCategoryReview,
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: queryKeys.categoryReview })
    qc.invalidateQueries({ queryKey: queryKeys.categories() })
    qc.invalidateQueries({ queryKey: ['files'] })
  }
  const approve = useMutation({
    mutationFn: (id: number) => approveCategory(id),
    onSuccess: () => { invalidate(); toast.success('Concept approved') },
    onError: (err) => { toast.error(`Failed to approve: ${errorDetail(err)}`) },
  })
  const reject = useMutation({
    mutationFn: (id: number) => rejectCategory(id),
    onSuccess: () => { invalidate(); toast.success('Concept rejected') },
    onError: (err) => { toast.error(`Failed to reject: ${errorDetail(err)}`) },
  })
  const merge = useMutation({
    mutationFn: ({ source, target }: { source: number; target: number }) =>
      mergeCategory(source, target),
    onSuccess: () => {
      invalidate()
      setProposals(null)
      toast.success('Concepts merged')
    },
    onError: (err) => { toast.error(`Failed to merge: ${errorDetail(err)}`) },
  })
  const rename = useMutation({
    mutationFn: ({ id, name }: { id: number; name: string }) =>
      updateCategory(id, { name }),
    onSuccess: () => {
      invalidate()
      setProposals(null)
      toast.success('Concept renamed')
    },
    onError: (err) => { toast.error(`Failed to rename: ${errorDetail(err)}`) },
  })
  const consolidate = useMutation({
    mutationFn: consolidateCategories,
    onSuccess: (result) => setProposals(result as ConsolidationResult),
    onError: (err) => { toast.error(`Consolidation failed: ${errorDetail(err)}`) },
  })

  if (isPending) {
    return <p className="text-sm text-muted-foreground">Loading review queue…</p>
  }
  if (isError) {
    return <p className="text-sm text-destructive">Failed to load the review queue.</p>
  }

  const items = (data ?? []) as ReviewItem[]

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-medium">Provisional concepts</h3>
        <p className="text-xs text-muted-foreground">
          Approve a concept to keep it, or reject it to remove the concept and its
          assignments. A merge is an explicit action; nothing consolidates on its own.
        </p>
      </div>
      {items.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No concepts are awaiting review.
        </p>
      )}
      {items.map((item) => (
        <div key={item.id} className="rounded-md border p-3 text-sm">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="truncate font-medium">{item.path}</div>
              <div className="text-xs text-muted-foreground">
                {item.document_count} document{item.document_count === 1 ? '' : 's'}
              </div>
            </div>
            <div className="flex shrink-0 gap-1">
              <Button size="sm" disabled={approve.isPending} onClick={() => approve.mutate(item.id)}>
                Approve
              </Button>
              <Button size="sm" variant="outline" disabled={reject.isPending} onClick={() => reject.mutate(item.id)}>
                Reject
              </Button>
            </div>
          </div>
          {item.definition && <p className="mt-2 text-xs">{item.definition}</p>}
          {item.samples.length > 0 && (
            <ul className="mt-2 space-y-0.5 text-xs text-muted-foreground">
              {item.samples.map((sample) => (
                <li key={sample.file_id} className="truncate">
                  {sample.filename}
                  {sample.summary ? ` — ${sample.summary}` : ''}
                </li>
              ))}
            </ul>
          )}
          {item.possible_duplicates.length > 0 && (
            <div className="mt-2 space-y-1 text-xs">
              <span className="text-muted-foreground">Possible duplicates:</span>
              {item.possible_duplicates.map((dup) => (
                <div key={dup.category_id} className="flex items-center justify-between gap-2">
                  <Badge variant="secondary">
                    {dup.path} ({Math.round(dup.similarity * 100)}%)
                  </Badge>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={merge.isPending}
                    onClick={() => {
                      if (window.confirm(`Merge "${item.path}" into "${dup.path}"? This cannot be undone automatically.`)) {
                        merge.mutate({ source: item.id, target: dup.category_id })
                      }
                    }}
                  >
                    Merge
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      ))}

      <div className="space-y-2 border-t pt-4">
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3 className="text-sm font-medium">Consolidation suggestions</h3>
            <p className="text-xs text-muted-foreground">
              Deterministic duplicate detection plus an optional LLM review. Nothing is
              applied until you choose Apply.
            </p>
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={consolidate.isPending}
            onClick={() => consolidate.mutate()}
          >
            {consolidate.isPending ? 'Analyzing…' : 'Analyze taxonomy'}
          </Button>
        </div>
        {proposals &&
          proposals.merges.length === 0 &&
          proposals.renames.length === 0 && (
            <p className="text-xs text-muted-foreground">No suggestions.</p>
          )}
        {proposals?.merges.map((proposal) => (
          <div
            key={`merge-${proposal.source_id}-${proposal.target_id}`}
            className="flex items-center justify-between gap-2 rounded border p-2 text-xs"
          >
            <span className="min-w-0">
              Merge <strong>{proposal.source_name}</strong> →{' '}
              <strong>{proposal.target_name}</strong>
              {proposal.similarity != null
                ? ` (${Math.round(proposal.similarity * 100)}%)`
                : ''}
              {proposal.reason ? ` — ${proposal.reason}` : ''}
            </span>
            <Button
              size="sm"
              disabled={merge.isPending}
              onClick={() =>
                merge.mutate({
                  source: proposal.source_id,
                  target: proposal.target_id,
                })
              }
            >
              Apply
            </Button>
          </div>
        ))}
        {proposals?.renames.map((proposal) => (
          <div
            key={`rename-${proposal.category_id}`}
            className="flex items-center justify-between gap-2 rounded border p-2 text-xs"
          >
            <span className="min-w-0">
              Rename <strong>{proposal.current_name}</strong> →{' '}
              <strong>{proposal.proposed_name}</strong>
              {proposal.reason ? ` — ${proposal.reason}` : ''}
            </span>
            <Button
              size="sm"
              disabled={rename.isPending}
              onClick={() =>
                rename.mutate({
                  id: proposal.category_id,
                  name: proposal.proposed_name,
                })
              }
            >
              Apply
            </Button>
          </div>
        ))}
      </div>
    </div>
  )
}

interface ProposalNode {
  name: string
  definition: string | null
  children: ProposalNode[]
}

interface TaxonomyInsightsData {
  total_documents: number
  total_concepts: number
  approved_concepts: number
  provisional_concepts: number
  merged_concepts: number
  singleton_rate: number
  concepts_per_100_documents: number
  possible_duplicate_pairs: number
  aliases_total: number
  concepts_with_aliases: number
  confidence_buckets: Record<string, number>
}

function ProposalTree({ nodes, depth = 0 }: { nodes: ProposalNode[]; depth?: number }) {
  return (
    <ul className={depth === 0 ? 'space-y-1' : 'ml-3 space-y-1'}>
      {nodes.map((node) => (
        <li key={`${depth}-${node.name}`}>
          <span className="text-xs font-medium">{node.name}</span>
          {node.definition && (
            <span className="ml-1 text-xs text-muted-foreground">— {node.definition}</span>
          )}
          {node.children.length > 0 && (
            <ProposalTree nodes={node.children} depth={depth + 1} />
          )}
        </li>
      ))}
    </ul>
  )
}

function InsightsTab() {
  const qc = useQueryClient()
  const [proposal, setProposal] = useState<ProposalNode[] | null>(null)
  const { data, isPending, isError } = useQuery({
    queryKey: queryKeys.taxonomyInsights,
    queryFn: getTaxonomyInsights,
  })

  const propose = useMutation({
    mutationFn: proposeTaxonomy,
    onSuccess: (result: { categories: ProposalNode[]; sampled_documents: number }) => {
      setProposal(result.categories)
      toast.success(`Proposed from ${result.sampled_documents} sampled documents`)
    },
    onError: (err) => { toast.error(`Proposal failed: ${errorDetail(err)}`) },
  })
  const apply = useMutation({
    mutationFn: (nodes: ProposalNode[]) =>
      applyTaxonomyProposal(nodes as unknown as Record<string, unknown>[]),
    onSuccess: () => {
      setProposal(null)
      qc.invalidateQueries({ queryKey: queryKeys.categories() })
      toast.success('Taxonomy applied — run a sync to assign files')
    },
    onError: (err) => { toast.error(`Apply failed: ${errorDetail(err)}`) },
  })

  if (isPending) {
    return <p className="text-sm text-muted-foreground">Loading insights…</p>
  }
  if (isError) {
    return <p className="text-sm text-destructive">Failed to load taxonomy insights.</p>
  }

  const insights = data as TaxonomyInsightsData
  const metrics = [
    { label: 'Documents', value: insights.total_documents },
    { label: 'Concepts', value: insights.total_concepts },
    { label: 'Provisional', value: insights.provisional_concepts },
    { label: 'Merged', value: insights.merged_concepts },
    { label: 'Singleton rate', value: `${Math.round(insights.singleton_rate * 100)}%` },
    { label: 'Concepts / 100 docs', value: insights.concepts_per_100_documents.toFixed(1) },
    { label: 'Possible duplicates', value: insights.possible_duplicate_pairs },
    {
      label: 'Aliases',
      value: `${insights.aliases_total} across ${insights.concepts_with_aliases} concepts`,
    },
  ]

  return (
    <div className="space-y-5">
      <div>
        <h3 className="text-sm font-medium">Taxonomy health</h3>
        <div className="mt-2 grid grid-cols-2 gap-2">
          {metrics.map((metric) => (
            <div key={metric.label} className="rounded border p-2">
              <div className="text-xs text-muted-foreground">{metric.label}</div>
              <div className="text-sm font-medium">{metric.value}</div>
            </div>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap gap-1">
          {Object.entries(insights.confidence_buckets).map(([bucket, count]) => (
            <Badge key={bucket} variant="secondary">
              {bucket}: {count}
            </Badge>
          ))}
        </div>
      </div>

      <div className="border-t pt-4">
        <div className="flex items-start justify-between gap-2">
          <div>
            <h3 className="text-sm font-medium">Propose a taxonomy</h3>
            <p className="text-xs text-muted-foreground">
              Samples a diverse slice of the corpus and asks the LLM for a compact
              tree. Applying creates/reuses concepts; run a sync afterwards to assign
              files.
            </p>
          </div>
          <Button
            size="sm"
            variant="outline"
            disabled={propose.isPending}
            onClick={() => propose.mutate()}
          >
            {propose.isPending ? 'Proposing…' : 'Propose'}
          </Button>
        </div>
        {proposal && (
          <div className="mt-3 rounded border p-3">
            {proposal.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                The proposal was empty. Try after a sync has built facts for more documents.
              </p>
            ) : (
              <ProposalTree nodes={proposal} />
            )}
            <div className="mt-3 flex gap-2">
              <Button
                size="sm"
                disabled={apply.isPending || proposal.length === 0}
                onClick={() => apply.mutate(proposal)}
              >
                Apply taxonomy
              </Button>
              <Button size="sm" variant="outline" onClick={() => setProposal(null)}>
                Dismiss
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function ExportTab() {
  const { data: views } = useViews()
  const [targetDir, setTargetDir] = useState('')
  const [scope, setScope] = useState<'library' | 'view'>('library')
  const [viewId, setViewId] = useState('')
  const [plan, setPlan] = useState<SymlinkTreePlan | null>(null)
  const [result, setResult] = useState<SymlinkTreeResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [tagPlan, setTagPlan] = useState<FinderTagsPlan | null>(null)
  const [tagResult, setTagResult] = useState<FinderTagsResult | null>(null)
  const [tagBusy, setTagBusy] = useState(false)
  const [vaultDir, setVaultDir] = useState('')
  const [vaultScope, setVaultScope] = useState<'library' | 'view'>('library')
  const [vaultViewId, setVaultViewId] = useState('')
  const [vaultPlan, setVaultPlan] = useState<VaultExportPlan | null>(null)
  const [vaultResult, setVaultResult] = useState<VaultExportResult | null>(null)
  const [vaultBusy, setVaultBusy] = useState(false)

  const buildRequest = (): SymlinkTreeRequest | null => {
    if (!targetDir.trim()) {
      toast.error('Choose a target folder')
      return null
    }
    if (scope === 'view' && !viewId) {
      toast.error('Choose a view')
      return null
    }
    return {
      target_dir: targetDir.trim(),
      scope,
      view_id: scope === 'view' ? Number(viewId) : null,
    }
  }

  const chooseFolder = async () => {
    const path = await window.api.selectFolder()
    if (path) {
      setTargetDir(path)
      setPlan(null)
      setResult(null)
    }
  }

  const preview = async () => {
    const request = buildRequest()
    if (!request) return
    setBusy(true)
    try {
      setPlan(await planSymlinkExport(request))
      setResult(null)
    } catch (err) {
      toast.error(`Preview failed: ${errorDetail(err)}`)
    } finally {
      setBusy(false)
    }
  }

  const apply = async () => {
    const request = buildRequest()
    if (!request) return
    if (
      !window.confirm(
        'Create or refresh symlinks in this folder? Existing non-generated files are never touched.'
      )
    ) {
      return
    }
    setBusy(true)
    try {
      const applied = await applySymlinkExport(request)
      setResult(applied)
      toast.success(
        `Exported: ${applied.created} created, ${applied.updated} updated, ${applied.removed} removed`
      )
    } catch (err) {
      toast.error(`Export failed: ${errorDetail(err)}`)
    } finally {
      setBusy(false)
    }
  }

  const previewTags = async () => {
    setTagBusy(true)
    try {
      setTagPlan(await planFinderTags())
      setTagResult(null)
    } catch (err) {
      toast.error(`Finder tag preview failed: ${errorDetail(err)}`)
    } finally {
      setTagBusy(false)
    }
  }

  const applyTags = async () => {
    if (
      !window.confirm(
        'Add each document’s concepts as Finder tags? Existing tags are kept.'
      )
    ) {
      return
    }
    setTagBusy(true)
    try {
      const applied = await applyFinderTags()
      setTagResult(applied)
      setTagPlan(null)
      if (applied.supported) {
        toast.success(`Tagged ${applied.tagged} files`)
      } else {
        toast.error(applied.message || 'Finder tags are not supported here')
      }
    } catch (err) {
      toast.error(`Finder tag export failed: ${errorDetail(err)}`)
    } finally {
      setTagBusy(false)
    }
  }

  const chooseVault = async () => {
    const path = await window.api.selectFolder()
    if (path) {
      setVaultDir(path)
      setVaultPlan(null)
      setVaultResult(null)
    }
  }

  const buildVaultRequest = (): VaultExportRequest | null => {
    if (!vaultDir.trim()) {
      toast.error('Choose a vault folder')
      return null
    }
    if (vaultScope === 'view' && !vaultViewId) {
      toast.error('Choose a view')
      return null
    }
    return {
      target_dir: vaultDir.trim(),
      scope: vaultScope,
      view_id: vaultScope === 'view' ? Number(vaultViewId) : null,
    }
  }

  const previewVault = async () => {
    const request = buildVaultRequest()
    if (!request) return
    setVaultBusy(true)
    try {
      setVaultPlan(await planObsidianExport(request))
      setVaultResult(null)
    } catch (err) {
      toast.error(`Vault preview failed: ${errorDetail(err)}`)
    } finally {
      setVaultBusy(false)
    }
  }

  const applyVault = async () => {
    const request = buildVaultRequest()
    if (!request) return
    if (
      !window.confirm(
        'Write markdown catalogue cards under the Cabinet folder? Existing notes are never overwritten.'
      )
    ) {
      return
    }
    setVaultBusy(true)
    try {
      const applied = await applyObsidianExport(request)
      setVaultResult(applied)
      setVaultPlan(null)
      toast.success(`Created ${applied.created} note(s)`)
    } catch (err) {
      toast.error(`Vault export failed: ${errorDetail(err)}`)
    } finally {
      setVaultBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-medium">Export as folders</h3>
        <p className="text-xs text-muted-foreground">
          Build a directory of symlinks mirroring the concept tree or one saved view.
          Files stay exactly where they are; only links are written, and a manifest
          tracks the ones Cabinet generated.
        </p>
      </div>

      <div>
        <label className="text-xs text-muted-foreground">Target folder</label>
        <div className="mt-1 flex gap-2">
          <Input
            value={targetDir}
            onChange={(event) => setTargetDir(event.target.value)}
            placeholder="/Users/you/Exports/Cabinet"
          />
          <Button variant="outline" onClick={chooseFolder}>
            Choose…
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="text-xs text-muted-foreground">Scope</label>
          <select
            value={scope}
            onChange={(event) => {
              setScope(event.target.value as 'library' | 'view')
              setPlan(null)
              setResult(null)
            }}
            className="mt-1 flex h-10 w-full rounded-md border bg-background px-3 text-sm"
          >
            <option value="library">Whole library (concept tree)</option>
            <option value="view">A saved view</option>
          </select>
        </div>
        {scope === 'view' && (
          <div>
            <label className="text-xs text-muted-foreground">View</label>
            <select
              value={viewId}
              onChange={(event) => {
                setViewId(event.target.value)
                setPlan(null)
                setResult(null)
              }}
              className="mt-1 flex h-10 w-full rounded-md border bg-background px-3 text-sm"
            >
              <option value="">Choose a view…</option>
              {(views ?? []).map((view: { id: number; name: string }) => (
                <option key={view.id} value={view.id}>
                  {view.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="flex gap-2">
        <Button size="sm" variant="outline" disabled={busy} onClick={preview}>
          Preview
        </Button>
        <Button size="sm" disabled={busy} onClick={apply}>
          Export
        </Button>
      </div>

      {plan && (
        <div className="rounded border p-3 text-xs">
          {!plan.supported ? (
            <p className="text-destructive">{plan.message}</p>
          ) : (
            <>
              <p>
                Create {plan.creates} · Update {plan.updates} · Remove{' '}
                {plan.removes} · Keep {plan.keeps}
                {plan.conflicts > 0 ? ` · Conflicts ${plan.conflicts}` : ''}
              </p>
              <p className="mt-1 text-muted-foreground">Target: {plan.target_dir}</p>
              {plan.actions.length > 0 && (
                <ul className="mt-2 max-h-32 overflow-auto text-muted-foreground">
                  {plan.actions.slice(0, 20).map((action) => (
                    <li
                      key={`${action.action}-${action.relative_path}`}
                      className="truncate"
                    >
                      {action.action}: {action.relative_path}
                      {action.reason ? ` — ${action.reason}` : ''}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </div>
      )}

      {result && (
        <div className="rounded border p-3 text-xs">
          Created {result.created} · Updated {result.updated} · Removed{' '}
          {result.removed} · Conflicts {result.conflicts}
          {result.errors.length > 0 && (
            <ul className="mt-1 text-destructive">
              {result.errors.map((error) => (
                <li key={error}>{error}</li>
              ))}
            </ul>
          )}
          {result.manifest_path && (
            <p className="mt-1 text-muted-foreground">
              Manifest: {result.manifest_path}
            </p>
          )}
        </div>
      )}

      <div className="space-y-2 border-t pt-4">
        <h3 className="text-sm font-medium">Finder tags (macOS)</h3>
        <p className="text-xs text-muted-foreground">
          Add each document’s concepts as native Finder tags. This is additive:
          existing tags (and their colours) are kept, and stale Cabinet tags are
          not removed.
        </p>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={tagBusy}
            onClick={previewTags}
          >
            Preview tags
          </Button>
          <Button
            size="sm"
            disabled={tagBusy || (tagPlan !== null && !tagPlan.supported)}
            onClick={applyTags}
          >
            Apply tags
          </Button>
        </div>
        {tagPlan && !tagPlan.supported && (
          <p className="text-xs text-destructive">{tagPlan.message}</p>
        )}
        {tagPlan?.supported && (
          <p className="text-xs text-muted-foreground">
            {tagPlan.files} files · {tagPlan.tagged} to tag ·{' '}
            {tagPlan.skipped} already tagged
          </p>
        )}
        {tagResult && (
          <p className="text-xs text-muted-foreground">
            {tagResult.supported
              ? `${tagResult.tagged} tagged · ${tagResult.skipped} skipped`
              : tagResult.message}
            {tagResult.errors.length > 0 && (
              <span className="text-destructive">
                {' '}
                · {tagResult.errors.length} error(s)
              </span>
            )}
          </p>
        )}
      </div>

      <div className="space-y-2 border-t pt-4">
        <h3 className="text-sm font-medium">Obsidian vault</h3>
        <p className="text-xs text-muted-foreground">
          Write one markdown catalogue card per document under a Cabinet folder:
          facts as frontmatter, concepts as tags, and a link to the original.
          Existing notes are never overwritten.
        </p>
        <div>
          <label className="text-xs text-muted-foreground">Vault folder</label>
          <div className="mt-1 flex gap-2">
            <Input
              value={vaultDir}
              onChange={(event) => setVaultDir(event.target.value)}
              placeholder="/Users/you/Vault"
            />
            <Button variant="outline" onClick={chooseVault}>
              Choose…
            </Button>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-xs text-muted-foreground">Scope</label>
            <select
              value={vaultScope}
              onChange={(event) => {
                setVaultScope(event.target.value as 'library' | 'view')
                setVaultPlan(null)
                setVaultResult(null)
              }}
              className="mt-1 flex h-10 w-full rounded-md border bg-background px-3 text-sm"
            >
              <option value="library">Whole library</option>
              <option value="view">A saved view</option>
            </select>
          </div>
          {vaultScope === 'view' && (
            <div>
              <label className="text-xs text-muted-foreground">View</label>
              <select
                value={vaultViewId}
                onChange={(event) => {
                  setVaultViewId(event.target.value)
                  setVaultPlan(null)
                  setVaultResult(null)
                }}
                className="mt-1 flex h-10 w-full rounded-md border bg-background px-3 text-sm"
              >
                <option value="">Choose a view…</option>
                {(views ?? []).map((view: { id: number; name: string }) => (
                  <option key={view.id} value={view.id}>
                    {view.name}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            disabled={vaultBusy}
            onClick={previewVault}
          >
            Preview notes
          </Button>
          <Button size="sm" disabled={vaultBusy} onClick={applyVault}>
            Export vault
          </Button>
        </div>
        {vaultPlan && (
          <p className="text-xs text-muted-foreground">
            {vaultPlan.notes} notes · {vaultPlan.creates} to create ·{' '}
            {vaultPlan.conflicts} existing
          </p>
        )}
        {vaultResult && (
          <p className="text-xs text-muted-foreground">
            Created {vaultResult.created} · Conflicts {vaultResult.conflicts}
            {vaultResult.errors.length > 0 &&
              ` · ${vaultResult.errors.length} error(s)`}
          </p>
        )}
      </div>
    </div>
  )
}

export function SettingsDialog() {
  const open = useUIStore((state) => state.settingsOpen)
  const setOpen = useUIStore((state) => state.setSettingsOpen)

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      <Dialog.Trigger asChild>
        <Button variant="ghost" size="icon" className="h-7 w-7">
          <Settings className="h-4 w-4" />
        </Button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 bg-black/50" />
        <Dialog.Content className="fixed left-1/2 top-1/2 w-[500px] max-h-[85vh] -translate-x-1/2 -translate-y-1/2 rounded-lg border bg-background p-6 shadow-lg overflow-auto">
          <Dialog.Title className="text-lg font-semibold">Settings</Dialog.Title>
          <Tabs.Root defaultValue="sources" className="mt-4">
            <Tabs.List className="flex border-b">
              <Tabs.Trigger value="sources" className="px-4 py-2 text-sm data-[state=active]:border-b-2 data-[state=active]:border-primary">
                <Folder className="mr-1 inline h-4 w-4" /> Sources
              </Tabs.Trigger>
              <Tabs.Trigger value="llm" className="px-4 py-2 text-sm data-[state=active]:border-b-2 data-[state=active]:border-primary">
                <Brain className="mr-1 inline h-4 w-4" /> LLM
              </Tabs.Trigger>
              <Tabs.Trigger value="review" className="px-4 py-2 text-sm data-[state=active]:border-b-2 data-[state=active]:border-primary">
                <Inbox className="mr-1 inline h-4 w-4" /> Review
              </Tabs.Trigger>
              <Tabs.Trigger value="insights" className="px-4 py-2 text-sm data-[state=active]:border-b-2 data-[state=active]:border-primary">
                <BarChart3 className="mr-1 inline h-4 w-4" /> Insights
              </Tabs.Trigger>
              <Tabs.Trigger value="export" className="px-4 py-2 text-sm data-[state=active]:border-b-2 data-[state=active]:border-primary">
                <FolderOutput className="mr-1 inline h-4 w-4" /> Export
              </Tabs.Trigger>
            </Tabs.List>
            <Tabs.Content value="sources" className="pt-4"><SourcesTab /></Tabs.Content>
            <Tabs.Content value="llm" className="pt-4"><LLMTab /></Tabs.Content>
            <Tabs.Content value="review" className="pt-4"><ReviewTab /></Tabs.Content>
            <Tabs.Content value="insights" className="pt-4"><InsightsTab /></Tabs.Content>
            <Tabs.Content value="export" className="pt-4"><ExportTab /></Tabs.Content>
          </Tabs.Root>
          <Dialog.Close asChild>
            <button className="absolute right-4 top-4">
              <X className="h-4 w-4" />
            </button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
