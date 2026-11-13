"""Deterministic demonstration data, not an AI story generator."""
import json

from .creative import Characters, ReviewReport, Script, Story, Storyboard, World
from .models import ProviderConfig


class DemoLLMProvider:
    last_usage: dict = {}

    def generate(self, prompt, response_model, config: ProviderConfig):
        self.last_usage = {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}
        # build_prompt always ends with one JSON object; split at its fixed delimiter.
        context = json.loads(prompt.split("input data. Preserve established facts and IDs.\n", 1)[1])
        zh = context["language"].lower().startswith("zh")
        title = context["title"]
        duration = context["targetDurationSec"]
        text = ("守燈人發現燈光能找回記憶。風暴迫近時，她帶著村民修復燈塔，選擇讓每個人記住彼此。"
                if zh else "The keeper discovers a light that restores memories. As a storm approaches, the villagers rebuild the lighthouse and remember one another.")
        if response_model is World:
            return World(era="近代海島" if zh else "A timeless island", rules=["燈光保存記憶" if zh else "Light preserves memories"],
                         technology_magic_rules=["Memories require a lit beacon"], organizations=["Village council"],
                         locations=[{"locationId": "lighthouse", "name": "燈塔" if zh else "Lighthouse", "description": "A stone lighthouse above a stormy sea"}],
                         timeline=["A storm approaches"], forbidden_contradictions=["An unlit beacon cannot restore memories"])
        if response_model is Characters:
            return Characters(characters=[{"characterId": "keeper", "name": "小光" if zh else "Mira", "age": 24,
                                           "appearance": "Short dark hair, brown eyes", "clothing": "Blue coat and brown boots",
                                           "personality": "Resourceful and caring", "background": "Raised by the former keeper",
                                           "speakingStyle": "Gentle, direct", "voiceId": "keeper-voice", "referenceImage": None,
                                           "immutableTraits": ["Brown eyes", "Blue coat"]}])
        if response_model is Story:
            return Story(title=title, target_duration_sec=duration,
                         **{key: f"{text} ({key})" for key in ("hook", "setup", "conflict", "escalation", "twist", "climax", "ending")})
        if response_model is ReviewReport:
            return ReviewReport(summary="Demo report: inspect the sample manually; this is not an AI review.", issues=[], recommendation="approve")
        if response_model is Script:
            return Script(title=title, scenes=[{"sceneId": f"scene_{i}", "locationId": "lighthouse", "characterIds": ["keeper"],
                                               "purpose": purpose, "narration": text, "dialogue": [], "action": text,
                                               "estimatedDurationSec": duration / 3}
                                              for i, purpose in enumerate(["Hook", "Conflict", "Resolution"], 1)])
        if response_model is Storyboard:
            return Storyboard(scenes=[{"sceneId": s["sceneId"], "order": i, "durationSec": s["estimatedDurationSec"],
                                       "locationId": s["locationId"], "characterIds": s["characterIds"], "narration": s["narration"],
                                       "dialogue": s["dialogue"], "action": s["action"], "shotType": "wide shot",
                                       "cameraMovement": "slow push in", "visualDescription": "The keeper lights the beacon above the sea",
                                       "bgmMood": "hopeful", "sfx": ["waves"], "assetRefs": [], "status": "draft"}
                                      for i, s in enumerate(context["script"]["scenes"], 1)])
        raise ValueError(f"Unsupported demo schema: {response_model.__name__}")
