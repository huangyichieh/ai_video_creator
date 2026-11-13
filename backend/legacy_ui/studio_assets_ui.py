"""World-owned reusable assets, independent of an episode's production stages."""
from .error_report_ui import render_error, error_project, report_widget_key
from .generation_ui import generation_button
import streamlit as st

from .assets import AssetPipeline
from .studio import WorldStudio
from .library_media import LibraryMedia
from .ui_tasks import busy, launch, launch_assets, generation_busy
from .ui_panels import polling_fragment


@polling_fragment
def generate_all_visuals_control(engine):
    from .character_visual_batch import generate_all_visuals_text
    from .ui_tasks import find_task, collect, take_task_result
    task = find_task(lambda item: item.get("generation_button_key") == "studio-character-all" and
                     item.get("generation_button_project") == engine.project_id)
    message_key = "bulk-visual-message-" + engine.project_id
    if task and task["future"].done():
        collect(task)
    result = take_task_result(engine.project_id, ["studio-character-all"])
    if result:
        st.session_state[message_key] = result
    def generate():
        st.session_state.pop(message_key, None)
        prompts = {character.character_id: st.session_state.get(
            f"visual-generation-{engine.project_id}-character_references-{character.character_id}-prompt", "")
            for character in engine.creative.artifact("characters").characters}
        launch_assets(engine, lambda: generate_all_visuals_text(engine, prompts),
                      "Generating all character visuals (Text)", rerun_on_start=False, service="openai")
    generation_button("Generate all Visual(Text)", key="studio-character-all",
                      disabled=generation_busy() or not engine.can_generate("character_references", engine.load()),
                      on_click=generate)
    message = st.session_state.get(message_key)
    if message and message[0][0] == "error":
        render_error(message[0][1], project_path=engine.path, task_key=message[1].get("task"), key=message_key + "-error")


@polling_fragment
def reference_approval(engine, gate, state):
    state = engine.load()
    manifest = engine.manager.load(engine.project_id)
    approved = gate in manifest.asset_approvals
    stage = "character_references" if gate == "characters" else "backgrounds"
    st.markdown(":green[Approved]" if approved else ":orange[Pending]" if gate == "characters" else ":red[Not approved]")
    if gate == "characters":
        from .asset_view_ui import character_summary
        subjects = [c.character_id for c in engine.creative.artifact("characters").characters]
        summaries = [character_summary(engine, cid, state) for cid in subjects]
        st.caption(f"{sum(item[0] for item in summaries)}/{len(subjects)} characters approved")
    label = "Approve all characters" if gate == "characters" else "Approve " + gate
    ready = gate != "characters" or bool(subjects) and all(all(item[1].values()) for item in summaries)
    if st.button("Approved" if approved else label, key="studio-approve-" + gate, disabled=busy() or approved or not ready):
        launch(lambda: engine.approve(gate), "Approving " + gate)


def select_character_tabs(engine, tab):
    for character in engine.creative.artifact("characters").characters:
        st.session_state[f"page-tab-character-{engine.project_id}-{character.character_id}"] = tab
        st.session_state[f"character-library-card-{engine.project_id}-{character.character_id}"] = True
    if tab == "Costumes":
        from .identity import IdentityManager
        library = IdentityManager(engine.manager, engine.project_id).load()
        if library:
            for costume in library.costumes.values():
                st.session_state[f"character-costume-card-{engine.project_id}-{costume.costume_id}"] = True


def character_prompt(studio, engine, *, exists):
    if exists:
        add_character_prompt(studio)
        return
    with st.container(border=True, key="initial-character-generation"):
        st.subheader("AI Generate characters")
        prompt_key = "studio-draft-new-character-" + engine.project_id
        st.session_state.setdefault(prompt_key, "")
        with st.container(horizontal=True, vertical_alignment="center", gap=None):
            clicked = generation_button("Generate initial characters", key="studio-add-character", disabled=busy())
        prompt = st.text_area("New character prompt", key=prompt_key, height=500,
                              placeholder="Describe a new character consistent with the approved World.", disabled=busy())
        if clicked:
            start_character_generation(studio, prompt, "initial")
    add_character_prompt(studio)


def start_character_generation(studio, prompt, kind, success=None):
    callback = (lambda: studio.generate_characters(prompt, single=True)) if kind == "single" else (lambda: studio.generate_characters(prompt))
    launch(callback, "Generating characters from approved World settings", success=success, rerun_on_start=False)
    st.session_state["workspace-task"].update(character_generation_project=studio.project_id, character_generation_kind=kind)
    st.rerun()


