import React, { useState } from 'react';
import { 
  Cloud, 
  DownloadCloud, 
  HardDrive, 
  Zap, 
  ExternalLink, 
  CheckCircle2, 
  ShieldCheck, 
  ArrowRight, 
  RefreshCw, 
  Trash2, 
  Plus, 
  Layers,
  Database,
  Radio
} from 'lucide-react';
import { TorrentMirrorTask, CloudStorageTarget } from '../types';
import { formatBytes } from '../data/mockStorageData';

interface TorrentCloudMirrorProps {
  tasks: TorrentMirrorTask[];
  onAddTorrent: (name: string, magnet: string, target: CloudStorageTarget, sizeGb: number) => void;
  onEvictLocal: (id: string) => void;
  onBroadcastMirrorEvent: (taskName: string, size: string, target: string) => void;
}

export const TorrentCloudMirror: React.FC<TorrentCloudMirrorProps> = ({
  tasks,
  onAddTorrent,
  onEvictLocal,
  onBroadcastMirrorEvent,
}) => {
  const [magnetInput, setMagnetInput] = useState('');
  const [torrentName, setTorrentName] = useState('');
  const [targetCloud, setTargetCloud] = useState<CloudStorageTarget>('terabox');
  const [estimatedSizeGb, setEstimatedSizeGb] = useState('120');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const totalMirroredBytes = tasks.reduce((acc, t) => acc + t.totalSizeBytes, 0);
  const totalLocalCachedBytes = tasks.reduce((acc, t) => acc + t.localCachedBytes, 0);

  const handleStartMirror = (e: React.FormEvent) => {
    e.preventDefault();
    if (!magnetInput.trim()) return;

    setIsSubmitting(true);
    const sizeNumber = parseFloat(estimatedSizeGb) || 60;
    const name = torrentName.trim() || `Soundbank_Torrent_${Date.now().toString().slice(-4)}.torrent`;

    setTimeout(() => {
      onAddTorrent(name, magnetInput.trim(), targetCloud, sizeNumber);
      onBroadcastMirrorEvent(name, `${sizeNumber} GB`, targetCloud.toUpperCase());
      setMagnetInput('');
      setTorrentName('');
      setIsSubmitting(false);
    }, 600);
  };

  const handleLoadSample = () => {
    setTorrentName('Hans_Zimmer_Percussion_Extended_24bit.torrent');
    setMagnetInput('magnet:?xt=urn:btih:4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2d3e&dn=Hans_Zimmer_Percussion_Pro');
    setEstimatedSizeGb('140');
    setTargetCloud('terabox');
  };

  return (
    <div className="space-y-8">
      {/* Top Banner: TeraBox 1TB+ Free Vault Engine */}
      <div className="p-6 bg-slate-900/80 border border-slate-800 rounded-xl space-y-4">
        <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-cyan-950/60 border border-cyan-800/80 flex items-center justify-center text-cyan-400">
                <Cloud className="w-4 h-4" />
              </div>
              <h2 className="text-xl font-bold tracking-tight text-white">
                1 TB+ Cloud Torrent Mirroring (Zero-Local Pipe)
              </h2>
            </div>
            <p className="text-xs text-slate-400 mt-1 max-w-3xl leading-relaxed">
              <strong>NO-LOCAL-CLUTTER LAW:</strong> Disk is a cache, not permanent storage. Magnet links and torrent streams are piped directly into TeraBox (1024 GB free tier), Google Drive, or Kaggle scratch workers. Zero gigabytes clutter your local SSD.
            </p>
          </div>

          <div className="flex items-center gap-4 bg-slate-950/80 border border-slate-800 p-3 rounded-lg text-right shrink-0">
            <div>
              <div className="text-[11px] text-slate-400 font-medium">Total Mirrored to Cloud Vault</div>
              <div className="text-xl font-bold font-mono text-cyan-300 tabular-nums">
                {formatBytes(totalMirroredBytes)}
              </div>
            </div>
            <div className="h-8 w-px bg-slate-800" aria-hidden="true" />
            <div>
              <div className="text-[11px] text-emerald-400 font-medium">Local Disk Footprint</div>
              <div className="text-xl font-bold font-mono text-emerald-300 tabular-nums">
                0.00 B
              </div>
            </div>
          </div>
        </div>

        {/* Free Cloud Providers Hook Matrix */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3 pt-2 text-xs">
          <div className="p-3 bg-slate-950/60 border border-cyan-800/40 rounded-lg space-y-1">
            <div className="font-semibold text-cyan-300 flex items-center justify-between">
              <span>TeraBox Free Vault</span>
              <span className="text-[10px] text-emerald-400 font-mono">1024 GB (1 TB)</span>
            </div>
            <p className="text-[11px] text-slate-400">
              Primary cloud destination for 100GB–500GB massive orchestral torrents. Free account tier.
            </p>
          </div>

          <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg space-y-1">
            <div className="font-semibold text-slate-200 flex items-center justify-between">
              <span>Google Drive Pipe</span>
              <span className="text-[10px] text-cyan-400 font-mono">15 GB – Unlimited</span>
            </div>
            <p className="text-[11px] text-slate-400">
              High-speed direct download streaming with rclone token mounting.
            </p>
          </div>

          <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg space-y-1">
            <div className="font-semibold text-slate-200 flex items-center justify-between">
              <span>Kaggle Dataset Scratch</span>
              <span className="text-[10px] text-purple-400 font-mono">20 GB Working / T4 GPU</span>
            </div>
            <p className="text-[11px] text-slate-400">
              Instant headless torrent leech node hooked directly to GPU audio processing.
            </p>
          </div>

          <div className="p-3 bg-slate-950/60 border border-slate-800 rounded-lg space-y-1">
            <div className="font-semibold text-slate-200 flex items-center justify-between">
              <span>GitHub Git LFS & Releases</span>
              <span className="text-[10px] text-slate-400 font-mono">2 GB / file (Free)</span>
            </div>
            <p className="text-[11px] text-slate-400">
              Permanent versioning for score autosaves, stems, and project configurations.
            </p>
          </div>
        </div>
      </div>

      {/* Torrent Leech Ingestion Form */}
      <div className="p-6 bg-slate-900/60 border border-slate-800 rounded-xl space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-semibold text-white flex items-center gap-2">
            <Zap className="w-4 h-4 text-cyan-400" />
            <span>Mirror New Torrent to 1TB+ Cloud Vault</span>
          </h3>

          <button
            type="button"
            onClick={handleLoadSample}
            className="text-xs text-cyan-400 hover:text-cyan-300 font-medium cursor-pointer"
          >
            Load Sample 140GB Orchestral Torrent
          </button>
        </div>

        <form onSubmit={handleStartMirror} className="space-y-4 text-xs">
          <div className="space-y-1.5">
            <label className="text-slate-300 font-semibold">
              Magnet Link, InfoHash, or .torrent URL
            </label>
            <input
              type="text"
              required
              value={magnetInput}
              onChange={(e) => setMagnetInput(e.target.value)}
              placeholder="magnet:?xt=urn:btih:8f3c7e91a0b5c4d3e2f180796857463524130291&dn=..."
              className="w-full p-2.5 bg-slate-950 border border-slate-700/80 rounded-lg font-mono text-cyan-300 placeholder-slate-600 focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400 transition-colors"
            />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="space-y-1.5">
              <label className="text-slate-300 font-semibold">Soundbank Label / Name</label>
              <input
                type="text"
                value={torrentName}
                onChange={(e) => setTorrentName(e.target.value)}
                placeholder="e.g. Hans Zimmer Strings 24bit"
                className="w-full p-2.5 bg-slate-950 border border-slate-700/80 rounded-lg text-slate-200 placeholder-slate-600 focus:outline-none focus:border-cyan-400"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-slate-300 font-semibold">Target Cloud Vault</label>
              <select
                value={targetCloud}
                onChange={(e) => setTargetCloud(e.target.value as CloudStorageTarget)}
                className="w-full p-2.5 bg-slate-950 border border-slate-700/80 rounded-lg text-slate-200 focus:outline-none focus:border-cyan-400 cursor-pointer"
              >
                <option value="terabox">TeraBox Vault (1024 GB Free Tier)</option>
                <option value="gdrive">Google Drive Pipe</option>
                <option value="kaggle">Kaggle Free Scratch (20GB)</option>
                <option value="github">GitHub Releases / LFS</option>
              </select>
            </div>

            <div className="space-y-1.5">
              <label className="text-slate-300 font-semibold">Total Torrent Size (GB)</label>
              <input
                type="number"
                min="1"
                max="2000"
                value={estimatedSizeGb}
                onChange={(e) => setEstimatedSizeGb(e.target.value)}
                className="w-full p-2.5 bg-slate-950 border border-slate-700/80 rounded-lg font-mono text-slate-200 focus:outline-none focus:border-cyan-400"
              />
            </div>
          </div>

          <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pt-2">
            <div className="flex items-center gap-2 text-[11px] text-slate-400">
              <ShieldCheck className="w-4 h-4 text-emerald-400 shrink-0" />
              <span>Headless streaming daemon pipes data directly to cloud API. Local disk write = 0 bytes.</span>
            </div>

            <button
              type="submit"
              disabled={isSubmitting || !magnetInput.trim()}
              className="px-4 py-2 text-xs font-semibold text-slate-950 bg-gradient-to-r from-cyan-400 to-cyan-300 hover:from-cyan-300 hover:to-cyan-200 rounded-lg transition-colors cursor-pointer disabled:opacity-40 inline-flex items-center gap-2 shrink-0 shadow-sm shadow-cyan-500/20"
            >
              {isSubmitting ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  <span>Hooking Remote Pipe...</span>
                </>
              ) : (
                <>
                  <DownloadCloud className="w-3.5 h-3.5" />
                  <span>Mirror to {targetCloud.toUpperCase()} (0 B Local)</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>

      {/* Mirrored Cloud Vault Table */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden p-5 space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 pb-2 border-b border-slate-800">
          <div>
            <h3 className="text-sm font-semibold text-white">
              Cloud-Mirrored Vault Assets ({tasks.length} entities · {formatBytes(totalMirroredBytes)})
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Permanently preserved in TeraBox 1TB and remote storage. Available on-demand via direct streaming.
            </p>
          </div>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs border-collapse">
            <thead>
              <tr className="border-b border-slate-800 text-slate-400">
                <th className="py-2.5 px-3 font-semibold">Soundbank / Archive Name</th>
                <th className="py-2.5 px-3 font-semibold">Cloud Target</th>
                <th className="py-2.5 px-3 font-semibold text-right">Vault Size</th>
                <th className="py-2.5 px-3 font-semibold">Local Disk</th>
                <th className="py-2.5 px-3 font-semibold">Mirror Status</th>
                <th className="py-2.5 px-3 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {tasks.map((task) => (
                <tr key={task.id} className="hover:bg-slate-800/30 transition-colors">
                  <td className="py-3 px-3">
                    <div className="font-medium text-slate-200 truncate max-w-xs sm:max-w-md">
                      {task.name}
                    </div>
                    <div className="text-[11px] font-mono text-slate-500 truncate max-w-xs sm:max-w-md mt-0.5">
                      {task.cloudPath}
                    </div>
                  </td>

                  <td className="py-3 px-3">
                    <span className="font-mono text-cyan-300 uppercase font-semibold text-[11px]">
                      {task.cloudTarget}
                    </span>
                  </td>

                  <td className="py-3 px-3 text-right font-mono font-semibold text-slate-100 tabular-nums">
                    {formatBytes(task.totalSizeBytes)}
                  </td>

                  <td className="py-3 px-3">
                    <span className="text-emerald-400 font-mono font-semibold text-xs tabular-nums">
                      {task.localCachedBytes === 0 ? '0 B (Evicted)' : formatBytes(task.localCachedBytes)}
                    </span>
                  </td>

                  <td className="py-3 px-3">
                    {task.status === 'mirroring_to_cloud' ? (
                      <div className="space-y-1">
                        <div className="flex items-center gap-2 text-cyan-400 font-medium">
                          <RefreshCw className="w-3 h-3 animate-spin" />
                          <span>Streaming {task.progressPercent}% ({task.speedMbps} MB/s)</span>
                        </div>
                        <div className="w-32 h-1.5 bg-slate-950 rounded-full overflow-hidden">
                          <div
                            className="h-full bg-cyan-400 transition-all duration-300"
                            style={{ width: `${task.progressPercent}%` }}
                          />
                        </div>
                      </div>
                    ) : (
                      <span className="text-emerald-400 font-medium inline-flex items-center gap-1.5">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        <span>Mirrored & Verified</span>
                      </span>
                    )}
                  </td>

                  <td className="py-3 px-3 text-right">
                    {task.localCachedBytes > 0 ? (
                      <button
                        onClick={() => onEvictLocal(task.id)}
                        className="px-2.5 py-1 text-[11px] font-medium text-rose-300 bg-rose-950/60 hover:bg-rose-900/60 border border-rose-800/60 rounded transition-colors cursor-pointer"
                      >
                        Evict Local
                      </button>
                    ) : (
                      <span className="text-slate-500 text-[11px] font-mono">
                        Vault Sealed
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};
