const { app, BrowserWindow, ipcMain, shell, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');

const isDev = !app.isPackaged;

function getPythonBinary() {
  if (isDev) {
    return { cmd: 'python3', script: path.join(__dirname, '../python/search.py') };
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

ipcMain.on('run-search', (event, { folder, keyword, contextChars }) => {
  const { cmd, script } = getPythonBinary();
  const args = script
    ? [script, '--folder', folder, '--keyword', keyword, '--context', String(contextChars || 80), '--json']
    : ['--folder', folder, '--keyword', keyword, '--context', String(contextChars || 80), '--json'];

  const proc = spawn(cmd, args);
  let buffer = '';

  proc.stdout.on('data', (data) => { buffer += data.toString(); });
  proc.stderr.on('data', (data) => {
    const line = data.toString().trim();
    if (line) event.sender.send('search-progress', line);
  });
  proc.on('close', () => {
    try {
      const parsed = JSON.parse(buffer);
      // Always send the results array, not the whole object
      const results = Array.isArray(parsed) ? parsed : (parsed.results || []);
      event.sender.send('search-results', { ok: true, results });
    } catch (e) {
      event.sender.send('search-results', { ok: false, results: [], error: 'Failed to parse results: ' + e.message });
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
