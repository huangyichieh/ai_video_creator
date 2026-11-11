import json
from threading import Event

import pytest
from streamlit.testing.v1 import AppTest

from story_channel.files import atomic_write
from story_channel.scene_production import SceneProduction
from test_animated_scene_cards_ui import prepare,display
from test_scene_production import episode
from test_studio import world


def generated_suggestions(monkeypatch):
    original=SceneProduction.propose
    calls=[]
    def propose(self,sid,instructions=""):
        calls.append(sid)
        review=original(self,sid,instructions)
        review["suggested"]["shots"][0]["action"]="A newly generated wave toward the window."
        reviews=self.proposals()
        reviews[sid]=review
        atomic_write(self.path/"scene_direction_reviews.json",json.dumps(reviews))
        return review
    monkeypatch.setattr(SceneProduction,"propose",propose)
    return calls


def test_regeneration_changes_only_requested_scene_and_rejects_stale_source(episode,monkeypatch):
    engine,scenes=prepare(episode)
    service=SceneProduction(engine.manager,engine.project_id)
    sid,other=scenes[0].scene_id,scenes[1].scene_id
    before=service.direction(other).model_dump()
    expected=service.direction_hash(sid)
    calls=generated_suggestions(monkeypatch)
    service.regenerate_direction(sid,expected=expected)
    assert calls==[sid]
    assert service.direction(sid).shots[0].action=="A newly generated wave toward the window."
    assert service.direction(other).model_dump()==before
    assert service.direction_ready(sid)
    assert service.proposals()[sid]["savedHash"]==service.direction_hash(sid)
    with pytest.raises(ValueError,match="changed"):
        service.regenerate_direction(sid,expected=expected)
    assert calls==[sid]


def test_regenerate_direction_button_is_per_scene_preserves_manual_editing_and_shows_progress(episode,monkeypatch):
    engine,scenes=prepare(episode)
    sid,other=scenes[0].scene_id,scenes[1].scene_id
    target=f"animated-scene-card-{engine.project_id}-{sid}"
    original=SceneProduction.regenerate_direction
    calls=generated_suggestions(monkeypatch)
    release=Event()
    def regenerate(self,*args,**kwargs):
        assert release.wait(20)
        return original(self,*args,**kwargs)
    monkeypatch.setattr(SceneProduction,"regenerate_direction",regenerate)
    app=AppTest.from_function(display,args=(engine.manager,engine.project_id),default_timeout=30).run()
    assert not app.exception
    app.button(key=target+"-regenerate_direction").click().run()
    try:
        assert not app.exception
        assert "Generating" in app.button(key=target+"-regenerate_direction").label
        assert any("Generating direction..." in caption.value for caption in app.caption)
        app.run()  # AppTest exposes only the targeted card after a named rerun.
        assert not app.button(key=f"animated-scene-card-{engine.project_id}-{other}-regenerate_direction").disabled
    finally:
        release.set()
    app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception and calls==[sid]
    assert any(w.value=="A newly generated wave toward the window." for w in app.text_area)
    service=SceneProduction(engine.manager,engine.project_id)
    shot=service.direction(sid).shots[0]
    key=f"{target}-direct-{service.direction_hash(sid)}-{shot.shot_id}-action"
    app.text_area(key=key).set_value("My manual direction.").run()
    assert not app.exception
    assert app.button(key=target+"-regenerate_direction").disabled
    assert not app.button(key=target+"-save-changes").disabled
    assert calls==[sid]


def test_direction_tab_regenerates_one_scene_directly(episode,monkeypatch):
    from test_direction_readiness import display as direction_display
    engine,scenes=prepare(episode)
    sid,other=scenes[0].scene_id,scenes[1].scene_id
    service=SceneProduction(engine.manager,engine.project_id)
    before=service.direction(other).model_dump()
    calls=generated_suggestions(monkeypatch)
    app=AppTest.from_function(direction_display,args=(engine.manager,engine.project_id),default_timeout=30)
    app.session_state[f"scene-direction-panel-{engine.project_id}-{sid}"]=True
    app.run()
    assert not app.exception
    assert any(item.value=="### Animation direction" for item in app.markdown)
    assert len([button for button in app.button if button.key==f"scene-regenerate-direction-{engine.project_id}-{sid}"])==1
    app.button(key=f"scene-regenerate-direction-{engine.project_id}-{sid}").click().run()
    app.session_state["workspace-task"]["future"].result(timeout=10)
    app.run()
    assert not app.exception and calls==[sid]
    assert service.direction(sid).shots[0].action=="A newly generated wave toward the window."
    assert service.direction(other).model_dump()==before
    assert any(panel.label.endswith(" · A newly generated wave toward the window.") for panel in app.expander)
    assert app.button(key=f"scene-regenerate-direction-{engine.project_id}-{sid}")
