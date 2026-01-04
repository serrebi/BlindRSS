import sqlite3
import os
import logging
from core.config import APP_DIR

log = logging.getLogger(__name__)

DB_FILE = os.path.join(APP_DIR, "rss.db")


def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    try:
        c = conn.cursor()
        # Improve concurrent writer/readers when refresh runs in multiple threads
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("PRAGMA busy_timeout=60000")
        except Exception as e:
            log.warning(f"Failed to set PRAGMAs: {e}")
        
        c.execute('''CREATE TABLE IF NOT EXISTS feeds (
            id TEXT PRIMARY KEY,
            url TEXT,
            title TEXT,
            category TEXT,
            icon_url TEXT
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS articles (
            id TEXT PRIMARY KEY,
            feed_id TEXT,
            title TEXT,
            url TEXT,
            content TEXT,
            date TEXT,
            author TEXT,
            is_read INTEGER DEFAULT 0,
            is_favorite INTEGER DEFAULT 0,
            media_url TEXT,
            media_type TEXT,
            FOREIGN KEY(feed_id) REFERENCES feeds(id)
        )''')
        
        c.execute("CREATE INDEX IF NOT EXISTS idx_articles_feed_id ON articles (feed_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_articles_is_read ON articles (is_read)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_articles_date ON articles (date)")

        c.execute('''CREATE TABLE IF NOT EXISTS chapters (
            id TEXT PRIMARY KEY,
            article_id TEXT,
            start REAL,
            title TEXT,
            href TEXT,
            FOREIGN KEY(article_id) REFERENCES articles(id)
        )''')

        c.execute('''CREATE TABLE IF NOT EXISTS categories (
            id TEXT PRIMARY KEY,
            title TEXT UNIQUE
        )''')

        c.execute(
            '''CREATE TABLE IF NOT EXISTS playback_state (
            id TEXT PRIMARY KEY,
            position_ms INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER,
            updated_at INTEGER NOT NULL,
            completed INTEGER NOT NULL DEFAULT 0,
            seek_supported INTEGER,
            title TEXT
        )'''
        )
        c.execute("CREATE INDEX IF NOT EXISTS idx_playback_state_updated_at ON playback_state (updated_at)")
        
        # Migration: Add columns if they don't exist
        try:
            c.execute("ALTER TABLE articles ADD COLUMN media_url TEXT")
        except sqlite3.OperationalError:
            pass
            
        try:
            c.execute("ALTER TABLE articles ADD COLUMN media_type TEXT")
        except sqlite3.OperationalError:
            pass

        try:
            c.execute("ALTER TABLE articles ADD COLUMN is_favorite INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass

        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_articles_is_favorite ON articles (is_favorite)")
        except sqlite3.OperationalError:
            pass
            
        try:
            c.execute("ALTER TABLE feeds ADD COLUMN etag TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            c.execute("ALTER TABLE feeds ADD COLUMN last_modified TEXT")
        except sqlite3.OperationalError:
            pass
            
        # Seed categories from existing feeds if empty
        c.execute("SELECT count(*) FROM categories")
        if c.fetchone()[0] == 0:
            c.execute(
                "INSERT OR IGNORE INTO categories (id, title) "
                "SELECT lower(hex(randomblob(16))), category FROM feeds WHERE category IS NOT NULL AND category != ''"
            )
            # Ensure Uncategorized exists
            c.execute("INSERT OR IGNORE INTO categories (id, title) VALUES (?, ?)", ("uncategorized", "Uncategorized"))
        
        conn.commit()
    finally:
        conn.close()


def get_connection():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    try:
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception as e:
        log.warning(f"Failed to set PRAGMAs on connection: {e}")
    return conn
