# Pre-publication verification checklist

Do not publish or distribute the application as validated until both the
automated tests and this real-flight acceptance test pass.

## 1. Confirm the physical mounting

The default course configuration assumes:

- The RX0 II lens points straight down.
- The top edge of the saved photograph points toward the aircraft nose.
- The right edge of the saved photograph points toward aircraft right.

Photograph a large ground arrow while the aircraft points north. Confirm that
the top of the resulting image is the aircraft-forward side. If the camera is
rotated or tilted, measure the offset and update `course_config.json`; do not
guess it.

## 2. Run the automated tests

On Windows, double-click `Run_Automated_Tests.bat`. On macOS or Linux, run
`Run_Automated_Tests.sh`. It should finish with `6 passed`; the Windows script
also prints `All automated tests passed`.

These tests verify:

- PX4 quaternion conversion for the nominal rigid nadir mount.
- EXIF GPS writing and rereading.
- Pix4D XMP yaw/pitch/roll writing and rereading.
- Replacement instead of duplication when a file is tagged twice.
- Automatic camera-clock offset estimation.
- An end-to-end copied-JPEG and audit-report workflow.
- Byte-for-byte preservation of the JPEG compressed scan data.

## 3. Prepare a small real-flight test

Use a short flight with 10–20 photographs before testing a full survey.

- Copy the original `.ulg` and original camera JPEGs to a test directory.
- Put only that flight's JPEGs in the image folder.
- Preserve a second untouched copy of the originals.
- Record the aircraft's approximate headings during at least two flight lines.
- Include a few photographs captured while hovering nearly level.

If possible, enable and verify PX4 camera-capture feedback. A log record with
`capture_result=-1` means the camera did not provide exposure feedback; attitude
may correspond to trigger-command time instead of the exposure instant.

## 4. Run the graphical application

1. Double-click `Launch_PX4_Pix4D_Tagger.bat`.
2. Select the test `.ulg`.
3. Select the original JPEG folder.
4. Accept a new output folder.
5. Click **Create Pix4D Images**.

Pass criteria:

- The application reports completion without errors.
- Output JPEG count equals input JPEG count.
- `tagging_report.csv` contains one row per JPEG.
- No original file's timestamp, size, or contents change.
- Match errors are small and consistent. Investigate large or irregular errors.
- Capture sequences increase in the same order as image filenames/timestamps.

## 5. Inspect capture-feedback status

Open `tagging_report.csv` and inspect `capture_result`.

- `1`: PX4 recorded successful camera feedback.
- `-1`: no camera feedback was available.
- `0`: failed captures are excluded by the program.

For a rapidly maneuvering rigid-camera quad, do not treat trigger-time attitude
as validated exposure-time attitude until Sony shutter latency has been tested.

## 6. Import into Pix4Dmapper

Create a new Pix4Dmapper project using only the tagged output copies. If the
images were already added to an existing project, open Image Properties Editor
and select **From EXIF** to reload their metadata.

Pass criteria before processing:

- Images are shown as geolocated rather than unreferenced.
- Latitude, longitude, and altitude are populated.
- Omega, Phi, and Kappa values are populated after Pix4D converts the XMP YPR.
- The initial camera-position plot follows the actual flight path.
- Camera headings follow the two known flight-line directions.
- Nearly level captures have plausible near-nadir orientations.

Use **Standard Calibration** for this test. Do not select Accurate Geolocation
and Orientation until actual GPS and orientation accuracy values have been
validated.

## 7. Compare initial and optimized orientation

Run only Step 1, Initial Processing, and review the quality report/rayCloud.

Pass criteria:

- All or nearly all intended images calibrate.
- The model is upright and located correctly.
- Optimized camera headings and tilts are reasonably close to the imported
  orientations; they are not consistently mirrored, 90° off, or 180° off.
- Roll/pitch residuals do not show a constant mounting bias.

A constant angular bias indicates that `course_config.json` needs a measured
mount correction. A varying error correlated with maneuver rate more strongly
suggests shutter delay or missing exposure feedback.

## 8. Record the validation build

Before publication, record:

- PX4 firmware version and flight-controller model.
- Camera trigger interface and `CAM_CAP_FBACK`/`CAM_CAP_MODE` values.
- RX0 II still-image mode and relevant firmware version.
- Physical mount convention and measured offsets.
- Pix4Dmapper version.
- Test image count, calibrated count, and observed orientation residuals.
- Any warnings or required workarounds.

Only after this checklist passes should the repository be published and a
Windows release be labeled as validated.
