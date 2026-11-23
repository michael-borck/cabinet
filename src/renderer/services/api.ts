import axios from 'axios'

const FALLBACK_PORT = 8420

// The Electron main process picks a free port per launch (falling back to
// 8420 outside Electron), so resolve the base URL before the first request.
let baseUrlPromise: Promise<string> | null = null

function resolveBaseUrl(): Promise<string> {
  if (!baseUrlPromise) {
    baseUrlPromise = (async () => {
      if (window.api?.getBackendPort) {
        try {
          const port = await window.api.getBackendPort()
          if (port) return `http://127.0.0.1:${port}/api/v1`
        } catch {
          // Fall through to the standalone default.
        }
      }
      return `http://127.0.0.1:${FALLBACK_PORT}/api/v1`
    })()
  }
  return baseUrlPromise
}

const api = axios.create({
  timeout: 30000,
})

// The backend requires the per-launch token from the Electron main process.
// Fetched once, awaited by every request; null when running outside Electron
// (e.g. plain vite dev against a token-less backend).
let apiTokenPromise: Promise<string | null> | null = null

api.interceptors.request.use(async (config) => {
  if (!config.baseURL) {
    config.baseURL = await resolveBaseUrl()
  }
  if (!apiTokenPromise) {
    apiTokenPromise = window.api?.getApiToken
      ? window.api.getApiToken().catch(() => null)
      : Promise.resolve(null)
  }
  const token = await apiTokenPromise
  if (token) config.headers['X-API-Token'] = token
  return config
})

// Sources
export const getSources = () => api.get('/sources').then(r => r.data)
export const createSource = (data: { path: string; label?: string; recursive?: boolean; exclude_patterns?: string[]; watch_enabled?: boolean; store_facts?: boolean; store_text_index?: boolean }) =>
  api.post('/sources', data).then(r => r.data)
export const updateSource = (id: number, data: { label?: string; recursive?: boolean; exclude_patterns?: string[]; watch_enabled?: boolean; store_facts?: boolean; store_text_index?: boolean }) =>
  api.patch(`/sources/${id}`, data).then(r => r.data)
export const deleteSource = (id: number) => api.delete(`/sources/${id}`)
export const scanSource = (id: number, mode: string = 'quick_sync') =>
  api.post(`/sources/${id}/scan`, { mode }).then(r => r.data)

// Files
export const getFiles = (params: Record<string, unknown>) =>
  api.get('/files', { params }).then(r => r.data)
export const getFile = (id: number) => api.get(`/files/${id}`).then(r => r.data)
export const getFileFacts = (id: number) => api.get(`/files/${id}/facts`).then(r => r.data)
export const getFileVersions = (id: number) => api.get(`/files/${id}/versions`).then(r => r.data)
export const getSimilarFiles = (id: number, limit = 10) =>
  api.get(`/files/${id}/similar`, { params: { limit } }).then(r => r.data)

export interface SimilarDocument {
  document_id: number | null
  file_id: number | null
  filename: string | null
  path: string | null
  summary: string | null
  doc_type: string | null
  topics: string[] | null
  similarity: number
}

export interface SimilarDocuments {
  source_document_id: number | null
  items: SimilarDocument[]
}
export const assignCategories = (fileId: number, categoryIds: number[]) =>
  api.post(`/files/${fileId}/categories`, { category_ids: categoryIds }).then(r => r.data)
export const removeFileCategory = (fileId: number, categoryId: number) =>
  api.delete(`/files/${fileId}/categories/${categoryId}`)

// Categories
export const getCategories = (format: 'tree' | 'flat' = 'tree') =>
  api.get('/categories', { params: { format } }).then(r => r.data)
export const createCategory = (data: { name: string; parent_id?: number; color?: string }) =>
  api.post('/categories', data).then(r => r.data)
export const updateCategory = (id: number, data: Record<string, unknown>) =>
  api.patch(`/categories/${id}`, data).then(r => r.data)
export const deleteCategory = (id: number) => api.delete(`/categories/${id}`)
export const getCategoryReview = () => api.get('/categories/review').then(r => r.data)
export const approveCategory = (id: number, data?: { definition?: string }) =>
  api.post(`/categories/${id}/approve`, data ?? {}).then(r => r.data)
export const rejectCategory = (id: number) =>
  api.post(`/categories/${id}/reject`).then(r => r.data)
export const mergeCategory = (sourceId: number, targetId: number) =>
  api.post(`/categories/${sourceId}/merge`, { target_id: targetId }).then(r => r.data)
export const consolidateCategories = () =>
  api.post('/categories/consolidate').then(r => r.data)
export const proposeTaxonomy = () =>
  api.post('/categories/propose').then(r => r.data)
export const applyTaxonomyProposal = (categories: Record<string, unknown>[]) =>
  api.post('/categories/propose/apply', { categories }).then(r => r.data)
export const getTaxonomyInsights = () =>
  api.get('/insights/taxonomy').then(r => r.data)

// Jobs
export const getJobs = () => api.get('/jobs').then(r => r.data)
export const getJob = (id: number) => api.get(`/jobs/${id}`).then(r => r.data)

