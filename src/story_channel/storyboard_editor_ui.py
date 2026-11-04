"""Editable scene cards; existing assets and pending requirements stay distinct."""
import json

import pandas as pd
import streamlit as st

from .files import input_hash
from .ui_tasks import busy
from .storyboard_editor import inventory,save,background_choice,select_background,reconcile_detail_requirements


def lines(text):
    return [value.strip() for value in text.splitlines() if value.strip()]


def render(pipeline,*,generated_details_only=False):
    source = (pipeline.path/"scenes.json").read_text(encoding="utf-8-sig")
    from .creative import Storyboard
    board = Storyboard.model_validate_json(source).model_dump(mode="json",by_alias=True)
    baseline = input_hash(board)
    regeneration = None
    catalog = inventory(pipeline)
    prefix = f"storyboard-fields-{pipeline.project_id}-{input_hash(source)[:12]}-"
    cache_prefix = "studio-draft-"+prefix
    def table(name,rows,columns,**kwargs):
        key = prefix+name
        cache = st.session_state.setdefault(cache_prefix+name,{"base":rows,"last":rows})
        if key not in st.session_state:
            cache["base"] = cache["last"]
        edited = st.data_editor(pd.DataFrame(cache["base"],columns=columns),num_rows="dynamic",hide_index=True,
            key=key,disabled=busy(),width="stretch",**kwargs)
        cache["last"] = edited.fillna("").to_dict("records")
        return edited
    st.caption("Edit each scene below. Existing asset references and new/pending requirements have distinct labels. Saving reopens storyboard review and invalidates affected prompts and animations; it does not approve content.")
    with st.expander("New reusable asset requirements",expanded=False):
        st.markdown(":orange[New assets / pending generation] — define these before selecting them in a scene. Saving registers them on Assets, including BGM.")
        for field,id_field,columns in (
            ("backgrounds","locationId",["locationId","name","description"]),
            ("props","propId",["propId","title","description","modes"]),
            ("bgm","bgmId",["bgmId","title","description"])):
            st.markdown("**"+{"backgrounds":"Backgrounds","props":"Props","bgm":"BGM"}[field]+"**")
            rows = [{**item,**({"modes":"\n".join(item["modes"])} if field == "props" else {})} for item in board[field]]
            edited = table(field,rows,columns)
            items = []
            for row in edited.fillna("").to_dict("records"):
                if not any(str(value).strip() for value in row.values()):
                    continue
                if field == "props":
                    row["modes"] = lines(row["modes"]) or ["default"]
                items.append(row)
            board[field] = items
    assets = {field:{**catalog[field],**{item.get(id_field,""):item for item in board[field] if item.get(id_field)}}
        for field,id_field in (("backgrounds","locationId"),("props","propId"),("bgm","bgmId"))}
    if cache_prefix+"detail-backgrounds" not in st.session_state:
        board["detailBackgrounds"] = reconcile_detail_requirements(board,catalog)
    with st.expander("New detail-background requirements",expanded=False):
        st.caption("Requirements follow scene background selections. Unused declarations are removed when backgrounds change or the storyboard is saved; shared assets and images are retained.")
        columns = ["locationId","detailId","area","description"]
        edited = table("detail-backgrounds",board.get("detailBackgrounds",[]),columns)
        board["detailBackgrounds"] = [row for row in edited.fillna("").to_dict("records") if any(str(value).strip() for value in row.values())]
    details = {key:value for key,value in catalog.get("detail_backgrounds",{}).items()
        if not generated_details_only or key in catalog["available"]["backgrounds"]}
    if not generated_details_only:
        for item in board["detailBackgrounds"]:
            if item.get("locationId") and item.get("detailId"):
                parent = assets["backgrounds"].get(item["locationId"],{})
                details.setdefault(item["locationId"]+"__detail_"+item["detailId"],{
                    **item,"name":parent.get("name",item["locationId"])+" · "+item["detailId"]})
    assets["backgrounds"].update(details)

    def label(field,identity):
        item = assets[field].get(identity,{})
        title = item.get("name",item.get("title",identity))
        return ("🟢 Existing · " if identity in catalog["available"][field] else "🟠 New / pending · ")+identity+" ("+title+")"

    def selection(field,title,current,key,multiple=False,optional=False,on_change=None):
        values = list(assets[field])
        for value in (current if multiple else [current]):
            if value and value not in values:
                values.append(value)
        if multiple:
            return st.multiselect(title,values,default=current,key=key,format_func=lambda value:label(field,value),disabled=busy(),persist_state="session")
        if optional:
            values = [None,*values]
        if not values:
            st.warning("Add a "+field+" requirement above first.")
            return current
        return st.selectbox(title,values,index=values.index(current) if current in values else 0,key=key,
            format_func=lambda value:"None" if value is None else label(field,value),disabled=busy(),persist_state="session",on_change=on_change)

    def refresh_requirements():
        rows = reconcile_detail_requirements(board,catalog)
        st.session_state[cache_prefix+"detail-backgrounds"] = {"base":rows,"last":rows}
        st.session_state.pop(prefix+"detail-backgrounds",None)

    for scene in board["scenes"]:
        sid = scene["sceneId"]
        key = prefix+sid+"-"
        with st.expander(sid+" · "+scene["shotType"],expanded=False):
            st.caption("Scene ID: "+sid+" · "+scene["status"]+" (managed by the pipeline)")
            order,duration = st.columns(2)
            scene["order"] = order.number_input("Order",min_value=1,value=scene["order"],step=1,key=key+"order",disabled=busy(),persist_state="session")
            scene["durationSec"] = duration.number_input("Duration (seconds)",min_value=0.1,value=float(scene["durationSec"]),key=key+"duration",disabled=busy(),persist_state="session")
            def changed_background(scene=scene,key=key):
                select_background(scene,st.session_state[key+"location"],details)
                # A previous target-area widget must not restore the old detail
                # after switching to another parent background or its master.
                scene["targetArea"] = details.get(st.session_state[key+"location"],{}).get("detailId","")
                for suffix in ("targetArea-select","targetArea"):
                    st.session_state.pop(key+suffix,None)
                refresh_requirements()
            chosen_background = selection("backgrounds","Background",background_choice(scene,details),key+"location",on_change=changed_background)
            select_background(scene,chosen_background,details)
            scene["characterIds"] = st.multiselect("Characters",list(catalog["characters"]),default=scene["characterIds"],key=key+"characters",
                format_func=lambda value:value+" ("+catalog["characters"][value]+")",disabled=busy(),persist_state="session")
            scene["propIds"] = selection("props","Props",scene["propIds"],key+"props",multiple=True)
            scene["bgmId"] = selection("bgm","BGM",scene.get("bgmId"),key+"bgm",optional=True)
            pending = [label(field,value) for field,values in (("backgrounds",[chosen_background]),("props",scene["propIds"]),("bgm",[scene["bgmId"]]))
                for value in values if value and value not in catalog["available"][field]]
            if pending:
                st.markdown(":orange[Needs creation / generation or audio import:]  \n"+"  \n".join(pending))
            for field,title in (("visualDescription","Visual description"),("action","Action"),("narration","Narration"),("bgmMood","BGM direction / mood")):
                scene[field] = st.text_area(title,value=scene[field],key=key+field,disabled=busy(),persist_state="session")
            scene["narrationAudioTags"] = lines(st.text_area("Narration audio tags (one per line)",value="\n".join(scene["narrationAudioTags"]),key=key+"narration-tags",disabled=busy(),persist_state="session"))
            dialogue = [{"characterId":item["characterId"],"text":item["text"],"audioTags":", ".join(item["audioTags"])} for item in scene["dialogue"]]
            edited = table(sid+"-dialogue",dialogue,["characterId","text","audioTags"],
                column_config={"characterId":st.column_config.SelectboxColumn("Speaker",options=list(catalog["characters"]),required=True),
                    "text":st.column_config.TextColumn("Dialogue",required=True),"audioTags":st.column_config.TextColumn("Audio tags (comma separated)")},
                )
            scene["dialogue"] = [{**item,"audioTags":[tag.strip() for tag in item["audioTags"].split(",") if tag.strip()]}
                for item in edited.fillna("").to_dict("records") if any(item.values())]
            known_sfx = list(dict.fromkeys([*catalog["sfx"],*scene["sfx"]]))
            selected_sfx = st.multiselect("Existing SFX requirements",known_sfx,default=scene["sfx"],key=key+"sfx",disabled=busy(),persist_state="session")
            fresh = lines(st.text_area("New SFX requirements (one per line)",key=key+"new-sfx",disabled=busy(),persist_state="session"))
            if fresh:
                st.markdown(":orange[New SFX — will be added to Assets when saved]")
            scene["sfx"] = list(dict.fromkeys([*selected_sfx,*fresh]))
            for field,title in (("shotType","Shot type"),("cameraMovement","Camera movement")):
                scene[field] = st.text_input(title,value=scene[field],key=key+field,disabled=busy(),persist_state="session")
            for field,title in (("cameraStartAngle","Camera start yaw"),("cameraEndAngle","Camera end yaw"),("cameraPitch","Camera pitch"),("cameraFov","Camera FOV")):
                scene[field] = st.number_input(title,value=float(scene[field]) if scene[field] is not None else None,key=key+field,disabled=busy(),persist_state="session")
            location = catalog["locations"].get(scene["locationId"],{})
            for field,title,options in (("targetArea","Target area",list(location.get("detail_references",{}))),
                ("interactionTarget","Interaction target",list(dict.fromkeys([*location.get("object_layout",{}),*scene["propIds"],*scene["characterIds"]])))):
                if field == "targetArea" and chosen_background in details:
                    st.caption("Target area: "+scene["targetArea"]+" (selected detail background)")
                    continue
                custom = "__custom__"
                current = scene[field]
                values = ["",*options,custom]
                def changed_target(scene=scene,key=key,field=field):
                    value = st.session_state[key+field+"-select"]
                    scene[field] = st.session_state.get(key+field,scene[field]) if value == "__custom__" else value
                    if field == "targetArea":
                        refresh_requirements()
                chosen = st.selectbox(title,values,index=values.index(current) if current in values else len(values)-1,
                    format_func=lambda value:"Other / new target" if value == custom else value or "None",key=key+field+"-select",disabled=busy(),persist_state="session",on_change=changed_target)
                scene[field] = st.text_input(title+" description",value=current,key=key+field,disabled=busy(),persist_state="session",on_change=changed_target) if chosen == custom else chosen
            position = scene.get("characterPosition")
            mode = st.selectbox("Character position",["Unspecified","Named position","Coordinates"],
                index=2 if isinstance(position,dict) else 1 if isinstance(position,str) else 0,key=key+"position-mode",disabled=busy(),persist_state="session")
            if mode == "Named position":
                scene["characterPosition"] = st.text_input("Position description",value=position if isinstance(position,str) else "",key=key+"position-name",disabled=busy(),persist_state="session")
            elif mode == "Coordinates":
                point = position if isinstance(position,dict) else {}
                scene["characterPosition"] = {**point,**{axis:st.number_input("Position "+axis,value=float(point.get(axis,0)),key=key+"position-"+axis,disabled=busy(),persist_state="session") for axis in ("x","y")}}
            else:
                scene["characterPosition"] = None
            instructions = st.text_input("Regeneration prompt (optional)",key=key+"regeneration-prompt",disabled=busy(),
                placeholder="For example: use a closer shot and emphasize the character's hesitation.",persist_state="session")
            from .generation_ui import generation_button
            from .action_labels import text_action_label
            if generation_button(text_action_label("Regenerate scene",pipeline,"storyboard"),key=key+"regenerate-scene",disabled=busy(),
                help="Regenerates this saved scene's visual treatment. Other scenes, timing, background selection and approved dialogue are preserved. Save or revert any edits first."):
                regeneration = (sid,instructions)
    if regeneration:
        if input_hash(Storyboard.model_validate(board).model_dump(mode="json",by_alias=True)) != baseline:
            st.warning("Save or revert current storyboard edits before regenerating a scene.")
        else:
            from .storyboard_regeneration import regenerate
            from .ui_tasks import launch
            sid,instructions = regeneration
            launch(lambda:regenerate(pipeline,sid,source,instructions),"Regenerating storyboard scene "+sid)
    controls = st.container(horizontal=True)
    if controls.button("Save storyboard changes",key=prefix+"save",disabled=busy()):
        try:
            save(pipeline,board,source)
            st.rerun()
        except (ValueError,OSError,RuntimeError) as exc:
            st.error(str(exc))
    def reset():
        for key in list(st.session_state):
            if key.startswith((prefix,cache_prefix)):
                del st.session_state[key]
    controls.button("Revert changes",key=prefix+"revert",disabled=busy(),on_click=reset)
