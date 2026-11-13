"""Keep visual and voice-design inputs separate from character lore and IDs."""
import json
import re


_VISUAL = re.compile(
    r"\b(?:hair|eyes?|eyebrows?|skin|scars?|tattoos?|freckles?|beard|moustache|mustache|glasses|"
    r"ears?|nose|face|horns?|tail|wings?|height|physique|build|silhouette|posture|expression|"
    r"limbs?|hands?|clothing|coat|jacket|shirt|dress|uniform|boots?|hat|bracelet|necklace|ring|"
    r"wears?|wearing|lantern)\b|"
    r"髮|发色|發色|瞳|眼睛|眼鏡|眼镜|膚色|肤色|疤|刺青|紋身|纹身|鬍|胡须|雀斑|"
    r"臉|脸|鼻|耳朵|身高|體型|体型|身形|輪廓|轮廓|姿勢|姿势|表情|服裝|服装|衣|外套|制服|靴|帽|飾品|饰品|項鍊|项链|戒指|翅膀|尾巴",
    re.IGNORECASE)
_NONVISUAL = re.compile(
    r"\b(?:voice|accent|speaks?|speaking|speech|loyal|betray|promise|secret|believes?|remembers?|"
    r"forgets?|loves?|hates?|prefers?)\b|聲音|声音|嗓音|口音|說話|说话|語速|语速|忠誠|忠诚|背叛|秘密|信念|承諾|承诺|喜歡|喜欢|討厭|讨厌",
    re.IGNORECASE)


def visual_immutable_traits(character):
    """Conservatively retain visible clauses; Appearance remains authoritative."""
    result = []
    for trait in character.immutable_traits:
        clauses = re.split(r"[;；。\n,，]|\b(?:and|but|while)\b|而且|並且|但是", trait, flags=re.IGNORECASE)
        for clause in clauses:
            clause = clause.strip(" .。")
            if clause and _VISUAL.search(clause) and not _NONVISUAL.search(clause) and clause not in result:
                result.append(clause)
    return result


def visual_context(character):
    data = {"age": character.age, "appearance": character.appearance,
            "clothing": character.clothing, "personality": character.personality}
    traits = visual_immutable_traits(character)
    if traits:
        data["immutableTraits"] = traits
    return data


def voice_context(character):
    return {"age": character.age, "personality": character.personality,
            "speakingStyle": character.speaking_style}


def visual_prompt(character, *, reference_pack=True):
    prompt = ("Create a clean character model sheet on a neutral background, with consistent identity across "
              "full-body front, side, back and three-quarter views. No text, labels or watermarks. "
              "Use only the supplied character fields. Personality affects facial expression, posture and body language; "
              "it must not invent physical features, accessories or story scenes. Visible immutable traits constrain appearance. ")
    if reference_pack:
        prompt += "Include face close-ups and common expressions, preserving the same clothing, anatomy and proportions in every panel. "
    return prompt + "Character visual data:\n" + json.dumps(visual_context(character), ensure_ascii=False)
