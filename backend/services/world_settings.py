"""Native World form adapter; production review and approval remain in WorldStudio."""
from story_channel.files import input_hash
from story_channel.locking import project_lock
from story_channel.project_documents import write_document
from story_channel.studio import WorldStudio
from story_channel.worldview import WorldSettings, WORLD_FIELD_NAMES


def version(state):
    return input_hash(state.model_dump(mode='json', by_alias=True))


class WorldSettingsService:
    def __init__(self, manager, project_id):
        self.manager, self.project_id = manager, project_id
        manifest = manager.load(project_id)
        if manifest.world_project_id:
            raise ValueError('Edit shared World settings on the parent project.')
        self.world = WorldStudio(manager, project_id)

    def snapshot(self, state=None):
        state = state or self.world.load()
        review = state.review
        data = None
        if review:
            source = review.source_text or (state.base_text if not review.decision else review.accepted_text)
            original = WorldSettings.from_text(source)
            suggested = WorldSettings.from_text(review.advice.proposed_text or source)
            if not suggested.name:
                suggested.name = original.name
            current = (review.accepted_text == state.base_text if review.decision
                       else review.source_hash == input_hash(state.base_text))
            data = {
                'reviewId': review.review_id, 'summary': review.advice.summary,
                'reasons': review.advice.reasons, 'conflicts': review.advice.conflicts,
                'original': original.model_dump(mode='json', by_alias=True),
                'suggested': suggested.model_dump(mode='json', by_alias=True),
                'accepted': WorldSettings.from_text(review.accepted_text).model_dump(mode='json', by_alias=True)
                            if review.decision else None,
                'decision': review.decision, 'current': current,
            }
        return {
            'settings': state.settings.model_dump(mode='json', by_alias=True),
            'defaultLanguage': state.default_language,
            'approved': state.approval is not None, 'version': version(state), 'review': data,
            'additions': [self.addition(item, input_hash({
                'worldSettings': state.base_text,
                'additionalSettings': [setting.text for setting in self.world.approved_additions(state)],
            })) for item in state.additions],
        }

    def addition(self, item, canon_hash):
        review = item.review
        return {
            'settingId': item.setting_id, 'text': item.text,
            'version': input_hash(item.model_dump(mode='json', by_alias=True)),
            'approved': item.approval is not None,
            'approvedAt': item.approval.approved_at.isoformat() if item.approval else '',
            'review': None if not review else {
                'original': review.source_text or item.text,
                'suggested': '' if review.decision else (review.advice.proposed_text or item.text),
                'decision': review.decision,
                'summary': review.advice.summary, 'reasons': review.advice.reasons,
                'conflicts': review.advice.conflicts,
                'current': (review.source_hash == input_hash(item.text) or
                            (review.decision in ('apply','revert') and review.accepted_text == item.text)) and review.context_hash == canon_hash,
            },
        }

    def add(self, text):
        if not text.strip():
            raise ValueError('Enter additional setting content.')
        self.world.add_setting(text.strip())
        return self.snapshot()

    def update_addition(self, setting_id, text, expected):
        if not text.strip():
            raise ValueError('Additional settings cannot be empty.')
        self.world.edit_setting(setting_id, text, expected=expected)
        return self.snapshot()

    def remove_addition(self, setting_id, expected):
        self.world.edit_setting(setting_id, remove=True, expected=expected)
        return self.snapshot()

    def resolve_addition(self, setting_id, decision, expected):
        self.world.resolve_setting(setting_id, decision=decision, expected=expected)
        return self.snapshot()

    @staticmethod
    def validate(settings, language):
        name = settings.name.strip()
        if not name or '\n' in name or '\r' in name:
            raise ValueError('Enter a World name on a single line.')
        if not language.strip():
            raise ValueError('Choose a default language.')
        if not any(getattr(settings, field).strip() for field in WORLD_FIELD_NAMES):
            raise ValueError('Enter at least one World settings point.')

    def save(self, settings, default_language, expected):
        self.validate(settings, default_language)
        with project_lock(self.world.path):
            state = self.world.load()
            if version(state) != expected:
                raise ValueError('World settings changed. Reload before saving.')
            manifest = self.manager.load(self.project_id)
            manifest.title = settings.name.strip()
            type(manifest).model_validate(manifest.model_dump())
            if state.approval:
                if (settings.model_dump(exclude={'name'}) != state.settings.model_dump(exclude={'name'})
                        or default_language.strip() != state.default_language):
                    raise ValueError('Approved World settings are immutable. Add an additional setting instead.')
            else:
                self.world._preserve_legacy_outline()
                text = settings.text()
                if text != state.base_text or default_language.strip() != state.default_language:
                    state.review = None
                state.base_text = text
                state.default_language = manifest.language = default_language.strip()
                write_document(self.world.path / 'outline.json', text)
            state.display_name = manifest.title
            self.world.save(state)
            self.manager.save(manifest)
            return self.snapshot(state)

    def resolve(self, settings, decision, expected):
        with project_lock(self.world.path):
            state = self.world.load()
            if version(state) != expected or state.approval:
                raise ValueError('World settings changed or are already approved.')
            review = state.review
            if not review:
                raise ValueError('Run AI Review before selecting a proposal.')
            current = review.accepted_text == state.base_text if review.decision else review.source_hash == input_hash(state.base_text)
            if not current:
                raise ValueError('World draft changed. Run AI Review again.')
            source = review.source_text or state.base_text
            if decision == 'revert':
                settings = WorldSettings.from_text(source)
            self.validate(settings, state.default_language)
            selected = source if decision == 'revert' else settings.text()
            manifest = self.manager.load(self.project_id)
            manifest.title = settings.name.strip()
            type(manifest).model_validate(manifest.model_dump())
            review.decision, review.accepted_text = decision, selected
            review.verification, review.verification_hash = None, ''
            state.base_text, state.display_name = selected, manifest.title
            self.world._preserve_legacy_outline()
            self.world.save(state)
            self.manager.save(manifest)
            write_document(self.world.path / 'outline.json', selected)
            return self.snapshot(state)
