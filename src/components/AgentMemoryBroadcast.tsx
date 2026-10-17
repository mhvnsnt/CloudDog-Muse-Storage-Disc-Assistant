import React, { useState } from 'react';
import { 
  Radio, 
  Send, 
  ShieldCheck, 
  ShieldAlert, 
  CheckCircle2, 
  Terminal, 
  FileCode, 
  Layers, 
  Activity,
  Flame,
  Hash
} from 'lucide-react';
import { AgentLane, MemoryLogRecord } from '../types';

interface AgentMemoryBroadcastProps {
  lanes: AgentLane[];
  memoryLogs: MemoryLogRecord[];
  diskUsagePercentage: number;
  onEnforceLawNow: () => void;
  onBroadcastCustomMessage: (msg: string) => void;
}

export const AgentMemoryBroadcast: React.FC<AgentMemoryBroadcastProps> = ({
  lanes,
  memoryLogs,
  diskUsagePercentage,
  onEnforceLawNow,
  onBroadcastCustomMessage,
}) => {
  const [customMsg, setCustomMsg] = useState('');
  const [isBroadcasting, setIsBroadcasting] = useState(false);

  const handleBroadcast = (e: React.FormEvent) => {
    e.preventDefault();
    if (!customMsg.trim()) return;

    setIsBroadcasting(true);
    setTimeout(() => {
      onBroadcastCustomMessage(customMsg.trim());
      setCustomMsg('');
      setIsBroadcasting(false);
    }, 400);
  };

  const isCompliant = diskUsagePercentage <= 75.0;

  return (
    <div className="space-y-8">
      {/* NO-LOCAL-CLUTTER LAW Supreme Invariant Banner */}
      <div className={`p-6 rounded-xl border space-y-4 ${
        isCompliant
          ? 'bg-slate-900/80 border-slate-800'
          : 'bg-rose-950/20 border-rose-800/80'
      }`}>
        <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div>
            <div className="flex items-center gap-2">
              <div className="w-8 h-8 rounded-lg bg-red-950/60 border border-red-800/80 flex items-center justify-center text-red-400">
                <Flame className="w-4 h-4" />
              </div>
              <h2 className="text-xl font-bold tracking-tight text-white">
                NO-LOCAL-CLUTTER LAW Enforcement
              </h2>
            </div>
            <p className="text-xs text-slate-300 mt-1 max-w-3xl leading-relaxed">
              <strong>CONSTITUTIONAL MANDATE:</strong> Disk is a cache, not permanent storage. Everything goes to TeraBox (1TB), Drive, Kaggle, or GitHub — then clears local. All agents, all chats, every lane. Local disk stays strictly under 75%.
            </p>
          </div>

          <div className="flex items-center gap-4 bg-slate-950/80 border border-slate-800 p-3 rounded-lg text-right shrink-0">
            <div>
              <div className="text-[11px] text-slate-400 font-medium">Local Disk Watermark</div>
              <div className={`text-xl font-bold font-mono tabular-nums ${isCompliant ? 'text-emerald-400' : 'text-rose-400'}`}>
                {diskUsagePercentage.toFixed(1)}% / 75.0%
              </div>
            </div>
            <div className="h-8 w-px bg-slate-800" aria-hidden="true" />
            <div>
              <div className="text-[11px] text-slate-400 font-medium">Law Status</div>
              <div className="text-xs font-semibold uppercase text-emerald-400 flex items-center gap-1 justify-end">
                <ShieldCheck className="w-3.5 h-3.5" />
                <span>Strictly Compliant</span>
              </div>
            </div>
          </div>
        </div>

        {/* Watermark Bar */}
        <div className="space-y-1.5">
          <div className="flex items-center justify-between text-xs text-slate-400 font-mono">
            <span>Disk Cache Capacity Used</span>
            <span>Hard 75% Ceiling Threshold</span>
          </div>
          <div className="h-3 w-full bg-slate-950 rounded-full overflow-hidden border border-slate-800 relative">
            <div
              className={`h-full transition-all duration-500 ${
                isCompliant ? 'bg-cyan-400' : 'bg-rose-500'
              }`}
              style={{ width: `${Math.min(100, diskUsagePercentage)}%` }}
            />
            {/* 75% marker */}
            <div
              className="absolute top-0 bottom-0 w-0.5 bg-rose-500 z-10"
              style={{ left: '75%' }}
              title="75% No-Local-Clutter Law Threshold"
            />
          </div>
        </div>

        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pt-1">
          <span className="text-xs text-slate-400">
            Auto-eviction occurs when staging torrent chunks or downloads touch internal drive.
          </span>

          <button
            onClick={onEnforceLawNow}
            className="px-4 py-2 text-xs font-semibold text-slate-950 bg-red-400 hover:bg-red-300 rounded-lg transition-colors cursor-pointer inline-flex items-center gap-1.5 shadow-sm"
          >
            <ShieldCheck className="w-3.5 h-3.5" />
            <span>Enforce 75% Law Across All Lanes</span>
          </button>
        </div>
      </div>

      {/* Live Agent Lanes Grid */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden p-5 space-y-4">
        <div className="flex items-center justify-between pb-2 border-b border-slate-800">
          <div>
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <Layers className="w-4 h-4 text-cyan-400" />
              <span>Multi-Agent Active Lanes (AGENTS.md)</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              5 synchronized agent channels orchestrating storage, torrent streaming, and compute offloading.
            </p>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3 text-xs">
          {lanes.map((lane) => (
            <div
              key={lane.id}
              className="p-3.5 bg-slate-950/70 border border-slate-800 rounded-lg space-y-2"
            >
              <div className="flex items-center justify-between">
                <span className="font-semibold text-slate-200">{lane.name}</span>
                <span className="font-mono text-[10px] text-cyan-400">{lane.lane}</span>
              </div>
              <p className="text-[11px] text-slate-400 leading-relaxed">
                {lane.role}
              </p>
              <div className="flex items-center justify-between pt-1 text-[10px] text-slate-500 border-t border-slate-800/60">
                <span>Last sync: {lane.lastBroadcast.slice(11)}</span>
                <span className="text-emerald-400 font-mono">Compliant</span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Broadcast Message Form to All Agents & Chats */}
      <div className="p-5 bg-slate-900/60 border border-slate-800 rounded-xl space-y-3">
        <h3 className="text-sm font-semibold text-white flex items-center gap-2">
          <Radio className="w-4 h-4 text-cyan-400" />
          <span>Broadcast Directive to All Agents & Chats Now</span>
        </h3>
        <p className="text-xs text-slate-400">
          Emits high-priority directive across IPC sockets and appends permanently to <span className="font-mono text-cyan-300">MEMORY.md</span>.
        </p>

        <form onSubmit={handleBroadcast} className="flex gap-2 pt-1">
          <input
            type="text"
            required
            value={customMsg}
            onChange={(e) => setCustomMsg(e.target.value)}
            placeholder="e.g. Purge intermediate wav buffers; lock TeraBox 1TB torrent mirror..."
            className="flex-1 p-2.5 bg-slate-950 border border-slate-700/80 rounded-lg text-xs text-slate-200 placeholder-slate-600 focus:outline-none focus:border-cyan-400"
          />
          <button
            type="submit"
            disabled={isBroadcasting || !customMsg.trim()}
            className="px-4 py-2 text-xs font-semibold text-slate-950 bg-cyan-400 hover:bg-cyan-300 rounded-lg transition-colors cursor-pointer disabled:opacity-40 inline-flex items-center gap-1.5 shrink-0"
          >
            <Send className="w-3.5 h-3.5" />
            <span>Broadcast Now</span>
          </button>
        </form>
      </div>

      {/* Immutable Memory Log Stream (MEMORY.md) */}
      <div className="bg-slate-900/60 border border-slate-800 rounded-xl overflow-hidden p-5 space-y-3">
        <div className="flex items-center justify-between pb-2 border-b border-slate-800">
          <h3 className="text-sm font-semibold text-white flex items-center gap-2">
            <Hash className="w-4 h-4 text-cyan-400" />
            <span>Permanent Memory Log (MEMORY.md Records)</span>
          </h3>
          <span className="text-[11px] font-mono text-slate-400">
            {memoryLogs.length} verified state records
          </span>
        </div>

        <div className="space-y-2 font-mono text-xs max-h-64 overflow-y-auto pr-1">
          {memoryLogs.map((log) => (
            <div
              key={log.id}
              className="p-2.5 bg-slate-950/80 border border-slate-800/80 rounded-lg flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2"
            >
              <div className="space-y-0.5 max-w-2xl">
                <div className="flex items-center gap-2 text-slate-500 text-[10px]">
                  <span>[{log.timestamp}]</span>
                  <span className="text-cyan-400">{log.agentId}</span>
                  <span>·</span>
                  <span className="uppercase text-slate-400">{log.category}</span>
                </div>
                <div className="text-slate-200 text-[11px] leading-relaxed">
                  {log.message}
                </div>
              </div>

              <div className="text-[10px] text-slate-500 font-mono shrink-0">
                Hash: <span className="text-slate-400">{log.hash}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
