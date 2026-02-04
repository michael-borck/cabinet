export const queryKeys = {
  sources: ['sources'] as const,
  files: (params?: Record<string, unknown>) => ['files', params] as const,
  file: (id: number) => ['file', id] as const,
  categories: (format?: string) => ['categories', format] as const,
  jobs: ['jobs'] as const,
  job: (id: number) => ['job', id] as const,
  llmConfig: ['llmConfig'] as const,
}
