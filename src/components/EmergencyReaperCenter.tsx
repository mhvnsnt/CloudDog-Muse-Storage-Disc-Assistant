import React, { useState } from 'react';
import { 
  AlertTriangle, 
  Flame, 
  Trash2, 
  CheckCircle2, 
  Clock, 
  RefreshCw, 
  Zap, 
  ShieldAlert, 
  ShieldCheck, 
  Activity, 
  ArrowDown, 
  FileCheck,
  Server,
  Layers,
  Sparkles
} from 'lucide-react';
import { InFlightLaneTask, WatchdogSweepLog } from '../types';
import { formatBytes } from '../data/mockStorageData';

interface EmergencyReaperCenterProps {
  diskUsagePercentage: number;
  lanes: InFlightLaneTask[];
  watchdogLogs: WatchdogSweepLog[];
  onReapScoobyBlobs: () => void;
  onReap8PriorityFiles: () => void;
  onForceWatchdogSweep: () => void;
  onCompleteCommitStep: () => void;
  isProcessing: boolean;
}

export const EmergencyReaperCenter: React.FC<EmergencyReaperCenterProps> = ({
  diskUsagePercentage,
  lanes,
  watchdogLogs,
  onReapScoobyBlobs,
  onReap8PriorityFiles,
  onForceWatchdogSweep,
  onCompleteCommitStep,
  isProcessing,
}) => {
  const scoobyLane = lanes.find((l) => l.id === 'lane-scooby');
  const priority8Lane = lanes.find((l) => l.id === 'lane-8-files');
  const adventureLane = lanes.find((l) => l.id === 'lane-adventure-time');
  const gumballLane = lanes.find((l) => l.id === 'lane-gumball');

  const totalInFlightBytes = lanes.reduce((acc, l) => acc + l.remainingLocalBytes, 0);
  const totalReapedSoFar = lanes.reduce((acc, l) => acc + l.reapedLocalBytes, 0);

  const isPopupBreached = diskUsagePercentage >= 90.0;

  return (
    <div className="space-y-8">
      {/* Critical Disk Alarm Banner (96% / 90% Popup Band Breached) */}
      <div className={`p-6 rounded-xl border space-y-4 ${
        isPopupBreached
          ? 'bg-rose-950/40 border-rose-600/80 shadow-lg shadow-rose-950/50'
          : 'bg-slate-900/80 border-slate-800'
      }`}>
        <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div className="space-y-1">
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-rose-500/20 border border-rose-500/40 flex items-center justify-center text-rose-400">
                <AlertTriangle className="w-5 h-5 animate-pulse" />
              </div>
              <h2 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
                <span>Emergency Recovery: In-Flight Stream Reaper</span>
                {isPopupBreached && (
                  <span className="text-xs px-2 py-0.5 rounded bg-rose-900/80 border border-rose-700 text-rose-200 font-mono font-normal">
                    POPUP BAND BREACHED ({diskUsagePercentage.toFixed(1)}%)
                  </span>
                )}
              </h2>
            </div>
            <p className="text-xs text-slate-300 max-w-3xl leading-relaxed mt-1">
              <strong>THE IN-FLIGHT REAPER RULE:</strong> Do not wait for 100% upload completion. As each file or blob hash verifies against GitHub/TeraBox, the local copy is deleted on the spot. Local disk drops <em>while</em> uploads run instead of after.
            </p>
          </div>

          <div className="flex items-center gap-4 bg-slate-950/90 border border-slate-800 p-3.5 rounded-lg text-right shrink-0">
            <div>
              <div className="text-[11px] text-slate-400 font-medium">Current Disk Usage</div>
              <div className={`text-2xl font-bold font-mono tabular-nums ${isPopupBreached ? 'text-rose-400' : 'text-emerald-400'}`}>
                {diskUsagePercentage.toFixed(1)}%
              </div>
              <div className="text-[10px] text-slate-500">Popup Line: 90.0%</div>
            </div>
            <div className="h-10 w-px bg-slate-800" aria-hidden="true" />
            <div>
              <div className="text-[11px] text-cyan-300 font-medium">Queued to Clear as Landed</div>
              <div className="text-2xl font-bold font-mono text-cyan-400 tabular-nums">
                ~12.0 GB
              </div>
              <div className="text-[10px] text-emerald-400 font-mono">+{formatBytes(totalReapedSoFar)} Reaped</div>
            </div>
          </div>
        </div>

        {/* Disk Watermark Progression Bar */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs text-slate-400 font-mono">
            <span>Critical Ceiling: 96%</span>
            <span>Popup Trigger Line: 90%</span>
            <span>Standing Law Target: &lt; 75%</span>
          </div>
          <div className="h-3.5 w-full bg-slate-950 rounded-full overflow-hidden border border-slate-800 relative">
            <div
              className={`h-full transition-all duration-500 ${
                diskUsagePercentage > 90 ? 'bg-rose-500' : diskUsagePercentage > 75 ? 'bg-amber-400' : 'bg-emerald-400'
              }`}
              style={{ width: `${Math.min(100, diskUsagePercentage)}%` }}
            />
            {/* 75% Law line */}
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-emerald-400 z-10"
              style={{ left: '75%' }}
              title="75% No-Clutter Target"
            />
            {/* 90% Popup line */}
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-rose-400 z-10"
              style={{ left: '90%' }}
              title="90% Popup Line"
            />
          </div>
        </div>

        {/* Quick Emergency Actions */}
        <div className="flex flex-wrap items-center justify-between gap-3 pt-2 text-xs">
          <div className="text-slate-400">
            Active upload lanes moving simultaneously through slow proxy. 10-minute watchdog loop sweeping continually.
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={onForceWatchdogSweep}
              disabled={isProcessing}
              className="px-3.5 py-2 font-medium text-slate-200 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg transition-colors cursor-pointer inline-flex items-center gap-1.5"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${isProcessing ? 'animate-spin' : ''}`} />
              <span>Force Immediate Watchdog Sweep</span>
            </button>
            <button
              onClick={onReapScoobyBlobs}
              disabled={isProcessing || (scoobyLane && scoobyLane.blobsVerified === scoobyLane.blobsTotal)}
              className="px-4 py-2 font-semibold text-slate-950 bg-gradient-to-r from-cyan-400 to-cyan-300 hover:from-cyan-300 hover:to-cyan-200 rounded-lg transition-colors cursor-pointer inline-flex items-center gap-1.5 shadow-sm"
            >
              <Trash2 className="w-3.5 h-3.5" />
              <span>Reap Remaining 47 Scooby Blobs</span>
            </button>
          </div>
        </div>
      </div>

      {/* Scooby Lane & Priority 8 Files Spotlight Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Card 1: Scooby Lane (670 / 717 blobs) */}
        <div className="p-6 bg-slate-900/70 border border-slate-800 rounded-xl space-y-4">
          <div className="flex items-start justify-between pb-3 border-b border-slate-800">
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-base font-bold text-white">Scooby Lane (Dedicated Stream Reaper)</h3>
                <span className="font-mono text-xs text-cyan-400 font-semibold">
                  {scoobyLane?.blobsVerified} of {scoobyLane?.blobsTotal} blobs
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-1">
                Local blobs delete on the spot as GitHub hash confirms. 47 blobs remaining to final commit step.
              </p>
            </div>
            <span className="font-mono text-xs text-emerald-400 font-semibold tabular-nums shrink-0">
              +{formatBytes(scoobyLane?.reapedLocalBytes || 0)} Freed
            </span>
          </div>

          {/* Blobs Progress Bar */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs font-mono">
              <span className="text-slate-400">
                Verified: {scoobyLane?.blobsVerified}/{scoobyLane?.blobsTotal}
              </span>
              <span className="text-cyan-300">
                {(((scoobyLane?.blobsVerified || 0) / (scoobyLane?.blobsTotal || 1)) * 100).toFixed(1)}%
              </span>
            </div>
            <div className="h-3 w-full bg-slate-950 rounded-full overflow-hidden border border-slate-800">
              <div
                className="h-full bg-gradient-to-r from-cyan-500 to-emerald-400 transition-all duration-500"
                style={{
                  width: `${((scoobyLane?.blobsVerified || 0) / (scoobyLane?.blobsTotal || 1)) * 100}%`,
                }}
              />
            </div>
          </div>

          <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg text-xs space-y-1">
            <div className="text-slate-300 flex items-center justify-between">
              <span>Remaining Local Footprint:</span>
              <span className="font-mono font-bold text-rose-400 tabular-nums">
                {formatBytes(scoobyLane?.remainingLocalBytes || 0)} (47 blobs left)
              </span>
            </div>
            <p className="text-[11px] text-slate-500">
              Watchdog nursing final commit step so nothing sits unreferenced in the repository manifest.
            </p>
          </div>

          <div className="pt-2 flex items-center gap-3">
            <button
              onClick={onReapScoobyBlobs}
              disabled={isProcessing || (scoobyLane?.blobsVerified === scoobyLane?.blobsTotal)}
              className="px-4 py-2 text-xs font-semibold text-slate-950 bg-cyan-400 hover:bg-cyan-300 rounded-lg transition-colors cursor-pointer disabled:opacity-40 inline-flex items-center gap-1.5"
            >
              <Zap className="w-3.5 h-3.5" />
              <span>Step Next 47 Blobs & Delete Local</span>
            </button>
            <button
              onClick={onCompleteCommitStep}
              disabled={isProcessing}
              className="px-4 py-2 text-xs font-medium text-slate-300 hover:text-white bg-slate-800 hover:bg-slate-700 rounded-lg transition-colors cursor-pointer"
            >
              Finalize Commit Step
            </button>
          </div>
        </div>

        {/* Card 2: Priority 8 Files Team (DOA4 tars, DXMMII, MP4s) */}
        <div className="p-6 bg-slate-900/70 border border-slate-800 rounded-xl space-y-4">
          <div className="flex items-start justify-between pb-3 border-b border-slate-800">
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-base font-bold text-white">Priority 8 Files Team</h3>
                <span className="font-mono text-xs text-amber-400 font-semibold">
                  3.7 GB Total
                </span>
              </div>
              <p className="text-xs text-slate-400 mt-1">
                DOA4 tars, DXMMII parts, and 2 MP4s uploading through slow proxy. Deletes second hash verifies.
              </p>
            </div>
            <span className="font-mono text-xs text-emerald-400 font-semibold tabular-nums shrink-0">
              {priority8Lane?.blobsVerified} of {priority8Lane?.blobsTotal} Done
            </span>
          </div>

          <div className="space-y-2 text-xs">
            <div className="p-2.5 bg-slate-950/70 border border-slate-800 rounded-lg flex items-center justify-between">
              <span className="font-mono text-slate-300">DOA4_Soundtrack_Raw_Disc_Tars.tar</span>
              <span className="text-cyan-400 font-mono">1.45 GB · Uploading (78%)</span>
            </div>
            <div className="p-2.5 bg-slate-950/70 border border-slate-800 rounded-lg flex items-center justify-between">
              <span className="font-mono text-slate-300">DXMMII_Multitrack_Parts.part01.rar</span>
              <span className="text-cyan-400 font-mono">1.28 GB · Uploading (62%)</span>
            </div>
            <div className="p-2.5 bg-slate-950/70 border border-slate-800 rounded-lg flex items-center justify-between">
              <span className="font-mono text-slate-300">Two Master Render Stems MP4</span>
              <span className="text-cyan-400 font-mono">970 MB · Uploading (85%)</span>
            </div>
          </div>

          <div className="pt-2 flex items-center justify-between">
            <span className="text-xs text-slate-400">
              Reclaims <strong>~3.7 GB</strong> instantly the second uploads land.
            </span>

            <button
              onClick={onReap8PriorityFiles}
              disabled={isProcessing}
              className="px-4 py-2 text-xs font-semibold text-slate-950 bg-amber-400 hover:bg-amber-300 rounded-lg transition-colors cursor-pointer inline-flex items-center gap-1.5"
            >
              <CheckCircle2 className="w-3.5 h-3.5" />
              <span>Verify Hashes & Purge Local 3.7GB</span>
            </button>
          </div>
        </div>
      </div>

      {/* Watchdog Scheduler Log (7:24, 7:54, 8:04, 8:34, 9:04, 9:24, next queued) */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden p-5 space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-3 border-b border-slate-800">
          <div>
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <Clock className="w-4 h-4 text-cyan-400" />
              <span>10-Minute Loop Watchdog Execution History</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Fires every 10 minutes continuously all night. Proves real automated background hygiene.
            </p>
          </div>

          <div className="flex items-center gap-2 text-xs text-slate-400 font-mono">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />
            <span>Next sweep queued now</span>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400">
                <th className="py-2.5 px-3 font-semibold">Run Timestamp</th>
                <th className="py-2.5 px-3 font-semibold">Actions & Work Executed</th>
                <th className="py-2.5 px-3 font-semibold text-right">Freed Space</th>
                <th className="py-2.5 px-3 font-semibold text-right">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-mono">
              {watchdogLogs.map((log) => (
                <tr key={log.id} className="hover:bg-slate-800/30 transition-colors">
                  <td className="py-2.5 px-3 text-cyan-300 font-semibold tabular-nums">
                    {log.timestamp}
                  </td>
                  <td className="py-2.5 px-3 font-sans text-slate-300">
                    {log.summary}
                  </td>
                  <td className="py-2.5 px-3 text-right text-emerald-400 font-semibold tabular-nums">
                    {log.freedBytes > 0 ? `+${formatBytes(log.freedBytes)}` : '—'}
                  </td>
                  <td className="py-2.5 px-3 text-right">
                    {log.status === 'success' ? (
                      <span className="text-emerald-400 font-medium font-sans">
                        ✓ Completed
                      </span>
                    ) : (
                      <span className="text-cyan-400 font-medium font-sans">
                        Queued Now
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Additional Active Pipelines: Adventure Time & Gumball */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
        <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-1.5">
          <div className="font-semibold text-slate-200 flex items-center justify-between">
            <span>Adventure Time Parts</span>
            <span className="font-mono text-cyan-400">15 of 17 Up</span>
          </div>
          <p className="text-[11px] text-slate-400">
            3.7 GB already deleted. 2 parts left and local source deletes immediately upon verification.
          </p>
        </div>

        <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-1.5">
          <div className="font-semibold text-slate-200 flex items-center justify-between">
            <span>Gumball Upload</span>
            <span className="font-mono text-amber-400">Restarted Clean</span>
          </div>
          <p className="text-[11px] text-slate-400">
            Recovered cleanly from proxy flake. Watchdog actively nursing connection.
          </p>
        </div>

        <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-1.5">
          <div className="font-semibold text-slate-200 flex items-center justify-between">
            <span>Johnny Test Push</span>
            <span className="font-mono text-purple-400">Streaming</span>
          </div>
          <p className="text-[11px] text-slate-400">
            Continuous cloud mirror active. Verified chunks deleted on-the-spot.
          </p>
        </div>
      </div>
    </div>
  );
};
