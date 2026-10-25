"""Build workspace-specific SVG diagrams and matching PNG previews (no API calls)."""
from html import escape
from pathlib import Path
import textwrap

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
COLORS = {"step": ("#eff6ff", "#60a5fa"), "gate": ("#fff7ed", "#fb923c"),
          "page": ("#ecfdf5", "#34d399")}


def font(size, bold=False):
    candidates = ([Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
                   Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")])
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def diagram(name, title, nodes, note, *, connect=True):
    width, height = 1280, 100 + ((len(nodes)+2)//3)*166 + 34
    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
           f'<title>{escape(title)}</title><desc>{escape(note)}</desc>',
           '<rect width="100%" height="100%" fill="white"/>',
           '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0L10 5L0 10Z" fill="#64748b"/></marker></defs>']

    def label(x, y, text, size=20, bold=False, fill="#0f172a"):
        draw.text((x, y), text, font=font(size, bold), fill=fill, anchor="mt")
        svg.append(f'<text x="{x}" y="{y+size}" text-anchor="middle" font-family="Segoe UI,Arial,sans-serif" font-size="{size}" font-weight="{600 if bold else 400}" fill="{fill}">{escape(text)}</text>')

    label(width//2, 16, title, 30, True)
    label(width//2, 57, "Blue: work  |  Orange: human approval  |  Green: workspace handoff", 18, fill="#475569")
    positions = []
    for i in range(len(nodes)):
        row, column = divmod(i, 3)
        if row % 2:
            column = 2-column
        positions.append((40+column*410, 100+row*166))
    for (x, y), (nx, ny) in (zip(positions, positions[1:]) if connect else []):
        if y == ny:
            start, end = ((x+380, y+64), (nx, ny+64)) if nx > x else ((x, y+64), (nx+380, ny+64))
        else:
            start, end = (x+190, y+128), (nx+190, ny)
        draw.line([start, end], fill="#64748b", width=3)
        ex, ey = end
        head = [(ex, ey), (ex-9, ey-5), (ex-9, ey+5)] if end[0] > start[0] else [(ex, ey), (ex+9, ey-5), (ex+9, ey+5)] if end[0] < start[0] else [(ex, ey), (ex-5, ey-9), (ex+5, ey-9)]
        draw.polygon(head, fill="#64748b")
        svg.append(f'<path d="M{start[0]} {start[1]}L{ex} {ey}" stroke="#64748b" stroke-width="3" fill="none" marker-end="url(#arrow)"/>')
    for i, ((heading, body, kind, page), (x, y)) in enumerate(zip(nodes, positions), 1):
        fill, border = COLORS[kind]
        href = f"../README.md#{page}"
        svg.append(f'<a href="{href}"><rect x="{x}" y="{y}" width="380" height="128" rx="12" fill="{fill}" stroke="{border}" stroke-width="2"/>')
        draw.rounded_rectangle((x, y, x+380, y+128), radius=12, fill=fill, outline=border, width=2)
        headings = textwrap.wrap(f"{i}. {heading}", 28)
        for line, text in enumerate(headings):
            label(x+190, y+12+line*27, text, 23, True)
        for line, text in enumerate(textwrap.wrap(body, 36)):
            label(x+190, y+20+len(headings)*27+line*23, text, 19)
        svg.append('</a>')
    label(width//2, height-30, note, 17, fill="#475569")
    svg.append('</svg>')
    (ROOT / f"{name}.svg").write_text("\n".join(svg), encoding="utf-8")
    canvas.save(ROOT / f"{name}.png")


def overview():
    """Analytics receives data from every workspace, not just the final output."""
    canvas = Image.new("RGB", (1280, 370), "white")
    draw = ImageDraw.Draw(canvas)
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="370" viewBox="0 0 1280 370" role="img">',
           '<title>Story Studio workspace map</title><desc>Story and Settings to Assets to Animations to Final. Every page reports to Analytics.</desc>',
           '<rect width="100%" height="100%" fill="white"/>']
    def label(x, y, text, size=23, bold=False):
        draw.text((x, y), text, font=font(size, bold), fill="#0f172a", anchor="mt")
        svg.append(f'<text x="{x}" y="{y+size}" text-anchor="middle" font-family="Segoe UI,Arial,sans-serif" font-size="{size}" font-weight="{600 if bold else 400}">{escape(text)}</text>')
    label(640, 18, "Story Studio | Workspace map", 30, True)
    nodes = [("Story & Settings", "Plan and approve the story", "story-settings"),
             ("Assets", "Reusable identity and voices", "assets"),
             ("Animations", "Generate / approve scenes", "animations"),
             ("Final", "Assemble / approve video", "final")]
    for i, (title, body, page) in enumerate(nodes):
        x = 28+i*315
        draw.rounded_rectangle((x, 100, x+279, 207), radius=12, fill="#eff6ff", outline="#60a5fa", width=2)
        svg.append(f'<a href="../README.md#{page}"><rect x="{x}" y="100" width="279" height="107" rx="12" fill="#eff6ff" stroke="#60a5fa" stroke-width="2"/>')
        label(x+139, 118, title, 23, True)
        label(x+139, 163, body, 18)
        svg.append('</a>')
        if i < 3:
            draw.line((x+280, 152, x+308, 152), fill="#64748b", width=3)
            draw.polygon([(x+308, 152), (x+299, 147), (x+299, 157)], fill="#64748b")
            svg.append(f'<path d="M{x+280} 152h28l-9 -5m9 5l-9 5" fill="none" stroke="#64748b" stroke-width="3"/>')
        draw.line((x+139, 208, x+139, 266), fill="#34a887", width=2)
        draw.polygon([(x+139, 266), (x+134, 257), (x+144, 257)], fill="#34a887")
        svg.append(f'<path d="M{x+139} 208v58l-5 -9m5 9l5 -9" fill="none" stroke="#34a887" stroke-width="2"/>')
    draw.rounded_rectangle((28, 267, 1252, 342), radius=12, fill="#ecfdf5", outline="#34d399", width=2)
    svg.append('<a href="../README.md#analytics"><rect x="28" y="267" width="1224" height="75" rx="12" fill="#ecfdf5" stroke="#34d399" stroke-width="2"/>')
    label(640, 275, "Analytics", 24, True)
    label(640, 310, "Generation usage, request attribution and separate billing reports across every workspace", 18)
    svg.extend(['</a>', '</svg>'])
    canvas.save(ROOT / 'workspace-flow.png')
    (ROOT / 'workspace-flow.svg').write_text('\n'.join(svg), encoding='utf-8')


def main():
    definitions = [
        ("workflow", "Story & Settings | Creative workflow", [
            ("Project + outline", "Title, language, target length", "step", "story-settings"),
            ("World + Characters", "Establish canon and cast", "step", "story-settings"),
            ("Story", "Generate original story content", "step", "story-settings"),
            ("AI Review (Story)", "Edit, apply/revert, save and approve", "gate", "story-settings"),
            ("Script + Dialogue", "Review dialogue and draft intent", "step", "story-settings"),
            ("Storyboard", "Plan scenes, shots and camera", "step", "story-settings"),
            ("AI Review (Storyboard)", "Edit, apply/revert or keep original", "gate", "story-settings"),
            ("Prompts", "Generate visual instructions", "step", "story-settings"),
            ("Go to Assets", "Character references can start early", "page", "assets"),
        ], "Story & Settings owns creative content. Characters unlock early references; other work needs approved planning."),
        ("assets-workflow", "Assets | Reusable references and voices", [
            ("From Story & Settings", "Characters ready; later gates apply", "page", "story-settings"),
            ("Identity + shared style", "Locations, costumes, props; 3 images", "step", "assets"),
            ("Character references", "Generate and approve appearance", "gate", "assets"),
            ("Background references", "After Prompts: generate and approve", "gate", "assets"),
            ("Animations: build plan", "Prepare direction, then return here", "page", "animations"),
            ("Assets: auditions", "Approve voices + library; continue", "gate", "assets"),
        ], "After auditions, continue on Animations for scene production. Voice auditions require the storyboard and plan."),
        ("animation-workflow", "Animations | Individual scene and shot production", [
            ("Approved inputs", "Storyboard + Assets reference packs", "page", "assets"),
            ("Plan + acting intent", "Build direction; audition on Assets", "step", "animations"),
            ("Approve direction", "Current references, voices, acting", "gate", "animations"),
            ("Scene images + voices", "Generate dialogue and alignment", "step", "animations"),
            ("Approve scene inputs", "Review consistency and asset batch", "gate", "animations"),
            ("Generate animated shots", "Kling; Act-Two only when selected", "step", "animations"),
            ("Sound + VFX layers", "SFX, ambience, independent effects", "step", "animations"),
            ("Approve scenes", "Continuity QA + each composed scene", "gate", "animations"),
            ("Go to Final", "Hand off current approved scenes", "page", "final"),
        ], "Retries retain successful work. Chaining reuses cut frames. Scene generation does not assemble the final video."),
        ("render-workflow", "Final | Assembly and final review", [
            ("From Animations", "Every current scene approved", "page", "animations"),
            ("Timeline + subtitles", "Review order, timing and captions", "step", "final"),
            ("Mix + output settings", "Changes? Recompose on Animations", "step", "final"),
            ("Assemble final video", "Use approved media; no model calls", "step", "final"),
            ("Preview + download", "MP4, SRT/VTT, timeline exports", "step", "final"),
            ("Approve final video", "Local approval; does not publish", "gate", "final"),
        ], "Final assembles approved scenes. Changed composition settings send affected scenes back to Animations."),
        ("analytics-workflow", "Analytics | Usage, attribution and billing", [
            ("Usage + request IDs", "Attempts attributed to each story", "step", "analytics"),
            ("Provider statistics", "Calls, failures, configured estimates", "step", "analytics"),
            ("OpenAI billed reports", "Separate admin API / snapshots", "step", "analytics"),
        ], "Analytics covers every workspace. Runway/ElevenLabs invoices and live audience/revenue feeds are not connected."),
    ]
    for args in definitions:
        diagram(*args, connect=args[0] != "analytics-workflow")
    overview()
    print(f"Generated {len(definitions)+1} workspace diagrams as PNG + SVG.")


if __name__ == "__main__":
    main()
