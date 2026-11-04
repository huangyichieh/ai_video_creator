from datetime import datetime, timezone
from decimal import Decimal
from concurrent.futures import Future
from pathlib import Path

import httpx
import pytest
from openai import OpenAI
from streamlit.testing.v1 import AppTest

from story_channel.billing import BillingReport, fetch_billing
from story_channel.config import Settings
from story_channel.costs import CostTracker
from story_channel.models import UsageRecord
from story_channel.projects import ProjectManager


def page(value, *, more=False, next_page=None):
    return {'data':[{'start_time':1700000000, 'end_time':1700086400, 'results':[
        {'amount':{'value':value,'currency':'usd'},'project_id':'proj_test','line_item':'Images'}]}],
        'has_more':more,'next_page':next_page}


def test_billing_admin_auth_pagination_filters_and_decimal_sum():
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.url.path == '/v1/organization/costs'
        assert request.headers['authorization'] == 'Bearer admin-test-secret'
        assert 'generation-secret' not in request.headers['authorization']
        assert request.url.params['bucket_width'] == '1d'
        assert 'proj_test' in str(request.url.params)
        if len(requests)==1:
            return httpx.Response(200, json=page(.005, more=True, next_page='second'))
        assert request.url.params['page']=='second'
        return httpx.Response(200, json=page(1.135))
    with OpenAI(api_key='generation-secret', admin_api_key='admin-test-secret', max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(handler))) as client:
        report=fetch_billing(1700000000,1700300000,key='admin-test-secret',project_id='proj_test',client=client)
    assert report.total == Decimal('1.140')
    assert f'{report.total:.2f}' == '1.14'
    assert len(report.rows)==2
    assert 'secret' not in repr(report)


@pytest.mark.parametrize('status', [401,403,429,500])
def test_billing_errors_are_sanitized(status):
    with OpenAI(api_key='unused',admin_api_key='admin-secret',max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status,
                  json={'error':{'message':'admin-secret sensitive account information'}})))) as client:
        with pytest.raises(RuntimeError) as exc:
            fetch_billing(1,2,key='admin-secret',client=client)
        assert str(status) in str(exc.value) or 'access denied' in str(exc.value)
        assert 'secret' not in str(exc.value)
        assert 'sensitive' not in str(exc.value)


@pytest.mark.parametrize('payload',[
 {'data':[]},page(None),page(float('inf')),
 {'data':[{'start_time':1,'results':[{'amount':{'value':1,'currency':'eur'}}]}],'has_more':False},
 page(.5,more=True,next_page=None),
])
def test_incomplete_reports_never_become_zero_or_partial_totals(payload):
    # json dumps Infinity isn't permitted by httpx; raw JSON exercises malformed numbers.
    import json
    with OpenAI(api_key='unused',admin_api_key='admin-secret',max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200,
                     text=json.dumps(payload),headers={'content-type':'application/json'})))) as client:
        with pytest.raises(ValueError):
            fetch_billing(1,2,key='admin-secret',client=client)


