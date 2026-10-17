import React from 'react';
import { HardDrive, AlertTriangle, ShieldCheck, RefreshCw, Zap } from 'lucide-react';

interface HeaderProps {
  activeTab: 'reaper' | 'overview' | 'mirror' | 'compute' | 'agents' | 'terminal' | 'repo';
  setActiveTab: (tab: 'reaper' | 'overview' | 'mirror' | 'compute' | 'agents' | 'terminal' | 'repo') => void;
  onEnforceLaw: () => void;
  isCleaning: boolean;
  diskUsagePercentage: number;
}

export const Header: React.FC<HeaderProps> = ({
  activeTab,
  setActiveTab,
  onEnforceLaw,
  isCleaning,
  diskUsagePercentage,
}) => {
  const isAlarm = diskUsagePercentage >= 90.0;

  return (
    <header className="border-b border-slate-800 bg-slate-950/80 backdrop-blur-md sticky top-0 z-40">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex items-center justify-between gap-6 h-16">
          {/* Zone 1: Single text element wordmark */}
          <button 
            onClick={() => setActiveTab('reaper')}
            className="flex items-center gap-2.5 text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-cyan-500 rounded-md cursor-pointer"
          >
            <div className={`w-8 h-8 rounded-lg flex items-center justify-center shadow-sm text-white shrink-0 ${
              isAlarm
                ? 'bg-gradient-to-br from-rose-500 to-red-600 shadow-rose-500/20'
                : 'bg-gradient-to-br from-cyan-500 to-indigo-600 shadow-cyan-500/20'
            }`}>
              {isAlarm ? <AlertTriangle className="w-4 h-4 animate-pulse" /> : <HardDrive className="w-4 h-4" />}
            </div>
            <span className="text-lg font-bold tracking-tight text-white whitespace-nowrap shrink-0">
              Muse Disc Assist
            </span>
          </button>

          {/* Zone 2: Concise single-line navigation links */}
          <nav className="hidden lg:flex items-center gap-5 text-sm font-medium">
            <button
              onClick={() => setActiveTab('reaper')}
              className={`whitespace-nowrap shrink-0 transition-colors py-1 cursor-pointer flex items-center gap-1.5 ${
                activeTab === 'reaper'
                  ? 'text-rose-400 font-semibold border-b-2 border-rose-400'
                  : 'text-slate-300 hover:text-white'
              }`}
            >
              <Zap className="w-3.5 h-3.5 text-rose-400" />
              <span>In-Flight Reaper</span>
            </button>
            <button
              onClick={() => setActiveTab('overview')}
              className={`whitespace-nowrap shrink-0 transition-colors py-1 cursor-pointer ${
                activeTab === 'overview'
                  ? 'text-cyan-400 font-semibold border-b-2 border-cyan-400'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Storage Auditor
            </button>
            <button
              onClick={() => setActiveTab('mirror')}
              className={`whitespace-nowrap shrink-0 transition-colors py-1 cursor-pointer ${
                activeTab === 'mirror'
                  ? 'text-cyan-400 font-semibold border-b-2 border-cyan-400'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              1TB Torrents
            </button>
            <button
              onClick={() => setActiveTab('compute')}
              className={`whitespace-nowrap shrink-0 transition-colors py-1 cursor-pointer ${
                activeTab === 'compute'
                  ? 'text-cyan-400 font-semibold border-b-2 border-cyan-400'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Compute & vVRAM
            </button>
            <button
              onClick={() => setActiveTab('agents')}
              className={`whitespace-nowrap shrink-0 transition-colors py-1 cursor-pointer ${
                activeTab === 'agents'
                  ? 'text-cyan-400 font-semibold border-b-2 border-cyan-400'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Agents & Memory Law
            </button>
            <button
              onClick={() => setActiveTab('terminal')}
              className={`whitespace-nowrap shrink-0 transition-colors py-1 cursor-pointer ${
                activeTab === 'terminal'
                  ? 'text-cyan-400 font-semibold border-b-2 border-cyan-400'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Terminal CLI
            </button>
            <button
              onClick={() => setActiveTab('repo')}
              className={`whitespace-nowrap shrink-0 transition-colors py-1 cursor-pointer ${
                activeTab === 'repo'
                  ? 'text-cyan-400 font-semibold border-b-2 border-cyan-400'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              GitHub Repo
            </button>
          </nav>

          {/* Zone 3: 1 primary action */}
          <div className="flex items-center gap-3 shrink-0">
            <button
              onClick={onEnforceLaw}
              disabled={isCleaning}
              className={`inline-flex items-center gap-2 px-4 py-2 text-xs font-semibold rounded-lg focus:outline-none disabled:opacity-50 transition-all shadow-sm whitespace-nowrap shrink-0 cursor-pointer ${
                isAlarm
                  ? 'text-slate-950 bg-gradient-to-r from-rose-400 via-amber-300 to-amber-200 hover:brightness-110 shadow-rose-500/30 font-bold'
                  : 'text-slate-950 bg-gradient-to-r from-cyan-400 to-cyan-300 hover:from-cyan-300 hover:to-cyan-200 shadow-cyan-500/20'
              }`}
            >
              {isCleaning ? (
                <>
                  <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  <span>Reaping Blobs...</span>
                </>
              ) : (
                <>
                  <Zap className="w-3.5 h-3.5 fill-current" />
                  <span>
                    {isAlarm
                      ? `Reap Blobs & Drop Disk (${diskUsagePercentage.toFixed(0)}%)`
                      : `Enforce <75% Law (${diskUsagePercentage.toFixed(0)}%)`}
                  </span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Mobile secondary navigation */}
        <div className="flex lg:hidden overflow-x-auto py-2.5 gap-4 text-xs border-t border-slate-800/60 no-scrollbar">
          <button
            onClick={() => setActiveTab('reaper')}
            className={`whitespace-nowrap shrink-0 cursor-pointer ${activeTab === 'reaper' ? 'text-rose-400 font-bold' : 'text-slate-400'}`}
          >
            Emergency Reaper
          </button>
          <button
            onClick={() => setActiveTab('overview')}
            className={`whitespace-nowrap shrink-0 cursor-pointer ${activeTab === 'overview' ? 'text-cyan-400 font-medium' : 'text-slate-400'}`}
          >
            Auditor
          </button>
          <button
            onClick={() => setActiveTab('mirror')}
            className={`whitespace-nowrap shrink-0 cursor-pointer ${activeTab === 'mirror' ? 'text-cyan-400 font-medium' : 'text-slate-400'}`}
          >
            1TB Torrents
          </button>
          <button
            onClick={() => setActiveTab('compute')}
            className={`whitespace-nowrap shrink-0 cursor-pointer ${activeTab === 'compute' ? 'text-cyan-400 font-medium' : 'text-slate-400'}`}
          >
            vVRAM
          </button>
          <button
            onClick={() => setActiveTab('agents')}
            className={`whitespace-nowrap shrink-0 cursor-pointer ${activeTab === 'agents' ? 'text-cyan-400 font-medium' : 'text-slate-400'}`}
          >
            Agents Law
          </button>
          <button
            onClick={() => setActiveTab('terminal')}
            className={`whitespace-nowrap shrink-0 cursor-pointer ${activeTab === 'terminal' ? 'text-cyan-400 font-medium' : 'text-slate-400'}`}
          >
            CLI Runner
          </button>
          <button
            onClick={() => setActiveTab('repo')}
            className={`whitespace-nowrap shrink-0 cursor-pointer ${activeTab === 'repo' ? 'text-cyan-400 font-medium' : 'text-slate-400'}`}
          >
            GitHub Repo
          </button>
        </div>
      </div>
    </header>
  );
};
