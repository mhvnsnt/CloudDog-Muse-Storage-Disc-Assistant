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
  Zap,
  Server
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
import { RealDiskBridge } from './components/RealDiskBridge';
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

  const [activeTab, setActiveTab] = useState<'reaper' | 'real_bridge' | 'overview' | 'mirror' | 'compute' | 'agents' | 'terminal' | 'repo'>('reaper');
  const [selectedCategory, setSelectedCategory] = useState<ClutterCategoryId | 'all'>('all');
  const [isCleaning, setIsCleaning] = useState(false);
  const [isDryRunModalOpen, setIsDryRunModalOpen] = useState(false);
  const [isCustomPathModalOpen, setIsCustomPathModalOpen] = useState(false);

  // Dynamic disk usage: down from 100% to 81.0% (20GB free, popup band cleared)
  const [diskUsagePct, setDiskUsagePct] = useState<number>(81.0);

  const [notification, setNotification] = useState<{ message: string; type: 'success' | 'info' | 'error' } | null>({
    message: 'DISK DOWN TO 81.0% (20GB Free) — Popup band cleared! Scooby 717/717 verified (+2.5GB), Small files 55/55 verified (+762MB), Western-heart #231 GREEN.',
    type: 'success',
  });

  // Terminal log state
  const [logs, setLogs] = useState<TerminalEntry[]>([
    {
      id: 'init-1',
      timestamp: '11:36:00',
      command: 'cloud_dog scan && cloud_dog enforce',
      output: [
        '✅ DISK USAGE: 81.0% (DOWN FROM 100% / 98% / 87.9%) — 20GB FREE.',
        'Scooby Lane: 717 of 717 blobs verified on GitHub. Local source cleared (+2.5GB).',
        'Small Files Team: 55 files stored on GitHub & verified (+762MB cleared).',
        'Western-heart #231: GREEN and mergeable (54k-file restore PR passed all checks).',
        'CloudDog v2: Merged & active in guardian every 10 min. All tests green.',
      ],
      type: 'info',
    },
    {
      id: 'init-2',
      timestamp: '11:37:44',
      command: 'python3 -m pytest test_cloud_dog.py',
      output: [
        'CloudDog v2 Engine: 24/24 tests PASSED (100% green).',
        'Backends: GitHub Releases API, TeraBox live ls check, Kaggle REST view.',
        'Daemon mode & Torrent mirror mode verified. Zero-leak memory invariant active.',
        'Ceiling target: Enforcing descent to < 75.0% NO-LOCAL-CLUTTER threshold.',
      ],
      type: 'info',
    },
  ]);

  const handleSetPlatform = (newPlat: OSPlatform) => {
    setPlatform(newPlat);
    setItems(INITIAL_CLUTTER_ITEMS[newPlat]);
    setNotification({
      message: `Switched target OS environment to ${newPlat.toUpperCase()}. Scanned paths updated.`,
      type: 'info',
    });
  };

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
    }, 800);
  };

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

        {/* Tab 2: Real OS Bridge (Native File System Access & Express Backend) */}
        {activeTab === 'real_bridge' && (
          <RealDiskBridge />
        )}

        {/* Tab 3: Storage Auditor & Table View */}
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

        {/* Tab 4: 1TB+ Cloud Torrent Mirroring */}
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

        {/* Tab 5: Compute & Non-Physical Virtual VRAM */}
        {activeTab === 'compute' && (
          <ComputeOffloadPanel
            computeState={computeState}
            onUpdateVvramSize={handleUpdateVvramSize}
            onToggleRemoteGpu={handleToggleRemoteGpu}
            onBroadcastComputeEvent={(msg) => handleBroadcastCustomMessage(msg)}
          />
        )}

        {/* Tab 6: Agents, MEMORY.md & NO-LOCAL-CLUTTER LAW */}
        {activeTab === 'agents' && (
          <AgentMemoryBroadcast
            lanes={lanes}
            memoryLogs={memoryLogs}
            diskUsagePercentage={diskUsagePct}
            onEnforceLawNow={handleEnforceLawNow}
            onBroadcastCustomMessage={handleBroadcastCustomMessage}
          />
        )}

        {/* Tab 7: Terminal CLI Runner */}
        {activeTab === 'terminal' && (
          <TerminalSimulator
            logs={logs}
            onExecuteCommand={handleExecuteCommand}
            onClearLogs={() => setLogs([])}
            items={items}
          />
        )}

        {/* Tab 8: GitHub Repository Explorer */}
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
              onClick={() => setActiveTab('real_bridge')}
              className="hover:text-emerald-400 transition-colors cursor-pointer"
            >
              Real OS Bridge
            </button>
            <span>·</span>
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
