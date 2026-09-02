# PX4 → Pix4D JPEG Tagger

A desktop tool that copies Sony RX0 II JPEG photographs, matches them to PX4
`.ulg` camera-capture records, and writes the GPS and camera orientation metadata
needed by Pix4Dmapper.

> **Windows students: download the Windows application below.**
> You do not need Python, an IDE, or an internet connection to run it.

## Windows student download

### 1. Download the application

[**Download the latest Windows version**](https://github.com/inventix/Pix4DMapper_Image_Geotagger_For_PX4/releases/latest/download/PX4_Pix4D_Tagger_Windows.zip)

The download contains:

- `PX4_Pix4D_Tagger.exe` — the complete Windows application.
- `course_config.json` — the instructor-controlled course settings.

### 2. Extract the download

1. Open `PX4_Pix4D_Tagger_Windows.zip`.
2. Click **Extract All**.
3. Choose a normal location such as `Documents\PX4 Pix4D Tagger`.
4. Open the extracted folder.

Do not run the application from inside the ZIP file. Keep
`course_config.json` in the same folder as the EXE.

### 3. Run the application

Double-click `PX4_Pix4D_Tagger.exe`.

The program runs entirely on the Windows computer. It does not upload flight
logs or photographs, and it does not require Python or internet access after
the ZIP has been downloaded.

The published application is a self-contained 64-bit build for supported
64-bit Windows 10 and Windows 11 computers. Windows may show a Microsoft
Defender SmartScreen warning because this open-source build is not yet
commercially code-signed. Confirm that the file came from this repository
before choosing **More info → Run anyway**.

## Build status and source code

Every Windows release is built from the public source code by GitHub Actions.
The automated test suite must pass before the versioned release is published.

Developers can inspect or download the complete source from this repository.
Python 3.11 or newer is required only to run or build from source; ordinary
Windows students should use the released EXE above.

## Tag one flight

Prepare these items before opening the tool:

- The PX4 `.ulg` flight log.
- A folder containing **only the original Sony JPEGs from that flight**.
- A new or empty folder for the tagged copies.

Then:

1. Double-click `Launch_PX4_Pix4D_Tagger.bat`.
2. Select the PX4 `.ulg` flight log.
3. Select the folder containing that flight's original Sony JPEGs.
4. Accept the suggested output folder or select another empty folder.
5. Click **Create Pix4D Images**.
6. Wait for the green completed status.
7. Import the completed output folder into Pix4Dmapper.

The program does not modify the original photographs. It creates tagged copies
and writes `tagging_report.csv` into the output folder.

## Windows troubleshooting

### “Python 3.11 or newer is not installed”

Install the latest stable 64-bit Python 3 release from the
[official Windows download page](https://www.python.org/downloads/windows/),
then double-click the launcher again.

### Setup did not finish

Check the internet connection, close the setup window, and double-click
`Launch_PX4_Pix4D_Tagger.bat` again. If the problem continues, give the
instructor the complete message shown in the setup window.

### The program was opened inside the ZIP

Close it, right-click the ZIP, select **Extract All**, open the extracted folder,
and run `Launch_PX4_Pix4D_Tagger.bat` there.

### Windows hides the `.bat` ending

In the extracted folder, select the file named
**Launch_PX4_Pix4D_Tagger** whose Type is **Windows Batch File**.

## Instructor and developer build from source

Ordinary students should use the published EXE. To reproduce the Windows build
from the open-source code, an instructor or developer can build it locally:

1. Use a Windows computer with Python 3.11 or newer installed.
2. Download and extract the complete repository.
3. Double-click `Build_Standalone_Windows_EXE.bat`.
4. After the build completes, open the `dist` folder.
5. Deploy these two files together:
   - `PX4_Pix4D_Tagger.exe`
   - `course_config.json`
6. Acceptance-test them on one lab computer before broad deployment.

Students using that standalone build do not need Python, the repository, or a
command prompt. The Windows EXE must be built on Windows.

Before classroom deployment or public release, complete
[VERIFICATION_CHECKLIST.md](VERIFICATION_CHECKLIST.md) with a real PX4 log,
matching RX0 II photographs, and the installed Pix4Dmapper version.

## Orientation page

The **ORIENTATION** page converts aircraft attitude into the physical camera
attitude for every exposure.

### Attitude source

- **Aircraft body — fixed camera (recommended):** reads PX4
  `vehicle_attitude.q` and interpolates it to each camera-capture timestamp.
- **Logged camera/gimbal:** uses `camera_capture.q` directly. Select this only
  when the log's camera or gimbal attitude is intentionally required.

### Fixed-mount settings

- **Camera faces:** forward/nose, right wing, rear/tail, left wing, or a custom
  angle.
- **Facing angle from nose:** degrees clockwise around the aircraft's down axis;
  0° is forward, 90° is right, 180° is rear, and 270° is left.
- **Downward angle:** angle below the aircraft body horizon; 0° looks forward
  and **90° looks straight down (nadir)**.
- **Photo layout:** landscape, landscape inverted, portrait clockwise, or
  portrait counter-clockwise. This determines which physical direction is the
  top edge of the photograph.

For the described downward-facing landscape camera with its top edge toward the
aircraft nose, use:

| Setting | Value |
| --- | --- |
| Attitude source | Aircraft body — fixed camera |
| Camera faces | Forward / nose |
| Facing angle | 0° |
| Downward angle | 90° |
| Photo layout | Landscape — top edge toward camera facing |

The program performs a full 3-D rotation. It does not simply add or subtract
90° from aircraft pitch. DJI gimbal metadata commonly uses approximately −90°
for a nadir gimbal, whereas Pix4D defines `Xmp.Camera.Pitch = 0°` as nadir and
`90°` as forward-looking. Output is converted to Pix4D's convention.

## What the tool writes

The tool writes:

- Standard EXIF GPS latitude, longitude, altitude, and reference fields.
- Pix4D `Xmp.Camera.Yaw`, `Pitch`, and `Roll` fields.
- A CSV audit report describing every match and written value.

It rereads every output file and verifies its GPS and orientation metadata. It
also verifies that the compressed JPEG scan data is unchanged, so photographs
are not recompressed.

## Assumed camera mounting

The default is a rigid nadir mount:

- The lens points straight down along the aircraft's +Z/down axis.
- The top edge of every photograph points toward the aircraft's nose.
- The right edge of every photograph points toward aircraft right.

This is Pix4D's documented `perpCamera=true` mounting. If the RX0 II is turned
or tilted relative to this orientation, use mount offsets only after measuring
them. Do not guess the offsets.

The graphical interface reads its locked mounting offsets and matching tolerance
from `course_config.json`. Keep that file beside a standalone EXE. Students do
not need to edit it.

## Supported computers

The source GUI and command-line engine support:

| Operating system | Student launcher |
| --- | --- |
| Windows | `Launch_PX4_Pix4D_Tagger.bat` |
| macOS | `Launch_PX4_Pix4D_Tagger.command` |
| Linux | `Launch_PX4_Pix4D_Tagger.sh` |

Standalone applications must be built separately on each operating system with
the corresponding `Build_Standalone_*` script. A Windows build cannot be used
as a macOS or Linux application.

Android and iOS are not included in this initial verified release.

## macOS note

Python 3.11 or newer is required. If setup reports that no version satisfies
`numpy>=1.25`, install a current Python version from the
[official Python macOS downloads page](https://www.python.org/downloads/macos/)
and use the current launcher.

## Manual command-line installation

Ordinary students do not need this section. For manual use, install Python 3.11
or newer, open PowerShell in the extracted repository folder, and run:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Run the command-line tagger with:

```powershell
python px4_pix4d_tagger.py "C:\Flights\flight.ulg" "C:\Flights\Original" "C:\Flights\Pix4D_Tagged"
```

The default `--match auto` mode uses the photographs' EXIF timestamps and PX4
trigger timestamps. It automatically estimates a constant difference between
the Sony and PX4 clocks. If every photograph and logged trigger are known to
correspond one-for-one, ordered matching is available:

```powershell
python px4_pix4d_tagger.py flight.ulg Original Pix4D_Tagged --match order
```

The program aborts before writing if it cannot match every source photograph.

## Pix4Dmapper import

Create a Pix4Dmapper project using the tagged copies. If the project was created
before tagging, open Image Properties Editor and select **From EXIF**.

Pix4D should show image coordinates and converted Omega/Phi/Kappa values. Begin
with **Standard Calibration**. Do not select **Accurate Geolocation and
Orientation** unless the stated GPS and attitude accuracies have been
independently validated.

## Required real-flight validation

Before operational or classroom use, verify with a real flight that:

1. Every photograph is paired with the correct trigger.
2. The initial camera-position plot follows the flown path.
3. Image top points in the aircraft-forward direction.
4. A level hover gives approximately 0° pitch and 0° roll in Pix4D's imported
   camera-orientation convention.
5. Pix4D's optimized camera orientations are reasonably close to the imported
   orientations after Step 1.

PX4's `camera_capture.q` contains vehicle attitude when no gimbal is present.
For the documented rigid nadir mounting, Pix4D's conversion treats those body
yaw, pitch, and roll values as the input orientation.

## Automated verification

The Windows build workflow runs **258 automated pytest cases** before producing
an EXE. Coverage includes hundreds of landscape, portrait, cardinal, oblique,
and randomized 3-D mount combinations; aircraft-body quaternion interpolation;
GPS edge cases; XMP/EXIF replacement; unchanged JPEG scan data; timestamp and
order matching; corrupted-output detection; and end-to-end output/report
creation.

These tests verify the software mechanics and mathematical invariants. A real
PX4 log, matching camera images, and Pix4Dmapper processing remain required to
validate the complete physical workflow and the measured camera mount.

## Technical documentation

- [Technical implementation notes](TECHNICAL_NOTES.md)
- [Verification checklist](VERIFICATION_CHECKLIST.md)
- [Automated tests](tests/test_tagger.py)
