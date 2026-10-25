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


def storyboard_markdown(board: Storyboard) -> str:
    sections = ["# Storyboard"]
    for scene in board.scenes:
        sections.append(
            f"## {scene.order}. {scene.scene_id} ({scene.duration_sec:g}s)\n\n"
            f"**Location:** {scene.location_id}  \n"
            f"**Characters:** {', '.join(scene.character_ids) or 'None'}  \n"
            f"**Shot:** {scene.shot_type}  \n"
            f"**Camera:** {scene.camera_movement}\n\n"
            f"### Visual description\n\n{readable_text(scene.visual_description)}\n\n"
            f"### Narration\n\n{readable_text(scene.narration)}\n\n"
            f"### Dialogue\n\n" + ("\n\n".join(f"{line.character_id}: {readable_text(line.text)}" for line in scene.dialogue) or "None")
            + f"\n\n### Action\n\n{readable_text(scene.action)}\n\n"
            f"**BGM mood:** {scene.bgm_mood}  \n"
            f"**SFX:** {', '.join(scene.sfx) or 'None'}  \n"
            f"**Assets:** {', '.join(scene.asset_refs) or 'None'}  \n"
            f"**Status:** {scene.status}")
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
