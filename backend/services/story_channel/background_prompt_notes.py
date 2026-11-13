"""Keep appearance notes without obsolete panorama/camera directions."""
import re


def clean_text(text):
    return re.sub(r"\bsoil\s*[?？]", "", text, flags=re.I)



def content_notes(text):
    # Existing projects may still have a generated panorama prompt saved here.
    # Keep appearance notes but remove incompatible camera/projection sentences.
    conflict = re.compile(r"panorama|equirectangular|spherical|360|180|camera|yaw|"
                          r"viewing direction|image center|start.end|seam|edges?.{0,30}(?:join|connect)|"
                          r"same (?:framing|composition|viewpoint)|(?:keep|preserve).{0,20}(?:framing|composition)|"
                          r"全景|球面|鏡頭|镜头|相機|相机|畫面方向|画面方向|軸線|轴线|接縫|接缝|拼接|保持.*構圖|保留.*構圖", re.I)
    parts = re.split(r"\n+|(?<=[.!?。！？])\s*", text)
    return clean_text(" ".join(part.strip() for part in parts if part.strip() and not conflict.search(part)))

