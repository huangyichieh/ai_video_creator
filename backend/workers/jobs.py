"""Small durable local job queue. Provider limits stay in production services."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from collections import defaultdict,deque
from datetime import datetime,timezone
import json
import sqlite3
from threading import Event,Lock
from uuid import uuid4


def now(): return datetime.now(timezone.utc).isoformat()

# These engines commit under their own short file transactions and already
# enforce provider limits. They can overlap with a project's main pipeline.
INDEPENDENT={'library.talking_sample','library.generate_costume',
    'voice_design.generate_voice_design','visual.design_reference',
    'storyboard_audio.generate'}


class Jobs:
    def __init__(self,database,execute):
        self.database=database
        self.execute=execute
        self.pool=ThreadPoolExecutor(max_workers=5,thread_name_prefix='story-job')
        self.events={}
        self.waiting=defaultdict(deque)
        self.active_projects=set()
        self.closing=False
        self.lock=Lock()
        database.parent.mkdir(parents=True,exist_ok=True)
        with closing(self.connect()) as db,db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,project_id TEXT NOT NULL,operation TEXT NOT NULL,arguments TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,result TEXT,error TEXT)')
            db.execute("UPDATE jobs SET status='interrupted',error=?,updated_at=? WHERE status IN ('queued','running','stopping')",('Server restarted. Retry explicitly; completed media and provider job receipts are retained.',now()))

    def connect(self):
        db=sqlite3.connect(self.database,timeout=30)
        db.row_factory=sqlite3.Row
        return db

    def get(self,identity):
        with closing(self.connect()) as db:
            row=db.execute('SELECT * FROM jobs WHERE id=?',(identity,)).fetchone()
        if row is None: raise KeyError(identity)
        result=dict(row)
        for name in ('arguments','result'):
            result[name]=json.loads(result[name]) if result[name] else None
        return result

    def list(self,project_id=None):
        with closing(self.connect()) as db:
            rows=db.execute('SELECT id,project_id,operation,arguments,status,created_at,updated_at,error FROM jobs WHERE (? IS NULL OR project_id=?) ORDER BY created_at DESC LIMIT 200',(project_id,project_id)).fetchall()
        result=[]
        for row in rows:
            item=dict(row)
            item['arguments']={key:value for key,value in json.loads(item['arguments']).items()
                if key not in {'expected','expected_input_hash','expected_layout','library','data','text','plan','settings','changes'}}
            item['result']=None # Large production results are loaded on demand.
            result.append(item)
        return result

    def update(self,identity,status,*,result=None,error=None):
        with closing(self.connect()) as db,db:
            db.execute('UPDATE jobs SET status=?,updated_at=?,result=?,error=? WHERE id=?',
                (status,now(),json.dumps(result,ensure_ascii=False) if result is not None else None,error,identity))

    def submit(self,request):
        identity=uuid4().hex
        with closing(self.connect()) as db,db:
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?)',(identity,request.project_id,request.operation,
                json.dumps(request.arguments,ensure_ascii=False),'queued',now(),now(),None,None))
        stop=Event()
        with self.lock:
            self.events[identity]=stop
        independent=request.operation in INDEPENDENT
        def run():
            try:
                if stop.is_set():
                    self.update(identity,'stopped')
                    return
                self.update(identity,'running')
                result=self.execute(request.project_id,request.operation,request.arguments,stop)
                self.update(identity,'stopped' if stop.is_set() else 'succeeded',result=result)
            except Exception as exc:
                from story_channel.provider_errors import safe_error_text
                self.update(identity,'failed',error=safe_error_text(str(exc)))
            finally:
                with self.lock:
                    self.events.pop(identity,None)
                    if not independent:
                        if self.waiting[request.project_id] and not self.closing:
                            self.pool.submit(self.waiting[request.project_id].popleft())
                        else:self.active_projects.discard(request.project_id)
        with self.lock:
            if independent:self.pool.submit(run)
            elif request.project_id in self.active_projects:self.waiting[request.project_id].append(run)
            else:
                self.active_projects.add(request.project_id)
                self.pool.submit(run)
        return self.get(identity)

    def stop(self,identity):
        with self.lock:
            event=self.events.get(identity)
            if event:event.set()
        job=self.get(identity)
        if job['status'] in ('queued','running'):
            self.update(identity,'stopping')
        return self.get(identity)

    def shutdown(self):
        with self.lock:
            self.closing=True
            for event in self.events.values():event.set()
            self.waiting.clear()
        with closing(self.connect()) as db,db:
            db.execute("UPDATE jobs SET status='stopped',updated_at=? WHERE status IN ('queued','stopping')",(now(),))
        self.pool.shutdown(wait=True,cancel_futures=False)
        with self.lock:self.events.clear()
