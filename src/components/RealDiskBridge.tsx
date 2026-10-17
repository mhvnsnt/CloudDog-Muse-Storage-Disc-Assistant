import React, { useState, useEffect } from 'react';
import { 
  HardDrive, 
  FolderCheck, 
  Trash2, 
  RefreshCw, 
  Terminal, 
  Key, 
  ExternalLink, 
  CheckCircle2, 
  ShieldCheck, 
  AlertTriangle,
  FolderOpen,
  ArrowRight,
  Zap
} from 'lucide-react';
import { formatBytes } from '../data/mockStorageData';

interface RealScannedFile {
  name: string;
  sizeBytes: number;
  handle?: any; // FileSystemFileHandle
  kind: 'file' | 'directory';
}

export const RealDiskBridge: React.FC = () => {
  const [serverDisk, setServerDisk] = useState<{
    totalBytes: number;
    usedBytes: number;
    freeBytes: number;
    diskUsagePercentage: number;
    mount: string;
    platform: string;
  } | null>(null);

  const [isLoadingServerDisk, setIsLoadingServerDisk] = useState(false);
  const [realFolderName, setRealFolderName] = useState<string | null>(null);
  const [realFiles, setRealFiles] = useState<RealScannedFile[]>([]);
  const [dirHandle, setDirHandle] = useState<any>(null);
  const [isScanningLocal, setIsScanningLocal] = useState(false);
  const [realDeleteStatus, setRealDeleteStatus] = useState<string | null>(null);

  // Cloud Dog Engine state
  const [cloudDogOutput, setCloudDogOutput] = useState<string | null>(null);
  const [isCloudDogRunning, setIsCloudDogRunning] = useState(false);

  // GitHub token state for real repo push
  const [githubRepo, setGithubRepo] = useState('username/muse-storage-vault');
  const [hasFileSystemAccess, setHasFileSystemAccess] = useState(false);

  useEffect(() => {
    setHasFileSystemAccess('showDirectoryPicker' in window);
    fetchServerDisk();
    runCloudDogScan();
  }, []);

  const fetchServerDisk = async () => {
    setIsLoadingServerDisk(true);
    try {
      const res = await fetch('/api/system-disk');
      if (res.ok) {
        const data = await res.json();
        if (data.success) {
          setServerDisk(data);
        }
      }
    } catch {
      // fallback
    } finally {
      setIsLoadingServerDisk(false);
    }
  };

  const runCloudDogScan = async () => {
    setIsCloudDogRunning(true);
    try {
      const res = await fetch('/api/cloud-dog/scan');
      const data = await res.json();
      if (data.success) {
        setCloudDogOutput(data.raw || JSON.stringify(data.report, null, 2));
      } else {
        setCloudDogOutput(data.error || 'Failed to scan');
      }
    } catch (e: any) {
      setCloudDogOutput(`Error executing cloud_dog.py: ${e.message}`);
    } finally {
      setIsCloudDogRunning(false);
    }
  };

  const runCloudDogReap = async (apply: boolean) => {
    setIsCloudDogRunning(true);
    try {
      const res = await fetch('/api/cloud-dog/reap', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ apply }),
      });
      const data = await res.json();
      setCloudDogOutput(data.output || data.error || 'Execution finished.');
      fetchServerDisk();
    } catch (e: any) {
      setCloudDogOutput(`Error executing cloud_dog reap: ${e.message}`);
    } finally {
      setIsCloudDogRunning(false);
    }
  };

  const runCloudDogRegistry = async () => {
    setIsCloudDogRunning(true);
    try {
      const res = await fetch('/api/cloud-dog/registry');
      const data = await res.json();
      setCloudDogOutput(JSON.stringify(data.registry, null, 2));
    } catch (e: any) {
      setCloudDogOutput(`Error fetching registry: ${e.message}`);
    } finally {
      setIsCloudDogRunning(false);
    }
  };

  const runCloudDogLog = async () => {
    setIsCloudDogRunning(true);
    try {
      const res = await fetch('/api/cloud-dog/log');
      const data = await res.json();
      setCloudDogOutput(data.log);
    } catch (e: any) {
      setCloudDogOutput(`Error reading cleanup log: ${e.message}`);
    } finally {
      setIsCloudDogRunning(false);
    }
  };

  // Browser Native File System Access API
  const handlePickRealDirectory = async () => {
    if (!('showDirectoryPicker' in window)) {
      alert('Your browser does not support the File System Access API. Please use Chrome, Edge, or Brave, or run the Python CLI in your terminal.');
      return;
    }

    try {
      setIsScanningLocal(true);
      // @ts-ignore
      const handle = await window.showDirectoryPicker({ mode: 'readwrite' });
      setDirHandle(handle);
      setRealFolderName(handle.name);

      const items: RealScannedFile[] = [];
      // @ts-ignore
      for await (const entry of handle.values()) {
        if (entry.kind === 'file') {
          const file = await entry.getFile();
          items.push({
            name: entry.name,
            sizeBytes: file.size,
            handle: entry,
            kind: 'file',
          });
        } else if (entry.kind === 'directory') {
          items.push({
            name: entry.name,
            sizeBytes: 0,
            handle: entry,
            kind: 'directory',
          });
        }
      }

      setRealFiles(items);
      setRealDeleteStatus(`Successfully connected real directory "${handle.name}". Found ${items.length} items.`);
    } catch (err: any) {
      if (err.name !== 'AbortError') {
        console.error(err);
      }
    } finally {
      setIsScanningLocal(false);
    }
  };

  // Real on-the-spot deletion using browser File System API
  const handleRealDeleteFile = async (item: RealScannedFile) => {
    if (!dirHandle) return;
    try {
      // @ts-ignore
      await dirHandle.removeEntry(item.name, { recursive: item.kind === 'directory' });
      setRealFiles((prev) => prev.filter((f) => f.name !== item.name));
      setRealDeleteStatus(`Deleted real file "${item.name}" (${formatBytes(item.sizeBytes)}) directly from host disk.`);
    } catch (err: any) {
      setRealDeleteStatus(`Failed to delete "${item.name}": ${err.message}`);
    }
  };

  return (
    <div className="space-y-8">
      {/* Explanation Banner */}
      <div className="p-6 bg-slate-900/80 border border-slate-800 rounded-xl space-y-4">
        <div className="flex items-start justify-between pb-4 border-b border-slate-800">
          <div>
            <h2 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
              <Zap className="w-5 h-5 text-cyan-400" />
              <span>Real System Execution Architecture</span>
            </h2>
            <p className="text-xs text-slate-400 mt-1 max-w-3xl leading-relaxed">
              This is not a locked UI mockup. The application operates through three real production mechanisms: the <strong>Native File System Access API</strong>, the <strong>Full-Stack Express OS Bridge</strong>, and the <strong>Standalone CLI Python/Bash Engine</strong>.
            </p>
          </div>
        </div>

        {/* 3 Pillars of Real Execution */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs pt-1">
          <div className="p-4 bg-slate-950/70 border border-slate-800 rounded-lg space-y-2">
            <div className="font-semibold text-cyan-300 flex items-center gap-2">
              <FolderOpen className="w-4 h-4 text-cyan-400" />
              <span>1. Browser File System API</span>
            </div>
            <p className="text-slate-400 leading-relaxed">
              Using Chromium's <span className="font-mono text-slate-300">showDirectoryPicker()</span>, you can connect your real Muse directory, inspect real files, and execute actual on-the-spot deletions directly through the browser.
            </p>
          </div>

          <div className="p-4 bg-slate-950/70 border border-slate-800 rounded-lg space-y-2">
            <div className="font-semibold text-purple-300 flex items-center gap-2">
              <HardDrive className="w-4 h-4 text-purple-400" />
              <span>2. Full-Stack Node / Express OS Bridge</span>
            </div>
            <p className="text-slate-400 leading-relaxed">
              The built-in <span className="font-mono text-slate-300">server.ts</span> backend executes real OS system calls: <span className="font-mono text-slate-300">df -k</span> for disk usage, <span className="font-mono text-slate-300">fs.promises.unlink</span> for file unlinking, and background daemon timers.
            </p>
          </div>

          <div className="p-4 bg-slate-950/70 border border-slate-800 rounded-lg space-y-2">
            <div className="font-semibold text-emerald-300 flex items-center gap-2">
              <Terminal className="w-4 h-4 text-emerald-400" />
              <span>3. Standalone CLI & Cron Runner</span>
            </div>
            <p className="text-slate-400 leading-relaxed">
              The repository files (<span className="font-mono text-slate-300">stream_blob_reaper.py</span> and <span className="font-mono text-slate-300">muse_disc_assist.py</span>) use standard Python libraries. You can run them directly in your macOS/Linux Terminal or Windows PowerShell.
            </p>
          </div>
        </div>
      </div>

      {/* Interactive Tool 1: Real Native Directory Picker */}
      <div className="p-6 bg-slate-900/60 border border-slate-800 rounded-xl space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-3 border-b border-slate-800">
          <div>
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <FolderCheck className="w-4 h-4 text-cyan-400" />
              <span>Connect & Clean Real Local Directory</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Select your actual Muse Hub downloads directory, staging folder, or soundfonts to inspect and reap real files.
            </p>
          </div>

          <button
            onClick={handlePickRealDirectory}
            disabled={isScanningLocal}
            className="px-4 py-2 text-xs font-semibold text-slate-950 bg-cyan-400 hover:bg-cyan-300 rounded-lg transition-colors cursor-pointer inline-flex items-center gap-2 shrink-0 shadow-sm shadow-cyan-500/20"
          >
            {isScanningLocal ? (
              <>
                <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                <span>Reading Directory...</span>
              </>
            ) : (
              <>
                <FolderOpen className="w-3.5 h-3.5" />
                <span>{realFolderName ? `Folder: ${realFolderName}` : 'Pick Real Local Directory'}</span>
              </>
            )}
          </button>
        </div>

        {realDeleteStatus && (
          <div className="p-3 bg-slate-950/80 border border-cyan-800/40 rounded-lg text-xs text-cyan-300 flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-cyan-400 shrink-0" />
            <span>{realDeleteStatus}</span>
          </div>
        )}

        {realFiles.length > 0 && (
          <div className="space-y-2">
            <div className="text-xs font-semibold text-slate-300">
              Real Files Found in "{realFolderName}" ({realFiles.length} items):
            </div>
            <div className="max-h-60 overflow-y-auto space-y-1.5 border border-slate-800 rounded-lg p-2 bg-slate-950/60 font-mono text-xs">
              {realFiles.map((file) => (
                <div
                  key={file.name}
                  className="flex items-center justify-between p-2 rounded bg-slate-900/60 hover:bg-slate-900 transition-colors"
                >
                  <div className="truncate pr-3">
                    <span className="text-slate-200">{file.name}</span>
                  </div>
                  <div className="flex items-center gap-3 shrink-0">
                    <span className="text-slate-400 tabular-nums">
                      {file.kind === 'file' ? formatBytes(file.sizeBytes) : 'DIR'}
                    </span>
                    <button
                      onClick={() => handleRealDeleteFile(file)}
                      className="px-2 py-0.5 text-[11px] font-medium text-rose-300 bg-rose-950/80 hover:bg-rose-900/80 border border-rose-800/80 rounded transition-colors cursor-pointer inline-flex items-center gap-1"
                      title="Deletes this file from your real disk"
                    >
                      <Trash2 className="w-3 h-3" />
                      <span>Delete Real File</span>
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Interactive Tool 2: Real Full-Stack OS Telemetry */}
      <div className="p-6 bg-slate-900/60 border border-slate-800 rounded-xl space-y-4">
        <div className="flex items-center justify-between pb-3 border-b border-slate-800">
          <div>
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <HardDrive className="w-4 h-4 text-purple-400" />
              <span>Real Server-Side OS Disk Usage (Node.js & df -k)</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Live telemetry fetched directly from the host operating system filesystem mount.
            </p>
          </div>

          <button
            onClick={fetchServerDisk}
            disabled={isLoadingServerDisk}
            className="px-3 py-1.5 text-xs font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 rounded-md transition-colors cursor-pointer inline-flex items-center gap-1.5"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isLoadingServerDisk ? 'animate-spin' : ''}`} />
            <span>Refresh OS Stat</span>
          </button>
        </div>

        {serverDisk && (
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-4 text-xs font-mono">
            <div className="p-3 bg-slate-950/80 border border-slate-800 rounded-lg">
              <div className="text-slate-400 font-sans">Mount Point</div>
              <div className="text-base font-bold text-white mt-0.5">{serverDisk.mount}</div>
            </div>
            <div className="p-3 bg-slate-950/80 border border-slate-800 rounded-lg">
              <div className="text-slate-400 font-sans">Total Filesystem</div>
              <div className="text-base font-bold text-white mt-0.5">{formatBytes(serverDisk.totalBytes)}</div>
            </div>
            <div className="p-3 bg-slate-950/80 border border-slate-800 rounded-lg">
              <div className="text-slate-400 font-sans">Used Storage</div>
              <div className="text-base font-bold text-cyan-300 mt-0.5">{formatBytes(serverDisk.usedBytes)}</div>
            </div>
            <div className="p-3 bg-slate-950/80 border border-slate-800 rounded-lg">
              <div className="text-slate-400 font-sans">Real OS Usage %</div>
              <div className="text-base font-bold text-emerald-400 mt-0.5">{serverDisk.diskUsagePercentage}%</div>
            </div>
          </div>
        )}
      </div>

      {/* Interactive Tool 3: Cloud Dog Real Engine Console */}
      <div className="p-6 bg-slate-900/80 border border-slate-800 rounded-xl space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-3 border-b border-slate-800">
          <div>
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <Zap className="w-4 h-4 text-cyan-400" />
              <span>Cloud Dog Real Engine Control (cloud_dog.py)</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Production CLI wired into disk-space-guardian cron (every 10 min). Verify-then-delete only.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={runCloudDogScan}
              disabled={isCloudDogRunning}
              className="px-3 py-1.5 text-xs font-medium text-slate-200 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-md transition-colors cursor-pointer"
            >
              Scan Workspace
            </button>
            <button
              onClick={() => runCloudDogReap(false)}
              disabled={isCloudDogRunning}
              className="px-3 py-1.5 text-xs font-medium text-cyan-300 bg-cyan-950/60 hover:bg-cyan-900/60 border border-cyan-800/60 rounded-md transition-colors cursor-pointer"
            >
              Dry-Run Reap
            </button>
            <button
              onClick={() => runCloudDogReap(true)}
              disabled={isCloudDogRunning}
              className="px-3 py-1.5 text-xs font-semibold text-rose-300 bg-rose-950/60 hover:bg-rose-900/60 border border-rose-800/60 rounded-md transition-colors cursor-pointer"
            >
              Apply Reap
            </button>
            <button
              onClick={runCloudDogRegistry}
              disabled={isCloudDogRunning}
              className="px-3 py-1.5 text-xs font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-md transition-colors cursor-pointer"
            >
              Store Registry
            </button>
            <button
              onClick={runCloudDogLog}
              disabled={isCloudDogRunning}
              className="px-3 py-1.5 text-xs font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-md transition-colors cursor-pointer"
            >
              Action Log
            </button>
          </div>
        </div>

        {/* Live Output */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs text-slate-400 font-mono">
            <span>Live Output from python3 cloud_dog.py:</span>
            {isCloudDogRunning && (
              <span className="text-cyan-400 flex items-center gap-1">
                <RefreshCw className="w-3 h-3 animate-spin" />
                Executing...
              </span>
            )}
          </div>
          <pre className="p-4 bg-slate-950 border border-slate-800 rounded-lg font-mono text-xs text-cyan-200/90 overflow-x-auto max-h-64 leading-relaxed">
            {cloudDogOutput || 'Click "Scan Workspace" or "Dry-Run Reap" to execute cloud_dog.py'}
          </pre>
        </div>
      </div>

      {/* Real One-Liner CLI Installation & Execution */}
      <div className="p-6 bg-slate-900/60 border border-slate-800 rounded-xl space-y-4">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <Terminal className="w-4 h-4 text-emerald-400" />
          <span>Execute Standalone Directly on Your Machine</span>
        </h3>
        <p className="text-xs text-slate-400">
          Run the production scripts from the repository directly in your terminal to manage real files, symlinks, and background cron loops:
        </p>

        <div className="space-y-2 font-mono text-xs">
          <div className="p-3 bg-slate-950 border border-slate-800 rounded-lg flex items-center justify-between text-slate-300">
            <span>./install.sh</span>
          </div>
          <div className="p-3 bg-slate-950 border border-slate-800 rounded-lg flex items-center justify-between text-cyan-300">
            <span>python3 cloud_dog.py scan</span>
          </div>
          <div className="p-3 bg-slate-950 border border-slate-800 rounded-lg flex items-center justify-between text-emerald-300">
            <span>python3 cloud_dog.py reap [--apply]</span>
          </div>
          <div className="p-3 bg-slate-950 border border-slate-800 rounded-lg flex items-center justify-between text-purple-300">
            <span>./muse-assist.sh enforce</span>
          </div>
        </div>
      </div>
    </div>
  );
};

