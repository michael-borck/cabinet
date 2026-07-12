import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import * as Tabs from '@radix-ui/react-tabs'
import { Settings, X, Folder, Brain, RefreshCw, ListFilter } from 'lucide-react'
import { isAxiosError } from 'axios'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getSources, createSource, updateSource, deleteSource, scanSource, getLLMConfig, setLLMConfig, testLLMConfig } from '@/services/api'
import { queryKeys } from '@/services/apiClient'
import { useSelectionStore } from '@/stores/selectionStore'
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
    mutationFn: (id: number) => scanSource(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.jobs })
      toast.success('Scan started')
    },
    onError: (err) => { toast.error(`Failed to start scan: ${errorDetail(err)}`) },
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
              <span className="truncate">{s.label || s.path}</span>
              <div className="flex shrink-0 gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  title="Edit exclusions"
                  className={s.exclude_patterns.length > 0 ? 'text-primary' : undefined}
                  onClick={() => toggleEditor(s)}
                >
                  <ListFilter className="h-3 w-3" />
                </Button>
                <Button variant="ghost" size="sm" title="Rescan" disabled={scanMut.isPending} onClick={() => scanMut.mutate(s.id)}>
                  <RefreshCw className="h-3 w-3" />
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
  return <LLMForm key={config ? `cfg-${config.id}` : 'new'} config={config ?? null} />
}

export function SettingsDialog() {
  return (
    <Dialog.Root>
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
            </Tabs.List>
            <Tabs.Content value="sources" className="pt-4"><SourcesTab /></Tabs.Content>
            <Tabs.Content value="llm" className="pt-4"><LLMTab /></Tabs.Content>
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