def test_admin_env_loaded_without_serialization(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('OPENAI_ADMIN_KEY',raising=False)
    (tmp_path/'.env').write_text('OPENAI_ADMIN_KEY=local-admin-secret\n',encoding='utf-8')
    settings=Settings.load()
    from story_channel.billing import admin_key
    assert admin_key()=='local-admin-secret'
    assert 'local-admin-secret' not in settings.model_dump_json()


def test_ui_auto_fetch_refresh_navigation_unknown_cost_and_stale_report(tmp_path,monkeypatch):
    import story_channel.billing_ui as ui
    calls=[]
    def fetch(start,end,**kwargs):
        calls.append((start,end,kwargs.get('project_id')))
        return BillingReport(start,end,datetime.now(timezone.utc),kwargs.get('project_id'),Decimal('1.14'),[
            {'date':'2026-10-21','project_id':'proj_test','line_item':'Images','amount':Decimal('1.14')}])
    monkeypatch.setattr(ui,'fetch_billing',fetch)
    monkeypatch.setenv('OPENAI_ADMIN_KEY','fake-admin-only')
    monkeypatch.setenv('OPENAI_BILLING_PROJECT_ID','proj_test')
    monkeypatch.setenv('STORY_DATA_ROOT',str(tmp_path/'data'))
    manager=ProjectManager(Settings(data_root=tmp_path/'data',llm_provider='demo'))
    outline=tmp_path/'outline.md'
    outline.write_text('A story',encoding='utf-8')
    project=manager.create('Billing test',outline)
    from story_channel.billing import set_billing_project
    project=set_billing_project(manager,project.project_id,'proj_test')
    CostTracker(manager,project.project_id).record(UsageRecord(provider='openai',model='gpt-4.1-mini',step='story',pricing_known=False))
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py'),default_timeout=30).run()
    app.run()
    app.button(key='nav-analytics').click().run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=='OpenAI account billed cost (USD)').value=='$1.14'
    assert sum(m.label=='Next step:' for m in app.metric)==1
    assert next(m for m in app.metric if m.label=='Next step:').value == 'World'
    assert all(m.label != 'Local estimate (USD)' for m in app.metric)
    assert len(calls)==1
    app.button(key='nav-analytics').click().run()
    assert not app.exception
    assert app.date_input
    assert len(calls)==1
    app.button(key='refresh-billed-costs').click().run()
    app.run()
    assert len(calls)==2
    # On refresh failure retain the previous fetched total, clearly marked stale.
    future=Future()
    future.set_exception(RuntimeError('Billing access denied. Check OPENAI_ADMIN_KEY and its organization permissions.'))
    app.session_state['billing-report-state']['future']=future
    app.run()
    app.button(key='nav-analytics').click().run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=='OpenAI account billed cost (USD)').value=='$1.14'
    assert any('last successful report' in w.value for w in app.error)
    # Selecting another period changes the cache scope without rounding individual rows.
    from datetime import timedelta
    today=datetime.now(timezone.utc).date()
    app.date_input[0].set_value((today-timedelta(days=2),today)).run()
    app.run()
    assert len(calls)==3
    app.button(key='nav-story-settings').click().run()
    app.button(key='nav-analytics').click().run()
    assert len(calls)==3
    # Simulate five minutes passing: automatic refresh starts without a click.
    import time
    app.session_state['billing-report-state']['checked']=time.monotonic()-301
    app.run()
    app.run()
    assert len(calls)==4


def test_default_billing_client_uses_only_admin_auth_and_fixed_endpoint(monkeypatch):
    import story_channel.billing as billing
    original=OpenAI
    requests=[]
    def handler(request):
        requests.append(request)
        assert request.method=='GET'
        assert str(request.url).startswith('https://api.openai.com/v1/organization/costs?')
        assert request.headers['authorization']=='Bearer local-admin'
        assert 'openai-project' not in request.headers
        return httpx.Response(200,json={'data':[],'has_more':False})
    def make_client(**kwargs):
        return original(**kwargs,http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setenv('OPENAI_API_KEY','generation-key-must-not-be-used')
    monkeypatch.setenv('OPENAI_PROJECT_ID','generation-project-header-must-not-be-used')
    monkeypatch.setenv('OPENAI_BASE_URL','https://invalid.example.com')
    monkeypatch.setattr(billing,'OpenAI',make_client)
    assert billing.fetch_billing(1,2,key='local-admin').total == 0
    assert len(requests)==1


@pytest.mark.parametrize('fields,expected', [
    ({'current_step':'characters'}, 'Story/Episode: Characters'),
    ({'current_step':'story_review'}, 'Story/Episode: AI Review (Story)'),
    ({'current_step':'complete','asset_step':'backgrounds'}, 'Assets: Backgrounds'),
    ({'current_step':'complete','asset_state':'approved','render_state':'review'}, 'Final: Human final approval'),
    ({'current_step':'complete','asset_state':'approved','render_state':'approved'}, 'Final: Complete'),
])
def test_next_step_identifies_workspace_page(fields,expected):
    from story_channel.billing_ui import next_step_label
    from story_channel.models import Manifest
    assert next_step_label(Manifest(title='Example',**fields))==expected


def test_project_mapping_is_saved_and_rejects_local_story_ids(tmp_path):
    from story_channel.billing import set_billing_project
    manager=ProjectManager(Settings(data_root=tmp_path/'data'))
    source=tmp_path/'outline.md'
    source.write_text('An outline',encoding='utf-8')
    one=manager.create('One',source)
    two=manager.create('Two',source)
    set_billing_project(manager,one.project_id,'proj_one')
    set_billing_project(manager,two.project_id,'proj_two')
    assert manager.load(one.project_id).billing_project_id=='proj_one'
    assert manager.load(two.project_id).billing_project_id=='proj_two'
    with pytest.raises(ValueError):
        set_billing_project(manager,one.project_id,one.project_id)
    assert manager.load(one.project_id).billing_project_id=='proj_one'
    assert set_billing_project(manager,one.project_id,'').billing_project_id is None


def test_account_billing_needs_no_story_or_openai_project_mapping(tmp_path,monkeypatch):
    import story_channel.billing_ui as ui
    calls=[]
    def fetch(start,end,**kwargs):
        calls.append(kwargs)
        assert 'project_id' not in kwargs
        return BillingReport(start,end,datetime.now(timezone.utc),None,Decimal('1.14'),[])
    monkeypatch.setattr(ui,'fetch_billing',fetch)
    monkeypatch.setenv('OPENAI_ADMIN_KEY','fake-admin')
    monkeypatch.setenv('OPENAI_BILLING_PROJECT_ID','proj_global_ignored')
    monkeypatch.setenv('STORY_DATA_ROOT',str(tmp_path/'data'))
    manager=ProjectManager(Settings(data_root=tmp_path/'data'))
    source=tmp_path/'outline.md'
    source.write_text('An outline',encoding='utf-8')
    manager.create('Unmapped',source)
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run()
    app.run()
    app.button(key='nav-analytics').click().run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=='OpenAI account billed cost (USD)').value=='$1.14'
    assert len(calls)==1
    app.button(key='nav-analytics').click().run()
    assert not any(t.label=='OpenAI project ID for this story' for t in app.text_input)
    assert not any(e.label=='Project billing settings' for e in app.expander)


