import React, { useState } from 'react';
import { 
  HardDrive, 
  RefreshCw, 
  CheckCircle2, 
  FolderPlus, 
  RotateCcw,
  Sparkles,
  Terminal,
  ShieldCheck,
  AlertCircle,
  Cloud,
  Cpu,
  Radio,
  Flame,
  Zap
} from 'lucide-react';
import { 
  OSPlatform, 
  ClutterCategoryId, 
  ClutterItem, 
  TerminalEntry,
  TorrentMirrorTask,
  ComputeOffloadState,
  AgentLane,
  MemoryLogRecord,
  CloudStorageTarget,
  InFlightLaneTask,
  WatchdogSweepLog
} from './types';
import { 
  INITIAL_CLUTTER_ITEMS, 
  INITIAL_TORRENT_TASKS,
  INITIAL_COMPUTE_STATE,
  INITIAL_AGENT_LANES,
  INITIAL_MEMORY_LOGS,
  INITIAL_IN_FLIGHT_LANES,
  INITIAL_WATCHDOG_LOGS,
  formatBytes 
} from './data/mockStorageData';
import { Header } from './components/Header';
import { StorageOverview } from './components/StorageOverview';
import { ClutterTable } from './components/ClutterTable';
import { RelocationWizard } from './components/RelocationWizard';
import { TerminalSimulator } from './components/TerminalSimulator';
import { DaemonScheduler } from './components/DaemonScheduler';
import { RepoExplorer } from './components/RepoExplorer';
import { TorrentCloudMirror } from './components/TorrentCloudMirror';
import { ComputeOffloadPanel } from './components/ComputeOffloadPanel';
import { AgentMemoryBroadcast } from './components/AgentMemoryBroadcast';
import { EmergencyReaperCenter } from './components/EmergencyReaperCenter';
import { DryRunModal } from './components/DryRunModal';
import { CustomPathModal } from './components/CustomPathModal';

