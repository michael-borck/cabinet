import { create } from 'zustand'

interface SelectionState {
  selectedFileId: number | null
  selectedCategoryId: number | null
  setSelectedFile: (id: number | null) => void
  setSelectedCategory: (id: number | null) => void
}

export const useSelectionStore = create<SelectionState>()((set) => ({
  selectedFileId: null,
  selectedCategoryId: null,
  setSelectedFile: (id) => set({ selectedFileId: id }),
  setSelectedCategory: (id) => set({ selectedCategoryId: id }),
}))
