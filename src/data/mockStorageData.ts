import { 
  ClutterItem, 
  OSPlatform, 
  ClutterCategoryId, 
  TorrentMirrorTask, 
  ComputeOffloadState, 
  AgentLane, 
  MemoryLogRecord,
  InFlightLaneTask,
  WatchdogSweepLog
} from '../types';

export const DEFAULT_PLATFORM_PATHS: Record<OSPlatform, {
  hubData: string;
  instruments: string;
  downloads: string;
  musescoreUserData: string;
  tempCache: string;
}> = {
  macos: {
    hubData: '/Library/Application Support/MuseHub',
    instruments: '/Library/Application Support/MuseHub/Instruments',
    downloads: '/Library/Application Support/MuseHub/Downloads',
    musescoreUserData: '~/Documents/MuseScore4/Scores/.autosave',
    tempCache: '~/Library/Caches/MuseHub/Staging',
  },
  windows: {
    hubData: 'C:\\ProgramData\\Muse Hub',
    instruments: 'C:\\ProgramData\\Muse Hub\\Instruments',
    downloads: 'C:\\Users\\User\\AppData\\Local\\MuseHub\\Downloads',
    musescoreUserData: 'C:\\Users\\User\\Documents\\MuseScore4\\Scores\\.autosave',
    tempCache: 'C:\\Users\\User\\AppData\\Local\\Temp\\MuseStems',
  },
  linux: {
    hubData: '/var/lib/muse-hub',
    instruments: '/var/lib/muse-hub/instruments',
    downloads: '~/.local/share/MuseHub/downloads',
    musescoreUserData: '~/.local/share/MuseScore/MuseScore4/autosave',
    tempCache: '/tmp/muse-audio-scratch',
  },
};

