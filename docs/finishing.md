# Lip-sync and generated effects

These features are available in **Animations → Video, Lip-sync & SFX / VFX → Lip-sync, generated SFX and VFX layers** after the scene input batch is approved. They are off by default and make paid requests only when explicitly enabled and generated.

## Set up provider keys

Fill in the new fields in the local `.env`, then restart the app:

```dotenv
ELEVENLABS_API_KEY=your_elevenlabs_key
```

SFX uses [ElevenLabs sound generation](https://elevenlabs.io/docs/api-reference/text-to-sound-effects/convert). OpenAI VFX uses the existing `OPENAI_API_KEY` and video adapter. Keys are read from the environment, not stored in project JSON or request logs. Downloads do not receive provider authentication headers.

Optional USD estimates use `STORY_LIP_SYNC_COST_PER_SECOND`, `STORY_SFX_COST_PER_SECOND` and the existing `STORY_VIDEO_COST_PER_SECOND`. Set each to the applicable numeric rate for your account/model. Missing rates appear as unknown. These estimates do not fetch Sync/ElevenLabs invoices, and their charges do not belong to the OpenAI billed-cost panel. The ElevenLabs `character-cost` response is retained separately as billing units, not incorrectly interpreted as USD.

## Sync status

Sync credentials and UI controls are removed for now. Legacy implementation and history remain for compatibility; no Sync setup is required. For an older enabled plan, save finishing settings in the UI to turn lip-sync off.

## Generate SFX and ambience

Choose **ElevenLabs generated SFX / ambience** and save finishing settings. Existing sound cues come from Storyboard and performance direction. Under **Music and sound effects**, the JSON supports a `prompt` on every cue; if empty, generation uses its name.

```json
{
  "name": "footsteps",
  "prompt": "Slow heavy footsteps on wet concrete in an empty tunnel",
  "at": 1.0,
  "duration": 2.0,
  "loop": false,
  "file": null,
  "source": "Procedurally synthesized locally; no third-party recording"
}
```

`file: null` selects generation; an existing licensed `file` is retained and is not overwritten. The source text above is the legacy placeholder default; generated-media provenance is stored in the separate finishing asset record with provider, model, prompt, duration, request IDs and cost information. A custom uploaded file must continue to record its actual source/license.

ElevenLabs requests use 0.5–30 seconds. Short cues request the minimum and are trimmed in the mix. Longer ambience uses a looping generated sample of at most 30 seconds; a non-looping generated cue longer than 30 seconds is rejected. Timing stays relative to the scene's approved voice duration. Generated sounds remain independent inputs to the mix, and the original sound plan remains editable.

## Generate and composite VFX

Use **Add an effect layer** to choose a scene, type, start, duration and opacity, then add it to the draft and save. Layers must fit within the recorded scene duration. More than one layer can be composed over a scene in plan order.

| Type | Generation and composition |
|---|---|
| Rain, snow, smoke, sparks, fire, lightning, energy, HUD | Local procedural RGBA animation, saved as an alpha-capable MOV. Uses kind/intensity/seed, not an AI interpretation of text. No API charge. |
| OpenAI | An isolated effect requested on a green background, saved separately from the base scene. The text prompt describes the effect. FFmpeg chroma-keys green and applies the chosen opacity/timing. Uses API credits. |

Generated AI video layers use a 4-, 8- or 12-second clip; longer layer windows loop the effect texture. Base animation is never overwritten by a layer. Local effect previews show the transparent effect against black; AI previews show the raw green-screen output. Review generated green-screen quality: green spill, opaque smoke or an imperfectly flat background can need prompt changes and regeneration. This is not an automatic professional VFX matte guarantee.

**Modify with OpenAI** on an existing AI layer opens a prompt dialog and edits that layer's saved remote video. The base animation is preserved. The plan editor also supports prompt-guided OpenAI modification, typo suggestions with Apply, Save and adjacent Revert; unsaved edits survive workspace navigation.

## Retry, stop, history and costs

- Check items in the finishing table. **Generate / retry selected** reuses intact cached media and resumes saved jobs. **Regenerate selected** deliberately creates new versions.
- Use **Generate scenes / shots** to apply current enabled finishing and recompose scenes. Changing effect opacity/timing reuses the generated layer; changing its generation inputs invalidates that layer. Sound prompt changes regenerate the affected cue. Base clips remain separately cached.
- Finishing runs one task at a time. **Stop after running tasks** finishes the current task and preserves completed media; Resume reuses it. Ordinary asset generation retains its existing five-task maximum.
- Sync submission uses a persisted idempotency key, and accepted remote job IDs are saved before polling. [Fresh download URLs](https://sync.so/docs/api-reference/api/generate-api/download) are resolved without another generation. Polling/resuming is not counted as a second generation charge.
- Synchronous sound requests have no documented idempotency guarantee; a lost/failed response is recorded and requires an explicit retry rather than automatic duplicate submissions. OpenAI VFX automatically resumes acknowledged jobs; an unacknowledged submission failure also requires explicit retry.
- Every attempt, including errors, records usage and available request/job IDs against the Story Studio project. Missing usage/prices stay unknown. Files and JSON revisions are retained, and changed finishing invalidates scene/final approval and animated continuity approval.

State is stored in `finishing.json`, with `finishing_plan.json`/`.md` exports and `history/finishing*` records. Sound binaries are under `audio/finishing/`; lip-sync/VFX binaries and previews are under `videos/finishing/`. Final `timeline.json` includes finishing provenance.

## CLI

```powershell
# Inspect current settings and generated records without an API request.
.\.venv\Scripts\story-channel finishing YOUR_PROJECT_ID show

# Validate and save an edited FinishingPlan JSON.
.\.venv\Scripts\story-channel finishing YOUR_PROJECT_ID save --file finishing-edited.json

# Generate/retry chosen items, preserving intact successes; repeat --item for a batch.
.\.venv\Scripts\story-channel finishing YOUR_PROJECT_ID generate --item vfx:rain_layer

# Explicitly force a new version of an item.
.\.venv\Scripts\story-channel finishing YOUR_PROJECT_ID regenerate --item vfx:rain_layer

# Compose the result using enabled lip-sync, sounds and VFX.
.\.venv\Scripts\story-channel render YOUR_PROJECT_ID run
```

Finishing schema exports are included in `schemas DIRECTORY --production`. Provider tests mock the official HTTP contracts; encoding tests use real local FFmpeg. No paid provider quality evaluation is performed by the test suite.

Validation: **239 regression tests passed**, plus **2 additional sound-failure checks** covering uncertain submissions, failed media validation, retained costs and prevention of automatic duplicate requests.
