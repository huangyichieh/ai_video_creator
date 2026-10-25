"""Convenience entry point matching the specification's outline-first invocation."""
import argparse
from pathlib import Path

from story_channel.config import Settings
from story_channel.logging_config import configure_logging
from story_channel.pipeline import Pipeline
from story_channel.projects import ProjectManager


def main():
    parser = argparse.ArgumentParser(description="Create a story project and run until human review")
    parser.add_argument("outline", type=Path)
    parser.add_argument("--title")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--demo", action="store_true", default=None)
    args = parser.parse_args()
    settings = Settings.load(args.config)
    if args.data_root:
        settings.data_root = args.data_root
    configure_logging(settings.log_level, settings.data_root / "logs" / "application.log")
    manager = ProjectManager(settings)
    project = manager.create(args.title or args.outline.stem, args.outline)
    print(f"Created project: {project.project_id}", flush=True)
    manifest = Pipeline(manager, project.project_id, demo=args.demo).run()
    print(manifest.model_dump_json(by_alias=True, indent=2))


if __name__ == "__main__":
    main()