export const INITIAL_CLUTTER_ITEMS: Record<OSPlatform, ClutterItem[]> = {
  macos: [
    {
      id: 'item-1',
      name: 'Scooby_Complete_Sound_Staging_Archive (In-Flight)',
      path: '/Library/Application Support/MuseHub/Downloads/Scooby_Doo_Archive_Blobs',
      category: 'downloads_cache',
      sizeBytes: 8_200_000_000,
      lastModified: '2026-10-17 09:20',
      isSafeToDelete: true,
      isRelocatable: false,
      selected: true,
      details: 'Scooby lane: 670 of 717 blobs verified. Local chunks deleting per-blob on the spot.',
      extension: '.blobs',
      offloadedToCloud: 'github',
    },
    {
      id: 'item-2',
      name: 'DOA4_Soundtrack_Raw_Disc_Tars.tar',
      path: '/Library/Application Support/MuseHub/Downloads/DOA4_Soundtrack_Raw_Disc_Tars.tar',
      category: 'downloads_cache',
      sizeBytes: 1_450_000_000,
      lastModified: '2026-10-17 09:25',
      isSafeToDelete: true,
      isRelocatable: false,
      selected: true,
      details: 'Priority 8 files team: uploading to GitHub now, deletes on hash verification.',
      extension: '.tar',
      offloadedToCloud: 'github',
    },
    {
      id: 'item-3',
      name: 'DXMMII_Multitrack_Parts.part01.rar',
      path: '/Library/Application Support/MuseHub/Downloads/DXMMII_Multitrack_Parts.part01.rar',
      category: 'downloads_cache',
      sizeBytes: 1_280_000_000,
      lastModified: '2026-10-17 09:26',
      isSafeToDelete: true,
      isRelocatable: false,
      selected: true,
      details: 'Priority 8 files team: GB-sized chunk uploading through proxy.',
      extension: '.rar',
      offloadedToCloud: 'github',
    },
    {
      id: 'item-4',
      name: 'Adventure_Time_Stems_Pack.part16_17',
      path: '/Library/Application Support/MuseHub/Downloads/Adventure_Time_Stems_Pack',
      category: 'downloads_cache',
      sizeBytes: 980_000_000,
      lastModified: '2026-10-17 09:22',
      isSafeToDelete: true,
      isRelocatable: false,
      selected: true,
      details: 'Adventure Time lane: 15 of 17 parts up and verified. 2 final chunks pending.',
      extension: '.part',
      offloadedToCloud: 'github',
    },
    {
      id: 'item-5',
      name: 'Gumball_Stem_Re-up_Clean.part',
      path: '/Library/Application Support/MuseHub/Downloads/Gumball_Stem_Re-up_Clean.part',
      category: 'downloads_cache',
      sizeBytes: 1_100_000_000,
      lastModified: '2026-10-17 09:24',
      isSafeToDelete: true,
      isRelocatable: false,
      selected: true,
      details: 'Restarted clean after proxy flake. Upload lane alive.',
      extension: '.part',
      offloadedToCloud: 'github',
    },
    {
      id: 'item-6',
      name: 'Johnny_Test_Audio_Push.pkg',
      path: '/Library/Application Support/MuseHub/Downloads/Johnny_Test_Audio_Push.pkg',
      category: 'downloads_cache',
      sizeBytes: 850_000_000,
      lastModified: '2026-10-17 09:28',
      isSafeToDelete: true,
      isRelocatable: false,
      selected: true,
      details: 'Johnny Test push streaming to GitHub/TeraBox.',
      extension: '.pkg',
      offloadedToCloud: 'terabox',
    },
    {
      id: 'item-7',
      name: 'Muse_Strings (Active Soundbank Directory)',
      path: '/Library/Application Support/MuseHub/Instruments/Muse Strings',
      category: 'active_soundbanks',
      sizeBytes: 15_600_000_000,
      lastModified: '2026-10-12 14:20',
      isSafeToDelete: false,
      isRelocatable: true,
      selected: false,
      details: 'Core orchestral strings soundbank. Protected playback instrument.',
      extension: 'DIR',
      offloadedToCloud: 'terabox',
    },
    {
      id: 'item-8',
      name: 'Muse_Brass (Active Soundbank Directory)',
      path: '/Library/Application Support/MuseHub/Instruments/Muse Brass',
      category: 'active_soundbanks',
      sizeBytes: 11_200_000_000,
      lastModified: '2026-10-12 14:22',
      isSafeToDelete: false,
      isRelocatable: true,
      selected: false,
      details: 'Active brass acoustic multisamples and articulation matrices.',
      extension: 'DIR',
      offloadedToCloud: 'terabox',
    },
    {
      id: 'item-9',
      name: 'crash_watchdog_buffer_underrun.dmp',
      path: '/Library/Application Support/MuseHub/Diagnostics/crash_watchdog_buffer_underrun.dmp',
      category: 'crash_dumps',
      sizeBytes: 1_280_000_000,
      lastModified: '2026-10-17 09:12',
      isSafeToDelete: true,
      isRelocatable: false,
      selected: true,
      details: 'Crash dump generated before watchdog restart. Instant purge candidate.',
      extension: '.dmp',
    },
  ],
  windows: [
    {
      id: 'win-1',
      name: 'Scooby_Blobs_Staging.zip',
      path: 'C:\\Users\\User\\AppData\\Local\\MuseHub\\Downloads\\Scooby_Blobs_Staging.zip',
      category: 'downloads_cache',
      sizeBytes: 8_200_000_000,
      lastModified: '2026-10-17 09:20',
      isSafeToDelete: true,
      isRelocatable: false,
      selected: true,
      details: 'Scooby lane: 670 of 717 blobs verified. Local reaper engaged.',
      extension: '.zip',
      offloadedToCloud: 'github',
    },
  ],
  linux: [
    {
      id: 'lin-1',
      name: 'scooby_blobs_staging.tar.gz',
      path: '~/.local/share/MuseHub/downloads/scooby_blobs_staging.tar.gz',
      category: 'downloads_cache',
      sizeBytes: 8_200_000_000,
      lastModified: '2026-10-17 09:20',
      isSafeToDelete: true,
      isRelocatable: false,
      selected: true,
      details: 'Scooby lane stream reaper active.',
      extension: '.tar.gz',
      offloadedToCloud: 'github',
    },
  ],
};

