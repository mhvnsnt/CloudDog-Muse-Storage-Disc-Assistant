import React, { useState } from 'react';
import { 
  Cpu, 
  Layers, 
  Zap, 
  Activity, 
  CheckCircle2, 
  ShieldCheck, 
  Sliders, 
  HardDrive,
  RefreshCw,
  Server,
  CloudLightning
} from 'lucide-react';
import { ComputeOffloadState } from '../types';
import { formatBytes } from '../data/mockStorageData';

interface ComputeOffloadPanelProps {
  computeState: ComputeOffloadState;
  onUpdateVvramSize: (sizeGb: number) => void;
  onToggleRemoteGpu: (provider: string) => void;
  onBroadcastComputeEvent: (message: string) => void;
}

export const ComputeOffloadPanel: React.FC<ComputeOffloadPanelProps> = ({
  computeState,
  onUpdateVvramSize,
  onToggleRemoteGpu,
  onBroadcastComputeEvent,
}) => {
  const [selectedSizeGb, setSelectedSizeGb] = useState<number>(
    Math.round(computeState.virtualVramTotalBytes / (1024 * 1024 * 1024))
  );
  const [isApplying, setIsApplying] = useState(false);

  const handleApplyVvram = () => {
    setIsApplying(true);
    setTimeout(() => {
      onUpdateVvramSize(selectedSizeGb);
      onBroadcastComputeEvent(
        `Allocated ${selectedSizeGb} GB non-physical vVRAM via RAM paging & free cloud GPU bridges.`
      );
      setIsApplying(false);
    }, 600);
  };

  const currentVvramGb = Math.round(computeState.virtualVramTotalBytes / (1024 * 1024 * 1024));
  const usedVvramGb = Math.round(computeState.virtualVramUsedBytes / (1024 * 1024 * 1024));

  return (
    <div className="space-y-8">
      {/* Compute Header */}
      <div className="p-6 bg-slate-900/80 border border-slate-800 rounded-xl space-y-4">
        <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-purple-950/60 border border-purple-800/80 flex items-center justify-center text-purple-400">
                <Cpu className="w-4 h-4" />
              </div>
              <h2 className="text-xl font-bold tracking-tight text-white">
                Compute, RAM & Non-Physical Virtual VRAM (vVRAM)
              </h2>
            </div>
            <p className="text-xs text-slate-400 mt-1 max-w-3xl leading-relaxed">
              Virtualizes high-capacity video memory across your host system RAM, NVMe Zero-Offload buffers, and free cloud GPU workers (Kaggle 2x T4 & Google Colab). Run massive multi-gigabyte orchestral soundbanks without needing expensive physical hardware.
            </p>
          </div>

          <div className="flex items-center gap-3 p-3 bg-slate-950/80 border border-slate-800 rounded-lg text-right shrink-0">
            <div>
              <div className="text-[11px] text-purple-300 font-medium">Active Virtual VRAM</div>
              <div className="text-xl font-bold font-mono text-purple-200 tabular-nums">
                {currentVvramGb} GB vVRAM
              </div>
            </div>
            <div className="h-8 w-px bg-slate-800" aria-hidden="true" />
            <div>
              <div className="text-[11px] text-emerald-400 font-medium">Hardware Cost</div>
              <div className="text-xl font-bold font-mono text-emerald-300 tabular-nums">
                $0.00 (Free)
              </div>
            </div>
          </div>
        </div>

        {/* 4-Column Compute Telemetry Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 pt-2">
          {/* CPU Card */}
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400 font-medium">CPU Core Allocation</span>
              <span className="font-mono text-cyan-300 tabular-nums">{computeState.cpuCores} Threads</span>
            </div>
            <div className="text-2xl font-bold font-mono text-white tabular-nums">
              {computeState.cpuUsagePct}% Load
            </div>
            <div className="w-full h-1.5 bg-slate-900 rounded-full overflow-hidden">
              <div
                className="h-full bg-cyan-400 transition-all duration-300"
                style={{ width: `${computeState.cpuUsagePct}%` }}
              />
            </div>
          </div>

          {/* Physical RAM */}
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400 font-medium">Physical System RAM</span>
              <span className="font-mono text-slate-400 tabular-nums">{formatBytes(computeState.physicalRamTotalBytes)}</span>
            </div>
            <div className="text-2xl font-bold font-mono text-white tabular-nums">
              {formatBytes(computeState.physicalRamUsedBytes)} Used
            </div>
            <div className="w-full h-1.5 bg-slate-900 rounded-full overflow-hidden">
              <div
                className="h-full bg-emerald-400 transition-all duration-300"
                style={{
                  width: `${(computeState.physicalRamUsedBytes / computeState.physicalRamTotalBytes) * 100}%`,
                }}
              />
            </div>
          </div>

          {/* Physical GPU */}
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-slate-400 font-medium">Physical GPU VRAM</span>
              <span className="font-mono text-slate-400 tabular-nums">{formatBytes(computeState.physicalVramTotalBytes)}</span>
            </div>
            <div className="text-2xl font-bold font-mono text-white tabular-nums">
              {formatBytes(computeState.physicalVramUsedBytes)} Used
            </div>
            <div className="w-full h-1.5 bg-slate-900 rounded-full overflow-hidden">
              <div
                className="h-full bg-amber-400 transition-all duration-300"
                style={{
                  width: `${(computeState.physicalVramUsedBytes / computeState.physicalVramTotalBytes) * 100}%`,
                }}
              />
            </div>
          </div>

          {/* Non-Physical Virtual VRAM */}
          <div className="p-4 bg-slate-950/60 border border-purple-800/40 rounded-lg space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="text-purple-300 font-medium">Non-Physical vVRAM</span>
              <span className="font-mono text-purple-300 tabular-nums">{currentVvramGb} GB</span>
            </div>
            <div className="text-2xl font-bold font-mono text-purple-200 tabular-nums">
              {usedVvramGb} GB Loaded
            </div>
            <div className="w-full h-1.5 bg-slate-900 rounded-full overflow-hidden">
              <div
                className="h-full bg-purple-400 transition-all duration-300"
                style={{ width: `${(usedVvramGb / currentVvramGb) * 100}%` }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* Virtual VRAM (vVRAM) Allocator Controls */}
      <div className="p-6 bg-slate-900/60 border border-slate-800 rounded-xl space-y-5">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div>
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <Sliders className="w-4 h-4 text-purple-400" />
              <span>Virtual VRAM Memory Scaler</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Choose non-physical VRAM size. Allocates RAM swap buffers and transparent paging for audio synthesis.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleApplyVvram}
              disabled={isApplying}
              className="px-4 py-2 text-xs font-semibold text-slate-950 bg-purple-400 hover:bg-purple-300 rounded-lg transition-colors cursor-pointer disabled:opacity-40 inline-flex items-center gap-1.5"
            >
              {isApplying ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  <span>Configuring Pagers...</span>
                </>
              ) : (
                <>
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  <span>Apply {selectedSizeGb} GB vVRAM</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Preset Size Buttons */}
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs">
          {[32, 64, 96, 128].map((size) => (
            <button
              key={size}
              onClick={() => setSelectedSizeGb(size)}
              className={`p-3 rounded-lg border text-left transition-all cursor-pointer ${
                selectedSizeGb === size
                  ? 'border-purple-500/80 bg-purple-950/30 text-purple-200 shadow-sm'
                  : 'border-slate-800 bg-slate-950/60 text-slate-300 hover:border-slate-700'
              }`}
            >
              <div className="font-mono font-bold text-lg tabular-nums">{size} GB</div>
              <div className="text-[11px] text-slate-400 mt-0.5">
                {size === 32 && 'Basic Orchestral Set'}
                {size === 64 && 'Full Symphonic Library (Standard)'}
                {size === 96 && 'Heavy Multi-Mic Stems'}
                {size === 128 && 'Massive Hollywood Film Score'}
              </div>
            </button>
          ))}
        </div>

        {/* Free Cloud GPU Worker Bridges */}
        <div className="pt-2 space-y-3">
          <div className="text-xs font-semibold text-slate-200">
            Free Remote GPU Bridges (Zero-Cost Compute)
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs">
            {computeState.activeRemoteGpus.map((gpu) => (
              <div
                key={gpu.provider}
                className="p-3 bg-slate-950/80 border border-slate-800 rounded-lg flex items-center justify-between"
              >
                <div>
                  <div className="font-semibold text-slate-200">
                    {gpu.provider === 'kaggle_free_t4' && 'Kaggle Dual T4 GPUs'}
                    {gpu.provider === 'colab_free_t4' && 'Google Colab T4 Node'}
                    {gpu.provider === 'hf_zero_gpu' && 'Hugging Face ZeroGPU'}
                  </div>
                  <div className="text-[11px] text-slate-500 font-mono">
                    +{gpu.allocatedVramGb} GB VRAM · Free Community Quota
                  </div>
                </div>

                <button
                  onClick={() => onToggleRemoteGpu(gpu.provider)}
                  className={`px-2.5 py-1 text-[11px] font-medium rounded cursor-pointer transition-colors ${
                    gpu.status === 'connected'
                      ? 'bg-emerald-950/60 border border-emerald-800/80 text-emerald-300'
                      : 'bg-slate-800 text-slate-400 hover:text-slate-200'
                  }`}
                >
                  {gpu.status === 'connected' ? 'Connected' : 'Connect'}
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
};
