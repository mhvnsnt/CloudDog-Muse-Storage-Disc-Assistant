import React, { useState } from 'react';
import { 
  GitBranch, 
  FileCode, 
  FileText, 
  Download, 
  Copy, 
  Check, 
  ExternalLink, 
  Star, 
  GitFork, 
  Scale, 
  Terminal,
  FolderGit2
} from 'lucide-react';
import { RepoFile } from '../types';
import { REPO_FILES } from '../data/repoFiles';

export const RepoExplorer: React.FC = () => {
  const [selectedFile, setSelectedFile] = useState<RepoFile>(REPO_FILES[0]);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const handleCopyFile = () => {
    navigator.clipboard.writeText(selectedFile.content);
    setCopiedKey('file');
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const handleCopyInstallCmd = (cmd: string, key: string) => {
    navigator.clipboard.writeText(cmd);
    setCopiedKey(key);
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const handleDownloadFile = () => {
    const blob = new Blob([selectedFile.content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = selectedFile.name;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleDownloadAllFiles = () => {
    // Downloads all repo files
    REPO_FILES.forEach((file, index) => {
      setTimeout(() => {
        const blob = new Blob([file.content], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = file.name;
        a.click();
        URL.revokeObjectURL(url);
      }, index * 200);
    });
  };

  const installCurl = 'curl -fsSL https://raw.githubusercontent.com/muse-community/muse-storage-disc-assist/main/install.sh | bash';
  const installPip = 'pip install muse-storage-disc-assist';

  return (
    <div className="space-y-6">
      {/* GitHub Repository Header Strip */}
      <div className="p-6 bg-slate-900/80 border border-slate-800 rounded-xl space-y-4">
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div className="space-y-1">
            <div className="flex items-center gap-2 text-sm text-slate-400 font-mono">
              <span className="hover:text-cyan-400 transition-colors">muse-community</span>
              <span>/</span>
              <span className="text-white font-bold text-base hover:text-cyan-400 transition-colors">
                muse-storage-disc-assist
              </span>
            </div>
            <p className="text-xs text-slate-300 leading-relaxed max-w-2xl">
              Open-source storage hygiene toolkit for Muse Hub, Muse Sounds, and MuseScore 4. Audits footprint, safe-cleans cache packages, and symlinks multi-gigabyte soundbanks to external SSDs.
            </p>
          </div>

          {/* Repo metadata indicators (Clean unboxed text, no pill sandwiches) */}
          <div className="flex flex-wrap items-center gap-4 text-xs text-slate-400">
            <div className="flex items-center gap-1.5 text-slate-300 font-mono">
              <Star className="w-3.5 h-3.5 text-amber-400" />
              <span>348 stars</span>
            </div>
            <span aria-hidden="true" className="text-slate-700">·</span>
            <div className="flex items-center gap-1.5 text-slate-300 font-mono">
              <GitFork className="w-3.5 h-3.5 text-cyan-400" />
              <span>42 forks</span>
            </div>
            <span aria-hidden="true" className="text-slate-700">·</span>
            <div className="flex items-center gap-1.5 text-slate-300">
              <Scale className="w-3.5 h-3.5 text-emerald-400" />
              <span>MIT License</span>
            </div>
            <span aria-hidden="true" className="text-slate-700">·</span>
            <span className="font-mono text-cyan-300 font-semibold">v1.4.2</span>
          </div>
        </div>

        {/* Quick Install One-Liners */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 pt-1">
          {/* Curl install */}
          <div className="p-3 bg-slate-950/80 border border-slate-800 rounded-lg flex items-center justify-between gap-3 font-mono text-xs">
            <div className="flex items-center gap-2 truncate">
              <span className="text-cyan-400 select-none">$</span>
              <span className="text-slate-200 truncate">{installCurl}</span>
            </div>
            <button
              onClick={() => handleCopyInstallCmd(installCurl, 'curl')}
              className="p-1.5 text-slate-400 hover:text-cyan-300 transition-colors shrink-0 cursor-pointer"
              title="Copy curl command"
            >
              {copiedKey === 'curl' ? (
                <Check className="w-4 h-4 text-emerald-400" />
              ) : (
                <Copy className="w-4 h-4" />
              )}
            </button>
          </div>

          {/* Pip install */}
          <div className="p-3 bg-slate-950/80 border border-slate-800 rounded-lg flex items-center justify-between gap-3 font-mono text-xs">
            <div className="flex items-center gap-2 truncate">
              <span className="text-cyan-400 select-none">$</span>
              <span className="text-slate-200 truncate">{installPip}</span>
            </div>
            <button
              onClick={() => handleCopyInstallCmd(installPip, 'pip')}
              className="p-1.5 text-slate-400 hover:text-cyan-300 transition-colors shrink-0 cursor-pointer"
              title="Copy pip command"
            >
              {copiedKey === 'pip' ? (
                <Check className="w-4 h-4 text-emerald-400" />
              ) : (
                <Copy className="w-4 h-4" />
              )}
            </button>
          </div>
        </div>
      </div>

      {/* Repository File Browser Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Left: File Tree */}
        <div className="lg:col-span-1 bg-slate-900/70 border border-slate-800 rounded-xl overflow-hidden p-3 space-y-2">
          <div className="flex items-center justify-between px-2 py-1 text-xs font-semibold text-slate-400 uppercase tracking-wider">
            <span>Repository Tree</span>
            <span className="font-mono text-[11px] text-cyan-400 font-normal">main branch</span>
          </div>

          <div className="space-y-1">
            {REPO_FILES.map((file) => {
              const isSelected = selectedFile.name === file.name;
              return (
                <button
                  key={file.name}
                  onClick={() => setSelectedFile(file)}
                  className={`w-full text-left px-3 py-2 rounded-lg text-xs font-medium transition-colors flex items-center justify-between cursor-pointer ${
                    isSelected
                      ? 'bg-cyan-950/40 text-cyan-300 border border-cyan-800/50'
                      : 'text-slate-300 hover:bg-slate-800/60'
                  }`}
                >
                  <div className="flex items-center gap-2 truncate">
                    {file.name.endsWith('.md') ? (
                      <FileText className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                    ) : (
                      <FileCode className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
                    )}
                    <span className="truncate">{file.name}</span>
                  </div>
                  <span className="font-mono text-[11px] text-slate-500 tabular-nums shrink-0">
                    {file.size}
                  </span>
                </button>
              );
            })}
          </div>

          <div className="pt-3 border-t border-slate-800/80 px-1">
            <button
              onClick={handleDownloadAllFiles}
              className="w-full py-2 text-xs font-semibold text-slate-950 bg-cyan-400 hover:bg-cyan-300 rounded-lg transition-colors inline-flex items-center justify-center gap-1.5 cursor-pointer shadow-sm"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Export All Files</span>
            </button>
          </div>
        </div>

        {/* Right: Code Viewer */}
        <div className="lg:col-span-3 bg-slate-900/70 border border-slate-800 rounded-xl overflow-hidden flex flex-col">
          {/* File Tab Bar */}
          <div className="px-4 py-3 bg-slate-950/80 border-b border-slate-800 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 font-mono text-xs text-white font-medium">
                <FileCode className="w-4 h-4 text-cyan-400" />
                <span>{selectedFile.path}</span>
                <span className="text-slate-500">({selectedFile.size})</span>
              </div>
              <div className="text-[11px] text-slate-400 mt-0.5">
                {selectedFile.description}
              </div>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={handleCopyFile}
                className="px-2.5 py-1.5 text-xs font-medium text-slate-300 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-md transition-colors inline-flex items-center gap-1.5 cursor-pointer"
              >
                {copiedKey === 'file' ? (
                  <>
                    <Check className="w-3.5 h-3.5 text-emerald-400" />
                    <span>Copied</span>
                  </>
                ) : (
                  <>
                    <Copy className="w-3.5 h-3.5" />
                    <span>Copy Code</span>
                  </>
                )}
              </button>
              <button
                onClick={handleDownloadFile}
                className="px-2.5 py-1.5 text-xs font-medium text-slate-200 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-md transition-colors inline-flex items-center gap-1.5 cursor-pointer"
              >
                <Download className="w-3.5 h-3.5" />
                <span>Download</span>
              </button>
            </div>
          </div>

          {/* Code Body */}
          <div className="p-4 bg-slate-950 flex-1 overflow-x-auto max-h-[600px] overflow-y-auto">
            <pre className="font-mono text-xs text-slate-200 leading-relaxed whitespace-pre font-normal selection:bg-cyan-500/30">
              {selectedFile.content}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
};
