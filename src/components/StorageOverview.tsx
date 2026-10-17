import React from 'react';
import { 
  CheckCircle2, 
  Trash2, 
  ExternalLink, 
  FolderSync, 
  Eye, 
  ShieldCheck, 
  SlidersHorizontal,
  HardDrive
} from 'lucide-react';
import { ClutterCategory, ClutterCategoryId, ClutterItem, OSPlatform } from '../types';
import { CATEGORY_DEFINITIONS, formatBytes } from '../data/mockStorageData';

interface StorageOverviewProps {
  items: ClutterItem[];
  currentPlatform: OSPlatform;
  setPlatform: (plat: OSPlatform) => void;
  onPurgeSafe: () => void;
  onPreviewDryRun: () => void;
  onNavigateRelocate: () => void;
  onNavigateCli: () => void;
  isCleaning: boolean;
  onFilterByCategory: (catId: ClutterCategoryId | 'all') => void;
  selectedCategory: ClutterCategoryId | 'all';
}

export const StorageOverview: React.FC<StorageOverviewProps> = ({
  items,
  currentPlatform,
  setPlatform,
  onPurgeSafe,
  onPreviewDryRun,
  onNavigateRelocate,
  onNavigateCli,
  isCleaning,
  onFilterByCategory,
  selectedCategory,
}) => {
  const totalBytes = items.reduce((acc, it) => acc + it.sizeBytes, 0);
  const safeReclaimableBytes = items
    .filter((it) => it.isSafeToDelete)
    .reduce((acc, it) => acc + it.sizeBytes, 0);
  const relocatableBytes = items
    .filter((it) => it.isRelocatable)
    .reduce((acc, it) => acc + it.sizeBytes, 0);

  // Group items by category
  const categoryGroups: Record<ClutterCategoryId, { items: ClutterItem[]; sizeBytes: number }> = {
    downloads_cache: { items: [], sizeBytes: 0 },
    active_soundbanks: { items: [], sizeBytes: 0 },
    crash_dumps: { items: [], sizeBytes: 0 },
    audio_scratch: { items: [], sizeBytes: 0 },
    autosave_backups: { items: [], sizeBytes: 0 },
    plugin_ir_cache: { items: [], sizeBytes: 0 },
  };

  items.forEach((item) => {
    if (categoryGroups[item.category]) {
      categoryGroups[item.category].items.push(item);
      categoryGroups[item.category].sizeBytes += item.sizeBytes;
    }
  });

  const categories = Object.keys(categoryGroups) as ClutterCategoryId[];

  return (
    <div className="space-y-8">
      {/* Platform Switcher & Scope Strip */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-slate-800">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            Muse Storage Footprint Audit
          </h1>
          <div className="flex items-center gap-2 text-xs text-slate-400 mt-1">
            <span>Target OS Target Profile</span>
            <span aria-hidden="true">·</span>
            <span className="font-mono text-cyan-400">
              {currentPlatform === 'macos' ? '/Library/Application Support/MuseHub' : currentPlatform === 'windows' ? 'C:\\ProgramData\\Muse Hub' : '/var/lib/muse-hub'}
            </span>
            <span aria-hidden="true">·</span>
            <span className="text-emerald-400 font-medium">Verified Clean Invariants</span>
          </div>
        </div>

        {/* Segmented OS control (Functional Buttons) */}
        <div className="flex items-center p-1 bg-slate-900 border border-slate-800 rounded-lg">
          {(['macos', 'windows', 'linux'] as OSPlatform[]).map((plat) => (
            <button
              key={plat}
              onClick={() => setPlatform(plat)}
              className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors cursor-pointer capitalize ${
                currentPlatform === plat
                  ? 'bg-slate-800 text-cyan-300 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {plat === 'macos' ? 'macOS' : plat === 'windows' ? 'Windows' : 'Linux'}
            </button>
          ))}
        </div>
      </div>

      {/* KPI Metric Strips */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <div className="p-5 bg-slate-900/70 border border-slate-800 rounded-xl space-y-1">
          <div className="text-xs font-medium text-slate-400">Total Muse Footprint</div>
          <div className="text-3xl font-bold font-mono tracking-tight text-white tabular-nums">
            {formatBytes(totalBytes)}
          </div>
          <div className="text-xs text-slate-500 flex items-center gap-1.5 pt-1">
            <span>{items.length} files & packages tracked</span>
          </div>
        </div>

        <div className="p-5 bg-slate-900/70 border border-slate-800 rounded-xl space-y-1">
          <div className="text-xs font-medium text-cyan-400">Safe to Reclaim Today</div>
          <div className="text-3xl font-bold font-mono tracking-tight text-cyan-300 tabular-nums">
            {formatBytes(safeReclaimableBytes)}
          </div>
          <div className="text-xs text-slate-400 flex items-center gap-1.5 pt-1">
            <ShieldCheck className="w-3.5 h-3.5 text-cyan-400" />
            <span>0% sound impact · Caches & dumps</span>
          </div>
        </div>

        <div className="p-5 bg-slate-900/70 border border-slate-800 rounded-xl space-y-1">
          <div className="text-xs font-medium text-purple-400">Relocatable Soundbanks</div>
          <div className="text-3xl font-bold font-mono tracking-tight text-purple-300 tabular-nums">
            {formatBytes(relocatableBytes)}
          </div>
          <div className="text-xs text-slate-400 flex items-center gap-1.5 pt-1">
            <FolderSync className="w-3.5 h-3.5 text-purple-400" />
            <span>Move to external SSD via symlink</span>
          </div>
        </div>

        <div className="p-5 bg-slate-900/70 border border-slate-800 rounded-xl space-y-1">
          <div className="text-xs font-medium text-slate-400">Estimated Reclaim Potential</div>
          <div className="text-3xl font-bold font-mono tracking-tight text-emerald-400 tabular-nums">
            {formatBytes(safeReclaimableBytes + relocatableBytes)}
          </div>
          <div className="text-xs text-slate-500 flex items-center gap-1.5 pt-1">
            <span>Total space freed from boot drive</span>
          </div>
        </div>
      </div>

      {/* Visual Disk Allocation Bar */}
      <div className="p-6 bg-slate-900/50 border border-slate-800 rounded-xl space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold text-white">Storage Allocation Breakdown</h2>
            <p className="text-xs text-slate-400">
              Proportional distribution of Muse data across caches, core libraries, and temporary files.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onPreviewDryRun}
              className="px-3 py-1.5 text-xs font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg transition-colors inline-flex items-center gap-1.5 cursor-pointer"
            >
              <Eye className="w-3.5 h-3.5 text-slate-400" />
              <span>Simulate Dry Run</span>
            </button>
            <button
              onClick={onPurgeSafe}
              disabled={isCleaning || safeReclaimableBytes === 0}
              className="px-3.5 py-1.5 text-xs font-semibold text-slate-950 bg-cyan-400 hover:bg-cyan-300 rounded-lg transition-colors inline-flex items-center gap-1.5 disabled:opacity-40 cursor-pointer"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>Purge Safe Items</span>
            </button>
          </div>
        </div>

        {/* Multi-segment progress bar */}
        <div className="h-4 w-full bg-slate-950 rounded-full overflow-hidden flex p-0.5 border border-slate-800">
          {categories.map((catKey) => {
            const group = categoryGroups[catKey];
            if (group.sizeBytes === 0 || totalBytes === 0) return null;
            const pct = (group.sizeBytes / totalBytes) * 100;
            const def = CATEGORY_DEFINITIONS[catKey];
            return (
              <div
                key={catKey}
                style={{ width: `${pct}%`, backgroundColor: def.color }}
                className="h-full first:rounded-l-full last:rounded-r-full transition-all duration-300 relative group cursor-pointer"
                onClick={() => onFilterByCategory(catKey)}
                title={`${def.name}: ${formatBytes(group.sizeBytes)} (${pct.toFixed(1)}%)`}
              />
            );
          })}
        </div>

        {/* Visual Bar Legend */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3 pt-2">
          {categories.map((catKey) => {
            const group = categoryGroups[catKey];
            const def = CATEGORY_DEFINITIONS[catKey];
            const isSelected = selectedCategory === catKey;
            return (
              <button
                key={catKey}
                onClick={() => onFilterByCategory(isSelected ? 'all' : catKey)}
                className={`text-left p-2.5 rounded-lg border transition-all cursor-pointer ${
                  isSelected
                    ? 'border-cyan-500/50 bg-cyan-950/20 shadow-sm'
                    : 'border-slate-800/80 bg-slate-950/40 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center gap-2">
                  <div
                    className="w-2.5 h-2.5 rounded-sm shrink-0"
                    style={{ backgroundColor: def.color }}
                  />
                  <span className="text-xs font-medium text-slate-300 truncate">
                    {def.name}
                  </span>
                </div>
                <div className="text-sm font-semibold font-mono text-white mt-1 tabular-nums">
                  {formatBytes(group.sizeBytes)}
                </div>
                <div className="text-[11px] text-slate-500">
                  {group.items.length} {group.items.length === 1 ? 'file' : 'items'}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Action Guidance & Relocation Spotlight */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Card 1: Safe Cleaner */}
        <div className="p-6 bg-slate-900/70 border border-slate-800 rounded-xl space-y-4">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="text-base font-semibold text-white">Clean Staged Installers & Dumps</h3>
              <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                Muse Hub does not clean up download archives (<span className="font-mono text-cyan-300">.musepkg</span>), torrent chunks, or crash reports after installation completes.
              </p>
            </div>
          </div>

          <div className="space-y-2 text-xs text-slate-300">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-cyan-400 shrink-0" />
              <span>Leaves your active MuseScore 4 scores and soundbanks completely untouched.</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-cyan-400 shrink-0" />
              <span>Reclaims ~<span className="font-mono font-semibold tabular-nums">{formatBytes(safeReclaimableBytes)}</span> instantly without restarting your computer.</span>
            </div>
          </div>

          <div className="pt-2 flex items-center gap-3">
            <button
              onClick={onPurgeSafe}
              disabled={isCleaning || safeReclaimableBytes === 0}
              className="px-4 py-2 text-xs font-semibold text-slate-950 bg-cyan-400 hover:bg-cyan-300 rounded-lg transition-colors cursor-pointer disabled:opacity-40"
            >
              Clean {formatBytes(safeReclaimableBytes)} Now
            </button>
            <button
              onClick={onPreviewDryRun}
              className="px-4 py-2 text-xs font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-lg transition-colors cursor-pointer"
            >
              Verify Dry Run
            </button>
          </div>
        </div>

        {/* Card 2: External SSD Relocator */}
        <div className="p-6 bg-slate-900/70 border border-slate-800 rounded-xl space-y-4">
          <div className="flex items-start justify-between">
            <div>
              <h3 className="text-base font-semibold text-white">External Drive Soundbank Relocation</h3>
              <p className="text-xs text-slate-400 mt-1 leading-relaxed">
                Move massive Muse Sounds (Strings, Brass, Percussion) to an external SSD using verified filesystem symlinks or NTFS junctions.
              </p>
            </div>
          </div>

          <div className="space-y-2 text-xs text-slate-300">
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-purple-400 shrink-0" />
              <span>Free up <span className="font-mono font-semibold tabular-nums">{formatBytes(relocatableBytes)}</span> on your primary boot partition.</span>
            </div>
            <div className="flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-purple-400 shrink-0" />
              <span>MuseScore 4 continues playing seamlessly without needing path re-configuration.</span>
            </div>
          </div>

          <div className="pt-2 flex items-center gap-3">
            <button
              onClick={onNavigateRelocate}
              className="px-4 py-2 text-xs font-semibold text-white bg-purple-600 hover:bg-purple-500 rounded-lg transition-colors cursor-pointer inline-flex items-center gap-1.5"
            >
              <FolderSync className="w-3.5 h-3.5" />
              <span>Open Relocation Wizard</span>
            </button>
            <button
              onClick={onNavigateCli}
              className="px-4 py-2 text-xs font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-lg transition-colors cursor-pointer"
            >
              Generate Shell Script
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
