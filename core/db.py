import sqlite3
import os
import sys
import logging

log = logging.getLogger(__name__)

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
    PARENT_DIR = os.path.dirname(APP_DIR)
else:
    # Use project root (parent of this file's directory) instead of current working directory
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    PARENT_DIR = APP_DIR

DB_FILE = os.path.join(APP_DIR, "rss.db")

# Ensure directory exists (useful for portable runs from a writable folder)
os.makedirs(APP_DIR, exist_ok=True)

# If frozen build lacks a DB, try copying from parent folder (e.g., repo root) so bundled data is reused.
if getattr(sys, 'frozen', False) and not os.path.exists(DB_FILE):
    parent_db = os.path.join(PARENT_DIR, "rss.db")
    if os.path.exists(parent_db):
        try:
            import shutil
            shutil.copyfile(parent_db, DB_FILE)
        except Exception as e:
            log.warning(f"Failed to copy parent rss.db: {e}")

def init_db():
    # Use a shorter timeout for the initial check/setup to avoid hanging
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    
    # Enable WAL mode for better concurrency
    try:
        # Check current mode first
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()
        if mode and mode[0].upper() != 'WAL':
            # Only try to set if not already WAL, to minimize locking
            conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;") 
    except Exception as e:
        # Non-fatal if we can't set WAL (e.g. locked), just log and continue
        log.warning(f"Failed to set WAL mode: {e}")

    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS feeds (
        id TEXT PRIMARY KEY,
        url TEXT,
        title TEXT,
        category TEXT,
        icon_url TEXT
    )''')
    
    # Check if articles table needs migration to composite primary key (id, feed_id)
    c.execute("PRAGMA table_info(articles)")
    columns = c.fetchall()
    # columns: (cid, name, type, notnull, dflt_value, pk)
    # Check if 'id' is PK and 'feed_id' is PK. 
    # If composite, both should have pk > 0.
    id_pk = False
    feed_id_pk = False
    table_exists = False
    
    for col in columns:
        table_exists = True
        if col[1] == 'id' and col[5] > 0:
            id_pk = True
        if col[1] == 'feed_id' and col[5] > 0:
            feed_id_pk = True
            
    if table_exists and id_pk and not feed_id_pk:
        log.warning("Migrating articles table to composite primary key (id, feed_id).")
        
        c.execute("ALTER TABLE articles RENAME TO old_articles")
        c.execute('''CREATE TABLE articles (
            id TEXT,
            feed_id TEXT,
            title TEXT,
            url TEXT,
            content TEXT,
            date TEXT,
            author TEXT,
            is_read INTEGER DEFAULT 0,
            media_url TEXT,
            media_type TEXT,
            PRIMARY KEY (id, feed_id),
            FOREIGN KEY(feed_id) REFERENCES feeds(id)
        )''')
        c.execute("INSERT OR IGNORE INTO articles (id, feed_id, title, url, content, date, author, is_read, media_url, media_type) SELECT id, feed_id, title, url, content, date, author, is_read, media_url, media_type FROM old_articles")
        c.execute("DROP TABLE old_articles")
        
        # Clear feed cache to force full refresh and populate missing items
        c.execute("UPDATE feeds SET etag=NULL, last_modified=NULL")
        
        log.info("Articles table migration complete.")
    elif not table_exists: # Table doesn't exist, create with new schema
        c.execute('''CREATE TABLE articles (
            id TEXT,
            feed_id TEXT,
            title TEXT,
            url TEXT,
            content TEXT,
            date TEXT,
            author TEXT,
            is_read INTEGER DEFAULT 0,
            media_url TEXT,
            media_type TEXT,
            PRIMARY KEY (id, feed_id),
            FOREIGN KEY(feed_id) REFERENCES feeds(id)
        )''')
    
    # Re-create or create indexes (even if table was migrated)
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
        c.execute("ALTER TABLE feeds ADD COLUMN etag TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        c.execute("ALTER TABLE feeds ADD COLUMN last_modified TEXT")
    except sqlite3.OperationalError:
        pass
        
    # Seed categories from existing feeds if empty
    try:
        c.execute("SELECT count(*) FROM categories")
        if c.fetchone()[0] == 0:
            c.execute("INSERT OR IGNORE INTO categories (id, title) SELECT lower(hex(randomblob(16))), category FROM feeds WHERE category IS NOT NULL AND category != ''")
            # Ensure Uncategorized exists
            c.execute("INSERT OR IGNORE INTO categories (id, title) VALUES (?, ?)", ("uncategorized", "Uncategorized"))
        
        conn.commit()
    except sqlite3.OperationalError as e:
        if "locked" in str(e):
            log.warning("DB locked during seed/migration commit. Skipping this time.")
        else:
            raise
    finally:
        conn.close()

def get_connection():
    return sqlite3.connect(DB_FILE, timeout=30.0)
