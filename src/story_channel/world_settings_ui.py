"""Field-based editor for the single World settings JSON source."""
from .error_report_ui import render_error, error_project, report_widget_key
import streamlit as st

from .files import input_hash
from .studio import WorldStudio
from .worldview import WorldSettings, WORLD_FIELD_NAMES, WORLD_DRAFT_POINTS, WORLD_DRAFT_EXAMPLES
from .ui_tasks import busy, launch


WORLD_LANGUAGES = {
    "zh-TW": "Traditional Chinese (繁體中文)", "zh-CN": "Simplified Chinese (简体中文)",
    "en": "English", "ja": "Japanese (日本語)", "ko": "Korean (한국어)",
    "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
    "it": "Italian", "hi": "Hindi", "ar": "Arabic",
}


def language_field(current, key, *, disabled=False):
    options = list(WORLD_LANGUAGES)
    if current not in options:
        options.append(current)
    st.session_state.setdefault(key, current)
    value = st.selectbox("Default language", options, key=key, disabled=disabled,
                         format_func=lambda code: WORLD_LANGUAGES.get(code, code))
    st.caption("Language for generated stories, dialogue, subtitles and voice preview text. New episodes inherit it. Example: Traditional Chinese (zh-TW).")
    return value


def settings_fields(settings, prefix, *, disabled=False, include_name=True):
    values = {}
    values["name"] = settings.name
    if include_name:
        st.session_state.setdefault(prefix + "name", settings.name)
        values["name"] = st.text_input("World name (optional)", key=prefix + "name", disabled=disabled)
        st.caption("A short name for this World and project. Example: The Memory Coast.")
    for field, (label, hint) in zip(WORLD_FIELD_NAMES, WORLD_DRAFT_POINTS):
        key = prefix + field
        st.session_state.setdefault(key, getattr(settings, field))
        values[field] = st.text_area(label, key=key, height=100, help=hint, disabled=disabled,
                                     placeholder="Leave unspecified if undecided.")
        st.caption(hint + "\n\nExample: " + WORLD_DRAFT_EXAMPLES[field])
    return WorldSettings(**{field: value.strip() for field, value in values.items()})


def settings_text(settings, *, include_name=False):
    """Readable presentation; values remain literal text, never editor widgets."""
    if include_name and settings.name:
        st.markdown("**World name**")
        st.text(settings.name)
    for field, (label, _) in zip(WORLD_FIELD_NAMES, WORLD_DRAFT_POINTS):
        st.markdown("**" + label + "**")
        st.text(getattr(settings, field).strip() or "Unspecified")


def saved_field_draft(settings, prefix):
    return WorldSettings(**{field: st.session_state.get(prefix + field, getattr(settings, field)).strip()
                            for field in WorldSettings.model_fields})


def new_world(manager):
    st.header("New project")
    st.subheader("World settings")
    st.caption("A short description is enough. Fill only the points you have decided. Review and approve this draft on World before continuing.")
    prefix = "studio-draft-new-world-"
    if st.session_state.pop("clear-new-world-draft", False):
        st.session_state.pop(prefix + "default-language", None)
        for field in WorldSettings.model_fields:
            st.session_state.pop(prefix + field, None)
    with st.form("new_world"):
        language = language_field(manager.settings.default_language, prefix + "default-language")
        settings = settings_fields(WorldSettings(), prefix)
        if st.form_submit_button("Create project", type="primary"):
            try:
                project = WorldStudio.create(manager, settings, default_language=language)
                from .models import ProviderConfig
                owner = "new"
                default = "Demo (offline)" if manager.settings.llm_provider == "demo" else "OpenAI"
                demo = st.session_state.get(f"project-setting-{owner}-generation-mode", default).startswith("Demo")
                project.provider_config["llm"] = ProviderConfig(provider="demo" if demo else "openai",
                    model="demo-v1" if demo else manager.settings.llm_model,
                    options={"max_output_tokens": manager.settings.llm_max_output_tokens})
                manager.save(project)
                from .generation_preferences import save_preferences, preset
                save_preferences(manager, project.project_id, st.session_state.get("new-generation-preferences", preset()), demo=demo)
                st.session_state.update(selected_project=project.project_id, creating_project=False, workspace_page="World")
                st.session_state["clear-new-world-draft"] = True
                st.rerun()
            except (OSError, ValueError) as exc:
                render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('world_settings_ui:89', locals()))


