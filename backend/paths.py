"""One configurable root for local runtime data, independent of launch cwd."""
from dataclasses import dataclass
import os
from pathlib import Path

PROJECT_ROOT=Path(__file__).resolve().parent.parent


def root_relative(value):
    path=Path(value).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT/path).resolve()


@dataclass(frozen=True)
class StoragePaths:
    root: Path
    @property
    def database(self): return self.root/'database'
    @property
    def uploads(self): return self.root/'uploads'
    @property
    def outputs(self): return self.root/'outputs'
    @property
    def temp(self): return self.root/'temp'
    def ensure(self):
        for path in (self.database,self.uploads,self.outputs,self.temp):path.mkdir(parents=True,exist_ok=True)
        return self


def storage_paths():
    return StoragePaths(root_relative(os.getenv('STORY_STORAGE_ROOT','storage')))


def data_path(value):
    path=root_relative(value)
    legacy=PROJECT_ROOT/'AI_Story_Channel'
    return storage_paths().outputs/'AI_Story_Channel' if path==legacy else path


def relocated_path(value):
    """Resolve historical absolute media references without rewriting history."""
    path=Path(value)
    legacy=PROJECT_ROOT/'AI_Story_Channel'
    if path.is_absolute() and path.is_relative_to(legacy):
        return storage_paths().outputs/'AI_Story_Channel'/path.relative_to(legacy)
    return path


def env_path():
    if os.getenv('STORY_ENV_FILE'):
        return root_relative(os.environ['STORY_ENV_FILE'])
    cwd=Path.cwd().resolve()
    # Root/backend launches share the same key file. Tests and unrelated cwd
    # locations keep their own explicit local .env, never discover parent keys.
    return PROJECT_ROOT/'.env' if cwd==PROJECT_ROOT or cwd.is_relative_to(PROJECT_ROOT/'backend') else cwd/'.env'
