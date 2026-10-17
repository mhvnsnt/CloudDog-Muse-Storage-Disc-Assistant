import React, { useState } from 'react';
import { 
  FolderSync, 
  Copy, 
  Check, 
  HardDrive, 
  ShieldCheck, 
  ArrowRight, 
  AlertTriangle, 
  CheckCircle2, 
  Terminal,
  RefreshCw
} from 'lucide-react';
import { OSPlatform, ClutterItem } from '../types';
import { formatBytes } from '../data/mockStorageData';

interface RelocationWizardProps {
  currentPlatform: OSPlatform;
  soundbankItems: ClutterItem[];
  onSimulateRelocateSuccess: (freedBytes: number) => void;
}

export const RelocationWizard: React.FC<RelocationWizardProps> = ({
  currentPlatform,
  soundbankItems,
  onSimulateRelocateSuccess,
}) => {
  const defaultTargets: Record<OSPlatform, string> = {
    macos: '/Volumes/AudioSSD/MuseSounds',
    windows: 'D:\\AudioLibraries\\MuseSounds',
    linux: '/mnt/audio-nvme/musesounds',
  };

  const defaultSources: Record<OSPlatform, string> = {
    macos: '/Library/Application Support/MuseHub/Instruments',
    windows: 'C:\\ProgramData\\Muse Hub\\Instruments',
    linux: '/var/lib/muse-hub/instruments',
  };

  const [targetPath, setTargetPath] = useState(defaultTargets[currentPlatform]);
  const [copiedIndex, setCopiedIndex] = useState<number | null>(null);
  const [isSimulating, setIsSimulating] = useState(false);
  const [simulationStep, setSimulationStep] = useState<number>(0);
  const [isCompleted, setIsCompleted] = useState(false);

  const totalSoundbankBytes = soundbankItems.reduce((acc, it) => acc + it.sizeBytes, 0);

  // Generate real commands for the user
  const sourcePath = defaultSources[currentPlatform];

  const getShellCommands = () => {
    if (currentPlatform === 'macos') {
      return [
        `# 1. Ensure target external SSD directory exists`,
        `mkdir -p "${targetPath}"`,
        `# 2. Safely sync active soundbanks to external drive (preserves permissions & timestamps)`,
        `sudo rsync -avh --progress "${sourcePath}/" "${targetPath}/"`,
        `# 3. Create a safety backup of original folder`,
        `sudo mv "${sourcePath}" "${sourcePath}.bak"`,
        `# 4. Construct transparent filesystem symlink`,
        `sudo ln -s "${targetPath}" "${sourcePath}"`,
        `# 5. Verify symlink points cleanly to external drive`,
        `ls -la "${sourcePath}"`,
      ];
    } else if (currentPlatform === 'windows') {
      return [
        `:: 1. Create target folder on D: or E: drive in Administrator Command Prompt`,
        `mkdir "${targetPath}"`,
        `:: 2. Robust file copy with Robocopy to preserve directory structure`,
        `robocopy "${sourcePath}" "${targetPath}" /E /COPYALL /DCOPY:DAT /R:2 /W:2`,
        `:: 3. Rename original folder as backup`,
        `ren "${sourcePath}" "Instruments.bak"`,
        `:: 4. Create NTFS Directory Junction (transparent to Windows & Muse Hub)`,
        `mklink /J "${sourcePath}" "${targetPath}"`,
        `:: 5. Test junction access`,
        `dir "${sourcePath}"`,
      ];
    } else {
      return [
        `# 1. Create destination directory on external mount`,
        `mkdir -p "${targetPath}"`,
        `# 2. Copy soundbanks preserving metadata`,
        `sudo cp -a "${sourcePath}/." "${targetPath}/"`,
        `# 3. Rename original directory`,
        `sudo mv "${sourcePath}" "${sourcePath}.bak"`,
        `# 4. Create symbolic link`,
        `sudo ln -s "${targetPath}" "${sourcePath}"`,
        `# 5. Test access`,
        `ls -l "${sourcePath}"`,
      ];
    }
  };

  const commands = getShellCommands();

  const handleCopyAll = () => {
    navigator.clipboard.writeText(commands.join('\n'));
    setCopiedIndex(-1);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  const handleCopySingle = (cmd: string, index: number) => {
    navigator.clipboard.writeText(cmd);
    setCopiedIndex(index);
    setTimeout(() => setCopiedIndex(null), 2000);
  };

  const runSimulation = () => {
    setIsSimulating(true);
    setSimulationStep(1);
    setTimeout(() => {
      setSimulationStep(2);
      setTimeout(() => {
        setSimulationStep(3);
        setTimeout(() => {
          setSimulationStep(4);
          setTimeout(() => {
            setIsSimulating(false);
            setIsCompleted(true);
            onSimulateRelocateSuccess(totalSoundbankBytes);
          }, 800);
        }, 900);
      }, 1000);
    }, 800);
  };

  return (
    <div className="space-y-8">
      {/* Overview Banner */}
      <div className="p-6 bg-slate-900/70 border border-slate-800 rounded-xl space-y-4">
        <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4 pb-4 border-b border-slate-800/80">
          <div>
            <h2 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
              <FolderSync className="w-5 h-5 text-purple-400" />
              <span>External SSD Relocation Architecture</span>
            </h2>
            <p className="text-xs text-slate-400 mt-1 leading-relaxed max-w-2xl">
              Muse Hub defaults to storing soundbanks on your main boot drive. By relocating the instruments folder and replacing it with an OS-level symlink, your system gains up to 80GB of free space while MuseScore 4 continues playing without missing a beat.
            </p>
          </div>

          <div className="p-3 bg-purple-950/30 border border-purple-800/40 rounded-lg text-right">
            <div className="text-xs text-purple-300 font-medium">Reclaimable Internal SSD Space</div>
            <div className="text-2xl font-bold font-mono text-purple-200 tabular-nums">
              +{formatBytes(totalSoundbankBytes)}
            </div>
          </div>
        </div>

        {/* Path Configurator */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-slate-300">
              Source Path (Internal Drive)
            </label>
            <div className="p-2.5 bg-slate-950 border border-slate-800 rounded-lg font-mono text-xs text-slate-400 select-all">
              {sourcePath}
            </div>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-slate-300 flex items-center justify-between">
              <span>Target Path (External SSD / Volume)</span>
              <span className="text-[11px] text-slate-500 font-normal">Edit if your drive has a custom name</span>
            </label>
            <input
              type="text"
              value={targetPath}
              onChange={(e) => setTargetPath(e.target.value)}
              className="w-full p-2.5 bg-slate-950 border border-slate-700/80 rounded-lg font-mono text-xs text-cyan-300 focus:outline-none focus:border-cyan-400 focus:ring-1 focus:ring-cyan-400 transition-colors"
            />
          </div>
        </div>

        {/* Quick Presets */}
        <div className="flex items-center gap-2 text-xs text-slate-400 pt-1">
          <span className="text-slate-500">Preset Target:</span>
          {currentPlatform === 'macos' && (
            <>
              <button
                onClick={() => setTargetPath('/Volumes/SamsungT7/MuseSounds')}
                className="text-cyan-400 hover:underline cursor-pointer"
              >
                Samsung T7
              </button>
              <span>·</span>
              <button
                onClick={() => setTargetPath('/Volumes/CrucialX9/MuseSounds')}
                className="text-cyan-400 hover:underline cursor-pointer"
              >
                Crucial X9
              </button>
              <span>·</span>
              <button
                onClick={() => setTargetPath('/Volumes/AudioSSD/MuseSounds')}
                className="text-cyan-400 hover:underline cursor-pointer"
              >
                AudioSSD
              </button>
            </>
          )}
          {currentPlatform === 'windows' && (
            <>
              <button
                onClick={() => setTargetPath('D:\\AudioLibraries\\MuseSounds')}
                className="text-cyan-400 hover:underline cursor-pointer"
              >
                D:\ Drive
              </button>
              <span>·</span>
              <button
                onClick={() => setTargetPath('E:\\MuseSounds')}
                className="text-cyan-400 hover:underline cursor-pointer"
              >
                E:\ Drive
              </button>
            </>
          )}
          {currentPlatform === 'linux' && (
            <>
              <button
                onClick={() => setTargetPath('/mnt/fast-nvme/musesounds')}
                className="text-cyan-400 hover:underline cursor-pointer"
              >
                /mnt/fast-nvme
              </button>
              <span>·</span>
              <button
                onClick={() => setTargetPath('/media/user/SSD/musesounds')}
                className="text-cyan-400 hover:underline cursor-pointer"
              >
                /media/user/SSD
              </button>
            </>
          )}
        </div>
      </div>

      {/* Generated Executable Script Block */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-hidden space-y-3 p-5">
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 pb-3 border-b border-slate-800">
          <div>
            <h3 className="text-sm font-semibold text-white flex items-center gap-2">
              <Terminal className="w-4 h-4 text-cyan-400" />
              <span>Shell Relocation Commands ({currentPlatform.toUpperCase()})</span>
            </h3>
            <p className="text-xs text-slate-400 mt-0.5">
              Copy and execute these commands in your Terminal or Administrator PowerShell.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleCopyAll}
              className="px-3 py-1.5 text-xs font-medium text-slate-200 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded-lg transition-colors inline-flex items-center gap-1.5 cursor-pointer"
            >
              {copiedIndex === -1 ? (
                <>
                  <Check className="w-3.5 h-3.5 text-emerald-400" />
                  <span>Copied All Commands</span>
                </>
              ) : (
                <>
                  <Copy className="w-3.5 h-3.5" />
                  <span>Copy All Commands</span>
                </>
              )}
            </button>
          </div>
        </div>

        {/* Code list */}
        <div className="space-y-1.5 font-mono text-xs">
          {commands.map((cmd, idx) => {
            const isComment = cmd.startsWith('#') || cmd.startsWith('::');
            return (
              <div
                key={idx}
                className={`group flex items-center justify-between p-2 rounded-md ${
                  isComment
                    ? 'text-slate-500 bg-transparent'
                    : 'text-cyan-200 bg-slate-950/70 hover:bg-slate-950 border border-slate-800/80'
                }`}
              >
                <span className="truncate pr-4">{cmd}</span>
                {!isComment && (
                  <button
                    onClick={() => handleCopySingle(cmd, idx)}
                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1 text-slate-400 hover:text-cyan-300 cursor-pointer"
                    title="Copy command"
                  >
                    {copiedIndex === idx ? (
                      <Check className="w-3 h-3 text-emerald-400" />
                    ) : (
                      <Copy className="w-3 h-3" />
                    )}
                  </button>
                )}
              </div>
            );
          })}
        </div>

        {/* Interactive Simulation Runner */}
        <div className="pt-4 border-t border-slate-800 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="text-xs text-slate-400">
            {isCompleted ? (
              <span className="text-emerald-400 font-semibold inline-flex items-center gap-1.5">
                <CheckCircle2 className="w-4 h-4" />
                Relocation simulated! Internal SSD now freed by {formatBytes(totalSoundbankBytes)}.
              </span>
            ) : isSimulating ? (
              <span className="text-cyan-300 font-medium inline-flex items-center gap-1.5">
                <RefreshCw className="w-4 h-4 animate-spin text-cyan-400" />
                {simulationStep === 1 && 'Step 1/4: Initializing destination volume on external drive...'}
                {simulationStep === 2 && 'Step 2/4: Copying orchestral soundbanks (Muse Strings, Brass, Choir)...'}
                {simulationStep === 3 && 'Step 3/4: Renaming original path and testing file checksums...'}
                {simulationStep === 4 && 'Step 4/4: Constructing atomic filesystem symlink...'}
              </span>
            ) : (
              <span>Test the relocation flow virtually before running real commands.</span>
            )}
          </div>

          <button
            onClick={runSimulation}
            disabled={isSimulating || isCompleted}
            className="px-4 py-2 text-xs font-semibold text-slate-950 bg-purple-400 hover:bg-purple-300 rounded-lg transition-colors cursor-pointer disabled:opacity-40 inline-flex items-center gap-2 whitespace-nowrap shrink-0"
          >
            {isCompleted ? 'Relocation Complete' : 'Run Virtual Simulation'}
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Safety Verification Checklist */}
      <div className="p-5 bg-slate-900/50 border border-slate-800 rounded-xl space-y-3">
        <h4 className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
          Safety Invariants & Rollback Instructions
        </h4>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-xs text-slate-400">
          <div className="p-3 bg-slate-950/50 border border-slate-800/80 rounded-lg space-y-1">
            <div className="font-semibold text-slate-200 flex items-center gap-1.5">
              <ShieldCheck className="w-4 h-4 text-emerald-400" />
              <span>Zero Risk of Data Loss</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Step 3 renames original instruments to <span className="font-mono text-slate-300">.bak</span> instead of deleting them immediately.
            </p>
          </div>

          <div className="p-3 bg-slate-950/50 border border-slate-800/80 rounded-lg space-y-1">
            <div className="font-semibold text-slate-200 flex items-center gap-1.5">
              <CheckCircle2 className="w-4 h-4 text-cyan-400" />
              <span>MuseScore 4 Compatibility</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              Muse Sampler reads symlinks as native local folders. No score playback corruption or missing instrument popups.
            </p>
          </div>

          <div className="p-3 bg-slate-950/50 border border-slate-800/80 rounded-lg space-y-1">
            <div className="font-semibold text-slate-200 flex items-center gap-1.5">
              <FolderSync className="w-4 h-4 text-purple-400" />
              <span>Instant Rollback</span>
            </div>
            <p className="text-[11px] text-slate-400 leading-relaxed">
              To undo, simply remove the symlink and rename <span className="font-mono text-slate-300">Instruments.bak</span> back to <span className="font-mono text-slate-300">Instruments</span>.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};
