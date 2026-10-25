# Specification alignment: v0.3

Reviewed 2026-10-23 against the newly supplied technical specification.
Source: `D:/AI_video_creator/doc/AI 故事影音自動化系統－技術開發規格書.docx`.
SHA-256: `18c92da0c88e7166578f0a07bd7d3ae306327504d95dd782fa80de9d44bd1d23`.

The user authorized alignment of Phase 0-3 and the minimum balanced production stack. The document supplies requirements; it does not authorize paid quality benchmarks, publishing, or credential disclosure.

## Phase-by-phase audit

| Phase | v0.3 requirement | Current implementation / update | Acceptance boundary |
| --- | --- | --- | --- |
| 0 | Project skeleton, settings, manifests, common models, provider interfaces, persistence, logging and cost tracking | Retained the existing project services, atomic writes, locks, schema export and request attribution. Added credential-safe Runway/ElevenLabs adapters, durable media task IDs and provider-specific optional price settings. | Automated persistence/contract checks; no secret values in project outputs or request logs. |
| 1 | World, characters, story, natural dialogue, review, storyboard/shot planning, initial performance intent and resumable approvals | Retained all creative modules, dialogue reviewer, Script performance-intent export, stage edits/history/retries and story/storyboard gates. New default LLM is GPT-6.1 Sol (`gpt-6.1-sol`), using the existing structured Responses API. | Existing pinned projects retain their selected LLM until changed in Story & Settings. Dialogue review remains an explicit action; production acting intent needs review. |
| 2 | Reusable character/location/style/costume/prop/voice identities before final scene production | Reusable identity library, stable IDs, reference contact sheets, up to three shared style images, approved backgrounds and voice profiles remain on Assets. Gen-4 Image is now the production reference adapter. All references are retained: input sets over Gen-4's three-image limit become a numbered contact sheet. | Images remain human-reviewed reference packs, not automatic identity certification. Costume/prop registry entries do not imply separately generated images for every item. |
| 3 | Every scene becomes reference-backed animation, with shared performance direction, interactions, voices, SFX, independent VFX, chaining, QA and final composition | Production defaults now use Kling O3 Pro through Runway, selectively uploaded Act-Two driving performances, Eleven v4 voices, ElevenLabs SFX and FFmpeg. Reference and performance approvals, cut-frame chaining, per-shot retries, Stop, scene approval and separate Final assembly are retained. | Integration contracts and local composition are tested with mocked providers. Real face/voice/acting/continuity acceptance requires paid provider output and human review; no such benchmark was run. |

## Minimum balanced production stack

| Role | Model / service | Credential |
| --- | --- | --- |
| Story / Script / Performance metadata / prompt compilation | GPT-6.1 Sol (`gpt-6.1-sol`) | `OPENAI_API_KEY` |
| Character / Location / Style reference images | Runway Gen-4 Image (`gen4_image`) | `RUNWAYML_API_SECRET` |
| Main reference-backed animation | Kling O3 Pro (`klingO3_pro`) through Runway Dev | `RUNWAYML_API_SECRET` |
| Selected important dialogue close-ups | Runway Act-Two (`act_two`) | `RUNWAYML_API_SECRET` |
| Character / narrator voices | ElevenLabs Eleven v4 (`eleven_v4`) | `ELEVENLABS_API_KEY` |
| SFX / ambience | ElevenLabs Sound Effects (`eleven_text_to_sound_v2`) | `ELEVENLABS_API_KEY` |
| Alignment | Existing Whisper alignment adapter | `OPENAI_API_KEY` |
| Timeline / audio mixing / subtitles / layered VFX / final assembly | FFmpeg | None |
| Optional corrective lip-sync | Existing Sync adapter, disabled by default | Optional `SYNC_API_KEY` |
| OpenAI account billing reports | OpenAI Admin Costs API | Optional `OPENAI_ADMIN_KEY` |

Runway Dev is the verified API transport for Kling O3 Pro in this implementation. This does not add an automatic model Router or require native Kling credentials. Gemini/Veo, MiniMax and Adobe credentials are not required by this minimum stack. Existing OpenAI image/video/TTS adapters and local offline previews remain available for explicit legacy use; they are not the new production defaults.

## Production workflow and constraints

