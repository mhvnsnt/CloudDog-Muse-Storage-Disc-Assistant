import React, { useState, useMemo } from 'react';
import { 
  Search, 
  Trash2, 
  ShieldCheck, 
  ShieldAlert, 
  FolderSync, 
  Filter, 
  CheckSquare, 
  Square, 
  ArrowUpDown, 
  Info,
  ChevronDown,
  ChevronUp
} from 'lucide-react';
import { ClutterItem, ClutterCategoryId } from '../types';
import { CATEGORY_DEFINITIONS, formatBytes } from '../data/mockStorageData';

interface ClutterTableProps {
  items: ClutterItem[];
  onToggleItem: (id: string) => void;
  onSelectAllSafe: () => void;
  onClearSelection: () => void;
  onDeleteSelected: () => void;
  selectedCategory: ClutterCategoryId | 'all';
  setSelectedCategory: (cat: ClutterCategoryId | 'all') => void;
  isCleaning: boolean;
}

export const ClutterTable: React.FC<ClutterTableProps> = ({
  items,
  onToggleItem,
  onSelectAllSafe,
  onClearSelection,
  onDeleteSelected,
  selectedCategory,
  setSelectedCategory,
  isCleaning,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [sortField, setSortField] = useState<'size' | 'name' | 'modified'>('size');
  const [sortAsc, setSortAsc] = useState(false);
  const [expandedItemId, setExpandedItemId] = useState<string | null>(null);

  // Filtered and sorted items
  const filteredItems = useMemo(() => {
    return items
      .filter((it) => {
        if (selectedCategory !== 'all' && it.category !== selectedCategory) {
          return false;
        }
        if (searchQuery.trim()) {
          const q = searchQuery.toLowerCase();
          return (
            it.name.toLowerCase().includes(q) ||
            it.path.toLowerCase().includes(q) ||
            (it.details && it.details.toLowerCase().includes(q))
          );
        }
        return true;
      })
      .sort((a, b) => {
        let diff = 0;
        if (sortField === 'size') {
          diff = a.sizeBytes - b.sizeBytes;
        } else if (sortField === 'name') {
          diff = a.name.localeCompare(b.name);
        } else {
          diff = a.lastModified.localeCompare(b.lastModified);
        }
        return sortAsc ? diff : -diff;
      });
  }, [items, selectedCategory, searchQuery, sortField, sortAsc]);

  const selectedCount = items.filter((it) => it.selected).length;
  const selectedBytes = items
    .filter((it) => it.selected)
    .reduce((acc, it) => acc + it.sizeBytes, 0);

  const toggleSort = (field: 'size' | 'name' | 'modified') => {
    if (sortField === field) {
      setSortAsc(!sortAsc);
    } else {
      setSortField(field);
      setSortAsc(false);
    }
  };

  return (
    <div className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden space-y-4 p-5">
      {/* Header Controls: Search & Category Filter */}
      <div className="flex flex-col lg:flex-row items-stretch lg:items-center justify-between gap-4">
        {/* Category Filter Tabs */}
        <div className="flex items-center gap-1 overflow-x-auto p-1 bg-slate-950/70 border border-slate-800 rounded-lg no-scrollbar">
          <button
            onClick={() => setSelectedCategory('all')}
            className={`px-3 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap cursor-pointer ${
              selectedCategory === 'all'
                ? 'bg-slate-800 text-cyan-300 shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            All Items ({items.length})
          </button>
          {Object.entries(CATEGORY_DEFINITIONS).map(([key, def]) => (
            <button
              key={key}
              onClick={() => setSelectedCategory(key as ClutterCategoryId)}
              className={`px-2.5 py-1.5 text-xs font-medium rounded-md transition-colors whitespace-nowrap cursor-pointer ${
                selectedCategory === key
                  ? 'bg-slate-800 text-cyan-300 shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              {def.name}
            </button>
          ))}
        </div>

        {/* Search input */}
        <div className="relative min-w-[260px]">
          <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Filter by filename, extension, or path..."
            className="w-full pl-9 pr-3 py-1.5 text-xs bg-slate-950 border border-slate-800 rounded-lg text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500/60 focus:ring-1 focus:ring-cyan-500/60 transition-colors"
          />
        </div>
      </div>

      {/* Batch Selection Action Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 pt-2 pb-1 border-b border-slate-800/80 text-xs">
        <div className="flex items-center gap-3">
          <span className="text-slate-400">
            Selected: <strong className="text-white font-mono tabular-nums">{selectedCount}</strong> items (
            <span className="font-mono text-cyan-400 font-semibold tabular-nums">{formatBytes(selectedBytes)}</span>)
          </span>
          <span className="text-slate-700" aria-hidden="true">|</span>
          <button
            onClick={onSelectAllSafe}
            className="text-cyan-400 hover:text-cyan-300 font-medium transition-colors cursor-pointer"
          >
            Select All Safe Caches
          </button>
          <span className="text-slate-700" aria-hidden="true">·</span>
          <button
            onClick={onClearSelection}
            className="text-slate-400 hover:text-slate-300 transition-colors cursor-pointer"
          >
            Deselect All
          </button>
        </div>

        <div>
          <button
            onClick={onDeleteSelected}
            disabled={selectedCount === 0 || isCleaning}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-rose-300 bg-rose-950/60 hover:bg-rose-900/60 border border-rose-800/60 rounded-lg transition-colors disabled:opacity-40 cursor-pointer"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>Delete Selected ({formatBytes(selectedBytes)})</span>
          </button>
        </div>
      </div>

      {/* Data Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs border-collapse">
          <thead>
            <tr className="border-b border-slate-800 text-slate-400">
              <th className="py-2.5 px-3 w-10">
                <span className="sr-only">Select</span>
              </th>
              <th className="py-2.5 px-3 font-semibold">
                <button
                  onClick={() => toggleSort('name')}
                  className="inline-flex items-center gap-1 hover:text-white transition-colors cursor-pointer"
                >
                  <span>File / Soundbank Identifier</span>
                  <ArrowUpDown className="w-3 h-3 text-slate-500" />
                </button>
              </th>
              <th className="py-2.5 px-3 font-semibold hidden md:table-cell">Category</th>
              <th className="py-2.5 px-3 font-semibold text-right">
                <button
                  onClick={() => toggleSort('size')}
                  className="inline-flex items-center gap-1 hover:text-white transition-colors ml-auto cursor-pointer"
                >
                  <span>Footprint</span>
                  <ArrowUpDown className="w-3 h-3 text-slate-500" />
                </button>
              </th>
              <th className="py-2.5 px-3 font-semibold hidden lg:table-cell">
                <button
                  onClick={() => toggleSort('modified')}
                  className="inline-flex items-center gap-1 hover:text-white transition-colors cursor-pointer"
                >
                  <span>Timestamp</span>
                  <ArrowUpDown className="w-3 h-3 text-slate-500" />
                </button>
              </th>
              <th className="py-2.5 px-3 font-semibold text-right">Recommended Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-800/60 font-normal">
            {filteredItems.length === 0 ? (
              <tr>
                <td colSpan={6} className="py-12 text-center text-slate-500">
                  <div className="max-w-xs mx-auto space-y-2">
                    <p className="text-sm font-medium text-slate-400">No items match your filter criteria.</p>
                    <p className="text-xs text-slate-600">Try adjusting your search terms or select "All Items" tab above.</p>
                  </div>
                </td>
              </tr>
            ) : (
              filteredItems.map((item) => {
                const categoryDef = CATEGORY_DEFINITIONS[item.category];
                const isExpanded = expandedItemId === item.id;

                return (
                  <React.Fragment key={item.id}>
                    <tr
                      className={`hover:bg-slate-800/30 transition-colors ${
                        item.selected ? 'bg-cyan-950/15' : ''
                      }`}
                    >
                      <td className="py-2.5 px-3">
                        <button
                          onClick={() => onToggleItem(item.id)}
                          className="text-slate-400 hover:text-cyan-400 cursor-pointer"
                          aria-label={`Select ${item.name}`}
                        >
                          {item.selected ? (
                            <CheckSquare className="w-4 h-4 text-cyan-400" />
                          ) : (
                            <Square className="w-4 h-4 text-slate-600" />
                          )}
                        </button>
                      </td>
                      <td className="py-2.5 px-3">
                        <div className="flex items-center gap-2">
                          <button
                            onClick={() => setExpandedItemId(isExpanded ? null : item.id)}
                            className="font-medium text-slate-200 hover:text-cyan-300 text-left truncate max-w-xs sm:max-w-md lg:max-w-lg cursor-pointer"
                          >
                            {item.name}
                          </button>
                        </div>
                        <div className="text-[11px] text-slate-500 truncate max-w-xs sm:max-w-md font-mono mt-0.5">
                          {item.path}
                        </div>
                      </td>
                      <td className="py-2.5 px-3 hidden md:table-cell">
                        <span className="text-slate-400 font-medium">
                          {categoryDef.name}
                        </span>
                      </td>
                      <td className="py-2.5 px-3 text-right font-mono font-semibold text-slate-100 tabular-nums">
                        {formatBytes(item.sizeBytes)}
                      </td>
                      <td className="py-2.5 px-3 hidden lg:table-cell text-slate-400 font-mono text-[11px] tabular-nums">
                        {item.lastModified}
                      </td>
                      <td className="py-2.5 px-3 text-right">
                        {item.isSafeToDelete ? (
                          <span className="text-cyan-400 font-medium inline-flex items-center gap-1 justify-end">
                            <ShieldCheck className="w-3.5 h-3.5" />
                            <span>Safe to delete</span>
                          </span>
                        ) : item.isRelocatable ? (
                          <span className="text-purple-400 font-medium inline-flex items-center gap-1 justify-end">
                            <FolderSync className="w-3.5 h-3.5" />
                            <span>Relocate to SSD</span>
                          </span>
                        ) : (
                          <span className="text-amber-400 font-medium inline-flex items-center gap-1 justify-end">
                            <ShieldAlert className="w-3.5 h-3.5" />
                            <span>Review manually</span>
                          </span>
                        )}
                      </td>
                    </tr>

                    {/* Expandable item details */}
                    {isExpanded && (
                      <tr className="bg-slate-950/60 border-y border-slate-800">
                        <td colSpan={6} className="p-4">
                          <div className="flex flex-col sm:flex-row gap-4 items-start justify-between text-xs">
                            <div className="space-y-1.5 max-w-2xl">
                              <div className="font-semibold text-slate-200">
                                Item Diagnosis & Reason
                              </div>
                              <p className="text-slate-400 leading-relaxed">
                                {item.details || 'Standard Muse cache entity.'}
                              </p>
                              <div className="flex items-center gap-3 text-[11px] text-slate-500 pt-1">
                                <span>Extension: <span className="font-mono text-slate-300">{item.extension}</span></span>
                                <span aria-hidden="true">·</span>
                                <span>Absolute Path: <span className="font-mono text-slate-300">{item.path}</span></span>
                              </div>
                            </div>

                            <div className="flex items-center gap-2 shrink-0">
                              <button
                                onClick={() => onToggleItem(item.id)}
                                className={`px-3 py-1.5 rounded-lg border text-xs font-medium cursor-pointer ${
                                  item.selected
                                    ? 'border-cyan-500/40 bg-cyan-950/30 text-cyan-300'
                                    : 'border-slate-700 bg-slate-800 text-slate-300 hover:text-white'
                                }`}
                              >
                                {item.selected ? 'Marked for Deletion' : 'Select for Deletion'}
                              </button>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
