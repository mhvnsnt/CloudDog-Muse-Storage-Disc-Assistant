import React from 'react';
import { Eye, ShieldCheck, X, Trash2, ArrowRight } from 'lucide-react';
import { ClutterItem } from '../types';
import { formatBytes } from '../data/mockStorageData';

interface DryRunModalProps {
  isOpen: boolean;
  onClose: () => void;
  onProceedClean: () => void;
  itemsToDelete: ClutterItem[];
}

export const DryRunModal: React.FC<DryRunModalProps> = ({
  isOpen,
  onClose,
  onProceedClean,
  itemsToDelete,
}) => {
  if (!isOpen) return null;

  const totalReclaimBytes = itemsToDelete.reduce((acc, it) => acc + it.sizeBytes, 0);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
      <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-2xl w-full overflow-hidden shadow-2xl space-y-4">
        {/* Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-cyan-950/60 border border-cyan-800/80 flex items-center justify-center text-cyan-400">
              <Eye className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-base font-bold text-white">
                Simulated Dry-Run Verification
              </h3>
              <p className="text-xs text-slate-400">
                Safe preview: zero files will be deleted until you click confirm.
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white p-1 rounded-lg transition-colors cursor-pointer"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content list */}
        <div className="px-5 space-y-3">
          <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg flex items-center justify-between text-xs">
            <span className="text-slate-300">
              Total candidates targeted for removal: <strong className="text-white font-mono">{itemsToDelete.length} files</strong>
            </span>
            <span className="text-cyan-300 font-mono font-bold text-sm tabular-nums">
              {formatBytes(totalReclaimBytes)}
            </span>
          </div>

          <div className="max-h-60 overflow-y-auto space-y-1.5 border border-slate-800/80 rounded-lg p-2 bg-slate-950/40 text-xs">
            {itemsToDelete.length === 0 ? (
              <div className="p-4 text-center text-slate-500">
                No items marked for deletion.
              </div>
            ) : (
              itemsToDelete.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between p-2 rounded bg-slate-900/60 hover:bg-slate-900 transition-colors"
                >
                  <div className="truncate pr-3">
                    <div className="font-medium text-slate-200 truncate">{item.name}</div>
                    <div className="text-[11px] text-slate-500 truncate font-mono">{item.path}</div>
                  </div>
                  <div className="text-right shrink-0">
                    <span className="font-mono text-cyan-300 tabular-nums font-semibold">
                      {formatBytes(item.sizeBytes)}
                    </span>
                  </div>
                </div>
              ))
            )}
          </div>

          <div className="p-3 bg-cyan-950/20 border border-cyan-800/40 rounded-lg flex items-start gap-2.5 text-xs text-cyan-300">
            <ShieldCheck className="w-4 h-4 text-cyan-400 shrink-0 mt-0.5" />
            <p className="leading-relaxed">
              <strong>Playback Safety Guarantee:</strong> All selected items are confirmed disposable cache packages, completed installers, or crash dumps. Active soundbanks and score files remain untouched.
            </p>
          </div>
        </div>

        {/* Footer actions */}
        <div className="p-5 border-t border-slate-800 flex items-center justify-end gap-3 bg-slate-950/50">
          <button
            onClick={onClose}
            className="px-4 py-2 text-xs font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-lg transition-colors cursor-pointer"
          >
            Cancel
          </button>
          <button
            onClick={() => {
              onProceedClean();
              onClose();
            }}
            disabled={itemsToDelete.length === 0}
            className="px-4 py-2 text-xs font-semibold text-slate-950 bg-cyan-400 hover:bg-cyan-300 rounded-lg transition-colors disabled:opacity-50 inline-flex items-center gap-1.5 cursor-pointer"
          >
            <Trash2 className="w-3.5 h-3.5" />
            <span>Proceed with Real Clean ({formatBytes(totalReclaimBytes)})</span>
          </button>
        </div>
      </div>
    </div>
  );
};
