# Scene effects and composition

Effects belong to [Animations](../README.md#animations). The current tabs are **Video effects**, **Sound effects**, and **Scene review & continuity**. Generate and approve animated scenes, then complete **Voices & input** before configuring timed effects. Final assembles approved composed scenes.

## Video effects

Add layers on **Video effects**. Local rain, snow, smoke, sparks, fire, lightning, energy and HUD layers are procedural transparent animations. Their kind, intensity and seed determine the result. OpenAI VFX layers use a prompt to generate an isolated effect on green; FFmpeg applies chroma key, timing and opacity during composition. Preview layers before approving a finished scene.

OpenAI layers use `OPENAI_API_KEY` and the existing video adapter. Changing layer timing/opacity reuses generated media; changing generation inputs requires a new layer. Multiple layers remain separate from the approved base visual. Use the service/model-labeled Modify button for supported existing AI layers.

Sync credentials and controls have been removed. Its adapter and old records remain readable for historical projects. If an old plan still enables Sync, save Video effects settings to disable it. Sync is not required by the current pipeline. Talking character samples combine speech and visual motion; they do not certify exact lip synchronization.

## Sound effects

Use **Sound effects** to select local placeholders or ElevenLabs generated SFX/ambience. Save the provider, edit cue descriptions/timing, and select cues to generate or regenerate. Production uses `ELEVENLABS_API_KEY`; save it through the key dialog or local `.env`.

Describe the source, action and texture, for example "short metal latch click, dry close-up, no voices." Avoid dialogue or instructions intended to be spoken. Non-English descriptions are translated before generation and translation usage is recorded. Preview every result: prompt constraints cannot guarantee sound matching or the absence of speech.

A licensed cue's existing `file` is retained. Set `file: null` to generate one. Record the actual license/source for uploaded audio. Use looping ambience for continuous backgrounds and sufficient duration for discrete actions. Licensed audio on the parent Assets page is copied with the episode's shared asset snapshot.

## Compose and approve

On **Scene review & continuity**, use **Compose scenes (voices & effects)**. Composition fits approved silent visuals to voice timing and adds subtitles, sound and enabled VFX without another base-video generation request. Review timing, delivery, effects and continuity. Approve each current scene or all scenes, then go to **Final** for **Assemble final video**.

The waiting table shows queued/running tasks for pipeline actions. Stop finishes running work and retains completed media. Retry reuses intact cached results and acknowledged remote jobs; Regenerate creates a revision. Finishing tasks run one at a time. Provider requests share server limits of OpenAI 2, Runway 1 and ElevenLabs 2. Independent sample/design jobs may overlap asset generation; dependent production stages and character/background image batches remain sequential.

Every attempt records available usage and request/job IDs against the project or episode. Rough USD estimates use recorded cost, configured rates, then available fallback rates. Unknown usage is not presented as an invoice. Provider account totals remain separate from project estimates.

State lives in `finishing.json`, `finishing_plan.json/.md`, and `history/finishing*`. Audio is under `audio/finishing/`; generated layers are under `videos/finishing/`. Final `timeline.json` retains provenance. Changed inputs reopen affected approvals.

## CLI

```powershell
# Inspect saved episode finishing state.
.\.venv\Scripts\story-channel finishing EPISODE_ID show

# Validate and save a modified FinishingPlan JSON file.
.\.venv\Scripts\story-channel finishing EPISODE_ID save --file finishing-edited.json

# Retry a layer, retaining intact output; repeat --item for a batch.
.\.venv\Scripts\story-channel finishing EPISODE_ID generate --item vfx:rain_layer

# Deliberately generate a new layer revision.
.\.venv\Scripts\story-channel finishing EPISODE_ID regenerate --item vfx:rain_layer

# Compose scene media, review it, then explicitly approve and assemble.
.\.venv\Scripts\story-channel render EPISODE_ID run
.\.venv\Scripts\story-channel render EPISODE_ID approve-scenes
.\.venv\Scripts\story-channel render EPISODE_ID assemble
```

CLI composition obeys the same asset/direction/visual gates; it does not grant scene approval. Finishing contracts are included in `schemas DIRECTORY --production`. Tests mock remote providers and use local FFmpeg; paid output quality still needs human review.
