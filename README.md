# PX4 → Pix4D JPEG Tagger

This tool creates new, Pix4D-ready JPEG copies from:

- Sony RX0 II JPEG photographs.
- A PX4 `.ulg` flight log containing the `camera_capture` topic.

It writes standard EXIF GPS latitude, longitude, altitude, and references, plus
Pix4D's `Xmp.Camera.Yaw`, `Pitch`, and `Roll` fields. It does not modify the
source images or recompress their JPEG scan data.

## Assumed camera mounting

The default is a rigid nadir mounting:

- Lens points straight down along the aircraft's +Z/down axis.
- The top edge of every photograph points toward the aircraft's nose.
- The right edge of every photograph points toward aircraft right.

This is Pix4D's documented `perpCamera=true` mounting. If your RX0 II is turned
or tilted relative to this orientation, use the three `--mount-*` options only
after measuring that offset. Do not guess the offsets.

## Supported platforms and initial scope

The source GUI and command-line engine run on Windows, macOS, and Linux. Use the
launcher for the computer:

- Windows: `Launch_PX4_Pix4D_Tagger.bat`
- macOS: `Launch_PX4_Pix4D_Tagger.command`
- Linux: `Launch_PX4_Pix4D_Tagger.sh`

Standalone applications must be built separately on each operating system with
the corresponding `Build_Standalone_*` script. A Windows build cannot be reused
as a macOS or Linux application.

Android and iOS are not part of the initial verified release. Mobile support
will require either a privacy-preserving browser/PWA port, a server-backed web
application, or native mobile applications. That decision should be made only
after capture matching, mounting axes, and Pix4D compatibility pass real-flight
desktop validation.

## Student workflow

For ordinary use, students should not type commands or open an IDE.

1. Double-click the launcher for the operating system.
2. Select the PX4 `.ulg` flight log.
3. Select the folder containing only that flight's original Sony JPEGs.
4. Accept the suggested output folder or choose another empty folder.
5. Click **Create Pix4D Images**.
6. Import the completed output folder into Pix4Dmapper.

The desktop interface uses a modern dark theme and shows a timestamped live
process log. While running, it reports the current image number and filename,
capture match, GPS and Pix4D yaw/pitch/roll values, copy and metadata-writing
steps, and the result of each output verification. A completed green status is
shown only after every tagged JPEG has been reread successfully.

The first launch creates a private Python environment and installs the required
components. That first setup needs internet access and can take several minutes.
Later launches open directly. Python 3.11 or newer must already be installed.

### macOS older-Python error

If setup reports that no version satisfies `numpy>=1.25`, the `python3` selected
by the old launcher is usually Python 3.8. Install Python 3.11 or newer from
https://www.python.org/downloads/macos/ and use the current launcher. The current
launcher detects this case and moves the incompatible `.venv` aside before
creating a correct one.

## Recommended computer-lab deployment

For the easiest student experience, build one standalone Windows application on
an instructor or IT Windows computer:

1. Double-click `Build_Standalone_Windows_EXE.bat` once.
2. After it finishes, take `dist\PX4_Pix4D_Tagger.exe` and
   `dist\course_config.json`.
3. Deploy those two files to every lab computer or a read-only network location.
4. Students double-click the EXE. No Python installation or command prompt is
   needed on student computers.

The Windows EXE must be built on Windows. The included source and automated
tests were validated here, but the compiled Windows binary should be acceptance
tested on one lab computer before broad deployment.

Before classroom deployment or public release, follow
`VERIFICATION_CHECKLIST.md` with a real PX4 log, matching RX0 II photographs,
and the installed Pix4Dmapper version.

`course_config.json` locks the mounting offsets and matching tolerance used by
the graphical interface. Keep it beside the EXE. Students do not need to edit
it. Once the physical RX0 II mount has been measured and validated, the
instructor can set those values once for the entire fleet.

## Manual command-line installation

Install Python 3.11 or newer, open PowerShell in this folder, then run:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
py -m pip install -r requirements.txt
```

## Manual command-line run

Put only the photographs from one flight in the source folder. Keep the `.ulg`
file separate. Select a new or empty output folder.

```powershell
py px4_pix4d_tagger.py "C:\Flights\flight.ulg" "C:\Flights\Original" "C:\Flights\Pix4D_Tagged"
```

The default `--match auto` mode uses the photographs' EXIF timestamps and the
PX4 trigger timestamps. A constant difference between the Sony clock and PX4
clock is estimated automatically. If every image and every logged trigger are
known to correspond one-for-one, ordered matching is available:

```powershell
py px4_pix4d_tagger.py flight.ulg Original Pix4D_Tagged --match order
```

The program aborts before writing if it cannot match every source photograph.
It also rereads every output, verifies all GPS and orientation fields, and
proves that the compressed image scan bytes are unchanged. Results are recorded
in `tagging_report.csv` inside the output folder.

## Pix4Dmapper

Create a project using the tagged copies. In Image Properties Editor, use
**From EXIF** if the project was created before tagging. Pix4D should show image
coordinates and converted Omega/Phi/Kappa values. Use Standard Calibration
first; do not select Accurate Geolocation and Orientation unless the stated GPS
and attitude accuracies have been independently validated.

## Important validation

Before using the results operationally, test one real flight and check:

1. Every photograph is paired with the correct trigger.
2. The initial camera-position plot follows the flown path.
3. Image top points in the aircraft-forward direction.
4. A level hover gives approximately pitch 0° and roll 0° in Pix4D's imported
   camera orientation convention.
5. Pix4D's optimized camera orientations are reasonably close to the imported
   orientations after Step 1.

PX4's `camera_capture.q` contains vehicle attitude when no gimbal is present.
For the documented rigid nadir mounting, Pix4D's own conversion treats those
body yaw/pitch/roll values as the correct input orientation.