export const CATEGORY_DEFINITIONS: Record<ClutterCategoryId, {
  name: string;
  description: string;
  safetyLevel: 'safe' | 'caution' | 'relocatable_only';
  color: string;
  badgeText: string;
}> = {
  downloads_cache: {
    name: 'Installer & Staged Torrents',
    description: 'Post-install .musepkg archives and in-flight blob torrent shards. Reaped per-blob on hash verify.',
    safetyLevel: 'safe',
    color: '#06B6D4',
    badgeText: 'Per-Blob In-Flight Reaper',
  },
  active_soundbanks: {
    name: 'Active Sound Libraries',
    description: 'Installed Muse Sounds (Strings, Brass, Choir). Relocate to external SSD or vVRAM.',
    safetyLevel: 'relocatable_only',
    color: '#8B5CF6',
    badgeText: 'Relocate / vVRAM Buffer',
  },
  crash_dumps: {
    name: 'Crash Dumps & Diagnostics',
    description: 'Unsent crash reports and core dumps. Hard-purged when disk breaches popup band.',
    safetyLevel: 'safe',
    color: '#F43F5E',
    badgeText: 'Instant Purge',
  },
  audio_scratch: {
    name: 'Audio Scratch & Temp WAVs',
    description: 'Temporary stem bounces and 32-bit float audio scratch buffers.',
    safetyLevel: 'safe',
    color: '#F59E0B',
    badgeText: 'Safe to Evict',
  },
  autosave_backups: {
    name: 'Stale Autosaves & Backups',
    description: 'Historical snapshots backed up to GitHub repo then cleared locally.',
    safetyLevel: 'caution',
    color: '#10B981',
    badgeText: 'Offloaded to Git LFS',
  },
  plugin_ir_cache: {
    name: 'MuseFX Plugin Caches',
    description: 'Convolution reverb impulse caches and VST3 scratch buffers.',
    safetyLevel: 'safe',
    color: '#3B82F6',
    badgeText: 'Safe to Purge',
  },
};

// In-Flight Lanes matching the user's live operational emergency
export const INITIAL_IN_FLIGHT_LANES: InFlightLaneTask[] = [
  {
    id: 'lane-scooby',
    name: 'Scooby Lane (Dedicated Reaper)',
    destination: 'github',
    blobsTotal: 717,
    blobsVerified: 670,
    totalSizeBytes: 8_200_000_000, // 8.2 GB
    reapedLocalBytes: 7_660_000_000, // 7.66 GB already deleted on the spot!
    remainingLocalBytes: 540_000_000, // 540 MB left (47 blobs)
    status: 'final_commit_pending',
    etaSeconds: 45,
    notes: '47 blobs remaining to final commit step. Per-blob reaper deletes local copy as each hash verifies against GitHub.',
  },
  {
    id: 'lane-8-files',
    name: 'Priority 8 Files Team (DOA4 tars, DXMMII, 2 MP4s)',
    destination: 'github',
    blobsTotal: 8,
    blobsVerified: 3,
    totalSizeBytes: 3_700_000_000, // 3.7 GB
    reapedLocalBytes: 1_200_000_000,
    remainingLocalBytes: 2_500_000_000, // 2.5 GB left
    status: 'reaping_per_blob',
    etaSeconds: 110,
    notes: 'Dedicated team storing all 8 files to GitHub through slow proxy connection. Each deletes the second it verifies.',
  },
  {
    id: 'lane-adventure-time',
    name: 'Adventure Time Parts Lane',
    destination: 'github',
    blobsTotal: 17,
    blobsVerified: 15,
    totalSizeBytes: 4_200_000_000,
    reapedLocalBytes: 3_700_000_000,
    remainingLocalBytes: 500_000_000, // 2 parts left
    status: 'reaping_per_blob',
    etaSeconds: 30,
    notes: '15 of 17 parts verified and cleared. 2 final parts being nursed across proxy.',
  },
  {
    id: 'lane-gumball',
    name: 'Gumball Lane (Clean Restart)',
    destination: 'github',
    blobsTotal: 12,
    blobsVerified: 5,
    totalSizeBytes: 2_600_000_000,
    reapedLocalBytes: 1_100_000_000,
    remainingLocalBytes: 1_500_000_000,
    status: 'restarted_clean',
    etaSeconds: 140,
    notes: 'Restarted clean after proxy flake. Upload lane alive and watchdog nursing.',
  },
  {
    id: 'lane-johnny-test',
    name: 'Johnny Test Push',
    destination: 'terabox',
    blobsTotal: 6,
    blobsVerified: 4,
    totalSizeBytes: 1_800_000_000,
    reapedLocalBytes: 1_200_000_000,
    remainingLocalBytes: 600_000_000,
    status: 'reaping_per_blob',
    etaSeconds: 65,
    notes: 'Direct streaming pipe to TeraBox 1TB Vault.',
  },
  {
    id: 'lane-flight-assist',
    name: 'Flight-Assist Pass',
    destination: 'terabox',
    blobsTotal: 24,
    blobsVerified: 24,
    totalSizeBytes: 8_400_000_000,
    reapedLocalBytes: 8_400_000_000,
    remainingLocalBytes: 0,
    status: 'verified_and_cleared',
    etaSeconds: 0,
    notes: 'Finished pass: 8.4GB cleared 100% off disk.',
  },
];

