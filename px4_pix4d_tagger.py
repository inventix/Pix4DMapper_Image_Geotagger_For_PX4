#!/usr/bin/env python3
"""Create Pix4D-ready JPEG copies from PX4 camera-capture logs.

The program preserves the JPEG scan data, adds/replaces standard EXIF GPS
metadata, and adds Pix4D's XMP Camera yaw/pitch/roll tags. Source files are
never modified.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import math
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
from lxml import etree
from PIL import ExifTags, Image, TiffImagePlugin
from pyulog import ULog
from scipy.spatial.transform import Rotation, Slerp


XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
XMP_NS = "adobe:ns:meta/"
RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
CAMERA_NS = "http://pix4d.com/camera/1.0"
JPEG_EXTENSIONS = {".jpg", ".jpeg", ".jpe"}
RAW_IMAGE_EXTENSIONS = {
    ".arw", ".cr2", ".cr3", ".dng", ".nef", ".nrw", ".orf", ".pef", ".raf", ".raw", ".rw2"
}
OTHER_IMAGE_EXTENSIONS = {".bmp", ".gif", ".heic", ".heif", ".png", ".tif", ".tiff", ".webp"}

# Columns are Pix4D image axes expressed in the PX4 FRD body frame:
# image right -> body right, image top -> body forward, camera back -> body up.
PIX4D_DEFAULT_BODY_FROM_IMAGE = np.array(
    [[0.0, 1.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, -1.0]], dtype=float
)


class TaggerError(RuntimeError):
    pass


class TaggerCancelled(TaggerError):
    pass


@dataclass(frozen=True)
class Capture:
    index: int
    sequence: int
    time_s: float
    latitude: float
    longitude: float
    altitude_m: float
    ground_distance_m: float | None
    quaternion_wxyz: tuple[float, float, float, float]
    result: int


@dataclass(frozen=True)
class ImageRecord:
    path: Path
    time_s: float | None
    relative_path: Path | None = None


@dataclass(frozen=True)
class ImageInventory:
    jpeg_paths: tuple[Path, ...]
    raw_paths: tuple[Path, ...]
    other_image_paths: tuple[Path, ...]
    ignored_file_count: int
    total_jpeg_bytes: int

    @property
    def nested_jpeg_count(self) -> int:
        return sum(len(path.parts) > 1 for path in self.jpeg_paths)


@dataclass(frozen=True)
class Match:
    image: ImageRecord
    capture: Capture
    time_error_s: float | None


def _first(data: dict, *names: str):
    for name in names:
        if name in data:
            return data[name]
    raise TaggerError(f"ULog field missing; tried: {', '.join(names)}")


def interpolate_body_quaternion(
    timestamps_s: Sequence[float], quaternions_wxyz: Sequence[Sequence[float]], target_s: float
) -> tuple[float, float, float, float]:
    """Interpolate PX4 body attitude at an exposure timestamp."""
    times = np.asarray(timestamps_s, dtype=float)
    quaternions = np.asarray(quaternions_wxyz, dtype=float)
    if times.ndim != 1 or quaternions.shape != (len(times), 4) or len(times) < 1:
        raise TaggerError("vehicle_attitude data has an invalid shape")
    valid = np.isfinite(times) & np.all(np.isfinite(quaternions), axis=1)
    valid &= np.linalg.norm(quaternions, axis=1) > 0.5
    times, quaternions = times[valid], quaternions[valid]
    if len(times) < 1:
        raise TaggerError("vehicle_attitude contains no valid quaternion samples")
    order = np.argsort(times, kind="stable")
    times, quaternions = times[order], quaternions[order]
    times, unique_indices = np.unique(times, return_index=True)
    quaternions = quaternions[unique_indices]
    if target_s < times[0] or target_s > times[-1]:
        raise TaggerError("Capture timestamp falls outside the vehicle_attitude time range")
    index = int(np.searchsorted(times, target_s))
    if index < len(times) and abs(times[index] - target_s) < 1e-9:
        q = quaternions[index] / np.linalg.norm(quaternions[index])
        return tuple(float(value) for value in q)
    before, after = index - 1, index
    if before < 0 or after >= len(times) or times[after] - times[before] > 0.5:
        raise TaggerError("No sufficiently close vehicle_attitude samples bracket the capture")
    xyzw = quaternions[[before, after]][:, [1, 2, 3, 0]]
    result = Slerp(times[[before, after]], Rotation.from_quat(xyzw))([target_s]).as_quat()[0]
    return float(result[3]), float(result[0]), float(result[1]), float(result[2])


def load_captures(log_path: Path, attitude_source: str = "body") -> list[Capture]:
    """Read valid camera_capture records from a PX4 ULog."""
    if attitude_source not in {"body", "camera_capture"}:
        raise TaggerError("Attitude source must be 'body' or 'camera_capture'")
    try:
        topics = ["camera_capture", "vehicle_attitude"] if attitude_source == "body" else ["camera_capture"]
        ulog = ULog(str(log_path), message_name_filter_list=topics)
    except Exception as exc:
        raise TaggerError(f"Could not read PX4 log {log_path}: {exc}") from exc

    datasets = [d for d in ulog.data_list if d.name == "camera_capture"]
    if not datasets:
        raise TaggerError(
            "No camera_capture topic is present in this ULog. Confirm PX4 camera "
            "capture feedback/logging was enabled for the flight."
        )

    body_times: np.ndarray | None = None
    body_quaternions: np.ndarray | None = None
    if attitude_source == "body":
        attitude_datasets = [d for d in ulog.data_list if d.name == "vehicle_attitude"]
        if not attitude_datasets:
            raise TaggerError(
                "Aircraft-body orientation was selected, but vehicle_attitude is missing from the ULog. "
                "Select logged camera/gimbal attitude only if camera_capture.q is known to be correct."
            )
        time_parts, quaternion_parts = [], []
        for attitude in attitude_datasets:
            data = attitude.data
            attitude_time = _first(data, "timestamp_sample", "timestamp").astype(np.float64) / 1_000_000.0
            attitude_q = np.column_stack(
                [_first(data, f"q[{axis}]", f"q_{axis}") for axis in range(4)]
            ).astype(float)
            time_parts.append(attitude_time)
            quaternion_parts.append(attitude_q)
        body_times = np.concatenate(time_parts)
        body_quaternions = np.concatenate(quaternion_parts)

    captures: list[Capture] = []
    failed_capture_count = 0
    invalid_record_count = 0
    unbracketed_attitude_count = 0
    raw_index = 0
    for dataset in datasets:
        d = dataset.data
        count = len(_first(d, "timestamp"))
        timestamp = _first(d, "timestamp").astype(np.float64) / 1_000_000.0
        timestamp_utc_raw = d.get("timestamp_utc")
        timestamp_utc = (
            timestamp_utc_raw.astype(np.float64) / 1_000_000.0
            if timestamp_utc_raw is not None
            else np.zeros(count)
        )
        seq = d.get("seq", np.arange(count))
        lat = _first(d, "lat")
        lon = _first(d, "lon")
        alt = _first(d, "alt")
        ground = d.get("ground_distance")
        result = d.get("result", np.full(count, -1))
        q_fields = [
            _first(d, f"q[{axis}]", f"q_{axis}") for axis in range(4)
        ]

        for i in range(count):
            # PX4: 0 explicitly means capture failed; -1 means no feedback.
            if int(result[i]) == 0:
                failed_capture_count += 1
                raw_index += 1
                continue
            la, lo, al = float(lat[i]), float(lon[i]), float(alt[i])
            if attitude_source == "body":
                assert body_times is not None and body_quaternions is not None
                try:
                    q = interpolate_body_quaternion(body_times, body_quaternions, float(timestamp[i]))
                except TaggerError:
                    unbracketed_attitude_count += 1
                    raw_index += 1
                    continue
            else:
                q = tuple(float(q_fields[j][i]) for j in range(4))
            if not (
                math.isfinite(la)
                and math.isfinite(lo)
                and math.isfinite(al)
                and -90 <= la <= 90
                and -180 <= lo <= 180
                and all(math.isfinite(v) for v in q)
                and np.linalg.norm(q) > 0.5
            ):
                invalid_record_count += 1
                raw_index += 1
                continue
            utc = float(timestamp_utc[i])
            event_time = utc if utc > 1_000_000_000 else float(timestamp[i])
            gd = float(ground[i]) if ground is not None else math.nan
            captures.append(
                Capture(
                    index=raw_index,
                    sequence=int(seq[i]),
                    time_s=event_time,
                    latitude=la,
                    longitude=lo,
                    altitude_m=al,
                    ground_distance_m=gd if math.isfinite(gd) and gd >= 0 else None,
                    quaternion_wxyz=q,
                    result=int(result[i]),
                )
            )
            raw_index += 1

    captures.sort(key=lambda c: (c.time_s, c.sequence, c.index))
    if not captures:
        details = []
        if failed_capture_count:
            details.append(f"{failed_capture_count} explicitly failed capture(s)")
        if unbracketed_attitude_count:
            details.append(
                f"{unbracketed_attitude_count} capture(s) without nearby vehicle_attitude samples"
            )
        if invalid_record_count:
            details.append(f"{invalid_record_count} record(s) with invalid GPS or quaternion data")
        reason = f" ({'; '.join(details)})" if details else ""
        raise TaggerError(
            "The camera_capture topic contains no usable capture records"
            f"{reason}. Check capture feedback, GPS validity, and the selected attitude source."
        )
    return captures


def _parse_exif_datetime(exif: Image.Exif) -> float | None:
    candidates = [
        (36867, 37521, 36881),  # DateTimeOriginal, SubSecTimeOriginal, OffsetTimeOriginal
        (36868, 37522, 36882),  # DateTimeDigitized
        (306, 37520, 36880),    # DateTime
    ]
    for date_tag, subsec_tag, offset_tag in candidates:
        raw = exif.get(date_tag)
        if not raw:
            continue
        if isinstance(raw, bytes):
            raw = raw.decode("ascii", "ignore")
        try:
            dt = datetime.strptime(str(raw).strip("\x00"), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
        fraction = 0.0
        subsec = exif.get(subsec_tag)
        if subsec is not None:
            if isinstance(subsec, bytes):
                subsec = subsec.decode("ascii", "ignore")
            digits = "".join(ch for ch in str(subsec) if ch.isdigit())
            if digits:
                fraction = float(f"0.{digits}")
        # The matching algorithm estimates a constant clock offset. Keeping a
        # naive local timestamp is intentional and handles missing time zones.
        return dt.timestamp() + fraction
    return None


def inventory_images(image_dir: Path, recursive: bool = False) -> ImageInventory:
    """Classify source-folder files without trusting their names as JPEG proof."""
    if not image_dir.is_dir():
        raise TaggerError(f"Image folder not found: {image_dir}")
    candidates = image_dir.rglob("*") if recursive else image_dir.iterdir()
    jpeg_paths, raw_paths, other_paths = [], [], []
    ignored = 0
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        suffix = path.suffix.lower()
        relative = path.relative_to(image_dir)
        if suffix in JPEG_EXTENSIONS:
            jpeg_paths.append(relative)
        elif suffix in RAW_IMAGE_EXTENSIONS:
            raw_paths.append(relative)
        elif suffix in OTHER_IMAGE_EXTENSIONS:
            other_paths.append(relative)
        else:
            ignored += 1
    key = lambda path: str(path).casefold()
    jpeg_paths.sort(key=key)
    raw_paths.sort(key=key)
    other_paths.sort(key=key)
    total_bytes = sum((image_dir / path).stat().st_size for path in jpeg_paths)
    return ImageInventory(
        tuple(jpeg_paths), tuple(raw_paths), tuple(other_paths), ignored, total_bytes
    )


def load_images(image_dir: Path, recursive: bool = False) -> list[ImageRecord]:
    inventory = inventory_images(image_dir, recursive=recursive)
    if not inventory.jpeg_paths:
        details = []
        if inventory.raw_paths:
            details.append(f"{len(inventory.raw_paths)} camera RAW file(s)")
        if inventory.other_image_paths:
            details.append(f"{len(inventory.other_image_paths)} other image file(s)")
        suffix = f"; found {' and '.join(details)}. Convert/export them to JPEG first" if details else ""
        raise TaggerError(f"No supported JPEG images found in {image_dir}{suffix}")
    records: list[ImageRecord] = []
    for relative_path in inventory.jpeg_paths:
        path = image_dir / relative_path
        try:
            with Image.open(path) as image:
                if image.format != "JPEG":
                    raise TaggerError(
                        f"{relative_path} has a JPEG filename extension but is actually {image.format or 'unknown data'}"
                    )
                records.append(
                    ImageRecord(
                        path=path,
                        time_s=_parse_exif_datetime(image.getexif()),
                        relative_path=relative_path,
                    )
                )
        except Exception as exc:
            if isinstance(exc, TaggerError):
                raise
            raise TaggerError(f"Cannot read JPEG metadata from {relative_path}: {exc}") from exc
    return records


def _monotonic_nearest_matches(
    images: Sequence[ImageRecord],
    captures: Sequence[Capture],
    offset_s: float,
    tolerance_s: float,
) -> list[Match]:
    timed_images = [(i, rec) for i, rec in enumerate(images) if rec.time_s is not None]
    out: list[Match] = []
    next_capture = 0
    for _, image in timed_images:
        target = float(image.time_s) - offset_s
        while (
            next_capture + 1 < len(captures)
            and abs(captures[next_capture + 1].time_s - target)
            <= abs(captures[next_capture].time_s - target)
        ):
            next_capture += 1
        if next_capture >= len(captures):
            break
        error = float(image.time_s) - (captures[next_capture].time_s + offset_s)
        if abs(error) <= tolerance_s:
            out.append(Match(image, captures[next_capture], error))
            next_capture += 1
    return out


def match_by_time(
    images: Sequence[ImageRecord], captures: Sequence[Capture], tolerance_s: float
) -> tuple[list[Match], float]:
    if any(image.time_s is None for image in images):
        raise TaggerError("One or more JPEGs have no usable EXIF capture timestamp.")

    images = sorted(images, key=lambda rec: (float(rec.time_s), rec.path.name.lower()))

    # Try every plausible constant index shift. For each, estimate the camera
    # clock offset by the median and score monotonic nearest-neighbor matches.
    best: tuple[int, float, float, list[Match]] | None = None
    n, m = len(images), len(captures)
    for shift in range(-(m - 1), n):
        diffs: list[float] = []
        for image_index in range(max(0, shift), min(n, m + shift)):
            capture_index = image_index - shift
            diffs.append(float(images[image_index].time_s) - captures[capture_index].time_s)
        if not diffs:
            continue
        offset = float(np.median(diffs))
        matches = _monotonic_nearest_matches(images, captures, offset, tolerance_s)
        rmse = (
            math.sqrt(sum(float(x.time_error_s) ** 2 for x in matches) / len(matches))
            if matches
            else math.inf
        )
        score = (len(matches), -rmse, -abs(shift))
        if best is None or score > (best[0], -best[1], -best[2]):
            best = (len(matches), rmse, abs(shift), matches)
    if best is None or not best[3]:
        raise TaggerError("No timestamp matches were found between JPEGs and camera captures.")
    matches = best[3]
    offsets = [float(x.image.time_s) - x.capture.time_s for x in matches]
    return matches, float(np.median(offsets))


def match_by_order(images: Sequence[ImageRecord], captures: Sequence[Capture]) -> list[Match]:
    if len(images) != len(captures):
        raise TaggerError(
            f"Order matching requires equal counts; found {len(images)} images and "
            f"{len(captures)} capture records."
        )
    ordered_images = sorted(
        images,
        key=lambda rec: (rec.time_s is None, rec.time_s if rec.time_s is not None else 0, rec.path.name),
    )
    return [Match(image, capture, None) for image, capture in zip(ordered_images, captures)]


def match_images(
    images: Sequence[ImageRecord],
    captures: Sequence[Capture],
    method: str,
    tolerance_s: float,
) -> tuple[list[Match], float | None]:
    if method not in {"auto", "time", "order"}:
        raise TaggerError(f"Unknown matching method: {method}")
    if not math.isfinite(float(tolerance_s)) or tolerance_s < 0:
        raise TaggerError("Timestamp tolerance must be a finite, non-negative number")
    if method == "order":
        return match_by_order(images, captures), None
    if method == "time":
        return match_by_time(images, captures, tolerance_s)
    # auto: prefer time matching, but use order when timestamps are unavailable
    # and the counts give an unambiguous one-to-one mapping.
    if all(image.time_s is not None for image in images):
        matches, offset = match_by_time(images, captures, tolerance_s)
        if len(matches) == len(images):
            return matches, offset
    if len(images) == len(captures):
        return match_by_order(images, captures), None
    raise TaggerError(
        "Automatic matching could not match every image. Correct the camera clock/tolerance, "
        "remove unrelated images, or use --match order when counts are equal."
    )


def rotation_matrix_xyz(roll_deg: float, pitch_deg: float, yaw_deg: float) -> np.ndarray:
    """Return Rz(yaw) Ry(pitch) Rx(roll)."""
    return Rotation.from_euler("ZYX", [yaw_deg, pitch_deg, roll_deg], degrees=True).as_matrix()


def fixed_camera_body_from_image(
    facing_deg: float = 0.0,
    down_angle_deg: float = 90.0,
    image_rotation_deg: float = 0.0,
) -> np.ndarray:
    """Return image axes expressed in the PX4 FRD body frame.

    ``facing_deg`` is clockwise around body-down from the aircraft nose.
    ``down_angle_deg`` is measured below the body X/Y plane: 0 looks toward
    the horizon and 90 looks straight down. ``image_rotation_deg`` rotates
    the physical camera clockwise as viewed from behind the camera; 0 means
    landscape with the image top toward the selected facing direction.

    Columns are image-right, image-top, and camera-back. This explicit vector
    construction avoids treating DJI-style gimbal pitch (-90 at nadir) as
    Pix4D Camera.Pitch (0 at nadir); those are different conventions.
    """
    values = (facing_deg, down_angle_deg, image_rotation_deg)
    if not all(math.isfinite(float(value)) for value in values):
        raise TaggerError("Camera orientation values must be finite numbers")
    if not -90.0 <= float(down_angle_deg) <= 90.0:
        raise TaggerError("Camera downward angle must be between -90 and 90 degrees")

    heading = math.radians(float(facing_deg))
    depression = math.radians(float(down_angle_deg))
    # Optical direction points from the lens into the scene.
    optical = np.array(
        [
            math.cos(depression) * math.cos(heading),
            math.cos(depression) * math.sin(heading),
            math.sin(depression),
        ],
        dtype=float,
    )
    camera_back = -optical
    # Image top points toward the upper side of the view for an unrotated
    # landscape camera and toward the chosen facing direction at nadir.
    image_top = np.array(
        [
            math.sin(depression) * math.cos(heading),
            math.sin(depression) * math.sin(heading),
            -math.cos(depression),
        ],
        dtype=float,
    )
    image_right = np.cross(image_top, camera_back)

    rotation = math.radians(float(image_rotation_deg))
    rotated_right = math.cos(rotation) * image_right - math.sin(rotation) * image_top
    rotated_top = math.sin(rotation) * image_right + math.cos(rotation) * image_top
    matrix = np.column_stack((rotated_right, rotated_top, camera_back))
    if not np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-10) or np.linalg.det(matrix) < 0.999999:
        raise TaggerError("Calculated camera frame is not a valid rotation")
    return matrix


def pix4d_ypr(
    quaternion_wxyz: Sequence[float],
    mount_roll_deg: float = 0,
    mount_pitch_deg: float = 0,
    mount_yaw_deg: float = 0,
    camera_facing_deg: float | None = None,
    camera_down_angle_deg: float | None = None,
    image_rotation_deg: float | None = None,
) -> tuple[float, float, float]:
    """Convert PX4 body attitude plus rigid camera mount to Pix4D Y/P/R.

    Mount offsets are intrinsic rotations about the nominal Pix4D image frame.
    Zero is a nadir camera with image top toward vehicle front.
    """
    w, x, y, z = quaternion_wxyz
    body_to_ned = Rotation.from_quat([x, y, z, w]).as_matrix()
    if any(value is not None for value in (camera_facing_deg, camera_down_angle_deg, image_rotation_deg)):
        if not all(value is not None for value in (camera_facing_deg, camera_down_angle_deg, image_rotation_deg)):
            raise TaggerError("Facing, downward angle, and image rotation must be supplied together")
        body_from_image = fixed_camera_body_from_image(
            float(camera_facing_deg), float(camera_down_angle_deg), float(image_rotation_deg)
        )
    else:
        mount_adjustment = rotation_matrix_xyz(mount_roll_deg, mount_pitch_deg, mount_yaw_deg)
        body_from_image = PIX4D_DEFAULT_BODY_FROM_IMAGE @ mount_adjustment
    image_to_ned = body_to_ned @ body_from_image
    equivalent_body_to_ned = image_to_ned @ PIX4D_DEFAULT_BODY_FROM_IMAGE.T
    yaw, pitch, roll = Rotation.from_matrix(equivalent_body_to_ned).as_euler(
        "ZYX", degrees=True
    )
    return float(yaw % 360.0), float(pitch), float(roll)


def _to_dms_rationals(value: float):
    absolute = abs(value)
    degrees = int(absolute)
    minutes_full = (absolute - degrees) * 60.0
    minutes = int(minutes_full)
    seconds = (minutes_full - minutes) * 60.0
    rational = TiffImagePlugin.IFDRational
    return (rational(degrees, 1), rational(minutes, 1), rational(round(seconds * 1_000_000), 1_000_000))


def build_exif(jpeg: bytes, capture: Capture) -> bytes:
    with Image.open(BytesIO(jpeg)) as image:
        exif = image.getexif()
    gps = dict(exif.get_ifd(ExifTags.IFD.GPSInfo))
    gps[0] = b"\x02\x03\x00\x00"  # GPSVersionID
    gps[1] = "N" if capture.latitude >= 0 else "S"
    gps[2] = _to_dms_rationals(capture.latitude)
    gps[3] = "E" if capture.longitude >= 0 else "W"
    gps[4] = _to_dms_rationals(capture.longitude)
    gps[5] = 0 if capture.altitude_m >= 0 else 1
    gps[6] = TiffImagePlugin.IFDRational(round(abs(capture.altitude_m) * 1000), 1000)
    exif[ExifTags.IFD.GPSInfo] = gps
    return exif.tobytes()


def _new_xmp_root():
    root = etree.Element(f"{{{XMP_NS}}}xmpmeta", nsmap={"x": XMP_NS})
    rdf = etree.SubElement(root, f"{{{RDF_NS}}}RDF", nsmap={"rdf": RDF_NS})
    etree.SubElement(
        rdf,
        f"{{{RDF_NS}}}Description",
        nsmap={"Camera": CAMERA_NS},
        attrib={f"{{{RDF_NS}}}about": ""},
    )
    return root


def build_xmp(existing_payload: bytes | None, capture: Capture, ypr: Sequence[float]) -> bytes:
    root = None
    if existing_payload:
        try:
            parser = etree.XMLParser(resolve_entities=False, remove_blank_text=False, recover=False)
            root = etree.fromstring(existing_payload, parser=parser)
        except etree.XMLSyntaxError:
            root = None
    if root is None:
        root = _new_xmp_root()
    descriptions = root.xpath("//*[local-name()='Description' and namespace-uri()=$rdf]", rdf=RDF_NS)
    if descriptions:
        description = descriptions[0]
    else:
        rdf_nodes = root.xpath("//*[local-name()='RDF' and namespace-uri()=$rdf]", rdf=RDF_NS)
        if not rdf_nodes:
            root = _new_xmp_root()
            description = root.xpath("//*[local-name()='Description']")[0]
        else:
            description = etree.SubElement(rdf_nodes[0], f"{{{RDF_NS}}}Description")

    yaw, pitch, roll = ypr
    tags = {
        "Yaw": f"{yaw:.8f}",
        "Pitch": f"{pitch:.8f}",
        "Roll": f"{roll:.8f}",
        "HorizCS": "EPSG:4326",
    }
    if capture.ground_distance_m is not None:
        tags["AboveGroundAltitude"] = f"{capture.ground_distance_m:.3f}"
    for key, value in tags.items():
        description.set(f"{{{CAMERA_NS}}}{key}", value)

    xml = etree.tostring(root, encoding="utf-8", xml_declaration=False, pretty_print=False)
    packet = (
        b'<?xpacket begin="\xef\xbb\xbf" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        + xml
        + b'\n<?xpacket end="w"?>'
    )
    return XMP_HEADER + packet


def _jpeg_segments(jpeg: bytes):
    if not jpeg.startswith(b"\xff\xd8"):
        raise TaggerError("Input is not a JPEG file")
    pos = 2
    while pos < len(jpeg):
        if jpeg[pos] != 0xFF:
            raise TaggerError("Malformed JPEG marker stream")
        marker_start = pos
        while pos < len(jpeg) and jpeg[pos] == 0xFF:
            pos += 1
        marker = jpeg[pos]
        pos += 1
        if marker == 0xDA:  # Start of scan; the remainder is entropy-coded data.
            yield marker, marker_start, len(jpeg), jpeg[marker_start:]
            return
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            yield marker, marker_start, pos, jpeg[marker_start:pos]
            continue
        if pos + 2 > len(jpeg):
            raise TaggerError("Truncated JPEG segment")
        length = int.from_bytes(jpeg[pos : pos + 2], "big")
        end = pos + length
        if length < 2 or end > len(jpeg):
            raise TaggerError("Invalid JPEG segment length")
        yield marker, marker_start, end, jpeg[marker_start:end]
        pos = end
    raise TaggerError("JPEG has no start-of-scan marker")


def _app1(payload: bytes) -> bytes:
    length = len(payload) + 2
    if length > 65535:
        raise TaggerError("Metadata block is too large for a JPEG APP1 segment")
    return b"\xff\xe1" + length.to_bytes(2, "big") + payload


def jpeg_scan_bytes(jpeg: bytes) -> bytes:
    for marker, _, _, segment in _jpeg_segments(jpeg):
        if marker == 0xDA:
            return segment
    raise TaggerError("JPEG scan data not found")


def write_metadata(jpeg: bytes, capture: Capture, ypr: Sequence[float]) -> bytes:
    segments = list(_jpeg_segments(jpeg))
    existing_xmp: bytes | None = None
    for marker, _, _, segment in segments:
        if marker == 0xE1:
            payload = segment[4:]
            if payload.startswith(XMP_HEADER):
                existing_xmp = payload[len(XMP_HEADER) :]
                break
    exif_segment = _app1(build_exif(jpeg, capture))
    xmp_segment = _app1(build_xmp(existing_xmp, capture, ypr))

    output = bytearray(b"\xff\xd8")
    inserted = False
    for marker, _, _, segment in segments:
        if marker == 0xDA:
            if not inserted:
                output += exif_segment + xmp_segment
            output += segment
            break
        payload = segment[4:] if marker == 0xE1 else b""
        if marker == 0xE1 and (payload.startswith(b"Exif\x00\x00") or payload.startswith(XMP_HEADER)):
            if not inserted:
                output += exif_segment + xmp_segment
                inserted = True
            continue
        output += segment
    return bytes(output)


def _gps_decimal(gps: dict, coord_tag: int, ref_tag: int) -> float:
    d, m, s = (float(x) for x in gps[coord_tag])
    value = d + m / 60 + s / 3600
    ref = gps[ref_tag]
    if isinstance(ref, bytes):
        ref = ref.decode("ascii", "ignore")
    return -value if str(ref).upper() in {"S", "W"} else value


def verify_output(source: bytes, tagged: bytes, capture: Capture, expected_ypr: Sequence[float]) -> None:
    if hashlib.sha256(jpeg_scan_bytes(source)).digest() != hashlib.sha256(jpeg_scan_bytes(tagged)).digest():
        raise TaggerError("JPEG scan data changed; refusing output")
    with Image.open(BytesIO(tagged)) as image:
        gps = image.getexif().get_ifd(ExifTags.IFD.GPSInfo)
    lat = _gps_decimal(gps, 2, 1)
    lon = _gps_decimal(gps, 4, 3)
    alt_ref = gps.get(5, 0)
    if isinstance(alt_ref, bytes):
        alt_ref = alt_ref[0] if alt_ref else 0
    alt = float(gps[6]) * (-1 if int(alt_ref) == 1 else 1)
    if abs(lat - capture.latitude) > 1e-7 or abs(lon - capture.longitude) > 1e-7:
        raise TaggerError("EXIF GPS coordinate verification failed")
    if abs(alt - capture.altitude_m) > 0.01:
        raise TaggerError("EXIF GPS altitude verification failed")

    xmp_payload = None
    for marker, _, _, segment in _jpeg_segments(tagged):
        if marker == 0xE1 and segment[4:].startswith(XMP_HEADER):
            xmp_payload = segment[4 + len(XMP_HEADER) :]
            break
    if xmp_payload is None:
        raise TaggerError("Pix4D XMP block verification failed")
    root = etree.fromstring(xmp_payload, parser=etree.XMLParser(recover=False))
    descriptions = root.xpath("//*[local-name()='Description']")
    if not descriptions:
        raise TaggerError("Pix4D XMP description is missing")
    desc = descriptions[0]
    actual = [float(desc.get(f"{{{CAMERA_NS}}}{key}")) for key in ("Yaw", "Pitch", "Roll")]
    if max(abs(a - b) for a, b in zip(actual, expected_ypr)) > 1e-5:
        raise TaggerError("Pix4D XMP orientation verification failed")


def _notify_progress(
    args: argparse.Namespace,
    current: int,
    total: int,
    image_name: str,
    stage: str,
) -> None:
    """Notify the optional GUI without coupling the command-line engine to Tk."""
    callback = getattr(args, "progress_callback", None)
    if callable(callback):
        callback(current, total, image_name, stage)


def save_conversion_log(
    output_dir: Path,
    lines: Sequence[str],
    app_version: str,
    result: str,
    created_at: datetime | None = None,
) -> Path:
    """Atomically save a unique, human-readable conversion session log."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = created_at or datetime.now()
    stem = f"conversion_log_{timestamp:%Y%m%d_%H%M%S}"
    destination = output_dir / f"{stem}.txt"
    counter = 2
    while destination.exists():
        destination = output_dir / f"{stem}_{counter}.txt"
        counter += 1
    body = "\n".join(
        [
            "PX4 → Pix4D JPEG Tagger — Conversion Log",
            f"Software version: {app_version}",
            f"Session started: {timestamp.astimezone().isoformat(timespec='seconds')}",
            f"Final result: {result}",
            "",
            *[str(line).rstrip("\n") for line in lines],
            "",
        ]
    )
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=output_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return destination


