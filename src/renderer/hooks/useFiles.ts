import { useEffect, useMemo } from 'react'
import { useQuery, useInfiniteQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getFiles, getFile, assignCategories, removeFileCategory } from '@/services/api'
import { queryKeys } from '@/services/apiClient'

export function useFiles(params: Record<string, unknown> = {}) {
  return useQuery({
    queryKey: queryKeys.files(params),
    queryFn: () => getFiles(params),
  })
}

// Backend caps per_page at 500 (routers/files.py: Query(50, ge=1, le=500)).
const PAGE_SIZE = 500

interface FileListPage {
  files: Record<string, unknown>[]
  total: number
  page: number
  per_page: number
}

/**
 * Progressively loads ALL files matching `params` by auto-fetching every page
 * of the paginated GET /files endpoint and flattening the results.
 * Changing `params` changes the query key, which resets pagination.
 */
export function useAllFiles(params: Record<string, unknown> = {}) {
  const query = useInfiniteQuery({
    queryKey: queryKeys.files({ ...params, per_page: PAGE_SIZE }),
    queryFn: ({ pageParam }): Promise<FileListPage> =>
      getFiles({ ...params, per_page: PAGE_SIZE, page: pageParam }),
    initialPageParam: 1,
    getNextPageParam: (lastPage, allPages) => {
      const loaded = allPages.reduce((n, p) => n + p.files.length, 0)
      // Stop if the last page came back empty, even if `total` says otherwise.
      if (lastPage.files.length === 0) return undefined
      return loaded < lastPage.total ? allPages.length + 1 : undefined
    },
  })

  const { hasNextPage, isFetchingNextPage, isError, fetchNextPage } = query
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage && !isError) {
      fetchNextPage()
    }
  }, [hasNextPage, isFetchingNextPage, isError, fetchNextPage])

  const files = useMemo(
    () => query.data?.pages.flatMap((p) => p.files) ?? [],
    [query.data]
  )
  const total = query.data?.pages[0]?.total ?? 0

  return {
    files,
    total,
    isLoading: query.isLoading,
    isError: query.isError,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
  }
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