// 10-Minute Watchdog Sweeper Run History matching scheduler log
export const INITIAL_WATCHDOG_LOGS: WatchdogSweepLog[] = [
  {
    id: 'wd-1',
    timestamp: '07:24',
    durationSeconds: 18,
    freedBytes: 11_000_000_000, // 11 GB
    summary: 'Cleared 11G dead download directory and stale staging containers.',
    status: 'success',
  },
  {
    id: 'wd-2',
    timestamp: '07:54',
    durationSeconds: 24,
    freedBytes: 5_400_000_000,
    summary: 'Cleared 445 Space Ghost files plus their associated zip archives.',
    status: 'success',
  },
  {
    id: 'wd-3',
    timestamp: '08:04',
    durationSeconds: 14,
    freedBytes: 2_100_000_000,
    summary: 'Purged Gumball failed partial chunks and buffer underrun scratch.',
    status: 'success',
  },
  {
    id: 'wd-4',
    timestamp: '08:34',
    durationSeconds: 32,
    freedBytes: 3_800_000_000,
    summary: 'Patched around GitHub file-size blocker and pruned failed chunk retries.',
    status: 'success',
  },
  {
    id: 'wd-5',
    timestamp: '09:04',
    durationSeconds: 22,
    freedBytes: 1_900_000_000,
    summary: 'Restarted stalled upload lanes after proxy flake; nursed Scooby lane.',
    status: 'success',
  },
  {
    id: 'wd-6',
    timestamp: '09:24',
    durationSeconds: 41,
    freedBytes: 8_400_000_000, // 8.4 GB
    summary: 'Flight-assist pass finished: 8.4 GB cleared. Every upload lane alive.',
    status: 'success',
  },
  {
    id: 'wd-7',
    timestamp: '09:34 (Next Queued)',
    durationSeconds: 0,
    freedBytes: 0,
    summary: '10-minute watchdog loop active: sweeping for verified blob hashes to reap on the spot.',
    status: 'queued',
  },
];

// Initial Mirrored Torrents
export const INITIAL_TORRENT_TASKS: TorrentMirrorTask[] = [
  {
    id: 'tor-1',
    name: 'BBC_Symphony_Orchestra_Professional_24bit_Samples.torrent',
    magnetOrUrl: 'magnet:?xt=urn:btih:8f3c7e91a0b5c4d3e2f180796857463524130291&dn=BBC_Symphony_Orchestra_Pro',
    totalSizeBytes: 560_000_000_000,
    downloadedBytes: 560_000_000_000,
    progressPercent: 100,
    status: 'local_evicted',
    cloudTarget: 'terabox',
    cloudPath: 'terabox://MusicVault/Orchestral/BBC_SO_Pro/',
    hash: '8f3c7e91a0b5c4d3e2f180796857463524130291',
    speedMbps: 0,
    localCachedBytes: 0,
    addedAt: '2026-10-14 11:20',
  },
  {
    id: 'tor-2',
    name: 'Spitfire_Audio_Albion_One_Complete_Soundfont_Pack.torrent',
    magnetOrUrl: 'magnet:?xt=urn:btih:5a4b3c2d1e0f9876543210fedcba9876543210fe&dn=Albion_One_Complete',
    totalSizeBytes: 320_000_000_000,
    downloadedBytes: 320_000_000_000,
    progressPercent: 100,
    status: 'local_evicted',
    cloudTarget: 'terabox',
    cloudPath: 'terabox://MusicVault/Soundfonts/Albion_One/',
    hash: '5a4b3c2d1e0f9876543210fedcba9876543210fe',
    speedMbps: 0,
    localCachedBytes: 0,
    addedAt: '2026-10-15 08:45',
  },
  {
    id: 'tor-3',
    name: 'Cinematic_Studio_Brass_And_Strings_Multitrack.torrent',
    magnetOrUrl: 'magnet:?xt=urn:btih:3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d&dn=Cinematic_Studio_Brass',
    totalSizeBytes: 185_000_000_000,
    downloadedBytes: 185_000_000_000,
    progressPercent: 100,
    status: 'local_evicted',
    cloudTarget: 'gdrive',
    cloudPath: 'gdrive://OrchestraVault/Cinematic_Studio/',
    hash: '3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d',
    speedMbps: 0,
    localCachedBytes: 0,
    addedAt: '2026-10-16 19:10',
  },
];

