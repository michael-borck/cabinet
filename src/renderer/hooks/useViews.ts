import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getViews,
  createView,
  updateView,
  deleteView,
  previewView,
  type ViewQuery,
} from '@/services/api'
import { queryKeys } from '@/services/apiClient'

export function useViews() {
  return useQuery({ queryKey: queryKeys.views, queryFn: getViews })
}

export function useCreateView() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: createView,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.views })
    },
  })
}

export function useUpdateView() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: Record<string, unknown> }) =>
      updateView(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.views })
      qc.invalidateQueries({ queryKey: ['view-files'] })
    },
  })
}

export function useDeleteView() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: deleteView,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: queryKeys.views })
      qc.invalidateQueries({ queryKey: ['view-files'] })
    },
  })
}

export function usePreviewView() {
  return useMutation({
    mutationFn: (query: ViewQuery) => previewView(query, { per_page: 1 }),
  })
}
