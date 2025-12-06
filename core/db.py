import sqlite3
import os
import sys

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.getcwd()

DB_FILE = os.path.join(APP_DIR, "rss.db")

def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
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
    c.execute("SELECT count(*) FROM categories")
    if c.fetchone()[0] == 0:
        c.execute("INSERT OR IGNORE INTO categories (id, title) SELECT lower(hex(randomblob(16))), category FROM feeds WHERE category IS NOT NULL AND category != ''")
        # Ensure Uncategorized exists
        c.execute("INSERT OR IGNORE INTO categories (id, title) VALUES (?, ?)", ("uncategorized", "Uncategorized"))
    
    conn.commit()
    conn.close()

def get_connection():
    return sqlite3.connect(DB_FILE)