def test_project_filter_rejects_other_project_rows():
    with OpenAI(api_key='unused',admin_api_key='admin-secret',max_retries=0,
                http_client=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json=page(9))))) as client:
        with pytest.raises(ValueError,match='outside the selected project'):
            fetch_billing(1,2,key='admin-secret',project_id='proj_other',client=client)


def test_switching_story_preserves_clearly_labeled_account_total(tmp_path,monkeypatch):
    import story_channel.billing_ui as ui
    from story_channel.billing import set_billing_project
    calls=[]
    def fetch(start,end,**kwargs):
        assert 'project_id' not in kwargs
        calls.append('account')
        return BillingReport(start,end,datetime.now(timezone.utc),None,Decimal('1.14'),[])
    monkeypatch.setattr(ui,'fetch_billing',fetch)
    monkeypatch.setenv('OPENAI_ADMIN_KEY','fake-admin')
    monkeypatch.setenv('STORY_DATA_ROOT',str(tmp_path/'data'))
    manager=ProjectManager(Settings(data_root=tmp_path/'data'))
    source=tmp_path/'outline.md'
    source.write_text('An outline',encoding='utf-8')
    one=manager.create('One',source)
    two=manager.create('Two',source)
    set_billing_project(manager,one.project_id,'proj_one')
    set_billing_project(manager,two.project_id,'proj_two')
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run()
    selector=next(s for s in app.selectbox if s.label=='Project')
    selector.select(one.project_id).run()
    app.button(key='nav-analytics').click().run()
    app.run()
    assert next(m for m in app.metric if m.label=='OpenAI account billed cost (USD)').value=='$1.14'
    next(s for s in app.selectbox if s.label=='Project').select(two.project_id).run()
    app.run()
    app.button(key='nav-analytics').click().run()
    assert not app.exception
    assert next(m for m in app.metric if m.label=='OpenAI account billed cost (USD)').value=='$1.14'
    assert calls==['account']


def test_story_without_attributed_api_usage_has_zero_cost(tmp_path,monkeypatch):
    monkeypatch.setenv('STORY_DATA_ROOT',str(tmp_path/'data'))
    monkeypatch.setenv('OPENAI_ADMIN_KEY','')
    manager=ProjectManager(Settings(data_root=tmp_path/'data'))
    source=tmp_path/'outline.md'
    source.write_text('An outline',encoding='utf-8')
    one=manager.create('Test',source)
    two=manager.create('The Last Lighthouse',source)
    CostTracker(manager,one.project_id).record(UsageRecord(provider='openai',model='test',step='story',pricing_known=False))
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run()
    app.selectbox(key='project-selector').select(two.project_id).run()
    assert next(m for m in app.metric if m.label=='Project billed cost (USD)').value=='$0.00'
    assert not any(m.label=='OpenAI account billed cost (USD)' for m in app.metric)
    app.selectbox(key='project-selector').select(one.project_id).run()
    assert next(m for m in app.metric if m.label=='Project billed cost (USD)').value=='$0.00'
    assert any('1 records have no usable usage/rate' in item.value for item in app.caption)
