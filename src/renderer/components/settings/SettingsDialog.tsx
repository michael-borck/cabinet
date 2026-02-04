import { useState } from 'react'
import * as Dialog from '@radix-ui/react-dialog'
import * as Tabs from '@radix-ui/react-tabs'
import { Settings, X, Folder, Brain, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getSources, createSource, deleteSource, scanSource, getLLMConfig, setLLMConfig, testLLMConfig } from '@/services/api'
import { queryKeys } from '@/services/apiClient'
import { toast } from 'sonner'

function SourcesTab() {
  const qc = useQueryClient()
  const { data: sources } = useQuery({ queryKey: queryKeys.sources, queryFn: getSources })
  const addSource = useMutation({
    mutationFn: async () => {
      const path = await window.api.selectFolder()
      if (!path) return
      return createSource({ path })
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: queryKeys.sources }) },
  })
  const rmSource = useMutation({
    mutationFn: deleteSource,
    onSuccess: () => { qc.invalidateQueries({ queryKey: queryKeys.sources }) },
  })

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-medium">Watched Folders</h3>
        <Button size="sm" onClick={() => addSource.mutate()}>
          <Folder className="mr-1 h-3 w-3" /> Add Folder
        </Button>
      </div>
      <div className="space-y-2">
        {sources?.map((s: { id: number; path: string; label: string | null }) => (
          <div key={s.id} className="flex items-center justify-between rounded-md border p-2 text-sm">
            <span className="truncate">{s.label || s.path}</span>
            <div className="flex gap-1">
              <Button variant="ghost" size="sm" onClick={() => scanSource(s.id)}>
                <RefreshCw className="h-3 w-3" />
              </Button>
              <Button variant="ghost" size="sm" onClick={() => rmSource.mutate(s.id)}>
                <X className="h-3 w-3" />
              </Button>
            </div>
          </div>
        ))}
        {(!sources || sources.length === 0) && (
          <p className="text-sm text-muted-foreground">No folders added yet.</p>
        )}
      </div>
    </div>
  )
}

function LLMTab() {
  const qc = useQueryClient()
  const { data: config } = useQuery({ queryKey: queryKeys.llmConfig, queryFn: getLLMConfig })
  const [provider, setProvider] = useState(config?.provider || 'openai')
  const [model, setModel] = useState(config?.model || 'gpt-4o-mini')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState(config?.base_url || '')

  const saveMut = useMutation({
    mutationFn: () => setLLMConfig({ provider, model, api_key: apiKey || undefined, base_url: baseUrl || undefined }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: queryKeys.llmConfig }); toast.success('LLM config saved') },
  })
  const testMut = useMutation({
    mutationFn: () => testLLMConfig({ provider, model, api_key: apiKey || undefined, base_url: baseUrl || undefined }),
    onSuccess: (r) => { r.success ? toast.success('Connection successful') : toast.error(r.message) },
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
          <Input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-..." className="mt-1" />
        </div>
        {(provider === 'ollama' || provider === 'openai') && (
          <div>
            <label className="text-xs text-muted-foreground">Base URL</label>
            <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://localhost:11434/v1" className="mt-1" />
          </div>
        )}
        <div className="flex gap-2">
          <Button size="sm" onClick={() => saveMut.mutate()}>Save</Button>
          <Button size="sm" variant="outline" onClick={() => testMut.mutate()}>Test Connection</Button>
        </div>
      </div>
    </div>
  )
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
