import re

from .creative import Storyboard


def readable_text(value: str) -> str:
    """Separate sentences and speaker turns without changing narrative wording."""
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    # Chinese punctuation needs no following space; include closing quotation marks.
    text = re.sub(r'([。！？][」』”’"\']*)(?=[^\s。！？])', r'\1\n', text)

    def sentence_break(match):
        before = text[:match.start() + 1]
        word = re.search(r"([\w.]+)[.!?]$", before)
        token = word.group(1).lower() if word else ""
        if token in {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e"} or re.search(r"(?:[A-Za-z]\.){2,}$", before):
            return match.group()
        return match.group(1) + "\n"

    text = re.sub(r'([.!?][”’"\']*)[ \t]+(?=[A-Z“「『])', sentence_break, text)
    # Speaker IDs sometimes arrive in one paragraph rather than separate lines.
    text = re.sub(r'(?<=\S)[ \t]+(?=[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_-]+:)', "\n", text)
    # Markdown soft newlines otherwise collapse when displayed in Streamlit.
    lines = text.split("\n")
    return "\n".join(line.rstrip() + ("  " if line.strip() and index + 1 < len(lines) and lines[index + 1].strip() else "")
                     for index, line in enumerate(lines))


def performance_text(text, tags):
    result = readable_text(text)
    if tags:
        result += "\n\n*Audio tags: " + " ".join("[" + tag.strip("[] ") + "]" for tag in tags) + "*"
    return result


def storyboard_markdown(board: Storyboard,locations=None) -> str:
    from .location_labels import location_label
    sections = ["# Storyboard"]
    for scene in board.scenes:
        from .sfx_duration import scene_settings
        sfx = []
        for cue in scene.sfx:
            timing = scene_settings(scene,cue)
            sfx.append(cue+(f" ({timing['duration']:g}s"+(", loop" if timing["loop"] else "")+")" if scene.sfx_timing else ""))
        sections.append(
            f"## {scene.order}. {scene.scene_id} ({scene.duration_sec:g}s)\n\n"
            f"**Location:** {location_label(scene.location_id,locations or {},scene.target_area)}  \n"
            f"**Characters:** {', '.join(scene.character_ids) or 'None'}  \n"
            f"**Shot:** {scene.shot_type}  \n"
            f"**Camera:** {scene.camera_movement}\n\n"
            f"### Visual description\n\n{readable_text(scene.visual_description)}\n\n"
            f"### Narration\n\n{performance_text(scene.narration, scene.narration_audio_tags)}\n\n"
            f"### Dialogue\n\n" + ("\n\n".join(f"{line.character_id}: {performance_text(line.text, line.audio_tags)}" for line in scene.dialogue) or "None")
            + f"\n\n### Action\n\n{readable_text(scene.action)}\n\n"
            f"**BGM mood:** {scene.bgm_mood}  \n"
            f"**BGM asset:** {scene.bgm_id or 'None'}  \n"
            f"**SFX:** {', '.join(sfx) or 'None'}  \n"
            f"**Assets:** {', '.join(scene.asset_refs) or 'None'}  \n"
            f"**Props:** {', '.join(scene.prop_ids) or 'None'}  \n"
            f"**Status:** {scene.status}")
    if board.backgrounds or board.props or board.bgm or board.detail_backgrounds:
        sections.append("## New reusable asset requirements")
        sections.extend(f"- Background {item.location_id}: **{item.name}** — {item.description}" for item in board.backgrounds)
        sections.extend(f"- Prop {item.prop_id}: **{item.title}** — {item.description}; modes: {', '.join(item.modes)}" for item in board.props)
        sections.extend(f"- BGM {item.bgm_id}: **{item.title}** — {item.description}" for item in board.bgm)
        sections.extend(f"- Detail background {item.location_id} · {item.detail_id}: **{item.area}** — {item.description}" for item in board.detail_backgrounds)
    return "\n\n".join(sections) + "\n"


def dialogue_review_markdown(report, provider, model):
    sections = ["# AI Review (Storyboard)", "Dialogue quality review for storyboard approval.", f"**Reviewer:** {provider} / {model}",
                f"**Recommendation:** {report.recommendation.title()}",
                "## Summary", readable_text(report.summary), "## Findings"]
    for index, issue in enumerate(report.issues, 1):
        sections.extend((f"### {index}. {issue.scene_id} · line {issue.line_index + 1}",
                         f"**Severity:** {issue.severity.title()}  \n**Category:** {issue.category.replace('_', ' ').title()}",
                         "**Finding**", readable_text(issue.description),
                         "**Suggestion**", readable_text(issue.suggestion)))
    if not report.issues:
        sections.append("No issues found.")
    return "\n\n".join(sections) + "\n"
