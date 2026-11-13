"""Inverse spherical bridge warps, with pixel-center sampling and soft angular weights."""
import numpy as np

MODELS = ("angular_equirectangular", "perspective")
VERSION = "spherical-bridge-warp-v2"


def options(spec):
    values = {"model":"angular_equirectangular", "masterVerticalFov":180.0,
              "bridgeVerticalFov":180.0, "featherDegrees":5.0}
    values.update(spec.get("bridge_warp", {}))
    if values["model"] not in MODELS:
        raise ValueError("Bridge warp model must be angular_equirectangular or perspective.")
    for key in ("masterVerticalFov", "bridgeVerticalFov"):
        value = float(values[key])
        if not np.isfinite(value) or not 0 < value <= 180:
            raise ValueError(f"{key} must be between 0 and 180 degrees.")
        values[key] = value
    if values["masterVerticalFov"] != 180:
        raise ValueError("Panorama masters require the existing 180-degree vertical angular coverage.")
    feather = float(values["featherDegrees"])
    if not np.isfinite(feather) or not 0 < feather < 30:
        raise ValueError("Bridge featherDegrees must be greater than 0 and less than 30.")
    values["featherDegrees"] = feather
    return values


def bilinear(image, x, y):
    """Sample pixel-center coordinates; clamp within an already validated image domain."""
    height,width = image.shape[:2]
    x,y = np.broadcast_arrays(np.clip(x,0,width-1),np.clip(y,0,height-1))
    x0,y0 = np.floor(x).astype(int),np.floor(y).astype(int)
    x1,y1 = np.minimum(x0+1,width-1),np.minimum(y0+1,height-1)
    wx,wy = (x-x0)[...,None],(y-y0)[...,None]
    return ((image[y0,x0]*(1-wx)+image[y0,x1]*wx)*(1-wy)
            +(image[y1,x0]*(1-wx)+image[y1,x1]*wx)*wy)


def inverse_warp(image, yaw, pitch, *, center_yaw, horizontal_fov=60,
                 vertical_fov=180, model="angular_equirectangular", feather_degrees=5):
    """Map world yaw/pitch rays into a bridge, returning RGB samples and blend weights.

    Coordinates use +Y up, +Z yaw zero, clockwise yaw toward +X. Both image models
    use pixel centers (-0.5 at the left/top image edge). Perspective vertical FOV is
    inferred from horizontal FOV and aspect ratio, as for square camera pixels.
    """
    if model not in MODELS:
        raise ValueError("Unknown bridge camera model.")
    if not 0 < horizontal_fov <= 360 or not 0 < vertical_fov <= 180:
        raise ValueError("Invalid bridge field of view.")
    if not 0 < feather_degrees < horizontal_fov/2:
        raise ValueError("Invalid angular feather width.")
    height,width = image.shape[:2]
    delta = (np.asarray(yaw)-center_yaw+180)%360-180
    pitch = np.asarray(pitch)
    if model == "perspective":
        if horizontal_fov >= 180:
            raise ValueError("Perspective bridge FOV must be less than 180 degrees.")
        # Rotate the world ray by -center_yaw into the level bridge camera frame.
        angle,latitude = np.deg2rad(delta),np.deg2rad(pitch)
        dx = np.cos(latitude)*np.sin(angle)
        dy = np.sin(latitude)
        dz = np.cos(latitude)*np.cos(angle)
        fx = width/(2*np.tan(np.deg2rad(horizontal_fov)/2))
        fy = fx
        cx,cy = (width-1)/2,(height-1)/2
        safe_z = np.where(dz > 1e-12,dz,1)
        x,y = fx*dx/safe_z+cx,-fy*dy/safe_z+cy
        valid = (dz > 1e-12)&(x >= -.5)&(x <= width-.5)&(y >= -.5)&(y <= height-.5)
    else:
        x = (delta/horizontal_fov+.5)*width-.5
        y = (.5-pitch/vertical_fov)*height-.5
        valid = (np.abs(delta) <= horizontal_fov/2)&(np.abs(pitch) <= vertical_fov/2)
    # Full strength around the center; a smoothstep taper at angular boundaries.
    t = np.clip((horizontal_fov/2-np.abs(delta))/feather_degrees,0,1)
    weights = np.broadcast_to(t*t*(3-2*t),np.broadcast_shapes(np.shape(x),np.shape(y)))
    weights = np.where(valid,weights,0)
    return bilinear(image,x,y),weights


def master_angles(size, center_yaw, horizontal_fov, vertical_fov=180):
    """Equirectangular target pixels -> world-space yaw/pitch, in degrees."""
    width,height = size
    yaw = center_yaw+((np.arange(width)+.5)/width-.5)*horizontal_fov
    pitch = (.5-(np.arange(height)+.5)/height)*vertical_fov
    return yaw[None,:],pitch[:,None]
