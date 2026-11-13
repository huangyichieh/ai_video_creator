from pathlib import Path
import shutil
from threading import Event
import time

from backend.services.frontend_host import FrontendHost


def host(tmp_path, monkeypatch, runner):
    frontend = tmp_path / 'frontend'
    (frontend / 'lib').mkdir(parents=True)
    (frontend / 'lib/main.dart').write_text('version one')
    monkeypatch.setattr(shutil, 'which', lambda command: str(tmp_path / 'flutter'))
    return FrontendHost(frontend, tmp_path / 'releases', run=runner)


def write_build(command, log):
    output = next((arg.split('=', 1)[1] for arg in command if arg.startswith('--output=')), None)
    if output:
        Path(output).mkdir()
        (Path(output) / 'index.html').write_text('successful build')


def test_success_is_published_failure_keeps_last_good_build(tmp_path, monkeypatch):
    server = host(tmp_path, monkeypatch, write_build)
    assert server.rebuild()
    published = server.current
    assert not server.rebuild()  # No changes, no rebuild.
    (server.frontend / 'lib/main.dart').write_text('invalid code')
    def fail(command, log):
        raise RuntimeError('compiler error')
    server.run = fail
    assert not server.rebuild()
    assert server.current == published and server.status()['available']
    assert server.status()['error'] == 'compiler error'
    server.run = write_build
    assert not server.rebuild()  # Wait for a source change instead of retrying forever.
    (server.frontend / 'lib/main.dart').write_text('fixed code')
    assert server.rebuild() and server.current != published
    restored = FrontendHost(server.frontend, server.cache)
    assert restored.current == server.current and restored.version == server.version


def test_edits_during_build_are_not_lost_and_outputs_are_ignored(tmp_path, monkeypatch):
    server = host(tmp_path, monkeypatch, write_build)
    def edit_during_build(command, log):
        write_build(command, log)
        if '--release' in command:
            (server.frontend / 'lib/main.dart').write_text('edited while compiling')
    server.run = edit_during_build
    assert not server.rebuild()
    assert server.version != server.signature()
    server.run = write_build
    assert server.rebuild()
    version = server.signature()
    (server.frontend / 'build').mkdir()
    (server.frontend / 'build/output.js').write_text('generated')
    assert server.signature() == version
    assert not server.rebuild()


def test_background_watcher_publishes_changes(tmp_path, monkeypatch):
    built = Event()
    def runner(command, log):
        write_build(command, log)
        if '--release' in command:
            built.set()
    server = host(tmp_path, monkeypatch, runner)
    server.start()
    try:
        assert built.wait(5)
        deadline = time.monotonic() + 2
        while not server.version and time.monotonic() < deadline:
            time.sleep(.01)
        previous = server.version
        built.clear()
        (server.frontend / 'lib/main.dart').write_text('new UI')
        assert built.wait(5)
        deadline = time.monotonic() + 2
        while server.version == previous and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.version != previous
    finally:
        server.stop()


def test_backend_serves_published_release_and_build_status(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from story_channel.config import Settings
    monkeypatch.setenv('STORY_STORAGE_ROOT',str(tmp_path/'storage'))
    monkeypatch.setenv('STORY_FRONTEND_WATCH','0')
    settings=Settings(data_root=tmp_path/'channel',llm_provider='demo',asset_provider='demo')
    with TestClient(create_app(settings,database=tmp_path/'jobs.sqlite3')) as client:
        publisher=host(tmp_path,monkeypatch,write_build)
        assert publisher.rebuild()
        client.app.state.frontend=publisher
        response=client.get('/')
        assert response.status_code==200 and response.text=='successful build'
        assert response.headers['cache-control']=='no-store'
        assert client.get('/world').text=='successful build'
        assert client.get('/api/frontend/status').json()['version']==publisher.version
        assert client.get('/api/does-not-exist').status_code==404
