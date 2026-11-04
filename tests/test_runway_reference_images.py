from pathlib import Path
import pytest
from PIL import Image
from story_channel.runway_reference_images import prepare,MIN_RATIO,MAX_RATIO
from story_channel.media_utils import file_hash


@pytest.mark.parametrize("size",[(2048,800),(1536,512),(3000,500),(100,1000),(640,640)])
def test_reference_preparation_preserves_every_source_pixel_and_original_file(tmp_path,size):
    source = tmp_path/"source.png"
    image = Image.new("RGB",size,"#123456")
    image.putpixel((size[0]-1,size[1]-1),(255,20,80))
    image.save(source)
    original = source.read_bytes()
    refs,metadata = prepare([source],tmp_path/"output.png")
    row = metadata[0]
    with Image.open(refs[0]) as result:
        assert MIN_RATIO <= result.width/result.height <= MAX_RATIO
        left,top,right,bottom = row["paddingLTRB"]
        assert result.crop((left,top,result.width-right,result.height-bottom)).tobytes() == image.tobytes()
        if left or top:
            assert result.getpixel((0,0)) == (255,255,255)
    assert source.read_bytes() == original
    assert row["sourceHash"] == file_hash(source) and row["submittedHash"] == file_hash(refs[0])
    if size == (640,640):
        assert refs == [source] and row["paddingLTRB"] == [0,0,0,0]


def test_exif_rotation_is_baked_into_upload_copy_without_changing_source(tmp_path):
    source = tmp_path/"rotated.jpg"
    image = Image.new("RGB",(100,200),"red")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source,exif=exif)
    old = source.read_bytes()
    refs,metadata = prepare([source],tmp_path/"out.png")
    with Image.open(refs[0]) as result:
        assert result.size == (200,100)
    assert metadata[0]["exifTransposed"] and source.read_bytes() == old


@pytest.mark.parametrize("packing",["none","legacy","scene"])
def test_provider_uploads_only_valid_reference_ratios_in_every_packing_path(tmp_path,packing):
    from story_channel.balanced_providers import RunwayImageProvider
    from story_channel.models import ProviderConfig
    roles = ["character","character","background","prop","style"]
    count = 2 if packing == "none" else 4 if packing == "legacy" else 5
    paths = []
    for index in range(count):
        path = tmp_path/f"source-{index}.png"
        Image.new("RGB",(2048,800) if index == 0 else (1536,512),"blue").save(path)
        paths.append(path)
    uploaded,bodies = [],[]
    class Provider(RunwayImageProvider):
        def upload(self,path):
            with Image.open(path) as image:
                assert MIN_RATIO <= image.width/image.height <= MAX_RATIO
            uploaded.append(Path(path))
            return "runway://"+Path(path).name
        def task(self,endpoint,body,config,*args):
            bodies.append(body)
            Image.new("RGB",(100,100),"white").save(config.options["output_path"])
            return "fake-task"
    options = {"output_path":str(tmp_path/"out.png")}
    if packing == "scene":
        options["scene_reference_roles"] = [{"role":roles[index],"id":str(index)} for index in range(count)]
    if packing == "legacy":
        options["reference_tags"] = ["content1","content2","content3","style"]
    asset = Provider().generate("Keep all source identities.",paths,ProviderConfig(provider="runway",model="gen4_image",options=options))
    receipt = asset.generation_params["runwayRequest"]
    assert len(uploaded) == len(bodies[0]["referenceImages"]) <= 3
    assert [item["submittedFile"] for item in receipt["referenceImagePreparation"]] == [str(path.resolve()) for path in uploaded]
    if packing == "scene":
        for item in receipt["sceneReferencePacking"]:
            with Image.open(item["file"]) as image:
                assert MIN_RATIO <= image.width/image.height <= MAX_RATIO
    else:
        assert any(item["paddingLTRB"] != [0,0,0,0] for item in receipt["referenceImagePreparation"])
