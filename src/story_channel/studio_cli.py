"""World and episode commands backed by the same services as the UI."""
from pathlib import Path

from .files import input_hash
from .pipeline import Pipeline
from .studio import WorldStudio, Episodes


def duration(value):
    """Accept HH:MM:SS, retaining seconds for existing command scripts."""
    try:
        parts = [int(part) for part in value.split(":")]
        if len(parts) == 1:
            total = parts[0]
        elif len(parts) == 3 and parts[0] >= 0 and all(0 <= part < 60 for part in parts[1:]):
            total = parts[0] * 3600 + parts[1] * 60 + parts[2]
        else:
            raise ValueError
        if total <= 0:
            raise ValueError
        return total
    except ValueError:
        from argparse import ArgumentTypeError
        raise ArgumentTypeError("Use a positive duration in HH:MM:SS or seconds.") from None


def register(commands):
    world = commands.add_parser("world", help="Review/approve World settings, additions, and shared characters")
    world.add_argument("project_id")
    world.add_argument("action", choices=("show", "save", "review", "approve", "characters", "add-setting", "edit-setting",
                                         "remove-setting", "review-setting", "approve-setting"))
    world.add_argument("--file", type=Path, help="UTF-8 World/settings text, or an edited review proposal")
    world.add_argument("--text", help="Setting text instead of --file")
    world.add_argument("--setting", help="Additional setting ID")
    world.add_argument("--prompt", default="", help="Direction for AI review or new characters")
    world.add_argument("--decision", choices=("apply", "revert"), help="Explicitly apply the supplied proposal or retain the original")
    world.add_argument("--demo", action="store_true", default=None)
    episode = commands.add_parser("episode", help="Create, list or explicitly refresh episodes within a World")
    episode.add_argument("world_id")
    episode.add_argument("action", choices=("create", "list", "refresh-world", "refresh-assets", "import"))
    episode.add_argument("--title")
    episode.add_argument("--file", type=Path, help="UTF-8 episode outline")
    episode.add_argument("--duration", type=duration, help="Target length HH:MM:SS")
    episode.add_argument("--episode", help="Episode ID for refresh operations")


def execute(manager, args):
    if args.command == "episode":
        service = Episodes(manager, args.world_id)
        if args.action == "list":
            return [item.model_dump(mode="json", by_alias=True) for item in service.list()]
        if args.action == "import":
            return service.adopt_existing()
        if args.action == "create":
            if not args.title or not args.file or args.duration is None:
                raise ValueError("Provide --title, --file and --duration HH:MM:SS.")
            return service.create(args.title, args.file.read_text(encoding="utf-8-sig"), args.duration)
        if not args.episode:
            raise ValueError("Provide --episode for the episode to refresh.")
        return (service.refresh_world if args.action == "refresh-world" else service.refresh_assets)(args.episode)
    pipeline = Pipeline(manager, args.project_id, demo=args.demo)
    service = WorldStudio(manager, args.project_id, pipeline)
    text = args.file.read_text(encoding="utf-8-sig") if args.file else args.text
    if args.file and args.file.suffix.lower() == ".json":
        import json
        from .worldview import WorldSettings
        data = json.loads(text)
        text = WorldSettings.model_validate(data.get("settings", data)).text()
    if args.action in ("edit-setting", "remove-setting", "review-setting", "approve-setting") and not args.setting:
        raise ValueError("Provide --setting with the pending additional setting ID.")
    if args.action in ("save", "add-setting", "edit-setting") and not text:
        raise ValueError("Provide --file or --text with settings text.")
    if args.action == "save":
        service.edit_base(text)
    elif args.action == "review":
        return service.review(instructions=args.prompt)
    elif args.action == "approve":
        if args.decision == "apply" and not text:
            raise ValueError("Provide --file or --text with the World proposal you reviewed.")
        if args.decision:
            service.resolve_base(args.decision, text or service.load().base_text, expected=input_hash(service.load().base_text))
        return service.approve_base(expected=input_hash(service.load().base_text))
    elif args.action == "characters":
        return service.generate_characters(args.prompt)
    elif args.action == "add-setting":
        return service.add_setting(text)
    elif args.action in ("edit-setting", "remove-setting"):
        service.edit_setting(args.setting, text, remove=args.action == "remove-setting")
    elif args.action == "review-setting":
        return service.review(setting_id=args.setting, instructions=args.prompt)
    elif args.action == "approve-setting":
        if not args.decision:
            raise ValueError("Choose --decision apply or revert after reviewing the pending setting.")
        if args.decision == "apply" and not text:
            raise ValueError("Provide --file or --text with the proposal you reviewed.")
        return service.approve_setting(args.setting, text=text or "", decision=args.decision)
    return service.load()