export default function App() {
  const [platform, setPlatform] = useState<OSPlatform>('macos');
  const [items, setItems] = useState<ClutterItem[]>(INITIAL_CLUTTER_ITEMS.macos);
  const [tasks, setTasks] = useState<TorrentMirrorTask[]>(INITIAL_TORRENT_TASKS);
  const [computeState, setComputeState] = useState<ComputeOffloadState>(INITIAL_COMPUTE_STATE);
  const [lanes, setLanes] = useState<AgentLane[]>(INITIAL_AGENT_LANES);
  const [memoryLogs, setMemoryLogs] = useState<MemoryLogRecord[]>(INITIAL_MEMORY_LOGS);
  const [inFlightLanes, setInFlightLanes] = useState<InFlightLaneTask[]>(INITIAL_IN_FLIGHT_LANES);
  const [watchdogLogs, setWatchdogLogs] = useState<WatchdogSweepLog[]>(INITIAL_WATCHDOG_LOGS);

  // Default to the Emergency Reaper tab to immediately address user's critical operation
  const [activeTab, setActiveTab] = useState<'reaper' | 'overview' | 'mirror' | 'compute' | 'agents' | 'terminal' | 'repo'>('reaper');
  const [selectedCategory, setSelectedCategory] = useState<ClutterCategoryId | 'all'>('all');
  const [isCleaning, setIsCleaning] = useState(false);
  const [isDryRunModalOpen, setIsDryRunModalOpen] = useState(false);
  const [isCustomPathModalOpen, setIsCustomPathModalOpen] = useState(false);

  // Dynamic disk usage: starts at 96.0% (Critical popup band breached)
  const [diskUsagePct, setDiskUsagePct] = useState<number>(96.0);

  const [notification, setNotification] = useState<{ message: string; type: 'success' | 'info' | 'error' } | null>({
    message: 'CRITICAL ALERT: Disk at 96.0% (Popup band breached). Scooby lane reaper active: 670/717 blobs verified, local chunks deleting on the spot.',
    type: 'error',
  });

  // Terminal log state
  const [logs, setLogs] = useState<TerminalEntry[]>([
    {
      id: 'init-1',
      timestamp: '09:32:00',
      command: 'muse-assist reaper --lane scooby --status',
      output: [
        '⚠️ DISK USAGE: 96.0% (CRITICAL POPUP BAND BREACHED)',
        'Scooby Lane: 670 of 717 blobs uploaded to GitHub.',
        'Stream Reaper: Local chunks deleting on the spot as hashes verify.',
        'Watchdog Loop: Nursing final commit step (47 blobs to go).',
        'Queued to clear as uploads land: ~12.0 GB (DOA4 tars, DXMMII, MP4s, Scooby chunks).',
      ],
      type: 'warning',
    },
    {
      id: 'init-2',
      timestamp: '09:32:45',
      command: 'muse-assist watchdog --audit-night-loop',
      output: [
        'Watchdog scheduler proof: runs executed all night at 7:24, 7:54, 8:04, 8:34, 9:04, 9:24.',
        'Cleared: 11G dead download dir, 445 Space Ghost files, Gumball parts, partial chunks.',
        'Flight-assist pass finished: 8.4GB cleared.',
        'Current bottleneck: In-flight downloads landing faster than uploads finish across proxy.',
      ],
      type: 'info',
    },
  ]);

  // Handle platform change
  const handleSetPlatform = (newPlat: OSPlatform) => {
    setPlatform(newPlat);
    setItems(INITIAL_CLUTTER_ITEMS[newPlat]);
    setNotification({
      message: `Switched target OS environment to ${newPlat.toUpperCase()}. Scanned paths updated.`,
      type: 'info',
    });
  };

  // Toggle item selection
  const handleToggleItem = (id: string) => {
    setItems((prev) =>
      prev.map((it) => (it.id === id ? { ...it, selected: !it.selected } : it))
    );
  };

  const handleSelectAllSafe = () => {
    setItems((prev) =>
      prev.map((it) => ({
        ...it,
        selected: it.isSafeToDelete,
      }))
    );
  };

  const handleClearSelection = () => {
    setItems((prev) => prev.map((it) => ({ ...it, selected: false })));
  };

  // 1. REAP REMAINING 47 SCOOBY BLOBS
  const handleReapScoobyBlobs = () => {
    setIsCleaning(true);

    setTimeout(() => {
      setInFlightLanes((prev) =>
        prev.map((l) =>
          l.id === 'lane-scooby'
            ? {
                ...l,
                blobsVerified: 717,
                reapedLocalBytes: 8_200_000_000,
                remainingLocalBytes: 0,
                status: 'final_commit_pending',
                notes: 'All 717 blobs verified on GitHub! Local chunks completely reaped on the spot. Ready for final commit step.',
              }
            : l
        )
      );

      // Drops disk from 96% down through 91%
      setDiskUsagePct((prev) => Math.max(74, prev - 5.2));
      setIsCleaning(false);

      const newHash = `0x${Math.random().toString(16).slice(2, 18)}`;
      const logEntry: MemoryLogRecord = {
        id: `mem-${Date.now()}`,
        timestamp: new Date().toISOString().slice(0, 19).replace('T', ' '),
        agentId: 'agent-scooby-reaper',
        category: 'cloud_offload',
        message: 'SCOOBY LANE 717/717 BLOBS VERIFIED: Final 47 blobs uploaded to GitHub and local copies deleted on the spot. Manifest locked.',
        hash: newHash,
      };
      setMemoryLogs((prev) => [logEntry, ...prev]);

      setNotification({
        message: 'Scooby Lane: All 717 blobs verified! Local copies reaped on the spot. Disk dropped down to 90.8%. Ready for final commit step.',
        type: 'success',
      });

      setLogs((prev) => [
        ...prev,
        {
          id: `scooby-${Date.now()}`,
          timestamp: new Date().toLocaleTimeString(),
          command: 'muse-assist reaper --lane scooby --reap-all-verified',
          output: [
            '⚡ Reaping remaining 47 Scooby blobs against GitHub...',
            '   ✓ Blobs 671-717: SHA-256 verified against remote tree.',
            '   ✓ Local chunks deleted on the spot (Freed ~540 MB).',
            '   ✓ Manifest updated: 717 of 717 blobs referenced.',
            '📉 Disk usage reduced. Ready for commit step.',
          ],
          type: 'success',
        },
      ]);
    }, 700);
  };

  // 2. FINALIZE SCOOBY COMMIT STEP
  const handleCompleteCommitStep = () => {
    setIsCleaning(true);
    setTimeout(() => {
      setInFlightLanes((prev) =>
        prev.map((l) =>
          l.id === 'lane-scooby'
            ? {
                ...l,
                status: 'verified_and_cleared',
                notes: 'Commit step completed! Clean tree committed to GitHub main. 0 unreferenced blobs.',
              }
            : l
        )
      );
      setDiskUsagePct((prev) => Math.max(73, prev - 1.5));
      setIsCleaning(false);

      setNotification({
        message: 'Scooby commit step finished! Nothing sits unreferenced. Staged archive evicted.',
        type: 'success',
      });
    }, 500);
  };

  // 3. REAP 8 PRIORITY FILES (DOA4 tars, DXMMII, MP4s ~3.7 GB)
  const handleReap8PriorityFiles = () => {
    setIsCleaning(true);

    setTimeout(() => {
      setInFlightLanes((prev) =>
        prev.map((l) =>
          l.id === 'lane-8-files'
            ? {
                ...l,
                blobsVerified: 8,
                reapedLocalBytes: 3_700_000_000,
                remainingLocalBytes: 0,
                status: 'verified_and_cleared',
                notes: 'All 8 files (DOA4 tars, DXMMII parts, MP4s) verified on GitHub and deleted immediately from disk.',
              }
            : l
        )
      );

      // Drops disk further by 3.7GB (~7.2%)
      setDiskUsagePct((prev) => Math.max(72, prev - 7.2));
      setIsCleaning(false);

      const newHash = `0x${Math.random().toString(16).slice(2, 18)}`;
      const logEntry: MemoryLogRecord = {
        id: `mem-${Date.now()}`,
        timestamp: new Date().toISOString().slice(0, 19).replace('T', ' '),
        agentId: 'agent-8-files-team',
        category: 'cloud_offload',
        message: 'PRIORITY 8 FILES VERIFIED & PURGED: DOA4 tars, DXMMII parts, and 2 MP4s stored to GitHub. 3.7 GB freed from local disk.',
        hash: newHash,
      };
      setMemoryLogs((prev) => [logEntry, ...prev]);

      setNotification({
        message: 'Priority 8 Files verified and purged! Reclaimed 3.7 GB. Disk dropped below popup line!',
        type: 'success',
      });

      setLogs((prev) => [
        ...prev,
        {
          id: `files-8-${Date.now()}`,
          timestamp: new Date().toLocaleTimeString(),
          command: 'muse-assist reaper --team priority-8 --verify-and-purge',
          output: [
            '📦 Verifying 8 priority files against GitHub remote...',
            '   ✓ DOA4_Soundtrack_Raw_Disc_Tars.tar (1.45 GB) -> Verified & Deleted',
            '   ✓ DXMMII_Multitrack_Parts.part01.rar (1.28 GB) -> Verified & Deleted',
            '   ✓ Master Render Stem 1 MP4 (485 MB) -> Verified & Deleted',
            '   ✓ Master Render Stem 2 MP4 (485 MB) -> Verified & Deleted',
            '🎉 Reclaimed 3.7 GB instantly! Local disk cleared.',
          ],
          type: 'success',
        },
      ]);
    }, 700);
  };

  // 4. FORCE WATCHDOG SWEEP
  const handleForceWatchdogSweep = () => {
    setIsCleaning(true);

    setTimeout(() => {
      const nowTime = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const newSweep: WatchdogSweepLog = {
        id: `wd-${Date.now()}`,
        timestamp: nowTime,
        durationSeconds: 19,
        freedBytes: 4_200_000_000,
        summary: 'Manual force sweep: pruned Adventure Time final chunks & Gumball partials. Nursed proxy channels.',
        status: 'success',
      };

      setWatchdogLogs((prev) => [newSweep, ...prev]);
      setDiskUsagePct((prev) => Math.max(68, prev - 8.5));
      setIsCleaning(false);

      setNotification({
        message: 'Watchdog sweep executed! Pruned 4.2 GB of partial chunks. Disk dropped to compliant range.',
        type: 'success',
      });

      setLogs((prev) => [
        ...prev,
        {
          id: `wd-sweep-${Date.now()}`,
          timestamp: new Date().toLocaleTimeString(),
          command: 'muse-assist watchdog --sweep-now',
          output: [
            '🐕 10-Minute Loop Watchdog executing immediate sweep...',
            '   ✓ Checked active upload pipelines (Adventure Time, Gumball, Johnny Test).',
            '   ✓ Pruned 4.2 GB of dead download staging and verified chunks.',
            '📉 Disk usage successfully dropped down below the 75% Law ceiling!',
          ],
          type: 'success',
        },
      ]);
    }, 800);
  };

  // Enforce NO-LOCAL-CLUTTER LAW (Clears everything across the board)
  const handleEnforceLawNow = () => {
    setIsCleaning(true);
    const safeCandidates = items.filter((it) => it.isSafeToDelete);
    const reclaimedBytes = safeCandidates.reduce((acc, it) => acc + it.sizeBytes, 0);

    setTimeout(() => {
      setItems((prev) => prev.filter((it) => !it.isSafeToDelete));
      setInFlightLanes((prev) =>
        prev.map((l) => ({
          ...l,
          blobsVerified: l.blobsTotal,
          reapedLocalBytes: l.totalSizeBytes,
          remainingLocalBytes: 0,
          status: 'verified_and_cleared',
        }))
      );

      // Plummet disk down to 68% (under the 75% law!)
      setDiskUsagePct(68.2);
      setIsCleaning(false);
      
      const newHash = `0x${Math.random().toString(16).slice(2, 18)}`;
      const logEntry: MemoryLogRecord = {
        id: `mem-${Date.now()}`,
        timestamp: new Date().toISOString().slice(0, 19).replace('T', ' '),
        agentId: 'agent-orchestrator',
        category: 'law_enforcement',
        message: `NO-LOCAL-CLUTTER LAW FULLY ENFORCED: All in-flight lanes cleared upon verification. Disk dropped to 68.2% (< 75% limit).`,
        hash: newHash,
      };

      setMemoryLogs((prev) => [logEntry, ...prev]);

      setNotification({
        message: 'NO-LOCAL-CLUTTER LAW RESTORED: Disk dropped to 68.2% (safely below 75% threshold). Popup dismissed!',
        type: 'success',
      });

      setLogs((prev) => [
        ...prev,
        {
          id: `law-${Date.now()}`,
          timestamp: new Date().toLocaleTimeString(),
          command: 'muse-assist enforce-law --force-all-lanes',
          output: [
            '⚖️ Enforcing NO-LOCAL-CLUTTER LAW across all lanes and chats...',
            '   ✓ Scooby Lane: 717/717 blobs verified & local reaped.',
            '   ✓ Priority 8 Files: 8/8 files verified & local purged.',
            '   ✓ Adventure Time & Gumball: Cleared 100%.',
            '📉 Disk usage plummeted from 96.0% -> 68.2%!',
            '🔒 Memory logged to AGENTS.md & MEMORY.md.',
          ],
          type: 'success',
        },
      ]);
    }, 800);
  };

  // Delete only manually selected items
  const handleDeleteSelected = () => {
    const selected = items.filter((it) => it.selected);
    if (selected.length === 0) return;

    setIsCleaning(true);
    const reclaimedBytes = selected.reduce((acc, it) => acc + it.sizeBytes, 0);

    setTimeout(() => {
      setItems((prev) => prev.filter((it) => !it.selected));
      setIsCleaning(false);
      setNotification({
        message: `Removed ${selected.length} items (${formatBytes(reclaimedBytes)} reclaimed).`,
        type: 'success',
      });
    }, 600);
  };

  // Add Torrent Mirror task
  const handleAddTorrent = (name: string, magnet: string, target: CloudStorageTarget, sizeGb: number) => {
    const sizeBytes = sizeGb * 1024 * 1024 * 1024;
    const newTask: TorrentMirrorTask = {
      id: `tor-${Date.now()}`,
      name,
      magnetOrUrl: magnet,
      totalSizeBytes: sizeBytes,
      downloadedBytes: sizeBytes,
      progressPercent: 100,
      status: 'local_evicted',
      cloudTarget: target,
      cloudPath: `${target}://MusicVault/${name.replace(/\.[^/.]+$/, '')}/`,
      hash: `0x${Math.random().toString(16).slice(2, 18)}`,
      speedMbps: 0,
      localCachedBytes: 0,
      addedAt: new Date().toISOString().slice(0, 16).replace('T', ' '),
    };

    setTasks((prev) => [newTask, ...prev]);

    const logEntry: MemoryLogRecord = {
      id: `mem-${Date.now()}`,
      timestamp: new Date().toISOString().slice(0, 19).replace('T', ' '),
      agentId: 'agent-torrent-mirror',
      category: 'torrent_mirror',
      message: `MIRRORED: ${name} (${formatBytes(sizeBytes)}) streamed directly to ${target.toUpperCase()}. Local disk: 0 B footprint.`,
      hash: newTask.hash,
    };
    setMemoryLogs((prev) => [logEntry, ...prev]);
  };

  const handleEvictLocal = (id: string) => {
    setTasks((prev) =>
      prev.map((t) => (t.id === id ? { ...t, localCachedBytes: 0, status: 'local_evicted' } : t))
    );
  };

  const handleUpdateVvramSize = (sizeGb: number) => {
    const sizeBytes = sizeGb * 1024 * 1024 * 1024;
    setComputeState((prev) => ({
      ...prev,
      virtualVramTotalBytes: sizeBytes,
      virtualVramUsedBytes: Math.round(sizeBytes * 0.42),
    }));
  };

  const handleToggleRemoteGpu = (provider: string) => {
    setComputeState((prev) => ({
      ...prev,
      activeRemoteGpus: prev.activeRemoteGpus.map((g) =>
        g.provider === provider
          ? { ...g, status: g.status === 'connected' ? 'idle' : 'connected' }
          : g
      ),
    }));
  };

  const handleBroadcastCustomMessage = (msg: string) => {
    const newHash = `0x${Math.random().toString(16).slice(2, 18)}`;
    const newLog: MemoryLogRecord = {
      id: `mem-${Date.now()}`,
      timestamp: new Date().toISOString().slice(0, 19).replace('T', ' '),
      agentId: 'agent-memory-broadcaster',
      category: 'memory_sync',
      message: `BROADCAST: ${msg}`,
      hash: newHash,
    };
    setMemoryLogs((prev) => [newLog, ...prev]);

    setNotification({
      message: `Broadcast emitted to all agents & chats! Saved to AGENTS.md & MEMORY.md.`,
      type: 'success',
    });
  };

  const handleAddCustomItem = (newItemData: Omit<ClutterItem, 'id' | 'selected'>) => {
    const newItem: ClutterItem = {
      ...newItemData,
      id: `custom-${Date.now()}`,
      selected: newItemData.isSafeToDelete,
    };
    setItems((prev) => [newItem, ...prev]);
  };

  const handleExecuteCommand = (rawCmd: string) => {
    const cmd = rawCmd.trim();
    const ts = new Date().toLocaleTimeString();

    if (cmd === 'clear') {
      setLogs([]);
      return;
    }

    if (cmd.includes('scooby') || cmd.includes('reap')) {
      handleReapScoobyBlobs();
      return;
    }

    if (cmd.includes('watchdog') || cmd.includes('sweep')) {
      handleForceWatchdogSweep();
      return;
    }

    if (cmd.includes('enforce-law') || cmd.includes('enforce')) {
      handleEnforceLawNow();
      return;
    }

    setLogs((prev) => [
      ...prev,
      {
        id: `cmd-${Date.now()}`,
        timestamp: ts,
        command: cmd,
        output: [
          `Command executed: ${cmd}`,
          `Current Disk Usage: ${diskUsagePct.toFixed(1)}%`,
          'In-flight stream reaper active.',
        ],
        type: 'info',
      },
    ]);
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col font-sans">
      {/* Top Header */}
      <Header
        activeTab={activeTab}
        setActiveTab={setActiveTab}
        onEnforceLaw={handleEnforceLawNow}
        isCleaning={isCleaning}
        diskUsagePercentage={diskUsagePct}
      />

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-8">
        {/* Urgent Notification Banner */}
        {notification && (
          <div className={`p-3.5 rounded-xl border flex items-center justify-between gap-3 text-xs shadow-lg ${
            notification.type === 'error'
              ? 'bg-rose-950/80 border-rose-600/80 text-rose-200 shadow-rose-950/40'
              : notification.type === 'success'
              ? 'bg-emerald-950/80 border-emerald-600/80 text-emerald-200'
              : 'bg-slate-900/90 border-cyan-800/40 text-slate-300'
          }`}>
            <div className="flex items-center gap-2.5">
              {notification.type === 'error' ? (
                <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
              ) : (
                <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
              )}
              <span className="font-medium">{notification.message}</span>
            </div>
            <button
              onClick={() => setNotification(null)}
              className="text-slate-400 hover:text-white text-xs cursor-pointer font-medium"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Tab 1: Emergency Stream Reaper (Scooby, Priority 8, Watchdog) */}
        {activeTab === 'reaper' && (
          <EmergencyReaperCenter
            diskUsagePercentage={diskUsagePct}
            lanes={inFlightLanes}
            watchdogLogs={watchdogLogs}
            onReapScoobyBlobs={handleReapScoobyBlobs}
            onReap8PriorityFiles={handleReap8PriorityFiles}
            onForceWatchdogSweep={handleForceWatchdogSweep}
            onCompleteCommitStep={handleCompleteCommitStep}
            isProcessing={isCleaning}
          />
        )}

        {/* Tab 2: Storage Auditor & Table View */}
        {activeTab === 'overview' && (
          <div className="space-y-8">
            <StorageOverview
              items={items}
              currentPlatform={platform}
              setPlatform={handleSetPlatform}
              onPurgeSafe={handleEnforceLawNow}
              onPreviewDryRun={() => setIsDryRunModalOpen(true)}
              onNavigateRelocate={() => setActiveTab('reaper')}
              onNavigateCli={() => setActiveTab('terminal')}
              isCleaning={isCleaning}
              onFilterByCategory={setSelectedCategory}
              selectedCategory={selectedCategory}
            />

            <div className="flex items-center justify-between pt-4">
              <div>
                <h2 className="text-base font-bold text-white">Itemized Clutter Inventory</h2>
                <p className="text-xs text-slate-400">
                  Granular inspection of individual files, caches, soundbank folders, and diagnostic core dumps.
                </p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => setIsCustomPathModalOpen(true)}
                  className="px-3 py-1.5 text-xs font-medium text-slate-300 bg-slate-900 hover:bg-slate-800 border border-slate-800 rounded-lg transition-colors inline-flex items-center gap-1.5 cursor-pointer"
                >
                  <FolderPlus className="w-3.5 h-3.5 text-cyan-400" />
                  <span>Add Custom Path</span>
                </button>
              </div>
            </div>

            <ClutterTable
              items={items}
              onToggleItem={handleToggleItem}
              onSelectAllSafe={handleSelectAllSafe}
              onClearSelection={handleClearSelection}
              onDeleteSelected={handleDeleteSelected}
              selectedCategory={selectedCategory}
              setSelectedCategory={setSelectedCategory}
              isCleaning={isCleaning}
            />
          </div>
        )}

        {/* Tab 3: 1TB+ Cloud Torrent Mirroring */}
        {activeTab === 'mirror' && (
          <TorrentCloudMirror
            tasks={tasks}
            onAddTorrent={handleAddTorrent}
            onEvictLocal={handleEvictLocal}
            onBroadcastMirrorEvent={(name, size, target) => {
              handleBroadcastCustomMessage(`Mirrored ${name} (${size}) to ${target}. Local disk: 0 B.`);
            }}
          />
        )}

        {/* Tab 4: Compute & Non-Physical Virtual VRAM */}
        {activeTab === 'compute' && (
          <ComputeOffloadPanel
            computeState={computeState}
            onUpdateVvramSize={handleUpdateVvramSize}
            onToggleRemoteGpu={handleToggleRemoteGpu}
            onBroadcastComputeEvent={(msg) => handleBroadcastCustomMessage(msg)}
          />
        )}

        {/* Tab 5: Agents, MEMORY.md & NO-LOCAL-CLUTTER LAW */}
        {activeTab === 'agents' && (
          <AgentMemoryBroadcast
            lanes={lanes}
            memoryLogs={memoryLogs}
            diskUsagePercentage={diskUsagePct}
            onEnforceLawNow={handleEnforceLawNow}
            onBroadcastCustomMessage={handleBroadcastCustomMessage}
          />
        )}

        {/* Tab 6: Terminal CLI Runner */}
        {activeTab === 'terminal' && (
          <TerminalSimulator
            logs={logs}
            onExecuteCommand={handleExecuteCommand}
            onClearLogs={() => setLogs([])}
            items={items}
          />
        )}

        {/* Tab 7: GitHub Repository Explorer */}
        {activeTab === 'repo' && (
          <RepoExplorer />
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-900 bg-slate-950 py-6 text-xs text-slate-500">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <span className="font-semibold text-slate-400">Muse Storage Disc Assist</span>
            <span>·</span>
            <span className="text-emerald-400 font-medium">STREAM REAPER ACTIVE</span>
            <span>·</span>
            <span>Watchdog 10-Minute Loop Sweeping</span>
          </div>

          <div className="flex items-center gap-4 text-slate-400">
            <button
              onClick={() => setActiveTab('reaper')}
              className="hover:text-rose-400 transition-colors cursor-pointer"
            >
              Scooby Lane (670/717)
            </button>
            <span>·</span>
            <button
              onClick={() => setActiveTab('repo')}
              className="hover:text-cyan-400 transition-colors cursor-pointer"
            >
              AGENTS.md & MEMORY.md
            </button>
            <span>·</span>
            <button
              onClick={() => setActiveTab('mirror')}
              className="hover:text-cyan-400 transition-colors cursor-pointer"
            >
              1TB Torrents
            </button>
          </div>
        </div>
      </footer>

      {/* Dry Run Modal */}
      <DryRunModal
        isOpen={isDryRunModalOpen}
        onClose={() => setIsDryRunModalOpen(false)}
        onProceedClean={handleEnforceLawNow}
        itemsToDelete={items.filter((it) => it.isSafeToDelete)}
      />

      {/* Custom Path Scanner Modal */}
      <CustomPathModal
        isOpen={isCustomPathModalOpen}
        onClose={() => setIsCustomPathModalOpen(false)}
        onAddCustomItem={handleAddCustomItem}
        currentPlatform={platform}
      />
    </div>
  );
}
