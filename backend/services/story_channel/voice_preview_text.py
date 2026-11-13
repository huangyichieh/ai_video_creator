"""Language-aware audition defaults shared by the editor and asset batches."""

ENGLISH_SAMPLE = "Keep the beacon burning. We will find our way through this storm together, one step at a time."

SAMPLES = {
    "en": ENGLISH_SAMPLE,
    "zh-Hant": "讓燈塔的光繼續照亮海面。別擔心，我們會一起穿越這場暴風雨，一步一步找到回家的路。",
    "zh-Hans": "让灯塔的光继续照亮海面。别担心，我们会一起穿越这场暴风雨，一步一步找到回家的路。",
    "ja": "灯台の明かりを絶やさないで。大丈夫、私たちは一緒にこの嵐を乗り越えて、一歩ずつ帰り道を見つけられる。",
    "ko": "등대의 불빛을 계속 밝혀 주세요. 걱정하지 마세요. 우리는 함께 이 폭풍을 이겨 내고 한 걸음씩 집으로 돌아가는 길을 찾을 거예요.",
    "es": "Mantén encendida la luz del faro. Juntos atravesaremos esta tormenta y encontraremos el camino a casa, paso a paso.",
    "fr": "Garde la lumière du phare allumée. Ensemble, nous traverserons cette tempête et retrouverons le chemin du retour, pas à pas.",
    "de": "Lass das Licht des Leuchtturms weiter brennen. Gemeinsam finden wir durch diesen Sturm, Schritt für Schritt auf dem Weg nach Hause.",
    "pt": "Mantenha a luz do farol acesa. Juntos vamos atravessar esta tempestade e encontrar o caminho de casa, um passo de cada vez.",
    "it": "Tieni accesa la luce del faro. Insieme attraverseremo questa tempesta e ritroveremo la strada di casa, un passo alla volta.",
}


def preview_sample_text(language, performances=(), character_id=None):
    tag = language.strip().lower().replace("_", "-")
    if tag.startswith("zh") or tag in {"chinese", "traditional chinese", "simplified chinese", "繁體中文", "简体中文", "中文"}:
        simplified = any(part in tag for part in ("hans", "cn", "sg", "simplified", "简体"))
        return SAMPLES["zh-Hans" if simplified else "zh-Hant"]
    names = {"english": "en", "japanese": "ja", "korean": "ko", "spanish": "es", "french": "fr", "german": "de", "portuguese": "pt", "italian": "it"}
    base = names.get(tag, tag.split("-")[0])
    if base in SAMPLES:
        return SAMPLES[base]
    # For other project languages, use the character's existing story dialogue
    # rather than silently falling back to an English audition.
    return " ".join(line.text for line in performances if line.character_id == character_id and line.text.strip())[:500]
