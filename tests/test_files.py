import os

import pytest

from story_channel.files import atomic_write


@pytest.mark.skipif(os.name != "nt", reason="Windows file-sharing behavior")
@pytest.mark.parametrize("permanent", [False, True])
def test_atomic_write_handles_windows_reader_lock_without_losing_original(tmp_path, monkeypatch, permanent):
    target = tmp_path / "manifest.json"
    target.write_text("original", encoding="utf-8")
    replace = os.replace
    attempts = []

    def locked(source, destination):
        attempts.append(source)
        if permanent or len(attempts) == 1:
            error = PermissionError("File is open for reading")
            error.winerror = 32
            raise error
        replace(source, destination)

    monkeypatch.setattr("story_channel.files.os.replace", locked)
    if permanent:
        with pytest.raises(PermissionError):
            atomic_write(target, "replacement")
        assert target.read_text(encoding="utf-8") == "original"
        assert len(attempts) == 8
    else:
        atomic_write(target, "replacement")
        assert target.read_text(encoding="utf-8") == "replacement"
        assert len(attempts) == 2
    assert list(tmp_path.iterdir()) == [target]
def test_read_text_retries_only_windows_sharing_violations(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    from story_channel import files
    path = tmp_path / "manifest.json"
    path.write_text('{"status":"draft"}', encoding="utf-8")
    original = Path.read_text
    attempts = []
    def temporarily_locked(self, **kwargs):
        attempts.append(self)
        if len(attempts) < 3:
            error = PermissionError("file is being replaced")
            error.winerror = 32
            raise error
        return original(self, **kwargs)
    monkeypatch.setattr(Path, "read_text", temporarily_locked)
    monkeypatch.setattr(files, "os", SimpleNamespace(name="nt"))
    assert files.read_text(path) == '{"status":"draft"}'
    assert len(attempts) == 3


@pytest.mark.parametrize("code", [None, 5, 32, 33])
def test_manifest_read_recovers_from_access_denied_without_winerror(tmp_path, monkeypatch, code):
    import errno
    from pathlib import Path
    from types import SimpleNamespace
    from story_channel import files
    from story_channel.config import Settings
    from story_channel.projects import ProjectManager
    source = tmp_path / "outline.md"
    source.write_text("A story", encoding="utf-8")
    manager = ProjectManager(Settings(data_root=tmp_path / "data"))
    project = manager.create("Test", source)
    target = manager.project_path(project.project_id) / "manifest.json"
    original = Path.read_text
    attempts = []
    def denied(self, **kwargs):
        if self == target:
            attempts.append(self)
            if len(attempts) < 5:
                exc = PermissionError(errno.EACCES, "Permission denied", str(self))
                if code is not None:
                    exc.winerror = code
                raise exc
        return original(self, **kwargs)
    monkeypatch.setattr(Path, "read_text", denied)
    monkeypatch.setattr(files, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(files.time, "sleep", lambda _: None)
    assert manager.load(project.project_id).project_id == project.project_id
    assert len(attempts) == 5


def test_permission_denial_stays_bounded_and_missing_files_do_not_retry(tmp_path, monkeypatch):
    import errno
    from pathlib import Path
    from types import SimpleNamespace
    from story_channel import files
    attempts, waits = [], []
    def denied(self, **kwargs):
        attempts.append(self)
        raise PermissionError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(Path, "read_text", denied)
    monkeypatch.setattr(files, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(files.time, "sleep", waits.append)
    with pytest.raises(PermissionError):
        files.read_text(tmp_path / "manifest.json")
    assert len(attempts) == 8 and len(waits) == 7
    assert sum(waits) < 1
    def missing(self, **kwargs):
        raise FileNotFoundError(self)
    monkeypatch.setattr(Path, "read_text", missing)
    waits.clear()
    with pytest.raises(FileNotFoundError):
        files.read_text(tmp_path / "missing.json")
    assert not waits


