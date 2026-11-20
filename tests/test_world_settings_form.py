from test_fastapi_workspace import client, wait_job
from story_channel.studio import WorldStudio
import pytest
from backend.services.world_settings import version


def create(client):
    response = client.post('/api/projects', json={'name':'Original World','premise':'An island stores memories.'})
    assert response.status_code == 201
    pid = response.json()['projectId']
    return pid, client.get(f'/api/projects/{pid}/world-settings').json()


def run(client, pid, operation, args):
    prepared = client.post(f'/api/projects/{pid}/prepare',json={'operation':operation,'arguments':args})
    assert prepared.status_code == 200, prepared.text
    response = client.post('/api/jobs',json={'project_id':pid,'operation':operation,'arguments':prepared.json()['values']})
    assert response.status_code == 202, response.text
    return wait_job(client,response.json()['id'])


def test_form_save_updates_name_language_fields_and_rejects_stale_write(client):
    pid, form = create(client)
    settings = {**form['settings'], 'name':'New World', 'eraSetting':'An industrial island', 'coreRules':'Memories cannot be copied.'}
    response = client.put(f'/api/projects/{pid}/world-settings',json={
        'settings':settings,'default_language':'en','expected':form['version']})
    assert response.status_code == 200,response.text
    saved = response.json()
    assert saved['settings'] == settings
    assert saved['defaultLanguage'] == 'en'
    assert saved['approved'] is False
    manifest = client.app.state.service.manager.load(pid)
    assert manifest.title == 'New World' and manifest.language == 'en'
    retry = client.put(f'/api/projects/{pid}/world-settings',json={
        'settings':form['settings'],'default_language':'zh-TW','expected':form['version']})
    assert retry.status_code == 409
    assert client.get(f'/api/projects/{pid}/world-settings').json() == saved


def test_review_original_survives_manual_resolution_and_approval_keeps_canon_immutable(client):
    pid, initial = create(client)
    job = run(client,pid,'world.review',{'expected_state':initial['version']})
    assert job['status'] == 'succeeded',job
    manager = client.app.state.service.manager
    world = WorldStudio(manager,pid)
    state = world.load()
    source = state.base_text
    # Simulate the provider's suggestion without a paid call.
    changed = state.settings.model_copy(update={'premise':'An island stores donated memories.', 'tone':'Warm mystery'})
    state.review.advice.proposed_text = changed.text()
    state.review.advice.reasons = ['Clarify the source of memories.']
    world.save(state)
    reviewed = client.get(f'/api/projects/{pid}/world-settings').json()
    assert reviewed['review']['original']['premise'] == initial['settings']['premise']
    assert reviewed['review']['suggested']['premise'] != initial['settings']['premise']
    selected = {**reviewed['review']['suggested'],'tone':'A quiet mystery with hopeful endings.'}
    resolved = client.post(f'/api/projects/{pid}/world-settings/resolve',json={
        'settings':selected,'decision':'apply','expected':reviewed['version']})
    assert resolved.status_code == 200,resolved.text
    form = resolved.json()
    assert form['settings'] == selected
    assert form['review']['original'] == reviewed['review']['original']
    assert form['review']['accepted'] == selected
    assert form['review']['current'] is True
    assert world.load().review.source_text == source
    job = run(client,pid,'world.approve_base',{'expected_state':form['version']})
    assert job['status'] == 'succeeded',job
    approved = client.get(f'/api/projects/{pid}/world-settings').json()
    assert approved['approved'] is True
    original_approval = world.load().approval.model_dump()
    invalid = client.put(f'/api/projects/{pid}/world-settings',json={
        'settings':{**selected,'premise':'Replaced canon'},'default_language':approved['defaultLanguage'],'expected':approved['version']})
    assert invalid.status_code == 400
    rename = client.put(f'/api/projects/{pid}/world-settings',json={
        'settings':{**selected,'name':'Renamed World'},'default_language':approved['defaultLanguage'],'expected':approved['version']})
    assert rename.status_code == 200,rename.text
    assert rename.json()['approved'] is True
    assert world.require_approved().approval.model_dump() == original_approval
    assert manager.load(pid).title == 'Renamed World'


