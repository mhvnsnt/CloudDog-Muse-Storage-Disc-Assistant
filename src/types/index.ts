export type OSPlatform = 'macos' | 'windows' | 'linux';

export type ClutterCategoryId = 
  | 'downloads_cache' 
  | 'active_soundbanks' 
  | 'crash_dumps' 
  | 'audio_scratch' 
  | 'autosave_backups' 
  | 'plugin_ir_cache';

export interface ClutterCategory {
  id: ClutterCategoryId;
  name: string;
  description: string;
  safetyLevel: 'safe' | 'caution' | 'relocatable_only';
  totalBytes: number;
  itemCount: number;
  color: string;
  badgeText: string;
}

export interface ClutterItem {
  id: string;
  name: string;
  path: string;
  category: ClutterCategoryId;
  sizeBytes: number;
  lastModified: string;
  isSafeToDelete: boolean;
  isRelocatable: boolean;
  selected: boolean;
  details?: string;
  extension: string;
  offloadedToCloud?: 'terabox' | 'gdrive' | 'kaggle' | 'github' | null;
}

export interface DiskMetrics {
  totalDiskCapacityBytes: number;
  usedBytes: number;
  freeSpaceBytes: number;
  diskUsagePercentage: number;
  museTotalFootprintBytes: number;
  reclaimableBytes: number;
  relocatableBytes: number;
  cloudMirroredBytes: number;
}

export interface RelocationState {
  sourcePath: string;
  targetVolume: string;
  symlinkPath: string;
  isSimulatedSuccess: boolean;
  commands: {
    macos: string[];
    windows: string[];
    linux: string[];
  };
}

export interface TerminalEntry {
  id: string;
  timestamp: string;
  command?: string;
  output: string[];
  type: 'info' | 'success' | 'warning' | 'error' | 'dim';
}

export interface RepoFile {
  name: string;
  path: string;
  language: 'markdown' | 'python' | 'bash' | 'json' | 'yaml' | 'xml';
  content: string;
  size: string;
  description: string;
}

export interface DaemonConfig {
  enabled: boolean;
  schedule: 'daily' | 'weekly' | 'on_boot' | 'disk_threshold';
  diskFreeThresholdGb: number;
  cleanStagedDownloads: boolean;
  cleanCrashDumps: boolean;
  cleanTempAudio: boolean;
  cleanAutosavesOlderThanDays: number;
  sendNotifications: boolean;
  enforce75PercentLaw: boolean;
  autoOffloadToTeraBox: boolean;
}

export type CloudStorageTarget = 'terabox' | 'gdrive' | 'kaggle' | 'github';

export interface TorrentMirrorTask {
  id: string;
  name: string;
  magnetOrUrl: string;
  totalSizeBytes: number;
  downloadedBytes: number;
  progressPercent: number;
  status: 'mirroring_to_cloud' | 'mirrored_in_cloud' | 'local_evicted' | 'paused' | 'error';
  cloudTarget: CloudStorageTarget;
  cloudPath: string;
  hash: string;
  speedMbps: number;
  localCachedBytes: number;
  addedAt: string;
}

export interface ComputeOffloadState {
  cpuCores: number;
  cpuUsagePct: number;
  physicalRamTotalBytes: number;
  physicalRamUsedBytes: number;
  gpuName: string;
  physicalVramTotalBytes: number;
  physicalVramUsedBytes: number;
  virtualVramEnabled: boolean;
  virtualVramTotalBytes: number;
  virtualVramUsedBytes: number;
  vramPagingStrategy: 'ram_swap' | 'nvme_zero_offload' | 'remote_kaggle_bridge' | 'colab_t4_pipe';
  activeRemoteGpus: {
    provider: 'kaggle_free_t4' | 'colab_free_t4' | 'hf_zero_gpu';
    status: 'connected' | 'idle' | 'off';
    allocatedVramGb: number;
  }[];
}

export interface AgentLane {
  id: string;
  name: string;
  role: string;
  lane: string;
  status: 'active' | 'syncing' | 'idle';
  lastBroadcast: string;
  diskEnforcementStatus: 'strictly_compliant' | 'evicting_cache';
}

export interface MemoryLogRecord {
  id: string;
  timestamp: string;
  agentId: string;
  category: 'law_enforcement' | 'cloud_offload' | 'torrent_mirror' | 'vvram_allocation' | 'memory_sync';
  message: string;
  hash: string;
}

// In-Flight Stream Reaper & Emergency 96% Recovery Types
export interface InFlightLaneTask {
  id: string;
  name: string;
  destination: 'github' | 'terabox' | 'gdrive';
  blobsTotal: number;
  blobsVerified: number;
  totalSizeBytes: number;
  reapedLocalBytes: number;
  remainingLocalBytes: number;
  status: 'reaping_per_blob' | 'final_commit_pending' | 'verified_and_cleared' | 'restarted_clean';
  etaSeconds: number;
  notes: string;
}

export interface WatchdogSweepLog {
  id: string;
  timestamp: string;
  durationSeconds: number;
  freedBytes: number;
  summary: string;
  status: 'success' | 'queued' | 'running';
}