def world_page(manager, project_id, pipeline):
    from .studio_ui import request_modify
    from .locking import ProjectBusyError
    studio = WorldStudio(manager, project_id, pipeline)
    try:
        state = studio.load() if busy() else studio.migrate()
    except ProjectBusyError:
        state = studio.load()
    st.header("World settings")
    st.caption("Saved in world_setting.json. Draft → AI Review → Apply / Revert or manual edits → Approve.")
    current_name = state.display_name if state.display_name is not None else (state.settings.name or manager.load(project_id).title)
    name_key = f"studio-draft-world-name-{project_id}-{input_hash(current_name)[:12]}"
    st.session_state.setdefault(name_key, current_name)
    name_column, save_name = st.columns([4, 1], vertical_alignment="bottom")
    name = name_column.text_input("World name", key=name_key, disabled=busy())
    name_column.caption("A short name for this World and project. Example: The Memory Coast. Editable after approval.")
    if save_name.button("Save name", key="studio-world-save-name", disabled=busy() or not name.strip() or name.strip() == current_name):
        try:
            studio.rename(name)
            st.rerun()
        except (OSError, ValueError) as exc:
            render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('world_settings_ui:113', locals()))
    if state.approval:
        try:
            studio.require_approved()
        except (OSError, ValueError) as exc:
            render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('world_settings_ui:118', locals()))
            return
        st.success("World settings · Approved")
    prefix = f"studio-draft-world-fields-{project_id}-{input_hash(state.base_text)[:12]}-"
    editing_key = prefix + "editing"
    reviewed = bool(state.review or state.approval)
    if reviewed and not state.approval:
        st.toggle("Edit World draft", key=editing_key, disabled=busy())
    editing = not reviewed or (not state.approval and st.session_state.get(editing_key, False))
    language_key = prefix + "default-language-" + state.default_language
    if editing:
        language = language_field(state.default_language, language_key, disabled=busy())
        draft = settings_fields(state.settings, prefix, disabled=busy(), include_name=False)
    else:
        language = st.session_state.get(language_key, state.default_language) if not state.approval else state.default_language
        draft = saved_field_draft(state.settings, prefix) if not state.approval else state.settings
        st.markdown("**Default language**")
        st.text(WORLD_LANGUAGES.get(language, language))
        settings_text(draft)
    if not state.approval:
        dirty = draft != state.settings or language != state.default_language
        save, review, modify = st.columns(3)
        if save.button("Save World draft", key="studio-world-save", disabled=busy() or not dirty):
            try:
                studio.edit_settings(draft, default_language=language)
                st.rerun()
            except (OSError, ValueError) as exc:
                render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('world_settings_ui:145', locals()))
        from .generation_ui import review_button
        review_button("AI Review",container=review,key="studio-world-review",disabled=busy() or dirty,
            on_click=lambda:launch(studio.review,"Reviewing worldview and typos",
                success=lambda _:st.session_state.update({editing_key:False})))
        modify.button("Modify with prompt", key="studio-world-modify", disabled=busy() or dirty,
                      on_click=request_modify, args=(project_id, "world"))
        report = state.review
        ready = bool(report and report.decision in ("apply", "revert") and report.accepted_text == state.base_text)
        if report:
            current = report.source_hash == input_hash(state.base_text)
            if ready:
                st.caption("AI Review resolved: " + ("changes applied" if report.decision == "apply" else "original retained") + ".")
                if report.verification and report.verification.conflicts:
                    for conflict in report.verification.conflicts:
                        render_error("Selected draft contradiction: " + conflict, project_path=error_project(locals()), key=report_widget_key('world_settings_ui:159', locals()))
                    ready = False
            elif not current:
                st.caption("The saved draft changed. Run AI Review again.")
            if current and not report.decision:
                with st.expander("AI Review suggestions", expanded=True):
                    st.write(report.advice.summary)
                    for reason in report.advice.reasons:
                        st.caption(reason)
                    for conflict in report.advice.conflicts:
                        st.warning(conflict)
                    original, suggestion = st.columns(2)
                    with original:
                        st.markdown("**Original**")
                        settings_text(state.settings, include_name=True)
                    with suggestion:
                        st.markdown("**Suggestion**")
                        proposal_prefix = f"studio-draft-world-proposal-{project_id}-{report.review_id}-"
                        suggestion_editing = st.toggle("Edit suggestion", key=proposal_prefix + "editing", disabled=busy())
                        proposed = WorldSettings.from_text(report.advice.proposed_text or state.base_text)
                        if suggestion_editing:
                            proposal = settings_fields(proposed, proposal_prefix, disabled=busy())
                        else:
                            proposal = saved_field_draft(proposed, proposal_prefix)
                            settings_text(proposal, include_name=True)
                    apply, revert = st.columns(2)
                    for column, choice, label in ((apply, "apply", "Apply changes"), (revert, "revert", "Revert changes")):
                        if column.button(label, key="studio-world-" + choice, disabled=busy() or dirty):
                            try:
                                studio.resolve_base(choice, proposal.text(), expected=input_hash(state.base_text))
                                st.rerun()
                            except (OSError, ValueError) as exc:
                                render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('world_settings_ui:191', locals()))
        if st.button("Approve World settings", key="studio-world-approve", type="primary", disabled=busy() or dirty or not ready):
            launch(lambda: studio.approve_base(expected=input_hash(state.base_text)), "Approving World settings")
        st.caption("Save your edits before AI Review. An Apply/Revert choice is required even when no changes are suggested.")
    st.subheader("Additional settings")
    if not state.approval:
        st.caption("Approve World settings before adding details.")
        return
    st.markdown("**Approved settings**")
    approved = studio.approved_additions(state)
    if not approved:
        st.caption("No approved settings added yet.")
    for item in approved:
        with st.container(border=True):
            st.markdown(":green[Approved]")
            st.text(item.text)
    st.markdown("**Draft settings**")
    pending = [item for item in state.additions if not item.approval]
    if not pending:
        st.caption("No draft settings.")
    for item in pending:
        addition_card(studio, item)
    new_addition(studio)