def test_stale_review_and_approval_jobs_cannot_use_an_old_form(client):
    pid, form = create(client)
    saved = client.put(f'/api/projects/{pid}/world-settings',json={
        'settings':{**form['settings'],'tone':'Comedy'},'default_language':form['defaultLanguage'],'expected':form['version']}).json()
    stale_review = run(client,pid,'world.review',{'expected_state':form['version']})
    assert stale_review['status'] == 'failed'
    assert 'changed' in stale_review['error'].lower()
    reviewed = run(client,pid,'world.review',{'expected_state':saved['version']})
    assert reviewed['status'] == 'succeeded'
    old = client.get(f'/api/projects/{pid}/world-settings').json()
    resolved = client.post(f'/api/projects/{pid}/world-settings/resolve',json={
        'settings':old['settings'],'decision':'revert','expected':old['version']}).json()
    assert resolved['review']['decision'] == 'revert'
    stale_approval = run(client,pid,'world.approve_base',{'expected_state':old['version']})
    assert stale_approval['status'] == 'failed'
    assert 'changed' in stale_approval['error'].lower()


def test_saving_new_text_invalidates_review_and_resolution_is_source_bound(client):
    pid, form = create(client)
    assert run(client,pid,'world.review',{'expected_state':form['version']})['status'] == 'succeeded'
    reviewed = client.get(f'/api/projects/{pid}/world-settings').json()
    new_form = client.put(f'/api/projects/{pid}/world-settings',json={
        'settings':{**reviewed['settings'],'tone':'Whimsical'},'default_language':reviewed['defaultLanguage'],'expected':reviewed['version']}).json()
    assert new_form['review'] is None
    stale = client.post(f'/api/projects/{pid}/world-settings/resolve',json={
        'settings':reviewed['settings'],'decision':'apply','expected':reviewed['version']})
    assert stale.status_code == 409
    assert client.get(f'/api/projects/{pid}/world-settings').json()['settings']['tone'] == 'Whimsical'


def test_auto_generation_fills_six_fields_and_requires_fresh_review(client):
    pid, form = create(client)
    assert run(client, pid, 'world.review', {'expected_state': form['version']})['status'] == 'succeeded'
    before = client.get(f'/api/projects/{pid}/world-settings').json()
    result = run(client, pid, 'world.generate_settings', {'expected_state': before['version']})
    assert result['status'] == 'succeeded', result
    after = client.get(f'/api/projects/{pid}/world-settings').json()
    assert after['settings']['name'] == before['settings']['name']
    assert after['settings']['premise'] == before['settings']['premise']
    for field in ('eraSetting', 'coreRules', 'technologyMagicLimits', 'societyKeyPlaces', 'tone', 'forbiddenContradictions'):
        assert after['settings'][field].strip(), field
    assert not after['approved']
    assert after['review'] is None
    assert run(client, pid, 'world.generate_settings', {'expected_state': before['version']})['status'] == 'failed'
    assert run(client, pid, 'world.review', {'expected_state': after['version']})['status'] == 'succeeded'
    reviewed = client.get(f'/api/projects/{pid}/world-settings').json()
    resolved = client.post(f'/api/projects/{pid}/world-settings/resolve', json={
        'settings': reviewed['settings'], 'decision': 'revert', 'expected': reviewed['version']}).json()
    assert run(client, pid, 'world.approve_base', {'expected_state': resolved['version']})['status'] == 'succeeded'
    approved = client.get(f'/api/projects/{pid}/world-settings').json()
    blocked = run(client, pid, 'world.generate_settings', {'expected_state': approved['version']})
    assert blocked['status'] == 'failed' and 'immutable' in blocked['error']


def test_generated_fields_validate_before_saving_and_preserve_inputs(client, monkeypatch):
    from story_channel.worldview import WORLD_FIELD_NAMES
    pid, _ = create(client)
    world = WorldStudio(client.app.state.service.manager, pid)
    world.pipeline.demo = False  # Generator is mocked: no provider request.
    before = world.load()

    def generate(pipeline, current, instructions, stage, **kwargs):
        assert stage == 'world'
        assert kwargs['creation'] and kwargs['retry_validation']
        assert kwargs['context']['defaultLanguage'] == before.default_language
        assert 'ALL six fields' in instructions
        output = current.model_copy(update={field: '草案提案： Generated ' + field for field in WORLD_FIELD_NAMES if field != 'premise'})
        output.name, output.premise = 'AI replacement', 'AI replacement'
        return kwargs['validate_output'](output)

    monkeypatch.setattr('story_channel.modification.modify_content', generate)
    generated = world.generate_settings(expected_state=version(before))
    assert generated.settings.name == before.settings.name
    assert generated.settings.premise == before.settings.premise
    for field in WORLD_FIELD_NAMES:
        if field != 'premise':
            assert getattr(generated.settings, field) == 'Generated ' + field
    saved_version = version(world.load())
    monkeypatch.setattr('story_channel.modification.modify_content', lambda *args, **kwargs:
                        before.settings.model_copy(update={'tone': 'Incomplete response'}))
    with pytest.raises(ValueError, match='all six'):
        world.generate_settings(expected_state=saved_version)
    assert version(world.load()) == saved_version


