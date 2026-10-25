from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.billing import BillingReport
from story_channel.billing_allocations import assign_report,assigned_billing,allocations
from story_channel.config import Settings
from story_channel.projects import ProjectManager


def test_saved_billing_snapshot_attribution_replacement_and_ui(tmp_path,monkeypatch):
    manager=ProjectManager(Settings(data_root=tmp_path/'data'))
    source=tmp_path/'outline.md'
    source.write_text('A story',encoding='utf-8')
    test=manager.create('Test',source)
    lighthouse=manager.create('The Last Lighthouse',source)
    report=BillingReport(100,200,datetime.now(timezone.utc),None,Decimal('1.14'),[])
    first=assign_report(manager,test.project_id,report)
    assert assigned_billing(manager,test.project_id)[0]==Decimal('1.14')
    assert assigned_billing(manager,lighthouse.project_id)[0]==0
    # A refreshed allocation replaces the overlapping snapshot, never adds twice.
    report=BillingReport(100,300,datetime.now(timezone.utc),None,Decimal('1.25'),[])
    second=assign_report(manager,test.project_id,report)
    assert assigned_billing(manager,test.project_id)[0]==Decimal('1.25')
    assert len(allocations(manager))==1
    assert (manager.root/'06_analytics'/'billing_allocation_history'/second.allocation_id/'previous_allocations.json').exists()
    # Conflicting partial overlaps must not silently drop existing charges.
    with pytest.raises(ValueError,match='overlaps'):
        assign_report(manager,lighthouse.project_id,BillingReport(150,350,datetime.now(timezone.utc),None,Decimal('.50'),[]))
    assert assigned_billing(manager,test.project_id)[0]==Decimal('1.25')
    monkeypatch.setenv('STORY_DATA_ROOT',str(manager.root))
    monkeypatch.setenv('OPENAI_ADMIN_KEY','')
    app=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run()
    app.selectbox(key='project-selector').select(test.project_id).run()
    assert not app.exception
    # Billing snapshots remain available separately; rough usage totals do not
    # mix account snapshots with per-request estimates and double count them.
    assert next(m for m in app.metric if m.label=='Project billed cost (USD)').value=='$0.00'
    app.selectbox(key='project-selector').select(lighthouse.project_id).run()
    assert next(m for m in app.metric if m.label=='Project billed cost (USD)').value=='$0.00'
