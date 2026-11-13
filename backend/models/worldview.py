"""Lightweight guidance for editable worldview drafts; not required form fields."""
import re
from .core import Model


class WorldSettings(Model):
    name: str = ""
    premise: str = ""
    era_setting: str = ""
    core_rules: str = ""
    technology_magic_limits: str = ""
    society_key_places: str = ""
    tone: str = ""
    forbidden_contradictions: str = ""

    def text(self):
        sections = (["# " + self.name] if self.name.strip() else [])
        sections += [f"## {label}\n{getattr(self, field).strip()}" for field, (label, _) in zip(WORLD_FIELD_NAMES, WORLD_DRAFT_POINTS)
                     if getattr(self, field).strip()]
        return "\n\n".join(sections)

    @classmethod
    def from_text(cls, text):
        """Project legacy briefs/Markdown into fields without changing their saved source."""
        aliases = {re.sub(r"[^a-z]", "", label.lower()): field for field, (label, _) in zip(WORLD_FIELD_NAMES, WORLD_DRAFT_POINTS)}
        aliases.update(era="era_setting", rules="core_rules", locations="society_key_places", organizations="society_key_places",
                       timeline="society_key_places", technologymagicrules="technology_magic_limits")
        values = {field: [] for field in WORLD_FIELD_NAMES}
        field, name = "premise", ""
        for line in text.splitlines():
            heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
            if heading:
                label = heading[2]
                matched = aliases.get(re.sub(r"[^a-z]", "", label.lower()))
                if matched:
                    field = matched
                    if label.lower() in ("locations", "organizations", "timeline"):
                        values[field].append(label + ":")
                    continue
                if len(heading[1]) == 1 and not name:
                    if label.lower() not in ("world", "world settings"):
                        name = label
                    continue
            values[field].append(line)
        return cls(name=name, **{key: "\n".join(lines).strip() for key, lines in values.items()})


WORLD_FIELD_NAMES = ("premise", "era_setting", "core_rules", "technology_magic_limits", "society_key_places", "tone", "forbidden_contradictions")

WORLD_DRAFT_POINTS = (
    ("Premise", "What makes this world distinctive? One or two sentences."),
    ("Era & setting", "Time period, broad geography, and everyday way of life."),
    ("Core rules", "A few facts that must remain true across characters and episodes."),
    ("Technology / magic & limits", "What is possible, what is impossible, and any costs or limits."),
    ("Society & key places", "Only important social expectations or recurring places already decided."),
    ("Tone", "The intended atmosphere, such as hopeful, mysterious, realistic, or comedic."),
    ("Forbidden contradictions", "Explicit boundaries future stories must never violate."),
)

WORLD_DRAFT_EXAMPLES = {
    "premise": "On a foggy island, donated memories power the lighthouses that guide ships home.",
    "era_setting": "An early-industrial island coast, with fishing villages, steam ferries, and no modern electronics.",
    "core_rules": "Only willingly donated memories can power a lighthouse; a donated memory fades from its owner's mind.",
    "technology_magic_limits": "Brass lenses extract and store memories. Each lens holds one memory and cracks after three uses.",
    "society_key_places": "Lighthouse keepers are trusted public servants. The harbor market and northern beacon are recurring places.",
    "tone": "Quiet mystery and warmth, with hopeful endings and gentle humor; avoid graphic violence.",
    "forbidden_contradictions": "No resurrection, time travel, or copying donated memories. A lost memory cannot return without an established cause.",
}


WORLD_REVIEW_GUIDANCE = (
    "Keep the worldview draft concise. Consider premise, era/setting, core rules, technology/magic and its limits, "
    "society/key places, tone, and forbidden contradictions. These are guidance, not mandatory filled fields. "
    "A brief is valid. Leave undecided details explicitly unspecified; do not turn missing information into invented "
    "permanent rules or unnecessary history. Suggest only useful clarifications. Distinguish a contradiction from "
    "an open question. For an addition, check it against the entire approved base and every approved addition."
)