def _check_cancelled(args: argparse.Namespace) -> None:
    event = getattr(args, "cancel_event", None)
    if event is not None and event.is_set():
        raise TaggerCancelled("Processing was cancelled. No staged images were published.")


def _existing_parent(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def verify_output_capacity(output_dir: Path, required_bytes: int) -> None:
    parent = _existing_parent(output_dir.parent)
    try:
        free = shutil.disk_usage(parent).free
    except OSError as exc:
        raise TaggerError(f"Could not check free disk space near {output_dir}: {exc}") from exc
    if free < required_bytes:
        raise TaggerError(
            f"Not enough free disk space. Need about {required_bytes / 1_048_576:.0f} MB, "
            f"but only {free / 1_048_576:.0f} MB is available near {output_dir}."
        )


def _directory_bytes(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file() and not item.is_symlink():
                total += item.stat().st_size
    except OSError as exc:
        raise TaggerError(f"Could not inventory existing output folder {path}: {exc}") from exc
    return total


def process(args: argparse.Namespace) -> int:
    log_path = args.log.resolve()
    image_dir = args.images.resolve()
    output_dir = args.output.resolve()
    if not log_path.is_file():
        raise TaggerError(f"Log file not found: {log_path}")
    if not image_dir.is_dir():
        raise TaggerError(f"Image directory not found: {image_dir}")
    if output_dir == image_dir or image_dir in output_dir.parents:
        raise TaggerError("Output directory must not be the source image directory or inside it")
    if output_dir.exists() and not output_dir.is_dir():
        raise TaggerError(f"Output path is not a folder: {output_dir}")
    overwrite = bool(getattr(args, "overwrite", False))
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise TaggerError("Output directory is not empty; use --overwrite or select an empty folder")
    match_method = str(getattr(args, "match", "auto"))
    tolerance = float(getattr(args, "tolerance", 2.0))
    if match_method not in {"auto", "time", "order"}:
        raise TaggerError(f"Unknown matching method: {match_method}")
    if not math.isfinite(tolerance) or tolerance < 0:
        raise TaggerError("Timestamp tolerance must be a finite, non-negative number")
    _check_cancelled(args)
    recursive = bool(getattr(args, "recursive", False))
    inventory = inventory_images(image_dir, recursive=recursive)
    existing_output_bytes = _directory_bytes(output_dir) if overwrite else 0
    required_bytes = max(
        int(inventory.total_jpeg_bytes * 1.25) + existing_output_bytes + 10 * 1_048_576,
        20 * 1_048_576,
    )
    verify_output_capacity(output_dir, required_bytes)

    print("=== PX4 → Pix4D IMAGE-TAGGING SESSION ===")
    print(f"Flight log:       {log_path}")
    print(f"Original JPEGs:   {image_dir}")
    print(f"Tagged output:    {output_dir}")
    print(f"Match method:     {match_method} (tolerance {tolerance:.3f} s)")
    attitude_source = getattr(args, "attitude_source", "body")
    print(f"Attitude source:  {'PX4 vehicle_attitude (aircraft body)' if attitude_source == 'body' else 'camera_capture.q (camera/gimbal)'}")
    camera_facing = getattr(args, "camera_facing", 0.0)
    camera_down_angle = getattr(args, "camera_down_angle", 90.0)
    image_rotation = getattr(args, "image_rotation", 0.0)
    print(
        "Fixed camera:     "
        f"faces {camera_facing:.3f}° clockwise from nose, "
        f"{camera_down_angle:.3f}° down, image rotation {image_rotation:.3f}°"
    )
    print(f"Existing outputs: {'replace when names match' if overwrite else 'must be empty'}")
    print(f"Search subfolders: {'yes' if recursive else 'no'}")
    print(f"Source JPEG data: {inventory.total_jpeg_bytes / 1_048_576:.1f} MB")
    print(
        "Input inventory:  "
        f"{len(inventory.jpeg_paths)} JPEG, {len(inventory.raw_paths)} RAW, "
        f"{len(inventory.other_image_paths)} other image, {inventory.ignored_file_count} other file(s)"
    )
    if inventory.raw_paths:
        print(
            f"WARNING: Ignoring {len(inventory.raw_paths)} camera RAW file(s); export them to JPEG before tagging.",
            file=sys.stderr,
        )
    if inventory.other_image_paths:
        print(
            f"WARNING: Ignoring {len(inventory.other_image_paths)} unsupported image file(s).",
            file=sys.stderr,
        )
    print()

    _notify_progress(args, 0, 0, image_dir.name, "Discovering source JPEGs…")
    print("STEP 1/5 — Discover source JPEGs")
    images = load_images(image_dir, recursive=recursive)
    timestamped = sum(image.time_s is not None for image in images)
    print(f"  Found {len(images)} JPEG image(s).")
    print(f"  EXIF capture timestamps available: {timestamped}/{len(images)}")
    _check_cancelled(args)

    _notify_progress(args, 0, 0, log_path.name, "Reading PX4 camera captures…")
    print("STEP 2/5 — Read PX4 camera_capture records")
    captures = load_captures(log_path, attitude_source=attitude_source)
    confirmed = sum(capture.result == 1 for capture in captures)
    no_feedback_total = sum(capture.result == -1 for capture in captures)
    print(f"  Loaded {len(captures)} valid capture record(s).")
    print(f"  Hardware-confirmed exposures: {confirmed}")
    print(f"  Records without exposure feedback: {no_feedback_total}")
    _check_cancelled(args)

    _notify_progress(args, 0, 0, f"{len(images)} images / {len(captures)} captures", "Matching images to trigger events…")
    print("STEP 3/5 — Match each image to one PX4 trigger event")
    matches, clock_offset = match_images(images, captures, match_method, tolerance)
    print(f"  Matched {len(matches)}/{len(images)} images.")
    if clock_offset is not None:
        print(f"  Estimated camera-clock offset: {clock_offset:.6f} s")
    else:
        print("  Matching used capture order; no camera-clock offset was needed.")
    if len(matches) != len(images):
        raise TaggerError(f"Matched {len(matches)} of {len(images)} images; no files were written")
    no_feedback = sum(match.capture.result == -1 for match in matches)
    if no_feedback:
        print(
            f"WARNING: {no_feedback} capture records report no hardware exposure feedback. "
            "Their position and attitude may correspond to the trigger command rather than "
            "the actual exposure instant.",
            file=sys.stderr,
        )

    report_rows = []
    try:
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        stage_dir = Path(
            tempfile.mkdtemp(prefix=f".{output_dir.name}.staging-", dir=output_dir.parent)
        )
        if output_dir.exists():
            print("  Preparing a recoverable copy of the existing output folder…")
            shutil.copytree(output_dir, stage_dir, dirs_exist_ok=True, symlinks=True)
    except OSError as exc:
        if "stage_dir" in locals() and stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
        raise TaggerError(
            f"Cannot create a temporary staging folder near {output_dir}. "
            f"Check folder permissions and available disk space: {exc}"
        ) from exc
    print()
    try:
        print("STEP 4/5 — Stage images, write EXIF/XMP metadata, and verify")
        for number, match in enumerate(matches, 1):
            _check_cancelled(args)
            total = len(matches)
            relative_path = match.image.relative_path or Path(match.image.path.name)
            image_name = str(relative_path)
            _notify_progress(args, number, total, image_name, "Copying and tagging image…")
            print(f"[{number:04d}/{total:04d}] {image_name}")
            print(f"  Capture sequence: {match.capture.sequence} (result {match.capture.result})")
            if match.time_error_s is None:
                print("  Match error: order matched")
            else:
                print(f"  Match error: {match.time_error_s:.6f} s")
            print(
                "  GPS to write: "
                f"{match.capture.latitude:.9f}, {match.capture.longitude:.9f}, "
                f"{match.capture.altitude_m:.3f} m AMSL"
            )
            print("  Copying source JPEG bytes into memory…")
            try:
                source = match.image.path.read_bytes()
            except OSError as exc:
                raise TaggerError(f"Cannot read source JPEG {relative_path}: {exc}") from exc
            ypr = pix4d_ypr(
                match.capture.quaternion_wxyz,
                camera_facing_deg=camera_facing,
                camera_down_angle_deg=camera_down_angle,
                image_rotation_deg=image_rotation,
            )
            print(f"  Pix4D orientation: yaw {ypr[0]:.6f}°, pitch {ypr[1]:.6f}°, roll {ypr[2]:.6f}°")
            print("  Writing EXIF GPS and Pix4D XMP orientation blocks…")
            tagged = write_metadata(source, match.capture, ypr)
            print("  Verifying GPS, orientation, and unchanged JPEG scan data…")
            verify_output(source, tagged, match.capture, ypr)
            destination = stage_dir / relative_path
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(tagged)
                shutil.copystat(match.image.path, destination)
            except OSError as exc:
                raise TaggerError(f"Cannot stage tagged JPEG {relative_path}: {exc}") from exc
            source_sha256 = hashlib.sha256(source).hexdigest()
            report_rows.append(
                {
                    "image": str(relative_path),
                    "source_bytes": len(source),
                    "source_sha256": source_sha256,
                    "capture_sequence": match.capture.sequence,
                    "capture_result": match.capture.result,
                    "attitude_source": attitude_source,
                    "match_method": match_method,
                    "latitude": f"{match.capture.latitude:.9f}",
                    "longitude": f"{match.capture.longitude:.9f}",
                    "altitude_m_amsl": f"{match.capture.altitude_m:.3f}",
                    "pix4d_yaw_deg": f"{ypr[0]:.6f}",
                    "pix4d_pitch_deg": f"{ypr[1]:.6f}",
                    "pix4d_roll_deg": f"{ypr[2]:.6f}",
                    "camera_facing_deg_from_nose": f"{camera_facing:.6f}",
                    "camera_down_angle_deg": f"{camera_down_angle:.6f}",
                    "image_rotation_deg_clockwise": f"{image_rotation:.6f}",
                    "match_error_s": "" if match.time_error_s is None else f"{match.time_error_s:.6f}",
                    "scan_sha256": hashlib.sha256(jpeg_scan_bytes(tagged)).hexdigest(),
                }
            )
            print(f"  VERIFIED IN STAGING: {relative_path}")
            _notify_progress(args, number, total, image_name, "Verified and staged image")

        _check_cancelled(args)
        print()
        print("STEP 5/5 — Write audit report and publish complete output set")
        staged_report = stage_dir / "tagging_report.csv"
        with staged_report.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(report_rows[0]))
            writer.writeheader()
            writer.writerows(report_rows)
        output_was_absent = not output_dir.exists()
        try:
            if output_was_absent:
                os.replace(stage_dir, output_dir)
            else:
                backup_dir = Path(
                    tempfile.mkdtemp(prefix=f".{output_dir.name}.previous-", dir=output_dir.parent)
                )
                backup_dir.rmdir()
                os.replace(output_dir, backup_dir)
                try:
                    os.replace(stage_dir, output_dir)
                except OSError:
                    os.replace(backup_dir, output_dir)
                    raise
                try:
                    shutil.rmtree(backup_dir)
                except OSError as cleanup_error:
                    print(
                        f"WARNING: New outputs were published, but the previous-output backup "
                        f"could not be removed: {backup_dir} ({cleanup_error})",
                        file=sys.stderr,
                    )
        except OSError as exc:
            raise TaggerError(
                f"All images were verified in staging, but the completed output set could not be "
                f"published to {output_dir}: {exc}"
            ) from exc
        report_path = output_dir / "tagging_report.csv"
        print(f"  Report saved: {report_path}")
    finally:
        if stage_dir.exists():
            shutil.rmtree(stage_dir, ignore_errors=True)
    print()
    print("=== COMPLETE — ALL OUTPUT IMAGES VERIFIED ===")
    print(f"Wrote and verified {len(matches)} tagged JPEG copies in {output_dir}")
    print("Original source JPEGs were not modified.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Write PX4 GPS and rigid-camera attitude into Pix4D-ready JPEG copies."
    )
    parser.add_argument("log", type=Path, help="PX4 .ulg flight log")
    parser.add_argument("images", type=Path, help="Folder containing original camera JPEGs")
    parser.add_argument("output", type=Path, help="Empty output folder for tagged copies")
    parser.add_argument("--match", choices=("auto", "time", "order"), default="auto")
    parser.add_argument(
        "--attitude-source",
        choices=("body", "camera_capture"),
        default="body",
        help="Use interpolated vehicle body attitude (default) or camera_capture.q",
    )
    parser.add_argument("--tolerance", type=float, default=2.0, help="Timestamp tolerance in seconds")
    parser.add_argument("--mount-roll", type=float, default=0.0, help="Camera mount roll offset, degrees")
    parser.add_argument("--mount-pitch", type=float, default=0.0, help="Camera mount pitch offset, degrees")
    parser.add_argument("--mount-yaw", type=float, default=0.0, help="Camera mount yaw offset, degrees")
    parser.add_argument(
        "--camera-facing",
        type=float,
        default=0.0,
        help="Camera facing clockwise from aircraft nose, degrees (0 forward, 90 right)",
    )
    parser.add_argument(
        "--camera-down-angle",
        type=float,
        default=90.0,
        help="Optical-axis angle below the body horizon, degrees (90 is nadir)",
    )
    parser.add_argument(
        "--image-rotation",
        type=float,
        default=0.0,
        help="Physical image rotation clockwise from landscape, degrees",
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace same-named files in output")
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Include JPEGs in subfolders and preserve their relative folder structure",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    try:
        return process(build_parser().parse_args(argv))
    except TaggerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
