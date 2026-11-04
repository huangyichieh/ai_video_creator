import argparse
import sys
from pathlib import Path

from .config import Settings
from .logging_config import configure_logging
from .projects import ProjectManager
from .modules import MODULES, STEPS
from .pipeline import Pipeline
from .assets import AssetPipeline
from .files import input_hash


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Story and asset production workspace")
    parser.add_argument("--config", type=Path, help="JSON settings file")
    parser.add_argument("--data-root", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    from .studio_cli import register, duration
    register(commands)
    create = commands.add_parser("create", help="Create a World from a UTF-8 settings file")
    create.add_argument("outline", type=Path)
    create.add_argument("--title", help="Optional World title; defaults to the first settings line")
    create.add_argument("--language")
    create.add_argument("--duration", type=duration, help="Legacy standalone duration; use episode create for current production")
    create.add_argument("--legacy", action="store_true", help="Create an old standalone story for existing automation")
    show = commands.add_parser("show", help="Validate and print a manifest")
    show.add_argument("project_id")
    outline = commands.add_parser("outline", help="Read a saved outline")
    outline.add_argument("project_id")
    commands.add_parser("list", help="List local projects")
    for name in ("run", "resume", "generate", "regenerate", "retry", "approve", "edit", "library-save", "library-use"):
        command = commands.add_parser(name)
        command.add_argument("project_id")
        command.add_argument("--demo", action="store_true", default=None, help="Offline demonstration data; no API calls")
        command.add_argument("--model", help="Override the configured LLM model")
        if name in ("generate", "regenerate", "retry"):
            command.add_argument("step", choices=STEPS)
        if name == "approve":
            command.add_argument("gate", choices=("world", "outline", "story", "script", "storyboard", "prompts"))
            command.add_argument("--note", default="")
        if name == "edit":
            command.add_argument("step", choices=("outline", *STEPS))
            command.add_argument("file", type=Path, help="UTF-8 outline or canonical JSON artifact")
        if name in ("library-save", "library-use"):
            command.add_argument("step", choices=("world", "characters"))
            command.add_argument("name", help="Shared library entry name (ASCII ID)")
    schemas = commands.add_parser("schemas", help="Export JSON Schemas for all creative artifacts")
    schemas.add_argument("directory", type=Path)
    schemas.add_argument("--production", action="store_true", help="Also export foundation, identity, performance and render contracts")
    identity = commands.add_parser("identity", help="Build, edit and approve the reusable identity library")
    identity.add_argument("project_id")
    identity.add_argument("action", choices=("prepare", "show", "save", "approve"))
    identity.add_argument("--file", type=Path)
    dialogue = commands.add_parser("dialogue-review", help="Independently review the current script dialogue")
    dialogue.add_argument("project_id")
    dialogue.add_argument("--force", action="store_true")
    finishing = commands.add_parser("finishing", help="Configure, inspect and generate lip-sync, SFX and VFX layers")
    finishing.add_argument("project_id")
    finishing.add_argument("action", choices=("show", "save", "generate", "regenerate"))
    finishing.add_argument("--file", type=Path, help="FinishingPlan JSON file")
    finishing.add_argument("--item", action="append", default=[], help="Finishing item key; repeat to select multiple")
    assets = commands.add_parser("assets", help="Generate, inspect, retry, and approve asset production")
    assets.add_argument("project_id")
    assets.add_argument("action", choices=("show", "run", "generate", "regenerate", "approve", "voice"))
    assets.add_argument("target", nargs="?", help="Asset stage, approval gate, or logical voice ID")
    assets.add_argument("--subject", help="Generate/retry only one asset subject")
    assets.add_argument("--note", default="")
    assets.add_argument("--voice", help="Built-in provider voice for the voice action")
    assets.add_argument("--generic-prompt", help="Legacy standalone projects only; World/Episode projects use individual asset prompts")
    reference = assets.add_mutually_exclusive_group()
    reference.add_argument("--style-reference", type=Path, action="append", help="Legacy standalone projects only: save shared style images (up to 3)")
    reference.add_argument("--remove-style-reference", action="store_true", help="Legacy standalone projects only: clear shared style images")
    assets.add_argument("--demo", action="store_true", default=None)
    assets.add_argument("--production", action="store_true", help="Use configured production image/voice providers")
    assets.add_argument("--openai", dest="production", action="store_true", help=argparse.SUPPRESS)
    render = commands.add_parser("render", help="Render approved assets into video and review the result")
    render.add_argument("project_id")
    render.add_argument("action", choices=("show", "configure", "run", "regenerate", "approve-scenes", "assemble", "approve"))
    render.add_argument("--scene", help="Render or regenerate only this scene; cache all other scenes")
    render.add_argument("--options", type=Path, help="RenderOptions JSON file")
    render.add_argument("--music-plan", type=Path, help="MusicPlan JSON file with audio sources and cue times")
    render.add_argument("--note", default="")
    render.add_argument("--shot", help="Retry/regenerate only this animated shot and any dependent chained shots")
    animation = commands.add_parser("animation", help="Prepare and approve animation direction, references, voice previews and continuity")
    animation.add_argument("project_id")
    animation.add_argument("action", choices=("prepare", "show", "save", "preview", "approve-voice", "approve-plan", "approve-performance", "approve-continuity"))
    animation.add_argument("--file", type=Path, help="Edited AnimationPlan JSON file")
    animation.add_argument("--character", help="Character ID for voice preview/approval")
    animation.add_argument("--text", help="Voice preview sample line")
    animation.add_argument("--note", default="")
    args = parser.parse_args(argv)
    try:
        settings = Settings.load(args.config)
        if args.data_root is not None:
            settings.data_root = args.data_root
        configure_logging(settings.log_level, settings.data_root / "logs" / "application.log")
        manager = ProjectManager(settings)
        if args.command == "create":
            if args.legacy:
                if not args.title:
                    raise ValueError("Provide --title for a legacy standalone story.")
                manifest = manager.create(args.title, args.outline, language=args.language, target_duration_sec=args.duration)
            else:
                if args.duration is not None:
                    raise ValueError("Set target length on episode create; World creation only needs settings.")
                from .studio import WorldStudio
                source = args.outline.read_text(encoding="utf-8-sig")
                if args.outline.suffix.lower() == ".json":
                    import json
                    from .worldview import WorldSettings
                    data = json.loads(source)
                    source = WorldSettings.model_validate(data.get("settings", data))
                manifest = WorldStudio.create(manager, source)
                manifest.title = args.title or manifest.title
                manifest.language = args.language or manifest.language
                manager.save(manifest)
            print(manifest.model_dump_json(by_alias=True, indent=2))
        elif args.command in ("world", "episode"):
            import json
            from .studio_cli import execute
            result = execute(manager, args)
            print(json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, list) else result.model_dump_json(by_alias=True, indent=2))
        elif args.command == "show":
            print(manager.load(args.project_id).model_dump_json(by_alias=True, indent=2))
        elif args.command == "outline":
            print(manager.read_outline(args.project_id))
        elif args.command == "list":
            import json
            print(json.dumps([m.model_dump(mode="json", by_alias=True) for m in manager.list_projects()], ensure_ascii=False, indent=2))
        elif args.command == "schemas":
            import json
            from .files import atomic_write
            contracts = {step: module.model for step, module in MODULES.items()}
            if args.production:
                from .animation_models import AnimationPlan, AnimationState, VoiceProfile, Performance
                from .identity import IdentityLibrary
                from .dialogue_review import SavedDialogueReview
                from .asset_models import Production
                from .render_models import RenderState
                from .models import Manifest, GeneratedAsset, UsageRecord
                from .finishing_models import FinishingPlan, FinishingState
                from .studio import WorldState, TextReview
                from .scene_production import SceneDirection
                contracts.update(manifest=Manifest, generated_asset=GeneratedAsset, usage=UsageRecord,
                    identity=IdentityLibrary, voice_profile=VoiceProfile, performance=Performance,
                    animation_plan=AnimationPlan, animation=AnimationState, production=Production,
                    render=RenderState, dialogue_review=SavedDialogueReview, finishing_plan=FinishingPlan, finishing=FinishingState,
                    world_setting=WorldState, text_review=TextReview, scene_direction=SceneDirection)
            for name, model in contracts.items():
                atomic_write(args.directory / f"{name}.schema.json", json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2))
            print(f"Exported {len(contracts)} schemas to {args.directory}")
        elif args.command == "finishing":
            from .rendering import RenderPipeline
            from .finishing_models import FinishingPlan
            engine = RenderPipeline(manager, args.project_id)
            service = engine.finishing
            if args.action == "show":
                result = service.load()
            elif args.action == "save":
                if args.file is None:
                    raise ValueError("Provide --file with edited finishing settings")
                result = service.configure(FinishingPlan.model_validate_json(args.file.read_text(encoding="utf-8-sig")),
                                           expected=service.load().plan.model_dump_json(by_alias=True))
            else:
                result = service.generate_selected(args.item, force=args.action == "regenerate")
            print(result.model_dump_json(by_alias=True, indent=2))
        elif args.command == "identity":
            from .identity import IdentityManager, IdentityLibrary
            identity = IdentityManager(manager, args.project_id)
            if args.action == "prepare":
                result = identity.prepare()
            elif args.action == "save":
                old = identity.load()
                if not args.file or old is None:
                    raise ValueError("Build the identity library and provide --file with edited JSON")
                result = identity.save(IdentityLibrary.model_validate_json(args.file.read_text(encoding="utf-8-sig")),
                                       expected=old.model_dump_json(by_alias=True))
            elif args.action == "approve":
                result = identity.approve()
            else:
                result = identity.load()
                if result is None:
                    raise ValueError("Build the identity library first")
            print(result.model_dump_json(by_alias=True, indent=2))
        elif args.command == "dialogue-review":
            from .dialogue_review import review_dialogue
            result = review_dialogue(Pipeline(manager, args.project_id), force=args.force)
            print(result.model_dump_json(by_alias=True, indent=2))
        elif args.command == "animation":
            from .animation import AnimationDirector
            from .animation_rendering import approve_continuity
            director = AnimationDirector(manager, args.project_id)
            if args.action == "prepare":
                result = director.prepare()
            elif args.action == "save":
                from .animation_models import AnimationPlan
                if not args.file:
                    raise ValueError("Provide --file with edited animation plan JSON")
                result = director.save_plan(AnimationPlan.model_validate_json(args.file.read_text(encoding="utf-8-sig")))
            elif args.action == "preview":
                if not args.character or not args.text:
                    raise ValueError("Provide --character and --text for the voice preview")
                result = director.preview_voice(args.character, args.text)
            elif args.action == "approve-voice":
                if not args.character:
                    raise ValueError("Provide --character for voice approval")
                result = director.approve_voice(args.character)
            elif args.action == "approve-plan":
                result = director.approve_plan()
            elif args.action == "approve-performance":
                result = director.approve_performance()
            elif args.action == "approve-continuity":
                result = approve_continuity(director, args.note)
            else:
                result = director.load()
                if not result:
                    raise ValueError("Build an animation plan first")
            print(result.model_dump_json(by_alias=True, indent=2))
        elif args.command == "render":
            import json
            from .rendering import RenderPipeline
            engine = RenderPipeline(manager, args.project_id)
            if args.options or args.music_plan:
                engine.configure(
                    options=json.loads(args.options.read_text(encoding="utf-8-sig")) if args.options else None,
                    music_plan=json.loads(args.music_plan.read_text(encoding="utf-8-sig")) if args.music_plan else None)
            if args.shot:
                result = engine.retry_animation_shot(args.shot)
            elif args.action in ("show", "configure"):
                result = engine.inspect()
            elif args.action == "approve":
                result = engine.approve(args.note)
            elif args.action == "approve-scenes":
                result = engine.approve_scenes([args.scene] if args.scene else list(engine.load().scenes))
            elif args.action == "assemble":
                result = engine.assemble_final()
            else:
                result = engine.run(subject=args.scene, force=args.action == "regenerate",
                                    assemble=not bool(manager.load(args.project_id).world_project_id))
            print(result.model_dump_json(by_alias=True, indent=2))
        elif args.command == "assets":
            if args.demo and args.production:
                raise ValueError("Choose either --demo or --production")
            engine = AssetPipeline(manager, args.project_id, demo=False if args.production else args.demo)
            if args.style_reference:
                engine.set_style_references([path.read_bytes() for path in args.style_reference])
            elif args.remove_style_reference:
                engine.set_style_reference(None)
            if args.generic_prompt is not None:
                engine.set_generic_prompt(args.generic_prompt)
            if args.action == "show":
                result = engine.inspect()
            elif args.action == "run":
                result = engine.run()
            elif args.action == "approve":
                result = engine.approve(args.target, args.note)
            elif args.action == "voice":
                result = engine.set_voice(args.target, args.voice)
            else:
                result = engine.generate(args.target, subject=args.subject, force=args.action == "regenerate")
            print(result.model_dump_json(by_alias=True, indent=2))
        else:
            if args.model:
                settings.llm_model = args.model
            llm_config = None
            if args.model:
                from .models import ProviderConfig
                llm_config = ProviderConfig(provider=settings.llm_provider, model=args.model,
                                            options={"max_output_tokens": settings.llm_max_output_tokens})
            pipeline = Pipeline(manager, args.project_id, demo=args.demo, llm_config=llm_config)
            if args.command in ("run", "resume"):
                manifest = pipeline.run()
            elif args.command == "approve":
                if args.gate == "world":
                    from .studio import WorldStudio
                    world = WorldStudio(manager, args.project_id, pipeline)
                    world.approve_base(expected=input_hash(world.load().base_text))
                    manifest = manager.load(args.project_id)
                elif args.gate in ("outline", "script", "prompts"):
                    from .studio import approve_content, content_digest
                    manifest = approve_content(pipeline, args.gate, expected=content_digest(pipeline, args.gate))
                else:
                    manifest = pipeline.approve(args.gate, args.note)
            elif args.command == "edit":
                pipeline.edit(args.step, args.file.read_text(encoding="utf-8-sig"))
                manifest = pipeline.inspect()
            elif args.command in ("library-save", "library-use"):
                if args.command == "library-save":
                    pipeline.save_to_library(args.step, args.name)
                else:
                    pipeline.use_library(args.step, args.name)
                manifest = pipeline.inspect()
            else:
                manifest = pipeline.generate(args.step, force=args.command in ("regenerate", "retry"))
            print(manifest.model_dump_json(by_alias=True, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
