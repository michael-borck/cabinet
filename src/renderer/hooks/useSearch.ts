import { useMemo } from 'react'
import { useAllFiles } from '@/hooks/useFiles'

interface FileResult {
  id: number
  filename: string
  path: string
  categories: { id: number; name: string; slug: string; color: string | null }[]
}

export function useSearch(query: string) {
  // Loads every page of the files list, so search matches the full set.
  const { files } = useAllFiles()

  const results = useMemo(() => {
    const q = query.toLowerCase().trim()
    if (!q || files.length === 0) return []

    return (files as unknown as FileResult[]).filter((file) => {
      if (file.filename.toLowerCase().includes(q)) return true
      if (file.categories?.some((c) => c.name.toLowerCase().includes(q))) return true
      return false
    })
  }, [query, files])

  return { results }
}
