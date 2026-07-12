import axios from 'axios'

const api = axios.create({
  baseURL: 'http://127.0.0.1:8420/api/v1',
  timeout: 30000,
})

// The backend requires the per-launch token from the Electron main process.
// Fetched once, awaited by every request; null when running outside Electron
// (e.g. plain vite dev against a token-less backend).
let apiTokenPromise: Promise<string | null> | null = null

api.interceptors.request.use(async (config) => {
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
export const createSource = (data: { path: string; label?: string; recursive?: boolean; exclude_patterns?: string[] }) =>
  api.post('/sources', data).then(r => r.data)
export const updateSource = (id: number, data: { label?: string; recursive?: boolean; exclude_patterns?: string[] }) =>
  api.patch(`/sources/${id}`, data).then(r => r.data)
export const deleteSource = (id: number) => api.delete(`/sources/${id}`)
export const scanSource = (id: number, mode: string = 'quick_sync') =>
  api.post(`/sources/${id}/scan`, { mode }).then(r => r.data)

// Files
export const getFiles = (params: Record<string, unknown>) =>
  api.get('/files', { params }).then(r => r.data)
export const getFile = (id: number) => api.get(`/files/${id}`).then(r => r.data)
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

// Jobs
export const getJobs = () => api.get('/jobs').then(r => r.data)
export const getJob = (id: number) => api.get(`/jobs/${id}`).then(r => r.data)

// Health
export const getHealth = () => api.get('/health').then(r => r.data)

// Settings
export const getLLMConfig = () => api.get('/settings/llm').then(r => r.data)
export const setLLMConfig = (data: { provider: string; model: string; api_key?: string; base_url?: string }) =>
  api.put('/settings/llm', data).then(r => r.data)
export const testLLMConfig = (data: { provider: string; model: string; api_key?: string; base_url?: string }) =>
  api.post('/settings/llm/test', data).then(r => r.data)

export default api