def add_character_prompt(studio):
    prompt_key = "studio-draft-add-character-prompt-" + studio.project_id
    with st.expander("Add character", expanded=False, key="add-character-box"):
        prompt = st.text_area("Character prompt", key=prompt_key, height=500,
            placeholder="Describe the character you want to add to this World.", disabled=busy())
        with st.container(horizontal=True, vertical_alignment="center", gap=None):
            clicked = generation_button("Generate with AI", key="studio-generate-added-character", disabled=busy() or not prompt.strip())
        if clicked:
            def done(_):
                st.session_state[prompt_key] = ""
            start_character_generation(studio, prompt, "single", success=done)


def assets_page(manager, project_id, pipeline=None):
    if not busy():
        from .storyboard_assets import sync
        try:
            sync(manager,project_id)
        except (OSError,ValueError,RuntimeError) as exc:
            st.warning("Storyboard asset drafts could not be synchronized: "+str(exc))
    studio = WorldStudio(manager, project_id, pipeline)
    default = AssetPipeline(manager, project_id)
    mode_key = "asset-mode-" + project_id
    if st.session_state.get(mode_key) == "OpenAI":
        st.session_state[mode_key] = "Production"
    st.session_state.setdefault(mode_key, "Demo (offline)" if default.demo else "Production")
    tab_key = f"page-tab-assets-{project_id}"
    if st.session_state.get(tab_key) in ("Characters", "Character Reference Packs"):
        st.session_state[tab_key] = "Character References"
    if st.session_state.get(tab_key) == "Others":
        st.session_state[tab_key] = "Props"
    st.header("Assets")
    st.caption("Shared across episodes. Generate and review reusable references before scene production.")
    st.markdown("""<style>
    [class*="st-key-page-tab-assets-"] {
        background-color: rgba(255, 255, 255, 0.045);
        border: none;
        box-shadow: none;
        border-radius: 0.75rem;
        padding: 1rem 1.1rem;
    }
    .st-key-initial-character-generation,
    .st-key-add-character-box,
    [class*="st-key-character-library-card-"],
    [class*="st-key-background-card-"] {
        background-color: rgba(255, 255, 255, 0.045);
        border-radius: 0.75rem;
        padding: 1rem 1.1rem;
    }
    </style>""", unsafe_allow_html=True)
    mode = st.selectbox("Asset generation mode", ["Production", "Demo (offline)"], key=mode_key, disabled=busy())
    engine = AssetPipeline(manager, project_id, demo=mode.startswith("Demo"))
    if engine.demo:
        st.info("Offline assets use placeholder images and silent sample audio.")
    else:
        from .library_media import PROP_IMAGE_MODEL
        from .visual_generation import DESIGN_MODEL as CHARACTER_IMAGE_MODEL
        st.caption(f"Characters/costumes: OpenAI / {CHARACTER_IMAGE_MODEL} · Backgrounds/props: OpenAI / {PROP_IMAGE_MODEL} · Voices: {manager.settings.tts_provider} / {manager.settings.tts_model} · Talking video: Runway / klingO3_pro")
    exists = (engine.path / "characters.json").exists()
    from .style_rules_ui import style_rules_box, style_check_controls
    if not exists:
        animation_asset_hint(engine,engine.load())
        style_rules_box(engine)
        characters, backgrounds, sfx, bgm, props, style_check = st.tabs(["Character References", "Background", "SFX", "BGM", "Props", "Style check"], key=f"page-tab-assets-{project_id}", on_change="rerun")
        with bgm:
            if bgm.open:
                from .bgm_library_ui import render
                render(manager,project_id)
        with style_check:
            if style_check.open:
                style_check_controls(engine, "style-check")
        with characters:
            if characters.open:
                st.markdown("**Total characters: 0**")
                character_prompt(studio, engine, exists=False)
                st.info("Generate an initial cast or use Add character to create your first character from a prompt.")
        with backgrounds:
            if backgrounds.open:
                from .background_assets_ui import background_cards
                background_cards(engine, engine.load())
        with sfx:
            if sfx.open:
                from .audio_asset_cards import library as audio_cards
                audio_cards(manager,project_id,"SFX")
        for tab in (props,):
            with tab:
                st.info("Generate the initial characters to initialize the reusable asset library.")
        return
    try:
        state = engine.load() if busy() else engine.inspect()
    except (ValueError, OSError) as exc:
        st.warning(str(exc))
        return
    asset_controls(engine, project_id)
    st.caption("Assets prerequisite: at least 1 character and 1 background. There is no complete-assets stage. SFX, BGM, props and style checks are optional.")
    style_rules_box(engine)
    characters, backgrounds, sfx, bgm, props, style_check = st.tabs(["Character References", "Background", "SFX", "BGM", "Props", "Style check"], key=f"page-tab-assets-{project_id}", on_change="rerun")
    with bgm:
        if bgm.open:
            from .bgm_library_ui import render
            render(manager,project_id)
    with style_check:
        if style_check.open:
            style_check_controls(engine, "style-check")
    media = LibraryMedia(manager, project_id)
    engine.creative = studio.pipeline
    media.assets = engine
    with characters:
        if characters.open:
            st.markdown(f"**Total characters: {len(engine.creative.artifact('characters').characters)}**")
            character_prompt(studio, engine, exists=True)
    with characters:
        if characters.open:
            generate_all_visuals_control(engine)
            reference_approval(engine, "characters", state)
            from .character_assets_ui import character_cards
            from .character_library_ui import character_voice, character_costumes
            character_cards(engine, state,
                voice_editor=lambda character: character_voice(engine, media, character),
                costumes_editor=lambda character: character_costumes(engine, state, media, character))
            from .animation_ui import narrator_voice_editor
            narrator_voice_editor(manager, project_id, demo=engine.demo)
    with backgrounds:
        if backgrounds.open:
            from .background_assets_ui import background_cards
            background_cards(engine, state)
    with sfx:
        if sfx.open:
            from .storyboard_assets_ui import show_requests
            show_requests(manager,project_id,"sfx")
            from .audio_asset_cards import library as audio_cards
            audio_cards(manager,project_id,"SFX")
    with props:
        if props.open:
            from .storyboard_assets_ui import show_requests
            show_requests(manager,project_id,"prop")
            with st.form("studio-new-prop"):
                title = st.text_input("Prop title")
                description = st.text_area("Prop description")
                modes = st.text_area("Modes (one per line)", placeholder="closed\nopen\nactive")
                if st.form_submit_button("Add prop", disabled=busy()):
                    try:
                        media.add_prop(title, description, modes.splitlines())
                        st.rerun()
                    except ValueError as exc:
                        render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('studio_assets_ui:225', locals()))
            from .check_prop_drafts_ui import render_library
            render_library(media)
            from .identity_ui import shared_asset_settings
            shared_asset_settings(manager, project_id)