def addition_card(studio, item):
    from .studio_ui import request_modify
    pid, sid = studio.project_id, item.setting_id
    edit_key = f"studio-draft-addition-editing-{pid}-{sid}"
    draft_key = f"studio-draft-addition-{sid}-{input_hash(item.text)[:8]}"
    st.session_state.setdefault(draft_key, item.text)
    dirty = st.session_state[draft_key] != item.text
    report = item.review
    proposal_key = f"studio-draft-addition-proposal-{sid}-{report.source_hash[:8]}-{report.review_id}" if report else ""
    choice_key = "studio-review-choice-addition-" + sid + "-" + report.review_id if report else ""
    if report:
        st.session_state.setdefault(proposal_key, report.advice.proposed_text or item.text)
    choice = st.session_state.get(choice_key) if report else None
    current = bool(report and report.source_hash == input_hash(item.text) and report.context_hash == input_hash(studio.canon()))
    with st.container(border=True):
        st.markdown(":orange[Draft] · " + sid[:8])
        st.text(item.text)
        edit, review, approve, remove = st.columns(4)
        edit.button("Edit", key="addition-edit-" + sid, disabled=busy() or st.session_state.get(edit_key, False),
                    on_click=lambda: st.session_state.update({edit_key: True}))
        from .generation_ui import review_button
        review_button("AI Review",container=review,key="addition-review-"+sid,disabled=busy() or dirty,
            on_click=lambda sid=sid:launch(lambda:studio.review(setting_id=sid),"Checking detail against approved World and additions"))
        if approve.button("Approve", key="addition-approve-" + sid, disabled=busy() or dirty or not current or choice not in ("apply", "revert")):
            selected = st.session_state[proposal_key]
            launch(lambda: studio.approve_setting(sid, text=selected, decision=choice), "Checking final consistency and approving addition")
        if remove.button("Remove", key="addition-remove-" + sid, disabled=busy()):
            studio.edit_setting(sid, remove=True)
            st.rerun()
        if st.session_state.get(edit_key):
            edited = st.text_area("Additional setting", key=draft_key, disabled=busy())
            save, cancel, modify = st.columns(3)
            if save.button("Save draft", key="addition-save-" + sid, disabled=busy() or not dirty):
                try:
                    studio.edit_setting(sid, edited)
                    st.session_state[edit_key] = False
                    st.rerun()
                except (OSError, ValueError) as exc:
                    render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('world_settings_ui:253', locals()))
            cancel.button("Cancel edit", key="addition-cancel-" + sid, disabled=busy(),
                          on_click=lambda: st.session_state.update({draft_key: item.text, edit_key: False}))
            modify.button("Modify with prompt", key="addition-modify-" + sid, disabled=busy() or dirty,
                          on_click=request_modify, args=(pid, "addition", sid))
        if dirty:
            st.caption("Unsaved changes. Save or cancel the edit before reviewing or approving.")
        if report:
            with st.expander("AI Review suggestions", expanded=True):
                st.write(report.advice.summary)
                for reason in report.advice.reasons:
                    st.caption(reason)
                for conflict in report.advice.conflicts:
                    st.warning(conflict)
                if report.verification:
                    for conflict in report.verification.conflicts:
                        render_error("Selected addition contradiction: " + conflict, project_path=error_project(locals()), key=report_widget_key('world_settings_ui:269', locals()))
                original, suggestion = st.columns(2)
                original.text_area("Original setting", value=item.text, disabled=True, key="addition-original-" + sid + "-" + report.review_id)
                suggestion.text_area("Suggested setting — editable", key=proposal_key, disabled=busy(),
                    on_change=lambda: st.session_state.pop(choice_key, None))
                apply, revert = st.columns(2)
                apply.button("Applied" if choice == "apply" else "Apply changes", key="addition-apply-" + sid,
                    disabled=busy() or dirty, on_click=lambda: st.session_state.update({choice_key: "apply"}))
                revert.button("Reverted" if choice == "revert" else "Revert changes", key="addition-revert-" + sid,
                    disabled=busy() or dirty, on_click=lambda: st.session_state.update({choice_key: "revert"}))
                if not current:
                    st.warning("The draft or approved World changed. Run AI Review again.")


def new_addition(studio):
    key = "studio-draft-addition-new-open-" + studio.project_id
    st.button("Add settings", key="world-add-settings-" + studio.project_id, disabled=busy() or st.session_state.get(key, False),
              on_click=lambda: st.session_state.update({key: True}))
    if st.session_state.get(key):
        with st.form("new_additional_setting_" + studio.project_id, clear_on_submit=True):
            text = st.text_area("New additional setting", height=110)
            add, cancel = st.columns(2)
            if add.form_submit_button("Add draft", disabled=busy()):
                try:
                    studio.add_setting(text)
                    st.session_state[key] = False
                    st.rerun()
                except (OSError, ValueError) as exc:
                    render_error(str(exc), project_path=error_project(locals()), key=report_widget_key('world_settings_ui:297', locals()))
            if cancel.form_submit_button("Cancel", disabled=busy()):
                st.session_state[key] = False
                st.rerun()
