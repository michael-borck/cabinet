import { useMemo } from 'react'
import { useFiles } from '@/hooks/useFiles'
import { useCategories } from '@/hooks/useCategories'

interface FileResult {
  id: number
  filename: string
  path: string
  categories: { id: number; name: string; slug: string; color: string | null }[]
}

export function useSearch(query: string) {
  const { data: filesData } = useFiles({ per_page: 200 })
  const { data: categories } = useCategories('flat')

  const results = useMemo(() => {
    const q = query.toLowerCase().trim()
    if (!q || !filesData?.files) return []

    const categoryNames = new Map<number, string>()
    if (Array.isArray(categories)) {
      for (const cat of categories) {
        categoryNames.set(cat.id, cat.name.toLowerCase())
      }
    }

    return filesData.files.filter((file: FileResult) => {
      if (file.filename.toLowerCase().includes(q)) return true
      if (file.categories?.some((c) => c.name.toLowerCase().includes(q))) return true
      return false
    })
  }, [query, filesData, categories])

  return { results }
}
