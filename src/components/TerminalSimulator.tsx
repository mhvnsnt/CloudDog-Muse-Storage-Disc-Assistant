import React, { useState, useRef, useEffect } from 'react';
import { Terminal, Copy, Check, Trash2, CornerDownLeft, Sparkles } from 'lucide-react';
import { TerminalEntry, ClutterItem } from '../types';
import { formatBytes } from '../data/mockStorageData';

interface TerminalSimulatorProps {
  logs: TerminalEntry[];
  onExecuteCommand: (cmd: string) => void;
  onClearLogs: () => void;
  items: ClutterItem[];
}

export const TerminalSimulator: React.FC<TerminalSimulatorProps> = ({
  logs,
  onExecuteCommand,
  onClearLogs,
  items,
}) => {
  const [inputCommand, setInputCommand] = useState('');
  const [copied, setCopied] = useState(false);
  const terminalEndRef = useRef<HTMLDivElement>(null);

  const quickCommands = [
    { label: 'Audit Footprint', cmd: 'muse-assist scan' },
    { label: 'Dry Run Clean', cmd: 'muse-assist clean --dry-run' },
    { label: 'Safe Purge Caches', cmd: 'muse-assist clean --safe' },
    { label: 'Check Soundbanks', cmd: 'muse-assist soundbanks' },
    { label: 'Daemon Status', cmd: 'muse-assist daemon --status' },
    { label: 'Help manual', cmd: 'muse-assist --help' },
  ];

  useEffect(() => {
    terminalEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [logs]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputCommand.trim()) return;
    onExecuteCommand(inputCommand.trim());
    setInputCommand('');
  };

  const handleCopyLogs = () => {
    const rawText = logs
      .map((entry) => {
        const prefix = entry.command ? `$ ${entry.command}\n` : '';
        return prefix + entry.output.join('\n');
      })
      .join('\n\n');
    navigator.clipboard.writeText(rawText);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="space-y-4">
      {/* Top Header & Quick Commands */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 p-4 bg-slate-900/60 border border-slate-800 rounded-xl">
        <div>
          <h2 className="text-base font-bold text-white flex items-center gap-2">
            <Terminal className="w-4 h-4 text-cyan-400" />
            <span>Interactive CLI Execution Console</span>
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Test and run <span className="font-mono text-cyan-300">muse-assist</span> commands in real-time.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={handleCopyLogs}
            className="px-2.5 py-1.5 text-xs font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-md transition-colors inline-flex items-center gap-1.5 cursor-pointer"
          >
            {copied ? (
              <>
                <Check className="w-3.5 h-3.5 text-emerald-400" />
                <span>Copied</span>
              </>
            ) : (
              <>
                <Copy className="w-3.5 h-3.5" />
                <span>Copy Logs</span>
              </>
            )}
          </button>
          <button
            onClick={onClearLogs}
            className="px-2.5 py-1.5 text-xs font-medium text-slate-400 hover:text-slate-200 bg-slate-800/60 hover:bg-slate-800 rounded-md transition-colors cursor-pointer"
          >
            Clear Screen
          </button>
        </div>
      </div>

      {/* Suggested Quick Commands */}
      <div className="flex items-center gap-2 overflow-x-auto text-xs pb-1 no-scrollbar">
        <span className="text-slate-500 shrink-0">Quick Run:</span>
        {quickCommands.map((q) => (
          <button
            key={q.cmd}
            onClick={() => onExecuteCommand(q.cmd)}
            className="px-2.5 py-1 text-slate-300 bg-slate-900 hover:bg-slate-800 border border-slate-800 rounded-md font-mono text-[11px] whitespace-nowrap cursor-pointer hover:border-cyan-500/40 transition-colors"
          >
            {q.cmd}
          </button>
        ))}
      </div>

      {/* Terminal Viewport */}
      <div className="bg-slate-950 border border-slate-800 rounded-xl overflow-hidden shadow-2xl flex flex-col h-[520px]">
        {/* Terminal Title Bar */}
        <div className="px-4 py-2.5 bg-slate-900/90 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full bg-rose-500/80" />
            <div className="w-3 h-3 rounded-full bg-amber-500/80" />
            <div className="w-3 h-3 rounded-full bg-emerald-500/80" />
            <span className="ml-2 text-xs font-mono text-slate-400">
              user@workstation: ~/muse-disc-assist (v1.4.2)
            </span>
          </div>

          <div className="text-[11px] font-mono text-cyan-400">
            Interactive Session Active
          </div>
        </div>

        {/* Scrollable logs */}
        <div className="flex-1 p-4 font-mono text-xs overflow-y-auto space-y-3 leading-relaxed">
          {logs.map((entry) => (
            <div key={entry.id} className="space-y-1">
              {entry.command && (
                <div className="flex items-center gap-2 text-cyan-300">
                  <span className="text-slate-500 select-none">➜</span>
                  <span className="text-emerald-400 select-none font-semibold">muse-assist</span>
                  <span className="text-white font-medium">{entry.command.replace(/^muse-assist\s*/, '')}</span>
                  <span className="text-slate-600 text-[10px] ml-auto tabular-nums">{entry.timestamp}</span>
                </div>
              )}
              <div
                className={`pl-4 space-y-0.5 ${
                  entry.type === 'success'
                    ? 'text-emerald-300'
                    : entry.type === 'warning'
                    ? 'text-amber-300'
                    : entry.type === 'error'
                    ? 'text-rose-400'
                    : entry.type === 'dim'
                    ? 'text-slate-500'
                    : 'text-slate-300'
                }`}
              >
                {entry.output.map((line, idx) => (
                  <div key={idx} className="whitespace-pre-wrap break-all">
                    {line}
                  </div>
                ))}
              </div>
            </div>
          ))}
          <div ref={terminalEndRef} />
        </div>

        {/* Command Input Prompt */}
        <form
          onSubmit={handleSubmit}
          className="p-3 bg-slate-900 border-t border-slate-800 flex items-center gap-2"
        >
          <span className="text-emerald-400 font-mono text-xs font-semibold pl-2 select-none">➜</span>
          <span className="text-cyan-400 font-mono text-xs select-none">$</span>
          <input
            type="text"
            value={inputCommand}
            onChange={(e) => setInputCommand(e.target.value)}
            placeholder="Type 'muse-assist scan', 'clean --safe', or 'help'..."
            className="flex-1 bg-transparent border-none text-xs font-mono text-slate-100 placeholder-slate-600 focus:outline-none focus:ring-0"
          />
          <button
            type="submit"
            className="px-2.5 py-1 text-xs font-medium text-slate-400 hover:text-white bg-slate-800 hover:bg-slate-700 rounded transition-colors inline-flex items-center gap-1 cursor-pointer"
          >
            <CornerDownLeft className="w-3 h-3" />
            <span className="hidden sm:inline">Run</span>
          </button>
        </form>
      </div>
    </div>
  );
};
