import json
import pytest

from story_channel.pixabay_music import register,eligible,choose,catalog
from story_channel.media_utils import silence
from story_channel.storyboard_asset_check import check,document
from test_scene_production import episode
from test_studio import world


class Chooser:
    last_usage = {"input_tokens":100,"output_tokens":20}
    def __init__(self,identity):
        self.identity = identity
    def generate(self,prompt,response_model,config):
        data = json.loads(prompt.rsplit("\n",1)[-1])
        assert data["context"]["candidates"]
        return response_model(track_id=self.identity,reason="Gentle instrumental matches the hopeful scene.")


def test_ai_chooses_only_screened_imports_and_retains_evidence(episode):
    world,project,pipeline = episode
    identity = register(world.manager,world.project_id,"Hopeful piano","Example artist","https://pixabay.com/music/hopeful-piano-12345/",
        silence(3),"track.wav",b"Saved download and license evidence","license.txt",commercial_confirmed=True,content_id_status="not_registered",description="Gentle hopeful instrumental piano")
    assert identity in eligible(world.manager,world.project_id)
    row = next(row for row in check(world.manager,project.project_id) if row["Category"] == "BGM")
    choice = choose(world.manager,project.project_id,row["ID"],provider=Chooser(identity))
    assert choice.track_id == identity
    binding = document(pipeline.path,"storyboard_bgm.json")["entries"][row["ID"]]
    assert binding["provenance"]["provider"] == "pixabay"
    assert binding["provenance"]["track"]["url"].startswith("https://pixabay.com/music/")
    assert (pipeline.path/binding["provenance"]["track"]["licenseEvidence"]).is_file()
    from story_channel.bgm_library import ready
    assert ready(pipeline.path,binding)
    (pipeline.path/binding["provenance"]["track"]["licenseEvidence"]).write_bytes(b"tampered")
    assert not ready(pipeline.path,binding)
    track = catalog(world.manager,world.project_id)[identity]
    (world.path/track["licenseEvidence"]).write_bytes(b"changed evidence")
    assert identity not in eligible(world.manager,world.project_id)


def test_unknown_or_content_id_tracks_are_rejected_and_ai_cannot_invent_track(episode):
    world,project,pipeline = episode
    for content_id in ("unknown","registered"):
        with pytest.raises(ValueError,match="Content ID"):
            register(world.manager,world.project_id,"Piano","Artist","https://pixabay.com/music/piano-12345/",
                silence(3),"track.wav",b"license","license.txt",commercial_confirmed=True,content_id_status=content_id)
    identity = register(world.manager,world.project_id,"Piano","Artist","https://pixabay.com/music/piano-12345/",
        silence(3),"track.wav",b"license","license.txt",commercial_confirmed=True,content_id_status="not_registered")
    row = next(row for row in check(world.manager,project.project_id) if row["Category"] == "BGM")
    old = document(pipeline.path,"storyboard_bgm.json")["entries"][row["ID"]]
    with pytest.raises(ValueError,match="eligible imported"):
        choose(world.manager,project.project_id,row["ID"],provider=Chooser("invented"))
    assert document(pipeline.path,"storyboard_bgm.json")["entries"][row["ID"]] == old
