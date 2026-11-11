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
    diagram("workspace-flow", "Story Studio | Workspace map", [
        ("World", "Review and approve shared canon", "gate", "world"),
        ("Assets", "Characters, backgrounds, voices, props", "page", "assets"),
        ("Story/Episode", "Outline, Story, Script, Storyboard", "page", "story-episode"),
        ("Animations", "Approve individual animated scenes", "page", "animations"),
        ("Final", "Assemble and approve the finished video", "page", "final"),
        ("Analytics", "Usage and billing across all workspaces", "page", "analytics"),
    ], "World and Assets are shared. Episodes keep independent approvals and production outputs.")


def main():
    definitions = [
        ("world-workflow", "World | Shared immutable canon", [
            ("World settings fields", "Seven essential points in one JSON", "step", "world"),
            ("AI Review", "Required worldview and typo review", "gate", "world"),
            ("Apply / Revert", "Edit suggestion manually if needed", "step", "world"),
            ("Approve World", "Freeze base canon; unlock production", "gate", "world"),
            ("Draft settings list", "Add settings; Edit / AI Review / Approve", "step", "world"),
            ("AI Review additions", "Check base and approved additions", "gate", "world"),
            ("Apply / Revert", "Edit suggestion manually if needed", "step", "world"),
            ("Approve addition", "Verify selected text; append only", "gate", "world"),
        ], "Existing episodes retain canon snapshots until the user explicitly includes new approved additions."),
        ("workflow", "Story/Episode | Content and approval gates", [
            ("Episode outline", "Title, outline, target HH:MM:SS", "step", "story-episode"),
            ("Review and approve", "AI review or prompt-guided suggestions", "gate", "story-episode"),
            ("Story", "Hook through Ending; AI Review (Story)", "gate", "story-episode"),
            ("Script", "Generate, review, edit and approve", "gate", "story-episode"),
            ("Storyboard", "AI Review (Storyboard); approve", "gate", "story-episode"),
            ("Prepare prompts", "Automatic local compilation; no extra gate", "step", "story-episode"),
            ("Ready to animate", "Storyboard approved; prompts ready", "page", "animations"),
        ], "Outline changes reopen episode content; shared World and reusable assets are retained."),
        ("assets-workflow", "Assets | Reusable world-owned library", [
            ("Approved World", "Canon guides every reusable asset", "page", "world"),
            ("Style rules", "Shared rules and optional style image", "step", "assets"),
            ("Character References", "Text or image -> model sheet; Voice; Costumes", "step", "assets"),
            ("Location layout", "Floor plan + objects -> camera view regions", "step", "assets"),
            ("North / south masters", "Visible camera regions; 2560x1440; two outputs", "step", "assets"),
            ("Approve master pair", "Review layout, objects and shared style", "gate", "assets"),
            ("Optional local details", "Stable area IDs; linked objects; upload or AI", "step", "assets"),
            ("Location resolver", "Nearest master + relevant details; 1-3 refs", "step", "animations"),
            ("SFX and Props (optional)", "Libraries and mode visuals; no required gate", "step", "assets"),
            ("Style check (optional)", "New/updated targets; unchanged art is context", "step", "assets"),
            ("Assets prerequisite done", "At least 1 character + 1 background exist", "gate", "assets"),
            ("Reuse in episodes", "Copy approved media; independent state", "page", "story-episode"),
        ], "No complete-assets stage. Unused drafts never block an episode; actual scene references are checked when used."),
        ("animation-workflow", "Animations | Ordered scene production", [
            ("Scene images", "Resolve 1-3 angle/area references per shot", "step", "animations"),
            ("Direction / performance", "Build, browse and save edits", "step", "animations"),
            ("Animated scenes", "Generate motion; automatically approved", "step", "animations"),
            ("Voices and input", "Dialogue, alignment and input QA", "gate", "animations"),
            ("VFX", "Episode VFX inventory and previews", "step", "animations"),
            ("SFX", "Episode SFX / ambience inventory", "step", "animations"),
            ("Scene review / continuity", "OpenAI sampled-frame continuity review", "gate", "animations"),
            ("Go to Final", "Hand off reviewed animations and inputs", "page", "final"),
        ], "Approved visuals are fitted to recorded voices without another video API request. Review timing before Final."),
        ("render-workflow", "Final | Assembly and final review", [
            ("From Animations", "Current animations and AI continuity report", "page", "animations"),
            ("Timeline / subtitles", "Compose audio/effects; review finished scenes", "step", "final"),
            ("Mix / output settings", "Recompose affected scenes if changed", "step", "final"),
            ("Assemble final video", "Approved media; progress shown", "step", "final"),
            ("Preview / download", "MP4, SRT/VTT and timeline exports", "step", "final"),
            ("Approve final video", "Local approval; does not publish", "gate", "final"),
        ], "Final keeps the existing composition and approval workflow; no generation API calls during assembly."),
        ("analytics-workflow", "Analytics | Usage, attribution and billing", [
            ("Usage / request IDs", "Every attempt attributed to its episode", "step", "analytics"),
            ("Provider statistics", "Costs, failures and retries", "step", "analytics"),
            ("World project cost", "Shared assets plus episode costs", "step", "analytics"),
            ("OpenAI billed reports", "Account reports remain separate", "step", "analytics"),
        ], "Audience and revenue metrics require a connected publishing analytics source."),
    ]
    for args in definitions:
        diagram(*args, connect=args[0] != "analytics-workflow")
    overview()
    print(f"Generated {len(definitions)+1} workspace diagrams as PNG + SVG.")


if __name__ == "__main__":
    main()
