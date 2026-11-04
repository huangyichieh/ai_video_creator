"""Inspect recorded generation inputs without substituting current settings."""
import json
import streamlit as st

from .error_reports import redact


def details(record):
    asset = record.asset if record else None
    params = asset.generation_params if asset else {}
    request = params.get("runwayRequest")
    context = params.get("backgroundContext")
    refs = request.get("referenceInputs", []) if request else [
        {"index": index, "file": file} for index, file in enumerate(params.get("inputImages", []), 1)]
    return redact({"provider": asset.provider if asset else None, "model": asset.model if asset else None,
        "revision": record.revision if record else None,
        "prompt": request["body"]["promptText"] if request else params.get("submittedPrompt") or params.get("prompt"),
        "request": request, "context": context, "references": refs,
        "locationReferences": params.get("locationReferencePack", []), "settings": params.get("config", {}),
        "jobId": params.get("jobId"), "inputMode": params.get("inputMode"), "yawFill": params.get("yawFill"),
        "projection": params.get("projection") or params.get("canonicalProjection"),
        "canonicalEnhancement":params.get("canonicalEnhancement"),"masterPrefill":params.get("masterPrefill"),
        "outputResolution":params.get("outputResolution"),"locationPairId":params.get("locationPairId")})


def hover_text(info):
    if not info["prompt"]:
        return "No saved request yet. Generate this image to inspect its inputs."
    summary = {"provider": info["provider"], "model": info["model"], "revision": info["revision"],
               "references": info["references"], "sourceContext": info["context"],
               "requestSettings": {key: val for key, val in (info["request"] or {}).get("body", {}).items() if key != "promptText"}}
    return info["prompt"] + "\n\n" + json.dumps(summary, ensure_ascii=False, indent=2) + "\nClick to inspect images and full request details."


def render(engine, info):
    st.markdown("**Recorded generation request**")
    if not info["prompt"]:
        st.caption("No saved request yet. Generate this image to inspect its inputs.")
        return
    st.caption(f"Saved revision {info['revision']} · {info['provider']} / {info['model']}" + (f" · Task {info['jobId']}" if info["jobId"] else ""))
    if info.get("outputResolution"):
        st.json({"outputResolution":info["outputResolution"],"locationPairId":info.get("locationPairId")},expanded=False)
    if info.get("masterPrefill"):
        st.markdown("**Protected bridge pixels & edit mask**")
        st.caption("Opaque mask regions contain approved bridge pixels; only transparent regions are generated. The final image restores protected pixels exactly.")
        st.json(info["masterPrefill"],expanded=False)
    st.markdown("**Submitted prompt**")
    st.code(info["prompt"], language=None, wrap_lines=True)
    if isinstance(info.get("projection"), dict):
        st.markdown("**Deterministic panorama projection**")
        st.caption("No AI generation: this camera view deterministically projects its recorded source panorama.")
        st.json(info["projection"])
    if info.get("canonicalEnhancement"):
        st.markdown("**Canonical appearance enhancement**")
        st.json(info["canonicalEnhancement"])
    if info.get("yawFill"):
        fill = info["yawFill"]
        st.markdown("**Transformed fill draft**")
        try:
            from .media_utils import file_hash
            draft = engine.local_path(fill["draftImage"])
            if file_hash(draft) != fill["draftHash"]:
                raise ValueError("The saved draft changed after generation.")
            st.image(str(draft), caption="Black regions are missing pixels to complete; other pixels are reprojected from the previous yaw.")
        except (OSError, ValueError) as exc:
            st.caption(str(exc))
        st.markdown("**Source image and transform parameters**")
        st.json(fill, expanded=False)
    if info["request"]:
        st.markdown("**Runway request body**")
        st.caption(f"{info['request']['method']} {info['request']['endpoint']} · API version {info['request']['apiVersion']}. Authentication is omitted.")
        st.json(info["request"]["body"], expanded=False)
    elif info["provider"] == "runway":
        st.caption("The exact Runway request body was not recorded for this revision. Saved prompt, image paths and app settings are shown where available.")
    else:
        st.caption(f"This revision used {info['provider']}; no Runway request was sent.")
    st.markdown("**Reference images, in request order**")
    if not info["references"]:
        st.caption("No recorded reference images.")
    for ref in info["references"]:
        match = next((item for item in info["locationReferences"] if item.get("file") == ref.get("file")), {})
        label = f"Image {ref['index']}" + (" · @" + ref["tag"] if ref.get("tag") else "")
        if match:
            label += " · " + match["key"].split(":", 1)[-1] + f" · revision {match['revision']} at generation"
            if match["key"].endswith("__master") and (info["context"] or {}).get("targetYaw") is not None:
                label += " · approved master"
            elif "__view_" in match["key"]:
                label += " · previous yaw / starting camera orientation" if (info["context"] or {}).get("yawReferencePolicy") == "sequential_previous" else " · neighboring angle"
        elif info["context"] and ref.get("file") == (info["context"].get("styleImage") or {}).get("file"):
            label += " · shared style only"
        st.caption(label)
        try:
            from .media_utils import file_hash
            path = engine.local_path(ref["file"])
            expected = ref.get("fileHash") or match.get("fileHash")
            if not path.is_file():
                raise ValueError("Recorded reference image is missing.")
            if expected and file_hash(path) != expected:
                raise ValueError("This reference file changed after generation; it is not the recorded input.")
            st.image(str(path))
            if not expected:
                st.caption("This older revision did not record a reference-image hash.")
        except (OSError, ValueError) as exc:
            st.caption(str(exc))
        st.code(json.dumps(ref, ensure_ascii=False, indent=2), language="json", wrap_lines=True)
    if info["locationReferences"]:
        st.markdown("**Recorded reference provenance**")
        st.json(info["locationReferences"], expanded=False)
    st.markdown("**Source context before prompt shortening**")
    st.caption("Floor plan, object layout and style rules guide prompt preparation. They are not separate Runway API fields; the submitted prompt above shows what reached Runway.")
    if info["context"]:
        context = info["context"]
        st.markdown("**Floor plan**")
        st.write(context.get("floorPlan") or "No saved floor plan supplied.")
        st.markdown("**Object layout**")
        st.json(context.get("objectLayout", {}))
        st.markdown("**Style rules**")
        st.write(context.get("styleRules") or "No shared style rules supplied.")
        st.markdown("**Full recorded source context**")
        st.json(context, expanded=False)
    else:
        st.caption("A floor-plan/object-layout snapshot was not recorded for this revision. Current settings are not substituted for historical inputs.")
    st.markdown("**App generation settings**")
    st.caption("Some app settings control preparation and are not fields in the Runway request body.")
    st.json(info["settings"], expanded=False)
