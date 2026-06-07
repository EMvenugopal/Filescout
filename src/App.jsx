import React, { useState, useEffect, useRef } from 'react';
import styles from './App.module.css';
import ResultCard from './components/ResultCard';
import ProgressLog from './components/ProgressLog';
import LanguageSelector from './components/LanguageSelector';
import OcrQualitySelector from './components/OcrQualitySelector';

const ipc = window.fileScout;

export default function App() {
  const [folder, setFolder]           = useState('');
  const [keyword, setKeyword]         = useState('');
  const [searching, setSearching]     = useState(false);
  const [results, setResults]         = useState(null);
  const [progress, setProgress]       = useState([]);
  const [error, setError]             = useState('');
  const [searchLanguage, setLang]     = useState('en');
  const [ocrQuality, setOcrQuality]   = useState('balanced');
  const inputRef = useRef(null);

  useEffect(() => {
    if (!ipc) return;
    ipc.onSetFolder((f) => setFolder(f));
    ipc.onSearchResults((data) => {
      setSearching(false);
      if (data.ok) {
        const arr = Array.isArray(data.results)
          ? data.results
          : Array.isArray(data.results?.results)
            ? data.results.results
            : [];
        setResults(arr);
        setError('');
      } else {
        setError(data.error || 'Search failed.');
        setResults([]);
      }
    });
    ipc.onProgress((line) => {
      setProgress((p) => [...p.slice(-199), line]);
    });
    return () => {
      ipc.removeAllListeners('set-folder');
      ipc.removeAllListeners('search-results');
      ipc.removeAllListeners('search-progress');
    };
  }, []);

  const handleBrowse = async () => {
    if (!ipc) return;
    const f = await ipc.browseFolder();
    if (f) setFolder(f);
  };

  const handleSearch = () => {
    if (!folder.trim()) { setError('Please select a folder first.'); return; }
    if (!keyword.trim()) { setError('Please enter a keyword.'); return; }
    setError('');
    setResults(null);
    setProgress([]);
    setSearching(true);
    const lang = searchLanguage !== 'en' ? searchLanguage : undefined;
    ipc.runSearch(folder.trim(), keyword.trim(), 80, undefined, lang, ocrQuality);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleSearch();
  };

  const totalMatches = Array.isArray(results)
    ? results.reduce((sum, r) => sum + (r.match_count || 0), 0)
    : 0;

  return (
    <div className={styles.root}>
      <header className={styles.titlebar}>
        <div className={styles.logo}>
          <span className={styles.logoIcon}>◈</span>
          <span className={styles.logoText}>FileScout</span>
        </div>
        <div className={styles.tagline}>search inside any file</div>
      </header>

      <div className={styles.folderRow}>
        <div className={styles.folderLabel}>FOLDER</div>
        <div className={styles.folderPath} title={folder}>
          {folder || <span className={styles.placeholder}>No folder selected — right-click a folder or browse below</span>}
        </div>
        <button className={styles.browseBtn} onClick={handleBrowse}>Browse</button>
      </div>

      <div className={styles.searchRow}>
        <div className={styles.searchWrap}>
          <span className={styles.searchIcon}>⌕</span>
          <input
            ref={inputRef}
            className={styles.searchInput}
            type="text"
            placeholder="Type a keyword and press Enter…"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={searching}
            autoFocus
          />
          {keyword && (
            <button className={styles.clearBtn} onClick={() => { setKeyword(''); inputRef.current?.focus(); }}>✕</button>
          )}
        </div>

        <LanguageSelector value={searchLanguage} onChange={setLang} />
        <OcrQualitySelector value={ocrQuality} onChange={setOcrQuality} />

        <button
          className={styles.searchBtn}
          onClick={handleSearch}
          disabled={searching || !folder || !keyword}
        >
          {searching ? <span className={styles.spinner} /> : 'Search'}
        </button>
      </div>

      {error && <div className={styles.error}>⚠ {error}</div>}

      <div className={styles.body}>
        <div className={styles.resultsPane}>
          {results === null && !searching && (
            <div className={styles.emptyState}>
              <div className={styles.emptyIcon}>◈</div>
              <div className={styles.emptyTitle}>Ready to scout</div>
              <div className={styles.emptyDesc}>Select a folder, type a keyword, hit Search</div>
            </div>
          )}

          {Array.isArray(results) && results.length === 0 && (
            <div className={styles.emptyState}>
              <div className={styles.emptyIcon}>∅</div>
              <div className={styles.emptyTitle}>No matches found</div>
              <div className={styles.emptyDesc}>Try a different keyword or folder</div>
            </div>
          )}

          {Array.isArray(results) && results.length > 0 && (
            <>
              <div className={styles.resultsSummary}>
                <span className={styles.badge}>{results.length} file{results.length !== 1 ? 's' : ''}</span>
                <span className={styles.badge2}>{totalMatches} match{totalMatches !== 1 ? 'es' : ''}</span>
                <span className={styles.summaryKeyword}>for "{keyword}"</span>
                {searchLanguage !== 'en' && (
                  <span className={styles.summaryLang}>{searchLanguage.toUpperCase()}</span>
                )}
              </div>
              <div className={styles.resultsList}>
                {results.map((r, i) => (
                  <ResultCard key={i} result={r} keyword={keyword} />
                ))}
              </div>
            </>
          )}
        </div>

        {(searching || progress.length > 0) && (
          <ProgressLog lines={progress} searching={searching} />
        )}
      </div>
    </div>
  );
}
