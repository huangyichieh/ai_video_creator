"""Rebuild Flutter sources and publish only complete, successful web releases."""
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from threading import Event, Lock, Thread
import time
from uuid import uuid4

logger = logging.getLogger(__name__)


class FrontendHost:
    def __init__(self, frontend: Path, cache: Path, *, run=None):
        self.frontend = frontend.resolve()
        self.cache = cache.resolve()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.stop_event = Event()
        self.lock = Lock()
        self.thread = None
        self.current = self.frontend / 'build/web'
        self.version = ''
        self.failed_signature = None
        self.building = False
        self.error = None
        self.run = run or self._run
        try:
            saved = json.loads((self.cache / 'current.json').read_text())
            release = (self.cache / saved['release']).resolve()
            if release.is_relative_to(self.cache) and (release / 'index.html').is_file():
                self.current, self.version = release, saved['version']
        except (OSError, ValueError, KeyError):
            pass

    def signature(self):
        entries = []
        excluded = {'build', '.dart_tool', '.git', '.idea', '.vscode', 'coverage', 'node_modules', '.plugin_symlinks'}
        for root, folders, files in os.walk(self.frontend):
            folders[:] = sorted(name for name in folders if name not in excluded)
            for name in sorted(files):
                if name.startswith('.flutter-plugins'):
                    continue
                file = Path(root) / name
                stamp = file.stat()
                entries.append((file.relative_to(self.frontend).as_posix(), stamp.st_mtime_ns, stamp.st_size))
        return sha256(json.dumps(entries).encode()).hexdigest()

    def _run(self, command, log):
        with log.open('a', encoding='utf-8') as stream:
            result = subprocess.run(command, cwd=self.frontend, stdout=stream, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0, timeout=600)
        if result.returncode:
            raise RuntimeError(f'Build command exited with code {result.returncode}. See {log.name}.')

    def rebuild(self):
        signature = self.signature()
        if signature in (self.version, self.failed_signature):
            return False
        with self.lock:
            self.building, self.error = True, None
        release = self.cache / ('release-' + uuid4().hex)
        log = self.cache / 'build.log'
        try:
            flutter = shutil.which(os.getenv('STORY_FLUTTER_COMMAND', 'flutter'))
            if not flutter:
                raise RuntimeError('Flutter was not found. Add Flutter to PATH or set STORY_FLUTTER_COMMAND.')
            dart = Path(flutter).resolve().with_name('dart.bat' if os.name == 'nt' else 'dart')
            log.write_text('Generating Dart serializers and building Flutter Web.\n', encoding='utf-8')
            self.run([str(dart), 'run', 'build_runner', 'build'], log)
            # Generated .g.dart files and a resolved lockfile belong to this build.
            # Changes made after this capture cause a subsequent rebuild.
            signature = self.signature()
            self.run([flutter, 'build', 'web', '--release', '--pwa-strategy=none', '--no-wasm-dry-run',
                f'--output={release}'], log)
            if not (release / 'index.html').is_file():
                raise RuntimeError('Flutter did not produce index.html. See build.log.')
            # Retire service workers installed by older offline-first builds.
            # Existing open pages stay put; a refresh loads the latest release.
            (release / 'flutter_service_worker.js').write_text(
                "self.addEventListener('install',()=>self.skipWaiting());\n"
                "self.addEventListener('activate',event=>event.waitUntil((async()=>{"
                "for(const key of ['flutter-app-cache','flutter-temp-cache','flutter-app-manifest'])"
                "{await caches.delete(key);}await self.clients.claim();await self.registration.unregister();"
                "})()));\n", encoding='utf-8')
            if self.stop_event.is_set() or self.signature() != signature:
                return False
            temporary = self.cache / 'current.tmp'
            temporary.write_text(json.dumps({'release': release.name, 'version': signature}), encoding='utf-8')
            temporary.replace(self.cache / 'current.json')
            with self.lock:
                self.current, self.version = release, signature
                self.failed_signature = None
            logger.info('Published updated Flutter web build')
            return True
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            with self.lock:
                self.failed_signature, self.error = signature, str(exc)
            logger.error('Frontend rebuild failed: %s', exc)
            return False
        finally:
            with self.lock:
                self.building = False

    def start(self):
        def watch():
            previous, changed_at = None, time.monotonic()
            while not self.stop_event.wait(.5):
                try:
                    signature = self.signature()
                    if signature != previous:
                        previous, changed_at = signature, time.monotonic()
                    elif time.monotonic() - changed_at >= 1:
                        self.rebuild()
                except OSError:
                    # Editors may temporarily replace a file while saving it.
                    previous = None
        self.thread = Thread(target=watch, name='frontend-builder', daemon=True)
        self.thread.start()

    def status(self):
        with self.lock:
            return {'watching': bool(self.thread and self.thread.is_alive()), 'building': self.building,
                'version': self.version or None, 'error': self.error,
                'available': (self.current / 'index.html').is_file()}

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join()
