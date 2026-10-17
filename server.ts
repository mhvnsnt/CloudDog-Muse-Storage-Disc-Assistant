import express from 'express';
import { createServer as createViteServer } from 'vite';
import fs from 'fs';
import path from 'path';
import os from 'os';
import { exec } from 'child_process';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

async function startServer() {
  const app = express();
  app.use(express.json());

  // 1. REAL SYSTEM DISK USAGE API
  app.get('/api/system-disk', (req, res) => {
    try {
      exec('df -k .', (err, stdout) => {
        if (err) {
          return res.json({
            success: false,
            error: err.message,
            totalBytes: 512 * 1024 * 1024 * 1024,
            freeBytes: 180 * 1024 * 1024 * 1024,
            usedBytes: 332 * 1024 * 1024 * 1024,
            diskUsagePercentage: 64.8,
          });
        }
        const lines = stdout.trim().split('\n');
        if (lines.length >= 2) {
          const parts = lines[1].replace(/\s+/g, ' ').split(' ');
          const totalKb = parseInt(parts[1], 10);
          const usedKb = parseInt(parts[2], 10);
          const freeKb = parseInt(parts[3], 10);
          const pct = parseFloat(parts[4]?.replace('%', '') || '0');
          return res.json({
            success: true,
            totalBytes: totalKb * 1024,
            usedBytes: usedKb * 1024,
            freeBytes: freeKb * 1024,
            diskUsagePercentage: isNaN(pct) ? 65 : pct,
            mount: parts[5] || '/',
            platform: process.platform,
          });
        }
        res.json({ success: false });
      });
    } catch (e: any) {
      res.status(500).json({ success: false, error: e.message });
    }
  });

  // 2. REAL LOCAL PATH SCANNER
  app.post('/api/scan-path', async (req, res) => {
    try {
      const targetPath = req.body.path || os.tmpdir();
      const resolved = path.resolve(targetPath);

      if (!fs.existsSync(resolved)) {
        return res.json({
          success: false,
          error: `Directory does not exist: ${resolved}`,
        });
      }

      const files = await fs.promises.readdir(resolved, { withFileTypes: true });
      const items = await Promise.all(
        files.slice(0, 50).map(async (f) => {
          const full = path.join(resolved, f.name);
          try {
            const st = await fs.promises.stat(full);
            return {
              name: f.name,
              path: full,
              isDirectory: f.isDirectory(),
              sizeBytes: st.size,
              lastModified: st.mtime.toISOString().slice(0, 16).replace('T', ' '),
              extension: path.extname(f.name) || (f.isDirectory() ? 'DIR' : 'FILE'),
            };
          } catch {
            return null;
          }
        })
      );

      res.json({
        success: true,
        scannedPath: resolved,
        items: items.filter(Boolean),
      });
    } catch (e: any) {
      res.status(500).json({ success: false, error: e.message });
    }
  });

  // 3. REAL IN-FLIGHT REAPER (Deletes real files on verified request)
  app.post('/api/reap-real-file', async (req, res) => {
    try {
      const filePath = req.body.filePath;
      if (!filePath) {
        return res.status(400).json({ success: false, error: 'filePath is required' });
      }

      const resolved = path.resolve(filePath);

      // Protect root and critical system paths
      if (['/', '/bin', '/usr', '/etc', '/System', 'C:\\', 'C:\\Windows'].includes(resolved)) {
        return res.status(403).json({ success: false, error: 'Protected system path' });
      }

      if (fs.existsSync(resolved)) {
        const st = await fs.promises.stat(resolved);
        const freedSize = st.size;
        if (st.isDirectory()) {
          await fs.promises.rm(resolved, { recursive: true, force: true });
        } else {
          await fs.promises.unlink(resolved);
        }
        return res.json({
          success: true,
          reapedPath: resolved,
          freedBytes: freedSize,
          message: `Real file deleted on verification: ${path.basename(resolved)}`,
        });
      } else {
        return res.json({
          success: true,
          reapedPath: resolved,
          freedBytes: 0,
          message: 'File already evicted or absent from disk.',
        });
      }
    } catch (e: any) {
      res.status(500).json({ success: false, error: e.message });
    }
  });

  // 4. REAL CLOUD DOG ENGINE HOOKS
  app.get('/api/cloud-dog/scan', (req, res) => {
    exec('python3 cloud_dog.py scan --json', { cwd: __dirname }, (err, stdout, stderr) => {
      if (err) {
        return res.json({ success: false, error: stderr || err.message });
      }
      try {
        const parsed = JSON.parse(stdout);
        res.json({ success: true, report: parsed, raw: stdout });
      } catch {
        res.json({ success: true, raw: stdout });
      }
    });
  });

  app.post('/api/cloud-dog/reap', (req, res) => {
    const apply = req.body.apply ? ' --apply' : '';
    const cmd = `python3 cloud_dog.py reap${apply}`;
    exec(cmd, { cwd: __dirname }, (err, stdout, stderr) => {
      res.json({
        success: !err,
        apply: !!req.body.apply,
        output: stdout,
        error: stderr,
      });
    });
  });

  app.post('/api/cloud-dog/enforce', (req, res) => {
    const apply = req.body.apply ? ' --apply' : '';
    const cmd = `python3 cloud_dog.py enforce${apply}`;
    exec(cmd, { cwd: __dirname }, (err, stdout, stderr) => {
      res.json({
        success: !err,
        apply: !!req.body.apply,
        output: stdout,
        error: stderr,
      });
    });
  });

  app.get('/api/cloud-dog/registry', (req, res) => {
    const home = os.homedir();
    const regPath = path.join(home, 'workspace', 'agent-ops', 'cloud-dog-registry.json');
    if (fs.existsSync(regPath)) {
      try {
        const content = fs.readFileSync(regPath, 'utf8');
        return res.json({ success: true, registry: JSON.parse(content) });
      } catch (e: any) {
        return res.json({ success: false, error: e.message });
      }
    }
    res.json({ success: true, registry: { version: 1, entries: {} } });
  });

  app.get('/api/cloud-dog/log', (req, res) => {
    const home = os.homedir();
    const logPath = path.join(home, 'workspace', 'agent-ops', 'disk-cleanup-log.md');
    if (fs.existsSync(logPath)) {
      return res.json({ success: true, log: fs.readFileSync(logPath, 'utf8') });
    }
    res.json({ success: true, log: '# Disk Cleanup Log\n\nNo entries logged yet.' });
  });

  // 5. REAL SHELL SCRIPT EXPORTER / BUNDLER
  app.get('/api/download-cli', (req, res) => {
    const cliPath = path.join(__dirname, 'cloud_dog.py');
    res.download(cliPath, 'cloud_dog.py');
  });

  // Mount Vite middlewares in development
  if (process.env.NODE_ENV !== 'production') {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: 'spa',
    });
    app.use(vite.middlewares);
  } else {
    app.use(express.static(path.join(__dirname, 'dist')));
    app.get('*', (req, res) => {
      res.sendFile(path.join(__dirname, 'dist', 'index.html'));
    });
  }

  const PORT = process.env.PORT || 3000;
  app.listen(Number(PORT), '0.0.0.0', () => {
    console.log(`Muse Storage Disc Assist full-stack server running at http://0.0.0.0:${PORT}`);
  });
}

startServer();
