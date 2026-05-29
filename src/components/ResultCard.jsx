import React, { useState } from 'react';
import styles from './ResultCard.module.css';

const FILE_ICONS = {
  '.pdf':  '⬡',
  '.jpg':  '◻', '.jpeg': '◻', '.png': '◻', '.tiff': '◻', '.webp': '◻',
  '.html': '⟨⟩', '.htm': '⟨⟩', '.jsx': '⟨⟩', '.tsx': '⟨⟩', '.vue': '⟨⟩',
  '.js':   'JS', '.ts':  'TS',
  '.css':  '✦',
  '.json': '{ }', '.xml': '< >',
  '.csv':  '⊞', '.tsv': '⊞',
  '.txt':  '≡', '.md': '≡', '.log': '≡',
  '.py':   '🐍',
};

function getIcon(filepath) {
  const ext = filepath.slice(filepath.lastIndexOf('.')).toLowerCase();
  return FILE_ICONS[ext] || '◈';
}

function getTypeLabel(filepath) {
  const ext = filepath.slice(filepath.lastIndexOf('.')).toLowerCase();
  return ext.replace('.', '').toUpperCase();
}

export default function ResultCard({ result, keyword }) {
  const [expanded, setExpanded] = useState(true);

  const openFile = (e) => {
    e.stopPropagation();
    if (window.fileScout) window.fileScout.openFile(result.filepath);
  };

  const filename  = result.file.split(/[\\/]/).pop();
  const directory = result.file.includes('/') || result.file.includes('\\')
    ? result.file.slice(0, result.file.lastIndexOf(result.file.includes('/') ? '/' : '\\'))
    : '';

  return (
    <div className={styles.card}>
      <div className={styles.header} onClick={() => setExpanded(e => !e)}>
        <div className={styles.fileIcon}>{getIcon(result.file)}</div>
        <div className={styles.fileInfo}>
          <div className={styles.fileName}>
            {filename}
            <span className={styles.typeTag}>{getTypeLabel(result.file)}</span>
          </div>
          {directory && <div className={styles.filePath}>{directory}</div>}
        </div>
        <div className={styles.matchBadge}>{result.match_count} match{result.match_count !== 1 ? 'es' : ''}</div>
        <button className={styles.openBtn} onClick={openFile} title="Open file">↗</button>
        <div className={styles.chevron} style={{ transform: expanded ? 'rotate(90deg)' : 'rotate(0)' }}>›</div>
      </div>

      {expanded && (
        <div className={styles.snippets}>
          {result.matches.map((m, i) => (
            <div key={i} className={styles.snippet}>
              <span className={styles.snippetNum}>{i + 1}</span>
              <span className={styles.snippetText}>
                <span className={styles.context}>…{m.snippet.slice(0, m.match_start)}</span>
                <mark className={styles.highlight}>{m.match_text}</mark>
                <span className={styles.context}>{m.snippet.slice(m.match_end)}…</span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
