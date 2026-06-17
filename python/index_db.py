#!/usr/bin/env python3
import sqlite3
import os
import json
import re
from pathlib import Path
from typing import List, Dict, Set
import threading
import time

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.db')

class IndexDatabase:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
        self._index_lock = threading.Lock()

    def _create_tables(self):
        cursor = self.conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filepath TEXT UNIQUE,
                filename TEXT,
                extension TEXT,
                indexed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                ocr_method TEXT,
                confidence REAL,
                language TEXT,
                content_hash TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS content (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id INTEGER,
                word TEXT,
                word_normalized TEXT,
                position INTEGER,
                FOREIGN KEY (file_id) REFERENCES files (id)
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS inverted_index (
                word TEXT PRIMARY KEY,
                file_ids TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_files_language ON files(language)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_content_word ON content(word)
        ''')
        
        self.conn.commit()

    def add_file(self, filepath: str, filename: str, extension: str, 
                 ocr_method: str, confidence: float, language: str,
                 content_hash: str, words: List[str], positions: List[int]):
        with self._index_lock:
            cursor = self.conn.cursor()
            
            try:
                cursor.execute('''
                    INSERT INTO files (filepath, filename, extension, ocr_method, 
                                     confidence, language, content_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (filepath, filename, extension, ocr_method, confidence, 
                      language, content_hash))
                
                file_id = cursor.lastrowid
                
                for word, position in zip(words, positions):
                    cursor.execute('''
                        INSERT INTO content (file_id, word, word_normalized, position)
                        VALUES (?, ?, ?, ?)
                    ''', (file_id, word, self._normalize_word(word), position))
                
                self._update_inverted_index(file_id, words)
                
                self.conn.commit()
                return file_id
            except sqlite3.Error as e:
                self.conn.rollback()
                print(f"Database error adding file {filepath}: {e}")
                return None

    def _normalize_word(self, word: str) -> str:
        return re.sub(r'[^\w\s]', '', word.lower().strip())

    def _update_inverted_index(self, file_id: int, words: List[str]):
        cursor = self.conn.cursor()
        
        for word in set(words):
            word_norm = self._normalize_word(word)
            
            cursor.execute('''
                SELECT file_ids FROM inverted_index WHERE word = ?
            ''', (word_norm,))
            
            result = cursor.fetchone()
            
            if result:
                file_ids = json.loads(result['file_ids'])
                if file_id not in file_ids:
                    file_ids.append(file_id)
                    cursor.execute('''
                        UPDATE inverted_index SET file_ids = ? WHERE word = ?
                    ''', (json.dumps(file_ids), word_norm))
            else:
                cursor.execute('''
                    INSERT INTO inverted_index (word, file_ids)
                    VALUES (?, ?)
                ''', (word_norm, json.dumps([file_id])))
        
        self.conn.commit()

    def search(self, keyword: str, language: str = None, folder: str = None) -> List[Dict]:
        keyword_norm = self._normalize_word(keyword)
        
        cursor = self.conn.cursor()
        
        if language and folder:
            cursor.execute('''
                SELECT f.id, f.filepath, f.filename, f.extension, f.ocr_method,
                       f.confidence, f.language, c.position, c.word
                FROM files f
                JOIN content c ON f.id = c.file_id
                JOIN inverted_index ii ON c.word = ii.word
                WHERE ii.word = ? AND f.language = ? AND f.filepath LIKE ?
                ORDER BY f.confidence DESC, c.position
            ''', (keyword_norm, language, folder + '%'))
        elif language:
            cursor.execute('''
                SELECT f.id, f.filepath, f.filename, f.extension, f.ocr_method,
                       f.confidence, f.language, c.position, c.word
                FROM files f
                JOIN content c ON f.id = c.file_id
                JOIN inverted_index ii ON c.word = ii.word
                WHERE ii.word = ? AND f.language = ?
                ORDER BY f.confidence DESC, c.position
            ''', (keyword_norm, language))
        elif folder:
            cursor.execute('''
                SELECT f.id, f.filepath, f.filename, f.extension, f.ocr_method,
                       f.confidence, f.language, c.position, c.word
                FROM files f
                JOIN content c ON f.id = c.file_id
                JOIN inverted_index ii ON c.word = ii.word
                WHERE ii.word = ? AND f.filepath LIKE ?
                ORDER BY f.confidence DESC, c.position
            ''', (keyword_norm, folder + '%'))
        else:
            cursor.execute('''
                SELECT f.id, f.filepath, f.filename, f.extension, f.ocr_method,
                       f.confidence, f.language, c.position, c.word
                FROM files f
                JOIN content c ON f.id = c.file_id
                JOIN inverted_index ii ON c.word = ii.word
                WHERE ii.word = ?
                ORDER BY f.confidence DESC, c.position
            ''', (keyword_norm,))
        
        results = []
        current_file_id = None
        file_info = None
        
        for row in cursor.fetchall():
            if row['id'] != current_file_id:
                current_file_id = row['id']
                file_info = {
                    'file_id': row['id'],
                    'filepath': row['filepath'],
                    'filename': row['filename'],
                    'extension': row['extension'],
                    'ocr_method': row['ocr_method'],
                    'confidence': row['confidence'],
                    'language': row['language'],
                    'matches': []
                }
                results.append(file_info)
            
            if file_info:
                file_info['matches'].append({
                    'word': row['word'],
                    'position': row['position']
                })
        
        return results

    def get_all_files(self) -> List[Dict]:
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM files ORDER BY indexed_at DESC')
        
        return [dict(row) for row in cursor.fetchall()]

    def clear_index(self):
        with self._index_lock:
            cursor = self.conn.cursor()
            cursor.execute('DELETE FROM files')
            cursor.execute('DELETE FROM content')
            cursor.execute('DELETE FROM inverted_index')
            self.conn.commit()

    def close(self):
        self.conn.close()

if __name__ == '__main__':
    db = IndexDatabase()
    
    test_file_id = db.add_file(
        filepath='/test/document.pdf',
        filename='document.pdf',
        extension='.pdf',
        ocr_method='pdfplumber',
        confidence=0.95,
        language='en',
        content_hash='abc123',
        words=['test', 'document', 'file', 'test'],
        positions=[0, 5, 10, 15]
    )
    
    print(f"Added file with ID: {test_file_id}")
    
    results = db.search('test')
    print(f"Search results for 'test': {len(results)} files")
    
    db.close()
