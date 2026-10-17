import React, { useState } from 'react';
import { 
  Clock, 
  ShieldCheck, 
  Copy, 
  Check, 
  Download, 
  Sliders, 
  Bell, 
  Cpu, 
  FileCode,
  CheckCircle2
} from 'lucide-react';
import { DaemonConfig, OSPlatform } from '../types';

interface DaemonSchedulerProps {
  currentPlatform: OSPlatform;
}

export const DaemonScheduler: React.FC<DaemonSchedulerProps> = ({ currentPlatform }) => {
  const [config, setConfig] = useState<DaemonConfig>({
    enabled: true,
    schedule: 'weekly',
    diskFreeThresholdGb: 15,
    cleanStagedDownloads: true,
    cleanCrashDumps: true,
    cleanTempAudio: true,
    cleanAutosavesOlderThanDays: 30,
    sendNotifications: true,
    enforce75PercentLaw: true,
    autoOffloadToTeraBox: true,
  });

  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const getDaemonCode = () => {
    if (currentPlatform === 'macos') {
      return `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.muse.discassist.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/muse-assist</string>
        <string>clean</string>
        <string>--safe</string>
        <string>--days</string>
        <string>${config.cleanAutosavesOlderThanDays}</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Weekday</key>
        <integer>1</integer>
        <key>Hour</key>
        <integer>3</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
    <key>StandardOutPath</key>
    <string>/tmp/muse-assist-cron.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/muse-assist-cron.err</string>
</dict>
</plist>`;
    } else if (currentPlatform === 'windows') {
      return `# PowerShell Task Registration Script
$Action = New-ScheduledTaskAction -Execute "python.exe" -Argument "C:\\MuseAssist\\muse_disc_assist.py clean --safe"
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 3am
$Settings = New-ScheduledTaskSettingsSet -RunOnlyIfNetworkAvailable:$false -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "MuseStorageDiscAssist" -Action $Action -Trigger $Trigger -Settings $Settings -Description "Weekly Muse Hub clutter cleaner"
Write-Host "Muse Storage Disc Assist task registered successfully!" -ForegroundColor Green`;
    } else {
      return `[Unit]
Description=Muse Storage Disc Assist automated background cleaner
After=network.target

[Timer]
OnCalendar=Sun *-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target

# Install instructions:
# sudo cp muse-assist.timer /etc/systemd/system/
# sudo systemctl enable --now muse-assist.timer`;
    }
  };

  const code = getDaemonCode();

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopiedKey('code');
    setTimeout(() => setCopiedKey(null), 2000);
  };

  const handleDownload = () => {
    const filename =
      currentPlatform === 'macos'
        ? 'com.muse.discassist.daemon.plist'
        : currentPlatform === 'windows'
        ? 'register-muse-task.ps1'
        : 'muse-assist.timer';
    const blob = new Blob([code], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-8">
      {/* Overview Card */}
      <div className="p-6 bg-slate-900/70 border border-slate-800 rounded-xl space-y-4">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-slate-800">
          <div>
            <h2 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
              <Clock className="w-5 h-5 text-cyan-400" />
              <span>Automated Background Maintenance Daemon</span>
            </h2>
            <p className="text-xs text-slate-400 mt-1 max-w-2xl leading-relaxed">
              Never worry about your local SSD filling up silently again. Configure a background daemon or scheduled OS timer that automatically purges orphaned torrents, crash logs, and staging chunks on schedule.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <span className="text-xs text-slate-400">Daemon Status:</span>
            <button
              onClick={() => setConfig({ ...config, enabled: !config.enabled })}
              className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors cursor-pointer ${
                config.enabled
                  ? 'bg-emerald-950/70 border border-emerald-800/80 text-emerald-300'
                  : 'bg-slate-800 text-slate-400'
              }`}
            >
              {config.enabled ? 'Enabled · Active' : 'Disabled · Inactive'}
            </button>
          </div>
        </div>

        {/* Configuration Matrix */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5 pt-2">
          {/* Cadence */}
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-3">
            <div className="text-xs font-semibold text-slate-200">Execution Frequency</div>
            <div className="space-y-2">
              {(['daily', 'weekly', 'disk_threshold'] as const).map((mode) => (
                <label key={mode} className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
                  <input
                    type="radio"
                    name="schedule"
                    checked={config.schedule === mode}
                    onChange={() => setConfig({ ...config, schedule: mode })}
                    className="accent-cyan-500"
                  />
                  <span>
                    {mode === 'daily' && 'Daily at 3:00 AM (Idle)'}
                    {mode === 'weekly' && 'Weekly (Every Sunday 3:00 AM)'}
                    {mode === 'disk_threshold' && `Trigger when free disk < ${config.diskFreeThresholdGb} GB`}
                  </span>
                </label>
              ))}
            </div>
          </div>

          {/* Target Cleanup Rules */}
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-3">
            <div className="text-xs font-semibold text-slate-200">Target Cleanup Targets</div>
            <div className="space-y-2 text-xs text-slate-300">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.cleanStagedDownloads}
                  onChange={(e) => setConfig({ ...config, cleanStagedDownloads: e.target.checked })}
                  className="accent-cyan-500 rounded"
                />
                <span>Prune .musepkg & .part torrent chunks</span>
              </label>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.cleanCrashDumps}
                  onChange={(e) => setConfig({ ...config, cleanCrashDumps: e.target.checked })}
                  className="accent-cyan-500 rounded"
                />
                <span>Remove diagnostic .dmp crash files</span>
              </label>

              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.cleanTempAudio}
                  onChange={(e) => setConfig({ ...config, cleanTempAudio: e.target.checked })}
                  className="accent-cyan-500 rounded"
                />
                <span>Purge temporary 32-bit bounce WAVs</span>
              </label>
            </div>
          </div>

          {/* Retention Threshold */}
          <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-3">
            <div className="text-xs font-semibold text-slate-200">Retention & Alerts</div>
            <div className="space-y-3 text-xs">
              <div>
                <span className="text-slate-400 block mb-1">
                  Autosave Retention: <strong className="text-cyan-300 font-mono">{config.cleanAutosavesOlderThanDays} days</strong>
                </span>
                <input
                  type="range"
                  min="7"
                  max="90"
                  step="7"
                  value={config.cleanAutosavesOlderThanDays}
                  onChange={(e) => setConfig({ ...config, cleanAutosavesOlderThanDays: Number(e.target.value) })}
                  className="w-full accent-cyan-500 cursor-pointer"
                />
              </div>

              <label className="flex items-center gap-2 text-slate-300 cursor-pointer">
                <input
                  type="checkbox"
                  checked={config.sendNotifications}
                  onChange={(e) => setConfig({ ...config, sendNotifications: e.target.checked })}
                  className="accent-cyan-500 rounded"
                />
                <span>Show notification when &gt; 5GB reclaimed</span>
              </label>
            </div>
          </div>
        </div>
      </div>

      {/* Generated Daemon File Exporter */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-hidden p-5 space-y-3">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-3 border-b border-slate-800">
          <div>
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <FileCode className="w-4 h-4 text-cyan-400" />
              <span>
                {currentPlatform === 'macos' && 'macOS LaunchAgent Configuration (plist)'}
                {currentPlatform === 'windows' && 'Windows Task Scheduler Registration (PowerShell)'}
                {currentPlatform === 'linux' && 'Linux Systemd Timer Unit (systemd)'}
              </span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Install once to execute automatically in the background without high CPU consumption.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleCopy}
              className="px-3 py-1.5 text-xs font-medium text-slate-200 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg transition-colors inline-flex items-center gap-1.5 cursor-pointer"
            >
              {copiedKey === 'code' ? (
                <>
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Copied Script</span>
                </>
              ) : (
                <>
                  <Copy className="w-3.5 h-3.5" />
                  <span>Copy Script</span>
                </>
              )}
            </button>
            <button
              onClick={handleDownload}
              className="px-3 py-1.5 text-xs font-semibold text-slate-950 bg-cyan-400 hover:bg-cyan-300 rounded-lg transition-colors inline-flex items-center gap-1.5 cursor-pointer"
            >
              <Download className="w-3.5 h-3.5" />
              <span>Download File</span>
            </button>
          </div>
        </div>

        {/* Code View */}
        <pre className="p-4 bg-slate-950 border border-slate-800 rounded-lg font-mono text-xs text-cyan-200/90 overflow-x-auto leading-relaxed">
          {code}
        </pre>

        {/* Installation Instructions */}
        <div className="pt-2 text-xs text-slate-400 leading-relaxed">
          {currentPlatform === 'macos' && (
            <p>
              Save file to <span className="font-mono text-cyan-300">~/Library/LaunchAgents/com.muse.discassist.daemon.plist</span> and run{' '}
              <span className="font-mono text-slate-200 bg-slate-800/80 px-1 py-0.5 rounded">launchctl load ~/Library/LaunchAgents/com.muse.discassist.daemon.plist</span>.
            </p>
          )}
          {currentPlatform === 'windows' && (
            <p>
              Open PowerShell as Administrator and run the script above to create a native Windows background task.
            </p>
          )}
          {currentPlatform === 'linux' && (
            <p>
              Save to <span className="font-mono text-cyan-300">/etc/systemd/system/muse-assist.timer</span> and execute{' '}
              <span className="font-mono text-slate-200 bg-slate-800/80 px-1 py-0.5 rounded">systemctl enable --now muse-assist.timer</span>.
            </p>
          )}
        </div>
      </div>
    </div>
  );
};
