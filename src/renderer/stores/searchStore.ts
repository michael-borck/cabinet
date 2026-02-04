import { create } from 'zustand'

interface SearchState {
  query: string
  isOpen: boolean
  setQuery: (q: string) => void
  setOpen: (open: boolean) => void
  toggle: () => void
}

export const useSearchStore = create<SearchState>()((set) => ({
  query: '',
  isOpen: false,
  setQuery: (query) => set({ query }),
  setOpen: (isOpen) => set({ isOpen }),
  toggle: () => set((s) => ({ isOpen: !s.isOpen })),
}))
