# Specification alignment: v0.3

Reviewed 2026-10-23 against the newly supplied technical specification.
Source: `D:/AI_video_creator/doc/AI 故事影音自動化系統－技術開發規格書.docx`.
SHA-256: `18c92da0c88e7166578f0a07bd7d3ae306327504d95dd782fa80de9d44bd1d23`.

The user authorized alignment of Phase 0-3 and the minimum balanced production stack. The document supplies requirements; it does not authorize paid quality benchmarks, publishing, or credential disclosure.

The 2026-10-25 workspace request supersedes the original page layout and stage order in this audit. The current pages are **World, Assets, Story/Episode, Animations, Final and Analytics**. World approval freezes the base canon; reviewed additions extend it. Episodes retain independent approvals and approved shared-asset snapshots. See the [current workflow](../README.md#workspace-map) for the authoritative UI order. Cross-episode event canon remains TODO.

## Phase-by-phase audit

Backgrounds offer DETAIL and SIMPLE. DETAIL uses floor plan + object layout -> North/South camera regions -> two North/South master images -> approve pair. Masters use OpenAI at 2560x1440, with one shared prompt and paired generation/revision/approval controls. Each request includes only its visible camera guide, floor plan and object metadata, plus shared style settings. Optional local detail backgrounds supplement this pair without redefining geometry. SIMPLE uses a detail prompt and optional guidance image to generate one OpenAI master at 1536x864, then approve it; no floor-plan/camera-guide prerequisites. Both types support revisions and shared style rules/reference. The resolver selects the approved SIMPLE master directly, or the nearest DETAIL master plus relevant details (maximum three references). Type changes preserve media and reopen approval. Existing locations remain DETAIL; legacy ONE_TIME registrations retain their prior DETAIL migration. Existing episode bundles remain frozen until explicitly refreshed.

| Phase | v0.3 requirement | Current implementation / update | Acceptance boundary |
| --- | --- | --- | --- |
| 0 | Project skeleton, settings, manifests, common models, provider interfaces, persistence, logging and cost tracking | Retained the existing project services, atomic writes, locks, schema export and request attribution. Added credential-safe Runway/ElevenLabs adapters, durable media task IDs and provider-specific optional price settings. | Automated persistence/contract checks; no secret values in project outputs or request logs. |
| 1 | World, characters, story, natural dialogue, review, storyboard/shot planning, initial performance intent and resumable approvals | Retained all creative modules, dialogue reviewer, Script performance-intent export, stage edits/history/retries and story/storyboard gates. New default LLM is GPT-6.1 Sol (`gpt-6.1-sol`), using the existing structured Responses API. | Existing pinned projects retain their selected LLM until changed in Generation preferences. Dialogue review remains an explicit action; production acting intent needs review. |
| 2 | Reusable character/location/costume/prop/voice identities before final scene production | Reusable identities remain on Assets. Characters/backgrounds select exclusive text or design-image mode; the UI displays the actual Runway inputs. Optional OpenAI design references are generated separately and record project usage. Character source editing is labeled Character detail. Every location follows the two-master flow above; optional linked local details supplement shot references. Native 2:1 panorama uploads, rotating preview, seam inspection and per-revision coverage review are supported. Scenes reuse the approved panorama. No global style input is required. Gen-4 Image is the production reference adapter; input sets over its three-image limit become a numbered contact sheet. | Review character identity and panorama seams/room geometry manually. General image providers do not guarantee 360-degree coverage or seamless geometry. Ordinary wide images are retained without stretching and cannot be approved as spherical panoramas. Costume and prop-mode visuals are generated explicitly; registry entries alone do not imply those files exist. |
| 3 | Every scene becomes reference-backed animation, with shared performance direction, interactions, voices, SFX, independent VFX, chaining, QA and final composition | Production defaults now use Kling O3 Pro through Runway, selectively uploaded Act-Two driving performances, Eleven v4 voices, ElevenLabs SFX and FFmpeg. Saved editable direction/performance, cut-frame chaining, per-shot retries, Stop, scene approval and separate Final assembly are retained. | Integration contracts and local composition are tested with mocked providers. Real face/voice/acting/continuity acceptance requires paid provider output and human review; no such benchmark was run. |

## Minimum balanced production stack

| Role | Model / service | Credential |
| --- | --- | --- |
| Story / Script / Performance metadata / provider-prompt shortening | GPT-6.1 Sol (`gpt-6.1-sol`) | `OPENAI_API_KEY` |
| Character reference images | OpenAI GPT Image 2.5 Flare (`gpt-image-2.5-flare`) | `OPENAI_API_KEY` |
| Scene images with cast/background/prop references | OpenAI GPT Image 2.5 Sunburst (`gpt-image-2.5-sunburst`) | `OPENAI_API_KEY` |
| North/South masters and optional local details | OpenAI GPT Image 2.5 Flare (`gpt-image-2.5-flare`) | `OPENAI_API_KEY` |
| Main reference-backed animation | Kling O3 Pro (`klingO3_pro`) through Runway Dev | `RUNWAYML_API_SECRET` |
| Selected important dialogue close-ups | Runway Act-Two (`act_two`) | `RUNWAYML_API_SECRET` |
| Character / narrator voices | ElevenLabs Eleven v4 (`eleven_v4`) | `ELEVENLABS_API_KEY` |
| SFX / ambience | ElevenLabs Sound Effects (`eleven_text_to_sound_v2`) | `ELEVENLABS_API_KEY` |
| Alignment | Existing Whisper alignment adapter | `OPENAI_API_KEY` |
| Timeline / audio mixing / subtitles / layered VFX / final assembly | FFmpeg | None |
| Historical corrective lip-sync | Legacy Sync adapter/history only; absent from current setup/UI | Not required |
| OpenAI account billing reports | OpenAI Admin Costs API | Optional `OPENAI_ADMIN_KEY` |

Runway Dev is the verified API transport for Kling O3 Pro in this implementation. This does not add an automatic model Router or require native Kling credentials. Gemini/Veo, MiniMax and Adobe credentials are not required by this minimum stack. Existing OpenAI image/video/TTS adapters and local offline previews remain available for explicit legacy use; they are not the new production defaults.

## Production workflow and constraints

1. World and Story/Episode: approve immutable World settings, then create independently titled/timed episode outlines. Review and approve Outline, Story, Script and Storyboard before animation. Storyboard approval compiles internal prompts locally and moves the episode to READY TO ANIMATE; there is no separate Prompts tab or approval. Reviewed World additions are append-only; existing episodes adopt them explicitly.
2. Assets: generate character references and four center-facing location views, configure actual ElevenLabs Voice IDs (including a narrator ID), audition/approve voices and approve the identity library. Costume visuals and separate prop-mode visuals are available. Episodes import an approved shared snapshot and refresh it explicitly. Provider voice names such as `alloy` and `nova` are not ElevenLabs IDs; production never guesses a replacement.
3. Animations: generate scene snapshots, build/refine/save per-scene direction and shared performance (no direction approval gate), generate/approve silent animated scenes, then generate voices/alignment/input QA. Generate configured video/audio effects and compose/review scenes before Final. For important single-character dialogue close-ups only, upload a 3-30 second driving MP4 in the Act-Two panel and select that shot. The plan must identify its performer and close-up camera; split long or multi-character performances into individual short shots. The driving video must cover the requested generation duration. Attaching/changing it invalidates dependent generated clips and continuity review. Driving-file hashes bind cached outputs to the supplied bytes.
4. Kling receives the approved first frame plus character/location image references. Compatible adjacent cuts pass their ending frame to the next shot. Each cut is capped at eight seconds, with motion handles trimmed in composition. Runway submission task IDs are persisted before polling; retries retrieve saved tasks instead of resubmitting paid jobs. Unknown initial-submission outcomes fail explicitly rather than automatically resubmitting.
5. Voice rendering fixes the Voice ID, applies supported emotion tags and pace, converts output to WAV and applies volume and real pauses before alignment. General emotional/subtext quality still needs listening. OpenAI voice modification refines structured delivery metadata and re-synthesizes with the same ElevenLabs identity.
6. Runway's provider prompt limits are enforced. Oversized production prompts are condensed by the structured LLM with separately recorded usage instead of silently dropping identity/performance instructions. Image reference packing and submitted prompt text remain in generation provenance.
7. SFX/ambience are separate generated audio layers. VFX remains independent local transparent layers or explicitly selected legacy OpenAI effect footage. Sync lip-sync is optional corrective post-processing, not a core prerequisite. Enable it only for changed dubbing, multilingual speech, demanding mouth close-ups or QA-identified mismatch.
8. Review continuity and each scene on Animations; Final only assembles current approved media, then requires final human approval. It does not invoke model generation.

## Credentials and costs

The top-right key dialog edits the three production secrets plus optional admin/Sync keys. Values are masked replacement inputs; blanks preserve existing keys, Clear explicitly removes them. They persist only in local `.env` and the backend process environment. Existing credentials, production media, request history and billing allocations are preserved.

Old OpenAI rates are not applied to new providers. Optional model/account-specific USD rates are `STORY_RUNWAY_IMAGE_COST_PER_IMAGE`, `STORY_RUNWAY_VIDEO_COST_PER_SECOND`, `STORY_ACT_TWO_COST_PER_SECOND`, `STORY_ELEVENLABS_TTS_COST_PER_MILLION_CHARACTERS` and the existing SFX rate. Rough project estimates can use fallback rates; missing usable usage remains Unknown. OpenAI billed reports do not include Runway or ElevenLabs invoices. Upload/poll/download requests are attributed, while generation units are charged only for newly submitted tasks.

## Later work and honest quality limits

Automatic provider routing and benchmarks are Phase 4; series Canon memory is Phase 5; Shorts/publishing/audience feedback is Phase 6. This update does not claim those features. Kling first/last keyframes are supported: set a shot's `lastFrame` to a project-local approved image path in the direction JSON. The terminal frame is sent only for the shot's final generated part, and its hash binds dependent clip caches and media reviews. Native clip-extension controls are not implemented; longer scenes currently use generated short cuts with first-frame chaining. Multiple dialogue takes have version history/emotion auditions, not a dedicated best-take selector. Audience metrics, separate color grading, automatic cinematic quality scoring, multi-character Act-Two compositing and driving-performance capture remain outside this implementation. Real-provider acceptance is still required before marking cinematic Phase 3 quality fully accepted.

## Verified primary API contracts

- [OpenAI GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol): exact model ID and structured output capability.
- [Runway API reference](https://docs.dev.runwayml.com/api/): `/v1/text_to_image`, `/v1/image_to_video`, `/v1/character_performance`, ephemeral uploads and task retrieval; API version `2024-11-06`.
- [Runway model list](https://docs.dev.runwayml.com/guides/models/): Gen-4 Image, Kling O3 Pro and Act-Two.
- [ElevenLabs speech API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert): fixed voice endpoint, model ID, voice settings and encoded audio output.
- [ElevenLabs models](https://elevenlabs.io/docs/overview/models): Eleven v4.


## Subsequent credential change

At the user's request, Sync was removed from the key dialog, local `.env`, example configuration and finishing UI. Its legacy adapter/history remain for compatibility. The production keys are OpenAI, Runway and ElevenLabs; OpenAI admin billing is optional.

Assets prerequisite policy: at least one character definition and one background definition is sufficient. There is no full-library completion stage. Episode scene/reference tasks and shared import checks use only the characters and backgrounds actually referenced by that episode; unused drafts and their missing voices do not block production. Actual scene media, voice-audition and final-output approvals remain checked at their own stages.
