const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

const isDev = !app.isPackaged;

function getPythonBinary() {
  if (isDev) {
    const venvPython = path.join(__dirname, '../venv/bin/python3');
    const cmd = fs.existsSync(venvPython) ? venvPython : 'python3';
    return { cmd, script: path.join(__dirname, '../python/search.py') };
  }
  const ext = process.platform === 'win32' ? 'search.exe' : 'search';
  return { cmd: path.join(process.resourcesPath, 'python', ext), script: null };
}

function getFolderFromArgs() {
  const args = process.argv.slice(isDev ? 2 : 1);
  return args.find(a => { try { return fs.existsSync(a) && fs.statSync(a).isDirectory(); } catch { return false; } }) || null;
}

let mainWindow;

function createWindow(folderPath) {
  mainWindow = new BrowserWindow({
    width: 1100, height: 720, minWidth: 700, minHeight: 500,
    backgroundColor: '#0a0a0f',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (isDev) {
    mainWindow.loadURL('http://localhost:3000');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '../build/index.html'));
  }

  mainWindow.webContents.on('did-finish-load', () => {
    if (folderPath) mainWindow.webContents.send('set-folder', folderPath);
  });
}

app.whenReady().then(() => {
  const folderPath = getFolderFromArgs();
  createWindow(folderPath);
  app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(folderPath); });
});

app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });

ipcMain.on('run-search', (event, { folder, keyword, contextChars, language, ocrQuality }) => {
  const { cmd, script } = getPythonBinary();
  const args = script
    ? [script, '--folder', folder, '--keyword', keyword, '--context', String(contextChars || 80), '--json']
    : ['--folder', folder, '--keyword', keyword, '--context', String(contextChars || 80), '--json'];

  if (language && language !== 'en') {
    args.push('--language', language);
  }
  if (ocrQuality && ocrQuality !== 'balanced') {
    args.push('--ocr-quality', ocrQuality);
  }

  const proc = spawn(cmd, args, {
    maxBuffer: 1024 * 1024 * 10,
    timeout: 300000,
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  });
  let buffer = '';

  let stderrBuf = '';
  proc.stdout.on('data', (data) => { buffer += data.toString(); });
  proc.stderr.on('data', (data) => {
    const chunk = data.toString();
    stderrBuf += chunk;
    const line = chunk.trim();
    if (line) event.sender.send('search-progress', line);
  });

  proc.on('close', (code) => {
    const raw = buffer.trim();
    if (!raw) {
      event.sender.send('search-results', { ok: false, results: [], error: `Python process exited with code ${code} and produced no stdout output. stderr: ${stderrBuf.slice(-500)}` });
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      const results = Array.isArray(parsed) ? parsed : (parsed.results || []);
      event.sender.send('search-results', {
        ok: true,
        results,
        searchTime: parsed.search_time_seconds,
        language: parsed.language,
        totalMatched: parsed.total_files_matched,
      });
    } catch (e) {
      event.sender.send('search-results', { ok: false, results: [], error: `Failed to parse results: ${e.message}. Raw output: ${raw.slice(-300)}` });
    }
  });
  proc.on('error', (err) => {
    event.sender.send('search-results', { ok: false, results: [], error: err.message });
  });
});

ipcMain.on('open-file', (event, filePath) => { shell.openPath(filePath); });

ipcMain.handle('browse-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'], title: 'Select folder to search',
  });
  return result.canceled ? null : result.filePaths[0];
});
