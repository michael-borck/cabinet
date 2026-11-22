import { create } from 'zustand'

interface SelectionState {
  selectedFileId: number | null
  selectedCategoryId: number | null
  selectedViewId: number | null
  setSelectedFile: (id: number | null) => void
  setSelectedCategory: (id: number | null) => void
  setSelectedView: (id: number | null) => void
}

export const useSelectionStore = create<SelectionState>()((set) => ({
  selectedFileId: null,
  selectedCategoryId: null,
  selectedViewId: null,
  setSelectedFile: (id) => set({ selectedFileId: id }),
  // The category tree and saved views are alternative browse lenses.
  setSelectedCategory: (id) =>
    set({ selectedCategoryId: id, selectedViewId: null, selectedFileId: null }),
  setSelectedView: (id) =>
    set({ selectedViewId: id, selectedCategoryId: null, selectedFileId: null }),
}))
