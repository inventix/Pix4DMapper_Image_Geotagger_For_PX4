# Technical basis and limitations

## Why a custom tagger is justified

QGroundControl's geotagger currently reads PX4 capture events but writes only
the standard EXIF GPS version, latitude, longitude, altitude, and reference
fields. Its EXIF writer does not write camera attitude. PX4's `camera_capture`
topic already includes a quaternion (`q`) and documents it as camera attitude
when a gimbal is used, or vehicle attitude otherwise. Version 0.4 lets the user
choose that logged camera/gimbal attitude or the aircraft's `vehicle_attitude`
topic explicitly. The aircraft-body option is the default for fixed mounts.

- QGC geotag writer:
  https://api.qgroundcontrol.com/master/ExifParser_8cc_source.html
- PX4 `CameraCapture` definition:
  https://docs.px4.io/main/en/msg_docs/CameraCapture

## What Pix4Dmapper accepts

Pix4Dmapper accepts standard EXIF GPS fields. Its native orientation fields are
`Xmp.Camera.Yaw`, `Xmp.Camera.Pitch`, and `Xmp.Camera.Roll`, in degrees. Pix4D
converts those navigation angles to Omega/Phi/Kappa on import. The Camera XMP
namespace URI is `http://pix4d.com/camera/1.0`.

Pix4Dmapper's Standard Calibration does not require initial orientation. Its
own input specification labels Omega/Phi/Kappa optional. Therefore, missing
orientation alone should not make Mapper treat otherwise valid EXIF GPS data as
unreferenced. If the new tagged copies are still shown as unreferenced, inspect
the exact Pix4D message and the source/tagged metadata: the remaining cause is
likely a malformed/missing GPS field, duplicate coordinates, invalid camera
model information, or an import/project-state issue.

- Pix4D XMP Camera tags:
  https://support.pix4d.com/hc/en-us/articles/360016450032
- Mapper input/geolocation formats:
  https://support.pix4d.com/hc/en-us/articles/202558539
- Pix4D YPR and OPK definitions:
  https://support.pix4d.com/hc/en-us/articles/202558969
- Pix4D conversion note:
  https://data.pix4d.com/misc/KB/documents/Pix4D_Yaw_Pitch_Roll_Omega_to_Phi_Kappa_angles_and_conversion.pdf

## Fixed-mount rotation

PX4's vehicle attitude quaternion maps its FRD body frame to local NED. Pix4D's
documented perpendicular-camera relation is:

```text
body_from_image = [[0, 1,  0],
                   [1, 0,  0],
                   [0, 0, -1]]
```

This means image right is aircraft right, image top is aircraft forward, and
camera back is aircraft up. With that physical mounting, Pix4D's YPR input is
exactly PX4 vehicle YPR.

For the aircraft-body option, the tool interpolates `vehicle_attitude.q` with
quaternion spherical linear interpolation at every `camera_capture.timestamp`.
The physical mount is described by a facing angle clockwise from the aircraft
nose, an optical-axis angle below the body horizon (90 degrees is nadir), and an
image rotation around the optical axis (landscape or either portrait side).

The program constructs orthonormal image-right, image-top, and camera-back
vectors in the PX4 body frame, composes that matrix with the body-to-NED
quaternion, and only then converts the camera frame to Pix4D yaw/pitch/roll. It
does not simply add 90 degrees to an Euler pitch value.

That distinction is important when comparing DJI metadata. DJI images commonly
report a nadir gimbal pitch near -90 degrees, while Pix4D's documented
`Xmp.Camera.Pitch` convention defines 0 degrees as nadir and 90 degrees as
forward-looking. The output uses Pix4D's convention.

## Capture-time accuracy

Rigid mounting makes attitude accuracy more important, but correct orientation
at the wrong instant is still wrong. PX4 supports a camera-capture feedback pin
to timestamp the actual exposure. With `CAM_CAP_FBACK=1`, a compatible feedback
signal (often a flash/hot-shoe sync signal) can provide the exposure edge or
mid-exposure time. Without hardware feedback, a `camera_capture` record may
describe the trigger command time and carry `result=-1`; Sony shutter latency
then becomes a position and attitude error, especially on a fast, maneuvering
quad. The tool warns and records this status but cannot infer variable shutter
lag from the JPEGs.

- PX4 camera trigger/capture feedback:
  https://docs.px4.io/main/en/camera/fc_connected_camera
- PX4 camera capture parameters:
  https://docs.px4.io/main/en/advanced_config/parameter_reference

## Altitude

PX4 documents `camera_capture.alt` as altitude AMSL, and this value is written
to EXIF GPSAltitude with GPSAltitudeRef. The program deliberately does not claim
a specific vertical datum in XMP because that depends on the GNSS/estimator and
PX4 configuration. For survey-grade vertical results, GCPs or a validated
RTK/PPK vertical workflow remain necessary.
