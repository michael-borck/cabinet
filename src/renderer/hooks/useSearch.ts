import { useQuery } from '@tanstack/react-query'
import { getFiles } from '@/services/api'

interface FileResult {
  id: number
  filename: string
  path: string
  categories: { id: number; name: string; slug: string; color: string | null }[]
}

/**
 * Server-side search (design §5.3): the backend matches filename, path, and
 * cached facts (summary/doc type/topics), so the client no longer loads every
 * file just to search.
 */
export function useSearch(query: string) {
  const q = query.trim()
  const { data } = useQuery({
    queryKey: ['file-search', q],
    queryFn: () => getFiles({ q, per_page: 50 }),
    enabled: q.length > 0,
    staleTime: 10_000,
  })

  return { results: (data?.files ?? []) as FileResult[] }
}
