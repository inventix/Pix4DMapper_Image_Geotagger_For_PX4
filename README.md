# PX4 → Pix4D JPEG Tagger

An open-source Windows desktop tool that matches camera JPEGs to PX4 `.ulg`
capture records and writes the GPS and camera-orientation metadata used by
Pix4Dmapper. Processing is fully local: flight logs and photographs are not
uploaded, and the original images are never changed.

> **Windows students: use the ready-made application.** Python, an IDE, an
> internet connection, and administrator rights are not required after the ZIP
> is downloaded and extracted.

## Download for Windows

[**Download the latest Windows application**](https://github.com/inventix/Pix4DMapper_Image_Geotagger_For_PX4/releases/latest/download/PX4_Pix4D_Tagger_Windows.zip)

1. Download `PX4_Pix4D_Tagger_Windows.zip`.
2. In File Explorer, right-click the ZIP and select **Extract All**.
3. Open the extracted folder. Do not run the program from inside the ZIP.
4. Keep `PX4_Pix4D_Tagger.exe` and `course_config.json` together.
5. Double-click `PX4_Pix4D_Tagger.exe`.

The release is a traditional self-contained 64-bit `.exe` for 64-bit Windows
10 and Windows 11. It runs offline and stores output only in the folder the user
selects. Windows may show a Microsoft Defender SmartScreen warning because the
open-source build is not yet commercially code-signed. Confirm that the file
came from this repository before selecting **More info → Run anyway**.

## Tag one flight

Prepare the PX4 `.ulg` flight log, the original camera images from that flight,
and a location for the new tagged copies. Then:

1. On **FLIGHT DATA**, select the `.ulg` log, image folder, and output folder.
2. Enable **Include JPEGs in subfolders** only if the flight images are nested.
3. Leave **Image matching** on Automatic unless an instructor directs otherwise.
4. On **ORIENTATION**, describe the camera's physical fixed mount.
5. Select **Create Pix4D Images** and review the preflight summary.
6. Wait for the green completed status, then import the output folder into
   Pix4Dmapper.

The output contains verified tagged JPEG copies, `tagging_report.csv`, and a
timestamped `conversion_log_*.txt`. The log records the selected inputs,
settings, image-by-image progress, warnings, written orientation values, and
final success, failure, or cancellation result.

## Supported camera files

The current version tags JPEG files with `.jpg`, `.jpeg`, or `.jpe` extensions.
Camera brand and filename pattern do not matter. Files can have arbitrary names,
and optional recursive discovery preserves their relative subfolders.

RAW formats such as DNG, CR2/CR3, NEF, ARW, RAF, ORF, RW2, and PEF are detected
and reported but are not modified or converted. TIFF, PNG, HEIC, WebP, and other
image formats are also reported and ignored. Export these files to JPEG with the
camera manufacturer's software or another trusted converter before tagging.

The tool checks that every JPEG extension actually contains JPEG data. A damaged
file or a renamed non-JPEG stops the run with the filename and a clear error.

## Matching choices

| Choice | Behavior | Use when |
| --- | --- | --- |
| Automatic | Prefers EXIF timestamps; uses capture order only when the counts are an unambiguous one-to-one match | Most flights |
| Timestamps only | Requires a usable EXIF capture time on every JPEG | Camera clock and timestamps are trustworthy |
| Capture order | Requires exactly the same number of JPEGs and valid PX4 captures | An instructor has verified a one-to-one ordered dataset |

The program refuses to publish a partial dataset when every image cannot be
matched. Unrelated images from another flight should be removed from the source
folder before processing.

## Orientation page

For a rigid camera mount, choose **Aircraft body — fixed camera (recommended)**.
The tool interpolates PX4 `vehicle_attitude.q` to each `camera_capture` timestamp
and composes that body orientation with the physical camera mount.

Describe the mount with:

- **Camera faces:** forward, right, rear, left, or a custom direction.
- **Facing angle:** degrees clockwise from the aircraft nose.
- **Downward angle:** degrees below the aircraft body horizon; `90°` is straight
  down (nadir), while smaller values describe an oblique mount.
- **Photo layout:** landscape, inverted landscape, or either portrait rotation.

For a downward-facing landscape camera whose image top points toward the nose:

| Setting | Value |
| --- | --- |
| Attitude source | Aircraft body — fixed camera |
| Camera faces | Forward / nose |
| Facing angle | 0° |
| Downward angle | 90° |
| Photo layout | Landscape — top edge toward camera facing |

The calculation is a full three-dimensional rotation, not a scalar addition to
pitch. DJI gimbal metadata commonly represents nadir near `−90°`, whereas
Pix4D's `Xmp.Camera.Pitch` convention represents nadir as `0°` and forward as
`90°`. This tool writes the converted Pix4D convention.

Choose **Logged camera/gimbal — use camera_capture.q** only when that field is
known to be the required attitude source for the aircraft and payload.

## Safety and audit behavior

Before processing, the application shows JPEG/RAW/other-file counts, input size,
matching mode, attitude source, and mount settings. It also checks the output
location and available disk space.

During processing it:

1. Reads valid PX4 capture and attitude records.
2. Requires a complete image-to-capture match.
3. Writes all tagged files into a temporary sibling staging folder.
4. Rereads each staged image and verifies GPS, yaw/pitch/roll, and unchanged
   compressed JPEG scan data.
5. Publishes the output set only after every staged image passes.

Cancellation or a processing failure removes temporary staged files. Existing
source JPEGs are never overwritten. The CSV audit report includes relative
filename, original size and SHA-256 hash, capture sequence/result, attitude
source, matching method, GPS, Pix4D orientation, mount settings, time error, and
unchanged-scan hash.

## Troubleshooting

### No supported JPEGs

Enable subfolder discovery if appropriate. Otherwise, export RAW or other image
formats to JPEG first. Do not merely rename a file extension.

### `camera_capture` is missing

The selected ULog does not contain the PX4 capture events required for
geotagging. Confirm camera triggering/capture logging was enabled and select the
log from the matching flight.

### `vehicle_attitude` is missing or not close to captures

Aircraft-body orientation requires valid `vehicle_attitude` samples around each
capture timestamp. Confirm the complete, correct ULog was selected. Use logged
camera/gimbal attitude only when `camera_capture.q` is known to be correct.

### Images do not all match

Use one flight per folder, remove unrelated files, check the camera clock and
EXIF timestamps, or use order matching only after confirming equal one-to-one
counts.

### Output cannot be written

Choose a local folder where the user has write permission and enough free disk
space. Avoid the source image folder, a folder inside it, read-only media, and a
cloud folder that is currently offline.

### Windows blocks the application

Confirm it came from this repository, extract the ZIP, then use **More info →
Run anyway** if SmartScreen appears. An instructor can compare the release
artifact with the public GitHub Actions build record.

The GUI also has a **Troubleshooting** button. If a run fails, give the
instructor the saved `conversion_log_*.txt`; it contains the detailed diagnostic
trail without containing image data.

## Build and run from source

Students using the released EXE should skip this section. Developers and
instructors need the latest stable 64-bit Python 3 (Python 3.11 or newer):

1. Download Python from the [official Windows Python download page](https://www.python.org/downloads/windows/).
2. Run the installer and enable **Add python.exe to PATH**.
3. Open PowerShell in the extracted repository folder.
4. Create the environment and install dependencies:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Start the GUI with `Launch_PX4_Pix4D_Tagger.bat`, or run the command-line engine:

```powershell
python px4_pix4d_tagger.py "C:\Flights\flight.ulg" "C:\Flights\Original" "C:\Flights\Pix4D_Tagged"
```

Useful command-line options include `--recursive`, `--match auto|time|order`,
`--attitude-source body|camera_capture`, `--camera-facing`,
`--camera-down-angle`, and `--image-rotation`. Run
`python px4_pix4d_tagger.py --help` for the complete list.

To build the standalone Windows application, double-click
`Build_Standalone_Windows_EXE.bat`. The resulting EXE must be built on Windows
and acceptance-tested on a representative lab computer before deployment.

## Verification and project history

The automated suite currently contains **278 pytest cases** covering camera
mount rotations, body-attitude interpolation, GPS edge cases, JPEG/XMP/EXIF
round trips, damaged inputs, matching modes, mixed file inventories, recursive
folders, disk-space checks, cancellation cleanup, conversion logs, and complete
staged output runs.

GitHub Actions runs the tests and builds the Windows executable for pull
requests. Published releases are created from versioned branches and tags, so
earlier working releases remain downloadable and are not overwritten by current
development.

Automated tests cannot prove the complete physical workflow without real flight
data. Before classroom or operational use, complete the
[real-flight verification checklist](VERIFICATION_CHECKLIST.md) with a matching
ULog, camera dataset, measured mount, and the installed Pix4Dmapper version.

## Technical documentation

- [Technical implementation notes](TECHNICAL_NOTES.md)
- [Real-flight verification checklist](VERIFICATION_CHECKLIST.md)
- [Automated tests](tests/test_tagger.py)
