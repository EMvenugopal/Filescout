const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('fileScout', {
  runSearch:   (folder, keyword, contextChars, mode) =>
    ipcRenderer.send('run-search', { folder, keyword, contextChars, mode }),
  openFile:    (filePath) => ipcRenderer.send('open-file', filePath),
  browseFolder: () => ipcRenderer.invoke('browse-folder'),
  onSetFolder:     (cb) => ipcRenderer.on('set-folder',      (_e, v) => cb(v)),
  onSearchResults: (cb) => ipcRenderer.on('search-results',  (_e, v) => cb(v)),
  onProgress:      (cb) => ipcRenderer.on('search-progress', (_e, v) => cb(v)),
  removeAllListeners: (ch) => ipcRenderer.removeAllListeners(ch),
});
