import React, { useState } from 'react';
import { FolderPlus, X, Plus, HardDrive, Check } from 'lucide-react';
import { OSPlatform, ClutterCategoryId, ClutterItem } from '../types';

interface CustomPathModalProps {
  isOpen: boolean;
  onClose: () => void;
  onAddCustomItem: (item: Omit<ClutterItem, 'id' | 'selected'>) => void;
  currentPlatform: OSPlatform;
}

export const CustomPathModal: React.FC<CustomPathModalProps> = ({
  isOpen,
  onClose,
  onAddCustomItem,
  currentPlatform,
}) => {
  if (!isOpen) return null;

  const [path, setPath] = useState('');
  const [name, setName] = useState('');
  const [category, setCategory] = useState<ClutterCategoryId>('downloads_cache');
  const [sizeGb, setSizeGb] = useState('5.4');

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !path.trim()) return;

    const sizeBytes = Math.max(1, parseFloat(sizeGb) || 1) * 1024 * 1024 * 1024;
    const isSafe = category !== 'active_soundbanks';

    onAddCustomItem({
      name: name.trim(),
      path: path.trim(),
      category,
      sizeBytes,
      lastModified: new Date().toISOString().slice(0, 16).replace('T', ' '),
      isSafeToDelete: isSafe,
      isRelocatable: category === 'active_soundbanks',
      details: 'Custom scanned user directory or audio package.',
      extension: path.includes('.') ? `.${path.split('.').pop()}` : 'DIR',
    });

    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm">
      <div className="bg-slate-900 border border-slate-800 rounded-xl max-w-lg w-full overflow-hidden shadow-2xl space-y-4">
        {/* Header */}
        <div className="p-5 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-cyan-950/60 border border-cyan-800/80 flex items-center justify-center text-cyan-400">
              <FolderPlus className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-base font-bold text-white">
                Add Custom Scan Target
              </h3>
              <p className="text-xs text-slate-400">
                Track custom sample directories, audio stem folders, or alternate partitions.
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

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-5 space-y-4 text-xs">
          <div className="space-y-1.5">
            <label className="text-slate-300 font-semibold">Folder or Archive Name</label>
            <input
              type="text"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Orchestral_Percussion_Staging.musepkg"
              className="w-full p-2.5 bg-slate-950 border border-slate-700 rounded-lg text-slate-200 placeholder-slate-600 focus:outline-none focus:border-cyan-400"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-slate-300 font-semibold">Absolute Filesystem Path</label>
            <input
              type="text"
              required
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder={currentPlatform === 'windows' ? 'D:\\AudioDrives\\MuseCaches' : '/Volumes/SampleDrive/MuseDownloads'}
              className="w-full p-2.5 bg-slate-950 border border-slate-700 rounded-lg font-mono text-cyan-300 placeholder-slate-600 focus:outline-none focus:border-cyan-400"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-slate-300 font-semibold">Category Classification</label>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value as ClutterCategoryId)}
                className="w-full p-2.5 bg-slate-950 border border-slate-700 rounded-lg text-slate-200 focus:outline-none focus:border-cyan-400 cursor-pointer"
              >
                <option value="downloads_cache">Installer / Staged Torrent</option>
                <option value="active_soundbanks">Active Soundbank</option>
                <option value="audio_scratch">Audio Scratch / Stem Temp</option>
                <option value="crash_dumps">Crash Dumps</option>
                <option value="autosave_backups">Autosave Backups</option>
                <option value="plugin_ir_cache">Plugin IR Cache</option>
              </select>
            </div>

            <div className="space-y-1.5">
              <label className="text-slate-300 font-semibold">Estimated Size (GB)</label>
              <input
                type="number"
                step="0.1"
                min="0.1"
                required
                value={sizeGb}
                onChange={(e) => setSizeGb(e.target.value)}
                className="w-full p-2.5 bg-slate-950 border border-slate-700 rounded-lg font-mono text-slate-200 focus:outline-none focus:border-cyan-400"
              />
            </div>
          </div>

          <div className="pt-3 border-t border-slate-800 flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 font-medium text-slate-300 hover:text-white bg-slate-800 rounded-lg transition-colors cursor-pointer"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="px-4 py-2 font-semibold text-slate-950 bg-cyan-400 hover:bg-cyan-300 rounded-lg transition-colors inline-flex items-center gap-1.5 cursor-pointer"
            >
              <Plus className="w-4 h-4" />
              <span>Add to Audit</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};
