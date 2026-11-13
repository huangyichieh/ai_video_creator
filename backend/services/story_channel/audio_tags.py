"""Keep performance tags separate from transcript/subtitle wording."""


def speech_tags(options):
    performance = options.get("performance", {})
    source = performance.get("audio_tags") or performance.get("audioTags") or options.get("audio_tags") or []
    if not source:
        profile = options.get("voice_profile", {})
        emotion = performance.get("emotion") or profile.get("baseline_emotion") or profile.get("baselineEmotion")
        fallback = {"sad": "sad", "happy": "happy", "angry": "angry", "afraid": "nervous", "excited": "excited", "whispering": "whispers"}
        source = [fallback[emotion]] if emotion in fallback else []
    tags = []
    for value in source:
        tag = " ".join(str(value).strip("[] ").split())
        if tag and tag.casefold() not in {item.casefold() for item in tags}:
            tags.append(tag)
    return tags
