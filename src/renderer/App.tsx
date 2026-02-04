import { PanelGroup, Panel, PanelResizeHandle } from 'react-resizable-panels'
import { Moon, Sun } from 'lucide-react'
import { Toaster } from 'sonner'
import { CategoryTree } from '@/components/category-tree/CategoryTree'
import { FileTable } from '@/components/file-list/FileTable'
import { FileDetailsPanel } from '@/components/file-details/FileDetailsPanel'
import { SearchBar } from '@/components/search/SearchBar'
import { SettingsDialog } from '@/components/settings/SettingsDialog'
import { SyncProgress } from '@/components/progress/SyncProgress'
import { useUIStore } from '@/stores/uiStore'
import { useSelectionStore } from '@/stores/selectionStore'
import { Button } from '@/components/ui/button'

function App(): React.JSX.Element {
  const { theme, toggleTheme } = useUIStore()
  const { selectedFileId } = useSelectionStore()

  return (
    <div className="flex h-screen flex-col">
      {/* Titlebar */}
      <div className="titlebar-drag-region flex h-10 shrink-0 items-center border-b px-4">
        <span className="titlebar-no-drag text-sm font-semibold">File Sorter</span>
      </div>

      {/* Search */}
      <div className="shrink-0 border-b px-4 py-2 titlebar-no-drag">
        <SearchBar />
      </div>

      {/* Main 3-panel layout */}
      <PanelGroup direction="horizontal" className="flex-1">
        {/* Sidebar: Category Tree */}
        <Panel defaultSize={20} minSize={15} maxSize={35}>
          <CategoryTree />
        </Panel>
        <PanelResizeHandle className="w-px bg-border hover:bg-primary/50 transition-colors" />

        {/* Center: File list */}
        <Panel defaultSize={selectedFileId ? 50 : 80} minSize={30}>
          <FileTable />
        </Panel>

        {/* Right: Details panel */}
        {selectedFileId && (
          <>
            <PanelResizeHandle className="w-px bg-border hover:bg-primary/50 transition-colors" />
            <Panel defaultSize={30} minSize={20} maxSize={45}>
              <FileDetailsPanel />
            </Panel>
          </>
        )}
      </PanelGroup>

      {/* Status bar */}
      <div className="flex h-8 shrink-0 items-center justify-between border-t bg-muted/50 px-4 text-xs text-muted-foreground">
        <SyncProgress />
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="icon" className="h-6 w-6" onClick={toggleTheme}>
            {theme === 'light' ? <Moon className="h-3 w-3" /> : <Sun className="h-3 w-3" />}
          </Button>
          <SettingsDialog />
        </div>
      </div>

      <Toaster position="bottom-right" />
    </div>
  )
}

export default App