def test_native_additional_settings_autosave_review_and_immutable_approval(client):
    pid, _ = create(client)
    url = f'/api/projects/{pid}/world-settings/additions'
    assert client.post(url, json={'text':'New rule'}).status_code == 400
    world = WorldStudio(client.app.state.service.manager,pid)
    world.review()
    from story_channel.files import input_hash
    world.resolve_base('revert','',expected=input_hash(world.load().base_text))
    world.approve_base(expected=input_hash(world.load().base_text))
    created = client.post(url,json={'text':'Only donated memories can power a lighthouse.'})
    assert created.status_code == 201, created.text
    addition = created.json()['additions'][0]
    sid = addition['settingId']
    assert not addition['approved']
    changed = client.put(f'{url}/{sid}',json={'text':'Lighthouses store memories at night.','expected':addition['version']})
    assert changed.status_code == 200, changed.text
    edited = changed.json()['additions'][0]
    stale = client.put(f'{url}/{sid}',json={'text':'Stale replacement','expected':addition['version']})
    assert stale.status_code == 409
    assert run(client,pid,'world.review',{'setting_id':sid,'expected_setting':addition['version']})['status'] == 'failed'
    assert run(client,pid,'world.review',{'setting_id':sid,'expected_setting':edited['version']})['status'] == 'succeeded'
    reviewed = client.get(f'/api/projects/{pid}/world-settings').json()['additions'][0]
    assert reviewed['review']['current']
    result = run(client,pid,'world.approve_setting',{
        'setting_id':sid,'text':reviewed['text'],'decision':'revert','expected_setting':reviewed['version']})
    assert result['status'] == 'succeeded', result
    approved = client.get(f'/api/projects/{pid}/world-settings').json()['additions'][0]
    assert approved['approved'] and approved['approvedAt']
    assert client.put(f'{url}/{sid}',json={'text':'Replacement','expected':approved['version']}).status_code == 409
    assert client.delete(f'{url}/{sid}',params={'expected':approved['version']}).status_code == 409
    second = client.post(url,json={'text':'Another draft.'}).json()['additions'][1]
    assert client.delete(f"{url}/{second['settingId']}",params={'expected':second['version']}).status_code == 200
    assert len(client.get(f'/api/projects/{pid}/world-settings').json()['additions']) == 1


def test_editing_additional_content_clears_review_and_checks_delete_version(client):
    pid, _ = create(client)
    world = WorldStudio(client.app.state.service.manager,pid)
    from story_channel.files import input_hash
    world.review()
    world.resolve_base('revert','',expected=input_hash(world.load().base_text))
    world.approve_base(expected=input_hash(world.load().base_text))
    url = f'/api/projects/{pid}/world-settings/additions'
    draft = client.post(url,json={'text':'Additional canon.'}).json()['additions'][0]
    sid = draft['settingId']
    run(client,pid,'world.review',{'setting_id':sid,'expected_setting':draft['version']})
    reviewed = client.get(f'/api/projects/{pid}/world-settings').json()['additions'][0]
    updated = client.put(f'{url}/{sid}',json={'text':'Different additional canon.','expected':reviewed['version']}).json()['additions'][0]
    assert updated['review'] is None
    assert client.delete(f'{url}/{sid}',params={'expected':reviewed['version']}).status_code == 409
    assert client.put(f'{url}/{sid}',json={'text':'   ','expected':updated['version']}).status_code == 400
    blocked = run(client,pid,'world.approve_setting',{'setting_id':sid,'text':updated['text'],'decision':'revert','expected_setting':updated['version']})
    assert blocked['status'] == 'failed' and 'review' in blocked['error'].lower()


