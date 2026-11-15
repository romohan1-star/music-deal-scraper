import json
import sqlite3
from pathlib import Path
from .model import now


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, config_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS collections (run_id TEXT, retailer TEXT, url TEXT, status TEXT NOT NULL DEFAULT 'PARTIAL', advertised_count INTEGER, count_unit TEXT, count_reliable INTEGER DEFAULT 0, terminal INTEGER DEFAULT 0, reason TEXT, PRIMARY KEY(run_id,retailer,url));
        CREATE TABLE IF NOT EXISTS pages (run_id TEXT, retailer TEXT, collection_url TEXT, url TEXT, page_index INTEGER, status TEXT, http_status INTEGER, attempts INTEGER DEFAULT 0, sha256 TEXT, evidence_path TEXT, next_json TEXT, products INTEGER, fetched_at TEXT, error TEXT, PRIMARY KEY(run_id,retailer,collection_url,url));
        CREATE TABLE IF NOT EXISTS offers (id TEXT PRIMARY KEY, retailer TEXT, product_url TEXT, first_seen TEXT);
        CREATE TABLE IF NOT EXISTS observations (run_id TEXT, offer_id TEXT, collected_at TEXT, data_json TEXT, PRIMARY KEY(run_id,offer_id), FOREIGN KEY(offer_id) REFERENCES offers(id));
        CREATE TABLE IF NOT EXISTS membership (run_id TEXT, retailer TEXT, collection_url TEXT, offer_id TEXT, listing_id TEXT, PRIMARY KEY(run_id,retailer,collection_url,offer_id));
        CREATE TABLE IF NOT EXISTS errors (id INTEGER PRIMARY KEY, run_id TEXT, retailer TEXT, url TEXT, stage TEXT, message TEXT, created_at TEXT);
        CREATE TABLE IF NOT EXISTS validations (id INTEGER PRIMARY KEY, run_id TEXT, retailer TEXT, collection_url TEXT, source_url TEXT, check_type TEXT, status TEXT, detail TEXT);
        ''')

    def error(self, run, retailer, url, stage, message):
        with self.db:
            self.db.execute('INSERT INTO errors(run_id,retailer,url,stage,message,created_at) VALUES(?,?,?,?,?,?)',(run,retailer,url,stage,str(message),now()))

    def collection(self, run, retailer, url):
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO collections(run_id,retailer,url) VALUES(?,?,?)',(run,retailer,url))

    def page(self, run, retailer, collection, url):
        return self.db.execute('SELECT * FROM pages WHERE run_id=? AND retailer=? AND collection_url=? AND url=?',(run,retailer,collection,url)).fetchone()

    def save_page(self, run, retailer, collection, url, index, result, offers, next_urls, status='completed', error=None):
        # One transaction protects the checkpoint and all page observations.
        with self.db:
            old=self.page(run,retailer,collection,url)
            self.db.execute('INSERT OR REPLACE INTO pages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                run,retailer,collection,url,index,status,result.get('status'),(old['attempts'] if old else 0)+result.get('attempts',1),result.get('sha256'),result.get('evidence_path'),json.dumps(next_urls),len(offers),result.get('fetched_at',now()),error))
            for o in offers:
                record=o.record()
                self.db.execute('INSERT OR IGNORE INTO offers VALUES(?,?,?,?)',(o.offer_id,retailer,o.product_url,o.collected_at))
                self.db.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?,?)',(run,o.offer_id,o.collected_at,json.dumps(record)))
                self.db.execute('INSERT OR IGNORE INTO membership VALUES(?,?,?,?,?)',(run,retailer,collection,o.offer_id,o.listing_id))

    def current(self, run):
        return [json.loads(r[0]) for r in self.db.execute('SELECT data_json FROM observations WHERE run_id=? ORDER BY offer_id',(run,))]

    def coverage(self, run):
        rows=[]
        for c in self.db.execute('SELECT * FROM collections WHERE run_id=? ORDER BY retailer,url',(run,)):
            d=dict(c)
            pages=self.db.execute('SELECT status,COUNT(*) n,SUM(attempts) attempts FROM pages WHERE run_id=? AND retailer=? AND collection_url=? GROUP BY status',(run,c['retailer'],c['url'])).fetchall()
            d['pages_attempted']=sum(x['n'] for x in pages)
            d['request_attempts']=sum(x['attempts'] for x in pages)
            d['pages_completed']=sum(x['n'] for x in pages if x['status']=='completed')
            d['pages_failed']=d['pages_attempted']-d['pages_completed']
            counts=self.db.execute('SELECT COUNT(*) offers,COUNT(DISTINCT listing_id) listings FROM membership WHERE run_id=? AND retailer=? AND collection_url=?',(run,c['retailer'],c['url'])).fetchone()
            d['unique_offers']=counts['offers']; d['unique_listings']=counts['listings']
            d['duplicate_offers_across_collections']=self.db.execute('SELECT COUNT(*) FROM membership a WHERE a.run_id=? AND a.retailer=? AND a.collection_url=? AND EXISTS(SELECT 1 FROM membership b WHERE b.run_id=a.run_id AND b.retailer=a.retailer AND b.offer_id=a.offer_id AND b.collection_url<>a.collection_url)',(run,c['retailer'],c['url'])).fetchone()[0]
            denom=d['advertised_count']
            d['coverage_percentage']=round(100*d['unique_listings']/denom,2) if d['count_reliable'] and denom and d['unique_listings']<=denom else None
            d['scope']='Known collection only; retailer-wide discovery is not guaranteed'
            rows.append(d)
        return rows

    def close(self):
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        self.db.close()
