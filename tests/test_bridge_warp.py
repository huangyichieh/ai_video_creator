import numpy as np
import pytest

from story_channel.bridge_warp import bilinear,inverse_warp,master_angles,options


def test_bilinear_subpixel_and_clamped_pixel_centers():
    image = np.array([[[0,0,0],[100,100,100]],[[200,200,200],[240,240,240]]],dtype=np.uint8)
    assert np.allclose(bilinear(image,.5,.5),135)
    assert np.array_equal(bilinear(image,-.5,-.5),[0,0,0])


def test_angular_inverse_mapping_wrap_and_falloff():
    image = np.zeros((6,60,3),dtype=np.uint8)
    image[:,:,0] = np.arange(60)
    image[:,:,1] = np.arange(6)[:,None]*20
    yaw = np.array([[240,245,255,270,285,295,300,301,-90]])
    rgb,weight = inverse_warp(image,yaw,0,center_yaw=270,feather_degrees=10)
    assert np.allclose(weight,[[0,.5,1,1,1,.5,0,0,1]])
    assert np.allclose(rgb[0,3],[29.5,50,0])
    assert np.array_equal(rgb[0,3],rgb[0,-1])


def test_perspective_ray_projection_and_behind_camera_rejection():
    image = np.zeros((100,200,3),dtype=np.uint8)
    image[:,:,0] = np.arange(200)
    image[:,:,1] = np.arange(100)[:,None]
    rgb,weight = inverse_warp(image,np.array([[90,105,90,270]]),np.array([[0,0,10,0]]),
        center_yaw=90,horizontal_fov=60,model="perspective")
    fx = 200/(2*np.tan(np.deg2rad(30)))
    assert np.allclose(rgb[0,0],[99.5,49.5,0])
    assert np.isclose(rgb[0,1,0],99.5+fx*np.tan(np.deg2rad(15)))
    assert np.isclose(rgb[0,2,1],49.5-fx*np.tan(np.deg2rad(10)))
    assert weight[0,-1] == 0
    _,outside = inverse_warp(image,90,80,center_yaw=90,model="perspective")
    assert outside == 0  # Inside yaw coverage, outside the camera's vertical FOV.


def test_master_pixel_angles_and_invalid_models():
    yaw,pitch = master_angles((4,2),180,200,180)
    assert np.array_equal(yaw,[[105,155,205,255]])
    assert np.array_equal(pitch,[[45],[-45]])
    assert options({})["model"] == "angular_equirectangular"
    with pytest.raises(ValueError):
        options({"bridge_warp":{"model":"invalid"}})
    with pytest.raises(ValueError):
        inverse_warp(np.zeros((2,2,3)),0,0,center_yaw=0,horizontal_fov=200,model="perspective")


def test_feather_composites_against_master_without_darkening_known_rgb():
    from PIL import Image
    image = np.full((10,60,3),200,dtype=np.uint8)
    rgb,weight = inverse_warp(image,62.5,0,center_yaw=90,feather_degrees=5)
    assert np.allclose(rgb,200) and weight == .5
    alpha = Image.new("L",(1,1),round(float(weight)*255))
    actual = np.asarray(Image.composite(Image.new("RGB",(1,1),(200,200,200)),
                                      Image.new("RGB",(1,1),(100,100,100)),alpha))
    assert np.all(actual == 150)


def test_overlap_qa_uses_the_perspective_bridge_camera():
    from story_channel.dual_panorama import assess
    source = np.zeros((100,160,3),dtype=np.uint8)
    source[:,:,0] = np.arange(160)
    source[:,:,1] = np.arange(100)[:,None]*2
    source[:,:,2] = 100
    masters = []
    for center in (0,180):
        yaw,pitch = master_angles((800,720),center,200)
        master = np.full((720,800,3),100,dtype=np.uint8)
        for bridge_yaw in (90,270):
            sampled,weight = inverse_warp(source,yaw,pitch,center_yaw=bridge_yaw,model="perspective")
            master[weight > 0] = np.rint(sampled[weight > 0]).astype(np.uint8)
        masters.append(master)
    qa = assess(*masters,bridges={"east":source,"west":source},bridge_warp={"model":"perspective"})
    for overlap in qa["overlaps"]:
        for comparison in overlap["bridgeComparisons"]:
            assert comparison["cameraModel"] == "perspective"
            assert comparison["pixelMAE"] < .01
            assert comparison["comparedPixels"] < 192*128