@pytest.mark.parametrize('decision', ['apply','revert'])
def test_inline_suggestion_resolution_persists_without_approving(client, decision):
    from story_channel.files import input_hash
    pid, _ = create(client)
    world = WorldStudio(client.app.state.service.manager,pid)
    world.review()
    world.resolve_base('revert','',expected=input_hash(world.load().base_text))
    world.approve_base(expected=input_hash(world.load().base_text))
    url=f'/api/projects/{pid}/world-settings/additions'
    draft=client.post(url,json={'text':'Original additional rule.'}).json()['additions'][0]
    sid=draft['settingId']
    assert client.put(f'{url}/{sid}/suggestion',json={'decision':decision,'expected':draft['version']}).status_code == 400
    run(client,pid,'world.review',{'setting_id':sid,'expected_setting':draft['version']})
    state=world.load()
    state.additions[0].review.advice.proposed_text='AI suggested additional rule.'
    world.save(state)
    reviewed=client.get(f'/api/projects/{pid}/world-settings').json()['additions'][0]
    resolved=client.put(f'{url}/{sid}/suggestion',json={'decision':decision,'expected':reviewed['version']})
    assert resolved.status_code == 200, resolved.text
    result=resolved.json()['additions'][0]
    assert result['text'] == ('AI suggested additional rule.' if decision=='apply' else 'Original additional rule.')
    assert not result['approved']
    assert result['review']['suggested'] == ''
    assert result['review']['decision'] == decision and result['review']['current']
    assert client.get(f'/api/projects/{pid}/world-settings').json()['additions'][0] == result
    assert client.put(f'{url}/{sid}/suggestion',json={'decision':decision,'expected':reviewed['version']}).status_code == 409
    assert world.load().additions[0].review.source_text == 'Original additional rule.'
    job=run(client,pid,'world.approve_setting',{'setting_id':sid,'text':result['text'],'decision':decision,'expected_setting':result['version']})
    assert job['status'] == 'succeeded',job


@pytest.mark.parametrize('reviewed',[False,True])
def test_approve_draft_directly_uses_current_content_not_suggestion(client, reviewed):
    from story_channel.files import input_hash
    pid,_=create(client)
    world=WorldStudio(client.app.state.service.manager,pid)
    world.review()
    world.resolve_base('revert','',expected=input_hash(world.load().base_text))
    world.approve_base(expected=input_hash(world.load().base_text))
    draft=world.add_setting('Original rule.')
    if reviewed:
        world.review(setting_id=draft.setting_id)
        state=world.load()
        state.additions[0].review.advice.proposed_text='Unapplied suggestion.'
        world.save(state)
    before=client.get(f'/api/projects/{pid}/world-settings').json()['additions'][0]
    selected=before['text'] if reviewed else 'Latest edited rule.'
    job=run(client,pid,'world.approve_draft',{'setting_id':draft.setting_id,'text':selected,'expected_setting':before['version']})
    assert job['status']=='succeeded',job
    result=client.get(f'/api/projects/{pid}/world-settings').json()['additions'][0]
    assert result['approved'] and result['text']==selected
    assert world.load().additions[0].review is not None


def test_direct_approval_still_blocks_conflicts_and_stale_content(client,monkeypatch):
    from story_channel.files import input_hash
    from story_channel.studio import TextAdvice
    pid,_=create(client)
    world=WorldStudio(client.app.state.service.manager,pid)
    world.review()
    world.resolve_base('revert','',expected=input_hash(world.load().base_text))
    world.approve_base(expected=input_hash(world.load().base_text))
    draft=world.add_setting('A conflicting rule.')
    before=client.get(f'/api/projects/{pid}/world-settings').json()['additions'][0]
    stale=run(client,pid,'world.approve_draft',{'setting_id':draft.setting_id,'text':'Stale text','expected_setting':'old'})
    assert stale['status']=='failed' and 'changed' in stale['error']
    monkeypatch.setattr(WorldStudio,'_verify_selection',lambda *args,**kwargs:TextAdvice(conflicts=['Conflicts with approved canon']))
    blocked=run(client,pid,'world.approve_draft',{'setting_id':draft.setting_id,'text':before['text'],'expected_setting':before['version']})
    assert blocked['status']=='failed' and 'Conflicts' in blocked['error']
    assert not world.load().additions[0].approval
