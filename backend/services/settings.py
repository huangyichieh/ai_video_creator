"""Local credentials never enter project snapshots or job arguments."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from dotenv import dotenv_values,set_key
from backend.paths import env_path,storage_paths
from story_channel.files import atomic_write

API_KEYS=('OPENAI_API_KEY','OPENAI_ADMIN_KEY','RUNWAYML_API_SECRET','ELEVENLABS_API_KEY')
_lock=Lock()


def saved_keys():
    saved=dotenv_values(env_path()) if env_path().is_file() else {}
    return [{'name':key,'provided':bool(os.getenv(key,saved.get(key) or '')),
        'masked':(value[:4]+'…'+value[-4:] if len(value)>8 else '••••••••') if (value:=os.getenv(key,saved.get(key) or '')) else ''} for key in API_KEYS]


def save_keys(values):
    if not set(values)<=set(API_KEYS):raise ValueError('Unknown API key setting')
    if any(not isinstance(value,str) or any(c in value for c in '\r\n\x00') for value in values.values()):raise ValueError('Keys must be single-line text')
    with _lock,TemporaryDirectory(dir=storage_paths().ensure().temp) as folder:
        path=env_path()
        draft=Path(folder)/'.env'
        draft.write_text(path.read_text(encoding='utf-8-sig') if path.is_file() else '',encoding='utf-8')
        for key,value in values.items():set_key(str(draft),key,value.strip(),quote_mode='always')
        atomic_write(path,draft.read_text(encoding='utf-8'))
        os.environ.update({key:value.strip() for key,value in values.items()})
    return saved_keys()


def quotas():
    from story_channel.provider_quota import fetch_quota
    from .workspace import plain
    result={}
    for key in ('OPENAI_ADMIN_KEY','RUNWAYML_API_SECRET','ELEVENLABS_API_KEY'):
        if not os.getenv(key,'').strip():continue
        try:result[key]={'value':plain(fetch_quota(key,os.environ[key]))}
        except Exception as exc:
            from story_channel.provider_errors import safe_error_text
            result[key]={'error':safe_error_text(str(exc))}
    return result