// Health
export const getHealth = () => api.get('/health').then(r => r.data)

// Views (Phase 4a)
export interface ViewConceptFilter {
  ids: number[]
  include_descendants?: boolean
  match?: 'any' | 'all'
}

export type ViewGroupBy =
  | 'none'
  | 'concept'
  | 'doc_type'
  | 'topic'
  | 'source'
  | 'month'

export interface ViewQuery {
  concepts?: ViewConceptFilter
  doc_types?: string[]
  topics?: string[]
  sources?: number[]
  text?: string
  clusters?: 'collapse' | 'separate'
  versions?: 'all' | 'clustered' | 'singletons'
  group_by?: ViewGroupBy
  sort_by?: string
  sort_dir?: 'asc' | 'desc'
}

export interface ViewFacetValue {
  value: string
  count: number
}

export interface ViewSourceFacet {
  source_id: number
  label: string
  count: number
}

export interface ViewFacets {
  total: number
  doc_types: ViewFacetValue[]
  topics: ViewFacetValue[]
  sources: ViewSourceFacet[]
  months: ViewFacetValue[]
}

export interface ViewGroup {
  key: string
  label: string
  count: number
}

export interface ViewGroups {
  group_by: string
  total: number
  groups: ViewGroup[]
}

export interface SavedView {
  id: number
  name: string
  description: string | null
  icon: string | null
  color: string | null
  query: ViewQuery
  pinned: boolean
  created_at: string
  updated_at: string | null
}

export const getViews = () => api.get('/views').then(r => r.data)
export const createView = (data: {
  name: string
  query: ViewQuery
  description?: string
  color?: string | null
  pinned?: boolean
}) => api.post('/views', data).then(r => r.data)
export const updateView = (id: number, data: Record<string, unknown>) =>
  api.patch(`/views/${id}`, data).then(r => r.data)
export const deleteView = (id: number) => api.delete(`/views/${id}`)
export const getViewFiles = (id: number, params: Record<string, unknown>) =>
  api.get(`/views/${id}/files`, { params }).then(r => r.data)
export const getViewFacets = (id: number) =>
  api.get(`/views/${id}/facets`).then(r => r.data)
export const getViewGroups = (id: number, groupBy: string) =>
  api.get(`/views/${id}/groups`, { params: { group_by: groupBy } }).then(r => r.data)
export const previewView = (query: ViewQuery, params: Record<string, unknown> = {}) =>
  api.post('/views/preview', { query }, { params }).then(r => r.data)

// Curation sidecar (Phase 5a)
export interface CurationImportResult {
  concepts_created: number
  aliases_added: number
  assignments_applied: number
  assignments_skipped: number
  merges_applied: number
  views_created: number
  warnings: string[]
}

export const exportCuration = () => api.get('/curation/export').then(r => r.data)
export const importCuration = (data: unknown) =>
  api.post('/curation/import', data).then(r => r.data as CurationImportResult)

// Settings
export const getLLMConfig = () => api.get('/settings/llm').then(r => r.data)
export const setLLMConfig = (data: { provider: string; model: string; api_key?: string; base_url?: string }) =>
  api.put('/settings/llm', data).then(r => r.data)
export const testLLMConfig = (data: { provider: string; model: string; api_key?: string; base_url?: string }) =>
  api.post('/settings/llm/test', data).then(r => r.data)

export const getEmbeddingConfig = () => api.get('/settings/embeddings').then(r => r.data)
export const setEmbeddingConfig = (data: { provider: string; model: string; api_key?: string; base_url?: string }) =>
  api.put('/settings/embeddings', data).then(r => r.data)
export const testEmbeddingConfig = (data: { provider: string; model: string; api_key?: string; base_url?: string }) =>
  api.post('/settings/embeddings/test', data).then(r => r.data)

export default api

// Symlink-tree export (Phase 5b)
export interface SymlinkTreeRequest {
  target_dir: string
  scope: 'library' | 'view'
  view_id?: number | null
}

export interface SymlinkTreeAction {
  action: 'create' | 'update' | 'remove' | 'keep' | 'conflict'
  relative_path: string
  source_path: string | null
  reason: string | null
}

export interface SymlinkTreePlan {
  target_dir: string
  scope: string
  supported: boolean
  message: string | null
  creates: number
  updates: number
  removes: number
  keeps: number
  conflicts: number
  actions: SymlinkTreeAction[]
  manifest_path: string | null
}

export interface SymlinkTreeResult {
  target_dir: string
  scope: string
  created: number
  updated: number
  removed: number
  kept: number
  conflicts: number
  errors: string[]
  manifest_path: string | null
}

export const planSymlinkExport = (data: SymlinkTreeRequest) =>
  api.post('/exports/symlink-tree/plan', data).then(r => r.data as SymlinkTreePlan)
export const applySymlinkExport = (data: SymlinkTreeRequest) =>
  api.post('/exports/symlink-tree/apply', data).then(r => r.data as SymlinkTreeResult)
