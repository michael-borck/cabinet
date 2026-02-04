import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getFiles, getFile, assignCategories, removeFileCategory } from '@/services/api'
import { queryKeys } from '@/services/apiClient'

export function useFiles(params: Record<string, unknown> = {}) {
  return useQuery({
    queryKey: queryKeys.files(params),
    queryFn: () => getFiles(params),
  })
}

export function useFile(id: number | null) {
  return useQuery({
    queryKey: queryKeys.file(id!),
    queryFn: () => getFile(id!),
    enabled: id !== null,
  })
}

export function useAssignCategories() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ fileId, categoryIds }: { fileId: number; categoryIds: number[] }) =>
      assignCategories(fileId, categoryIds),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['files'] })
      qc.invalidateQueries({ queryKey: ['file'] })
      qc.invalidateQueries({ queryKey: ['categories'] })
    },
  })
}

export function useRemoveFileCategory() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ fileId, categoryId }: { fileId: number; categoryId: number }) =>
      removeFileCategory(fileId, categoryId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['files'] })
      qc.invalidateQueries({ queryKey: ['file'] })
      qc.invalidateQueries({ queryKey: ['categories'] })
    },
  })
}