export const INITIAL_COMPUTE_STATE: ComputeOffloadState = {
  cpuCores: 12,
  cpuUsagePct: 38,
  physicalRamTotalBytes: 32_000_000_000,
  physicalRamUsedBytes: 14_800_000_000,
  gpuName: 'Apple M-Series / NVIDIA Unified Co-Processor',
  physicalVramTotalBytes: 8_000_000_000,
  physicalVramUsedBytes: 5_200_000_000,
  virtualVramEnabled: true,
  virtualVramTotalBytes: 64_000_000_000,
  virtualVramUsedBytes: 28_400_000_000,
  vramPagingStrategy: 'ram_swap',
  activeRemoteGpus: [
    { provider: 'kaggle_free_t4', status: 'connected', allocatedVramGb: 16 },
    { provider: 'colab_free_t4', status: 'connected', allocatedVramGb: 16 },
    { provider: 'hf_zero_gpu', status: 'idle', allocatedVramGb: 24 },
  ],
};

export const INITIAL_AGENT_LANES: AgentLane[] = [
  {
    id: 'agent-scooby-reaper',
    name: 'Scooby Lane Stream Reaper',
    role: 'Deletes local blobs on the spot as upload hash verifies against GitHub',
    lane: 'Lane 1 · Scooby (670/717 blobs)',
    status: 'syncing',
    lastBroadcast: '2026-10-17 09:32:10',
    diskEnforcementStatus: 'evicting_cache',
  },
  {
    id: 'agent-8-files-team',
    name: 'Priority 8 Files Uploader',
    role: 'Pushes DOA4 tars, DXMMII parts, and MP4s directly to GitHub repo',
    lane: 'Lane 2 · Priority 8 Files (3.7 GB)',
    status: 'syncing',
    lastBroadcast: '2026-10-17 09:32:44',
    diskEnforcementStatus: 'evicting_cache',
  },
  {
    id: 'agent-10m-watchdog',
    name: '10-Minute Loop Watchdog',
    role: 'Sweeps every 10 min: nurses stalled upload lanes & clears verified files',
    lane: 'Lane 0 · Watchdog (Runs 7:24, 7:54, 8:04, 8:34, 9:04, 9:24)',
    status: 'active',
    lastBroadcast: '2026-10-17 09:33:00',
    diskEnforcementStatus: 'strictly_compliant',
  },
  {
    id: 'agent-adventure-gumball',
    name: 'Adventure & Gumball Nurse',
    role: 'Nurses 15/17 Adventure Time parts and Gumball clean restart across proxy',
    lane: 'Lane 3 · Pipeline Nurse',
    status: 'active',
    lastBroadcast: '2026-10-17 09:32:55',
    diskEnforcementStatus: 'strictly_compliant',
  },
  {
    id: 'agent-memory-broadcaster',
    name: 'Multi-Chat Broadcaster',
    role: 'Broadcasts verified space drops to all chats and commits MEMORY.md',
    lane: 'Lane 4 · Broadcast Channel',
    status: 'active',
    lastBroadcast: '2026-10-17 09:33:20',
    diskEnforcementStatus: 'strictly_compliant',
  },
];

export const INITIAL_MEMORY_LOGS: MemoryLogRecord[] = [
  {
    id: 'mem-101',
    timestamp: '2026-10-17 09:32:00',
    agentId: 'agent-scooby-reaper',
    category: 'law_enforcement',
    message: 'REAPING LIVE: Scooby lane at 670 of 717 blobs. Local chunks deleted on the spot as hash verifies against GitHub. 47 blobs remaining to final commit.',
    hash: '0x7a8b9c0d1e2f3a4b',
  },
  {
    id: 'mem-102',
    timestamp: '2026-10-17 09:30:15',
    agentId: 'agent-8-files-team',
    category: 'cloud_offload',
    message: 'IN-FLIGHT: Priority 8 files (DOA4 tars, DXMMII parts, MP4s ~3.7GB) uploading to GitHub. Scheduled for instant deletion upon SHA-256 match.',
    hash: '0x6b7c8d9e0f1a2b3c',
  },
  {
    id: 'mem-103',
    timestamp: '2026-10-17 09:24:00',
    agentId: 'agent-10m-watchdog',
    category: 'law_enforcement',
    message: 'WATCHDOG RUN 9:24: Flight-assist pass finished with 8.4GB cleared. All lanes alive and being nursed through connection proxy.',
    hash: '0x5c6d7e8f9a0b1c2d',
  },
  {
    id: 'mem-104',
    timestamp: '2026-10-17 09:04:00',
    agentId: 'agent-10m-watchdog',
    category: 'law_enforcement',
    message: 'WATCHDOG RUN 9:04: Restarted Gumball upload clean after proxy flake. Patched around GitHub file-size blocker.',
    hash: '0x4d5e6f7a8b9c0d1e',
  },
];

export function formatBytes(bytes: number, decimals: number = 2): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`;
}