def animation_asset_hint(engine,state):
    from .assets_workflow import animation_asset_readiness
    hint = "Assets prerequisite: create at least 1 character and 1 background. There is no complete-assets stage."
    key = "animation-asset-readiness-"+engine.project_id
    try:
        readiness = animation_asset_readiness(engine,state)
        st.session_state[key] = readiness
    except (OSError,ValueError,KeyError):
        readiness = st.session_state.get(key)
    if readiness is None:
        st.info(hint)
        return
    if readiness["characters"] >= 1 and readiness["backgrounds"] >= 1:
        st.success("Assets prerequisites done: at least 1 character and 1 background exist. There is no complete-assets stage.")
    else:
        st.info(hint)
    st.caption(f"Characters: {readiness['characters']} ({readiness['approvedCharacters']} approved) | Backgrounds: {readiness['backgrounds']} ({readiness['approvedBackgrounds']} approved)")


@polling_fragment
def asset_controls(engine, project_id):
    if st.session_state.pop("assets-controls-refresh-app", False):
        st.rerun(scope="app")
    state = engine.load()
    animation_asset_hint(engine,state)
    def start(callback,label,selected_stage):
        if selected_stage == "backgrounds":
            from .background_assets_ui import launch_background
            launch_background(engine,callback,label)
        else:
            launch_assets(engine,callback,label,rerun_on_start=False, service="openai" if selected_stage == "character_references" else None)
            st.session_state["assets-controls-refresh-app"] = True
    from .assets_status import assets_status_rows
    rows_key = "asset-controls-rows-" + project_id
    try:
        rows = [{"Regenerate": False, **row} for row in assets_status_rows(engine, state)]
        st.session_state[rows_key] = rows
    except (OSError, ValueError, KeyError) as exc:
        rows = st.session_state.get(rows_key, [])
        if not busy():
            render_error(str(exc), project_path=engine.path, key="asset-controls-error-" + project_id)
    if not rows:
        st.info("Waiting for reusable asset registrations to finish updating.")
        return
    table = st.data_editor(rows, hide_index=True, width="stretch", key="studio-assets-status-" + project_id,
                           disabled=generation_busy() or [name for name in rows[0] if name != "Regenerate"])
    chosen = [row for row in table if row["Regenerate"] and row["Stage"] in ("character_references", "backgrounds")]
    stages = {row["Stage"] for row in chosen}
    def regenerate():
        chosen_stage = next(iter(stages))
        subjects = [row["Subject"] for row in chosen]
        start(lambda: engine.generate(chosen_stage, subjects=subjects, force=True), "Regenerating selected reusable assets", chosen_stage)
    generation_button("Regenerate selected", key="studio-assets-regenerate-selected", disabled=generation_busy() or not chosen or len(stages) != 1,
                      on_click=regenerate)
    st.caption("Select image assets from one stage per batch. Approval rows never generate or approve content.")
    st.caption(f"Generating: {state.generation_active} · Queued: {state.generation_queued}. Character and background images run one at a time.")
