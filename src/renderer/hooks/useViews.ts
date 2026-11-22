import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  getViews,
  createView,
  updateView,
  deleteView,
  previewView,
  getViewFacets,
  getViewGroups,
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
      qc.invalidateQueries({ queryKey: ['view-facets'] })
      qc.invalidateQueries({ queryKey: ['view-groups'] })
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

export function useViewFacets(viewId: number | null) {
  return useQuery({
    queryKey: ['view-facets', viewId],
    queryFn: () => getViewFacets(viewId!),
    enabled: viewId !== null,
  })
}

export function useViewGroups(viewId: number | null, groupBy: string) {
  return useQuery({
    queryKey: ['view-groups', viewId, groupBy],
    queryFn: () => getViewGroups(viewId!, groupBy),
    enabled: viewId !== null && groupBy !== 'none',
  })
}