1. Story & Settings: use the selected structured LLM to create/review World, Characters, Story, Script, Storyboard and Prompts. Keep Story and Storyboard human approval gates.
2. Assets: build the identity library, set actual ElevenLabs Voice IDs (including a narrator ID), generate/review character and location references, approve the library and audition the voices. Provider voice names such as `alloy` and `nova` are not ElevenLabs IDs; production never guesses a replacement.
3. Animations: build/refine/approve shared Performance and shot direction (Generate performance direction with OpenAI creates shared acting metadata using the selected story LLM), generate scene images and dialogue tracks, then generate reference-backed animated shots. For important single-character dialogue close-ups only, upload a 3-30 second driving MP4 in the Act-Two panel and select that shot. The plan must identify its performer and close-up camera; split long or multi-character performances into individual short shots. The driving video must cover the requested generation duration. Attaching/changing it invalidates direction and continuity approval. Driving-file hashes bind approval to the reviewed bytes.
4. Kling receives the approved first frame plus character/location image references. Compatible adjacent cuts pass their ending frame to the next shot. Each cut is capped at eight seconds, with motion handles trimmed in composition. Runway submission task IDs are persisted before polling; retries retrieve saved tasks instead of resubmitting paid jobs. Unknown initial-submission outcomes fail explicitly rather than automatically resubmitting.
5. Voice rendering fixes the Voice ID, applies supported emotion tags and pace, converts output to WAV and applies volume and real pauses before alignment. General emotional/subtext quality still needs listening. OpenAI voice modification refines structured delivery metadata and re-synthesizes with the same ElevenLabs identity.
6. Runway's provider prompt limits are enforced. Oversized production prompts are condensed by the structured LLM with separately recorded usage instead of silently dropping identity/performance instructions. Image reference packing and submitted prompt text remain in generation provenance.
7. SFX/ambience are separate generated audio layers. VFX remains independent local transparent layers or explicitly selected legacy OpenAI effect footage. Sync lip-sync is optional corrective post-processing, not a core prerequisite. Enable it only for changed dubbing, multilingual speech, demanding mouth close-ups or QA-identified mismatch.
8. Review continuity and each scene on Animations; Final only assembles current approved media, then requires final human approval. It does not invoke model generation.

## Credentials and costs

The top-right key dialog edits the three production secrets plus optional admin/Sync keys. Values are masked replacement inputs; blanks preserve existing keys, Clear explicitly removes them. They persist only in local `.env` and the backend process environment. Existing credentials, production media, request history and billing allocations are preserved.

Old OpenAI rates are not applied to new providers. Optional model/account-specific USD rates are `STORY_RUNWAY_IMAGE_COST_PER_IMAGE`, `STORY_RUNWAY_VIDEO_COST_PER_SECOND`, `STORY_ACT_TWO_COST_PER_SECOND`, `STORY_ELEVENLABS_TTS_COST_PER_MILLION_CHARACTERS` and the existing SFX rate. Missing rates remain Unknown. OpenAI billed reports do not include Runway or ElevenLabs invoices. Upload/poll/download requests are attributed, while generation units are charged only for newly submitted tasks.

## Later work and honest quality limits

Automatic provider routing and benchmarks are Phase 4; series Canon memory is Phase 5; Shorts/publishing/audience feedback is Phase 6. This update does not claim those features. Kling first/last keyframes are supported: set a shot's `lastFrame` to a project-local approved image path in the direction JSON. The terminal frame is sent only for the shot's final generated part, and its hash binds approvals. Native clip-extension controls are not implemented; longer scenes currently use generated short cuts with first-frame chaining. Multiple dialogue takes have version history/emotion auditions, not a dedicated best-take selector. Audience metrics, separate color grading, automatic cinematic quality scoring, multi-character Act-Two compositing and driving-performance capture remain outside this implementation. Real-provider acceptance is still required before marking cinematic Phase 3 quality fully accepted.

## Verified primary API contracts

- [OpenAI GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol): exact model ID and structured output capability.
- [Runway API reference](https://docs.dev.runwayml.com/api/): `/v1/text_to_image`, `/v1/image_to_video`, `/v1/character_performance`, ephemeral uploads and task retrieval; API version `2024-11-06`.
- [Runway model list](https://docs.dev.runwayml.com/guides/models/): Gen-4 Image, Kling O3 Pro and Act-Two.
- [ElevenLabs speech API](https://elevenlabs.io/docs/api-reference/text-to-speech/convert): fixed voice endpoint, model ID, voice settings and encoded audio output.
- [ElevenLabs models](https://elevenlabs.io/docs/overview/models): Eleven v4.


## Subsequent credential change

At the user's request, Sync was removed from the key dialog, local `.env`, example configuration and finishing UI. Its legacy adapter/history remain for compatibility. The production keys are OpenAI, Runway and ElevenLabs; OpenAI admin billing is optional.
