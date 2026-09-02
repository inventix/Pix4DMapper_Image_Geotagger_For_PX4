from dataclasses import replace
import csv
from io import BytesIO
from pathlib import Path
from argparse import Namespace
from datetime import datetime, timezone
from types import SimpleNamespace
import threading

import numpy as np
import pytest
from lxml import etree
from PIL import Image
from scipy.spatial.transform import Rotation

import px4_pix4d_tagger as tagger


def jpeg_fixture() -> bytes:
    buffer = BytesIO()
    image = Image.new("RGB", (64, 48), color=(50, 100, 150))
    exif = Image.Exif()
    exif[36867] = "2026:08:20 12:00:01"
    exif[37521] = "250"
    exif[271] = "SONY"
    exif[272] = "DSC-RX0M2"
    exif[37386] = (24, 1)
    image.save(buffer, format="JPEG", quality=93, exif=exif)
    return buffer.getvalue()


def process_args(log_path, image_dir, output_dir, **overrides):
    values = {
        "log": log_path,
        "images": image_dir,
        "output": output_dir,
        "match": "auto",
        "tolerance": 2.0,
        "mount_roll": 0.0,
        "mount_pitch": 0.0,
        "mount_yaw": 0.0,
        "camera_facing": 0.0,
        "camera_down_angle": 90.0,
        "image_rotation": 0.0,
        "attitude_source": "body",
        "overwrite": False,
        "recursive": False,
    }
    values.update(overrides)
    return Namespace(**values)


def capture(index=0, time_s=10.0, yaw=0.0, pitch=0.0, roll=0.0):
    x, y, z, w = Rotation.from_euler("ZYX", [yaw, pitch, roll], degrees=True).as_quat()
    return tagger.Capture(
        index=index,
        sequence=index + 1,
        time_s=time_s,
        latitude=44.0521,
        longitude=-123.0868,
        altitude_m=153.42,
        ground_distance_m=30.5,
        quaternion_wxyz=(w, x, y, z),
        result=1,
    )


def test_default_nadir_mount_preserves_vehicle_ypr():
    got = tagger.pix4d_ypr(capture(yaw=127, pitch=-8, roll=11).quaternion_wxyz)
    assert np.allclose(got, (127, -8, 11), atol=1e-8)


def test_new_default_fixed_mount_equals_documented_nadir_frame():
    got = tagger.fixed_camera_body_from_image(0, 90, 0)
    assert np.allclose(got, tagger.PIX4D_DEFAULT_BODY_FROM_IMAGE, atol=1e-12)


@pytest.mark.parametrize("facing", [0, 30, 90, 180, 270, 359.9, -45])
@pytest.mark.parametrize("down", [-90, -30, 0, 35, 89.9, 90])
@pytest.mark.parametrize("rotation", [0, 90, 180, 270, 17.5])
def test_fixed_camera_frame_is_right_handed_orthonormal(facing, down, rotation):
    matrix = tagger.fixed_camera_body_from_image(facing, down, rotation)
    assert np.allclose(matrix.T @ matrix, np.eye(3), atol=1e-10)
    assert np.linalg.det(matrix) == pytest.approx(1.0, abs=1e-10)


@pytest.mark.parametrize(
    ("facing", "expected_yaw"),
    [(0, 0), (90, 90), (180, 180), (270, 270), (-90, 270)],
)
def test_level_aircraft_nadir_camera_facing_controls_pix4d_yaw(facing, expected_yaw):
    got = tagger.pix4d_ypr(
        capture().quaternion_wxyz,
        camera_facing_deg=facing,
        camera_down_angle_deg=90,
        image_rotation_deg=0,
    )
    assert np.allclose(got, (expected_yaw, 0, 0), atol=1e-8)


@pytest.mark.parametrize(
    ("rotation", "expected_yaw"),
    [(0, 0), (90, 90), (180, 180), (270, 270)],
)
def test_nadir_landscape_and_portrait_rotation_controls_image_top(rotation, expected_yaw):
    got = tagger.pix4d_ypr(
        capture().quaternion_wxyz,
        camera_facing_deg=0,
        camera_down_angle_deg=90,
        image_rotation_deg=rotation,
    )
    assert np.allclose(got, (expected_yaw, 0, 0), atol=1e-8)


@pytest.mark.parametrize(("down", "expected_pitch"), [(90, 0), (60, 30), (45, 45), (30, 60)])
def test_oblique_forward_mount_converts_to_pix4d_pitch_convention(down, expected_pitch):
    got = tagger.pix4d_ypr(
        capture().quaternion_wxyz,
        camera_facing_deg=0,
        camera_down_angle_deg=down,
        image_rotation_deg=0,
    )
    assert got[0] == pytest.approx(0, abs=1e-8)
    assert got[1] == pytest.approx(expected_pitch, abs=1e-8)
    assert got[2] == pytest.approx(0, abs=1e-8)


@pytest.mark.parametrize(
    ("body_ypr", "mount"),
    [
        ((127, -8, 11), (0, 90, 0)),
        ((15, 20, -30), (40, 65, 90)),
        ((305, -35, 42), (215, 25, 270)),
        ((0, 0, 0), (17.2, -20, 33)),
    ],
)
def test_reported_ypr_reconstructs_exact_camera_frame(body_ypr, mount):
    event = capture(yaw=body_ypr[0], pitch=body_ypr[1], roll=body_ypr[2])
    got = tagger.pix4d_ypr(
        event.quaternion_wxyz,
        camera_facing_deg=mount[0],
        camera_down_angle_deg=mount[1],
        image_rotation_deg=mount[2],
    )
    w, x, y, z = event.quaternion_wxyz
    body_to_ned = Rotation.from_quat([x, y, z, w]).as_matrix()
    expected_image_to_ned = body_to_ned @ tagger.fixed_camera_body_from_image(*mount)
    equivalent = tagger.rotation_matrix_xyz(got[2], got[1], got[0])
    reconstructed_image_to_ned = equivalent @ tagger.PIX4D_DEFAULT_BODY_FROM_IMAGE
    assert np.allclose(reconstructed_image_to_ned, expected_image_to_ned, atol=1e-8)


@pytest.mark.parametrize("down", [-90.01, 90.01, -180, 180])
def test_invalid_downward_angle_is_rejected(down):
    with pytest.raises(tagger.TaggerError, match="between -90 and 90"):
        tagger.fixed_camera_body_from_image(0, down, 0)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_mount_value_is_rejected(value):
    with pytest.raises(tagger.TaggerError, match="finite"):
        tagger.fixed_camera_body_from_image(value, 90, 0)


def test_partial_new_mount_arguments_are_rejected():
    with pytest.raises(tagger.TaggerError, match="supplied together"):
        tagger.pix4d_ypr(capture().quaternion_wxyz, camera_facing_deg=0)


def test_body_quaternion_interpolation_midpoint_uses_shortest_rotation():
    q0 = (1.0, 0.0, 0.0, 0.0)
    x, y, z, w = Rotation.from_euler("Z", 90, degrees=True).as_quat()
    q1 = (w, x, y, z)
    got = tagger.interpolate_body_quaternion([10.0, 10.2], [q0, q1], 10.1)
    rotation = Rotation.from_quat([got[1], got[2], got[3], got[0]])
    assert rotation.as_euler("ZYX", degrees=True)[0] == pytest.approx(45.0, abs=1e-8)


def test_body_quaternion_interpolation_normalizes_exact_sample():
    assert tagger.interpolate_body_quaternion([10.0], [(2.0, 0, 0, 0)], 10.0) == (1.0, 0.0, 0.0, 0.0)


def test_body_quaternion_interpolation_rejects_outside_range():
    with pytest.raises(tagger.TaggerError, match="outside"):
        tagger.interpolate_body_quaternion([10.0, 11.0], [(1, 0, 0, 0), (1, 0, 0, 0)], 9.0)


def test_body_quaternion_interpolation_rejects_large_logging_gap():
    with pytest.raises(tagger.TaggerError, match="sufficiently close"):
        tagger.interpolate_body_quaternion([10.0, 11.0], [(1, 0, 0, 0), (1, 0, 0, 0)], 10.5)


def test_body_quaternion_interpolation_ignores_invalid_samples():
    got = tagger.interpolate_body_quaternion(
        [9.0, 10.0, 10.1],
        [(float("nan"), 0, 0, 0), (1, 0, 0, 0), (1, 0, 0, 0)],
        10.05,
    )
    assert np.allclose(got, (1, 0, 0, 0), atol=1e-12)


def synthetic_ulog():
    camera_data = {
        "timestamp": np.array([1_000_000], dtype=np.uint64),
        "timestamp_utc": np.array([1_700_000_000_000_000], dtype=np.uint64),
        "seq": np.array([7]),
        "lat": np.array([44.0]),
        "lon": np.array([-123.0]),
        "alt": np.array([100.0]),
        "ground_distance": np.array([30.0]),
        "result": np.array([1]),
        "q[0]": np.array([0.70710678]),
        "q[1]": np.array([0.0]),
        "q[2]": np.array([0.0]),
        "q[3]": np.array([0.70710678]),
    }
    attitude_data = {
        "timestamp_sample": np.array([900_000, 1_100_000], dtype=np.uint64),
        "q[0]": np.array([1.0, 1.0]),
        "q[1]": np.array([0.0, 0.0]),
        "q[2]": np.array([0.0, 0.0]),
        "q[3]": np.array([0.0, 0.0]),
    }
    return SimpleNamespace(
        data_list=[
            SimpleNamespace(name="camera_capture", data=camera_data),
            SimpleNamespace(name="vehicle_attitude", data=attitude_data),
        ]
    )


def test_load_captures_body_source_replaces_camera_gimbal_quaternion(tmp_path, monkeypatch):
    log = tmp_path / "flight.ulg"
    log.write_bytes(b"synthetic")
    monkeypatch.setattr(tagger, "ULog", lambda *args, **kwargs: synthetic_ulog())
    loaded = tagger.load_captures(log, attitude_source="body")
    assert loaded[0].quaternion_wxyz == pytest.approx((1, 0, 0, 0))
    assert loaded[0].time_s == pytest.approx(1_700_000_000.0)


def test_load_captures_camera_source_keeps_camera_capture_quaternion(tmp_path, monkeypatch):
    log = tmp_path / "flight.ulg"
    log.write_bytes(b"synthetic")
    monkeypatch.setattr(tagger, "ULog", lambda *args, **kwargs: synthetic_ulog())
    loaded = tagger.load_captures(log, attitude_source="camera_capture")
    assert loaded[0].quaternion_wxyz == pytest.approx((0.70710678, 0, 0, 0.70710678))


def test_load_captures_body_source_requires_vehicle_attitude(tmp_path, monkeypatch):
    log = tmp_path / "flight.ulg"
    log.write_bytes(b"synthetic")
    fake = synthetic_ulog()
    fake.data_list = [fake.data_list[0]]
    monkeypatch.setattr(tagger, "ULog", lambda *args, **kwargs: fake)
    with pytest.raises(tagger.TaggerError, match="vehicle_attitude is missing"):
        tagger.load_captures(log, attitude_source="body")


def test_load_captures_explains_unbracketed_body_attitude(tmp_path, monkeypatch):
    log = tmp_path / "flight.ulg"
    log.write_bytes(b"synthetic")
    fake = synthetic_ulog()
    fake.data_list[1].data["timestamp_sample"] = np.array([2_000_000, 2_100_000])
    monkeypatch.setattr(tagger, "ULog", lambda *args, **kwargs: fake)
    with pytest.raises(tagger.TaggerError, match="without nearby vehicle_attitude samples"):
        tagger.load_captures(log, attitude_source="body")


def test_tagged_jpeg_has_verified_metadata_and_same_scan():
    source = jpeg_fixture()
    event = capture(yaw=42, pitch=3, roll=-4)
    ypr = tagger.pix4d_ypr(event.quaternion_wxyz)
    tagged = tagger.write_metadata(source, event, ypr)
    tagger.verify_output(source, tagged, event, ypr)
    assert tagger.jpeg_scan_bytes(source) == tagger.jpeg_scan_bytes(tagged)


def test_retag_replaces_blocks_instead_of_duplicating():
    source = jpeg_fixture()
    first = capture(yaw=10)
    once = tagger.write_metadata(source, first, tagger.pix4d_ypr(first.quaternion_wxyz))
    second = replace(first, latitude=44.1, longitude=-123.2, altitude_m=200, quaternion_wxyz=capture(yaw=20).quaternion_wxyz)
    twice = tagger.write_metadata(once, second, tagger.pix4d_ypr(second.quaternion_wxyz))
    tagger.verify_output(source, twice, second, tagger.pix4d_ypr(second.quaternion_wxyz))
    payloads = [seg[3][4:] for seg in tagger._jpeg_segments(twice) if seg[0] == 0xE1]
    assert sum(p.startswith(b"Exif\x00\x00") for p in payloads) == 1
    assert sum(p.startswith(tagger.XMP_HEADER) for p in payloads) == 1


@pytest.mark.parametrize(
    ("latitude", "longitude", "altitude"),
    [
        (0, 0, 0),
        (44.0521, -123.0868, 153.42),
        (-33.8688, 151.2093, 12.25),
        (89.999999, -179.999999, -25.5),
    ],
)
def test_exif_gps_round_trip_edge_cases(latitude, longitude, altitude):
    source = jpeg_fixture()
    event = replace(capture(), latitude=latitude, longitude=longitude, altitude_m=altitude)
    ypr = tagger.pix4d_ypr(event.quaternion_wxyz)
    tagged = tagger.write_metadata(source, event, ypr)
    tagger.verify_output(source, tagged, event, ypr)


def test_existing_non_pix4d_xmp_content_survives_retag():
    root = tagger._new_xmp_root()
    description = root.xpath("//*[local-name()='Description']")[0]
    description.set("CreatorTool", "synthetic-test")
    existing = etree.tostring(root, xml_declaration=False, encoding="UTF-8")
    event = capture(yaw=22)
    rebuilt = tagger.build_xmp(existing, event, (22, 1, 2))
    parsed = etree.fromstring(rebuilt[len(tagger.XMP_HEADER) :])
    description = parsed.xpath("//*[local-name()='Description']")[0]
    assert description.get("CreatorTool") == "synthetic-test"


def test_corrupted_scan_data_is_detected():
    source = jpeg_fixture()
    event = capture()
    ypr = tagger.pix4d_ypr(event.quaternion_wxyz)
    tagged = bytearray(tagger.write_metadata(source, event, ypr))
    tagged[-3] ^= 1
    with pytest.raises(tagger.TaggerError, match="scan data changed"):
        tagger.verify_output(source, bytes(tagged), event, ypr)


def test_time_matching_estimates_large_clock_offset():
    images = [
        tagger.ImageRecord(Path(f"IMG_{i}.JPG"), 1_700_000_000.0 + i * 2.0)
        for i in range(5)
    ]
    captures = [replace(capture(i, 100.0 + i * 2.0), sequence=i) for i in range(5)]
    matches, offset = tagger.match_by_time(images, captures, tolerance_s=0.2)
    assert len(matches) == 5
    assert abs(offset - 1_699_999_900.0) < 1e-6


def test_order_matching_rejects_ambiguous_counts():
    images = [tagger.ImageRecord(Path("a.jpg"), None)]
    captures = [capture(0), capture(1)]
    try:
        tagger.match_by_order(images, captures)
    except tagger.TaggerError as exc:
        assert "equal counts" in str(exc)
    else:
        raise AssertionError("Expected TaggerError")


def test_auto_matching_falls_back_to_order_without_exif_times():
    images = [tagger.ImageRecord(Path("a.jpg"), None), tagger.ImageRecord(Path("b.jpg"), None)]
    captures = [capture(0), capture(1)]
    matches, offset = tagger.match_images(images, captures, "auto", 2.0)
    assert [match.image.path.name for match in matches] == ["a.jpg", "b.jpg"]
    assert offset is None


def test_auto_matching_rejects_incomplete_ambiguous_dataset():
    images = [tagger.ImageRecord(Path("a.jpg"), None)]
    captures = [capture(0), capture(1)]
    with pytest.raises(tagger.TaggerError, match="could not match every image"):
        tagger.match_images(images, captures, "auto", 2.0)


def test_inventory_classifies_mixed_camera_files_and_subfolders(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "Alpha.JPE").write_bytes(jpeg_fixture())
    (tmp_path / "nested" / "not_a_sony_name.jpeg").write_bytes(jpeg_fixture())
    (tmp_path / "frame.DNG").write_bytes(b"raw placeholder")
    (tmp_path / "preview.TIFF").write_bytes(b"tiff placeholder")
    (tmp_path / "notes.txt").write_text("notes", encoding="utf-8")
    top_only = tagger.inventory_images(tmp_path)
    recursive = tagger.inventory_images(tmp_path, recursive=True)
    assert top_only.jpeg_paths == (Path("Alpha.JPE"),)
    assert recursive.jpeg_paths == (Path("Alpha.JPE"), Path("nested/not_a_sony_name.jpeg"))
    assert recursive.raw_paths == (Path("frame.DNG"),)
    assert recursive.other_image_paths == (Path("preview.TIFF"),)
    assert recursive.ignored_file_count == 1
    assert recursive.nested_jpeg_count == 1


def test_load_images_accepts_arbitrary_names_and_camera_makes(tmp_path):
    first = tmp_path / "survey-a.jpe"
    second = tmp_path / "CAMERA_000002.JPEG"
    first.write_bytes(jpeg_fixture())
    second.write_bytes(jpeg_fixture())
    records = tagger.load_images(tmp_path)
    assert [record.path.name for record in records] == ["CAMERA_000002.JPEG", "survey-a.jpe"]
    assert all(record.time_s is not None for record in records)


def test_load_images_rejects_file_disguised_with_jpeg_extension(tmp_path):
    image = Image.new("RGB", (10, 10), "red")
    image.save(tmp_path / "actually_png.jpg", format="PNG")
    with pytest.raises(tagger.TaggerError, match="actually PNG"):
        tagger.load_images(tmp_path)


def test_load_images_explains_that_raw_must_be_converted(tmp_path):
    (tmp_path / "flight_image.CR3").write_bytes(b"raw placeholder")
    with pytest.raises(tagger.TaggerError, match="Convert/export them to JPEG first"):
        tagger.load_images(tmp_path)


@pytest.mark.parametrize(("method", "tolerance"), [("guess", 2.0), ("auto", -0.1), ("time", float("nan"))])
def test_matching_rejects_invalid_controls(method, tolerance):
    with pytest.raises(tagger.TaggerError):
        tagger.match_images([tagger.ImageRecord(Path("a.jpg"), 1.0)], [capture()], method, tolerance)


def test_disk_capacity_check_reports_required_and_available_space(tmp_path, monkeypatch):
    monkeypatch.setattr(tagger.shutil, "disk_usage", lambda _path: SimpleNamespace(free=1_048_576))
    with pytest.raises(tagger.TaggerError, match="Need about 20 MB"):
        tagger.verify_output_capacity(tmp_path / "new" / "tagged", 20 * 1_048_576)


def test_end_to_end_writes_copy_and_report(tmp_path, monkeypatch):
    image_dir = tmp_path / "original"
    output_dir = tmp_path / "tagged"
    image_dir.mkdir()
    (image_dir / "DSC00001.JPG").write_bytes(jpeg_fixture())
    log_path = tmp_path / "flight.ulg"
    log_path.write_bytes(b"test placeholder")
    event = capture(time_s=100.0, yaw=75, pitch=2, roll=-1)
    monkeypatch.setattr(tagger, "load_captures", lambda _, attitude_source="body": [event])
    progress_events = []
    args = process_args(
        log_path,
        image_dir,
        output_dir,
        progress_callback=lambda *event: progress_events.append(event),
    )
    assert tagger.process(args) == 0
    tagged_path = output_dir / "DSC00001.JPG"
    assert tagged_path.exists()
    assert (output_dir / "tagging_report.csv").exists()
    assert progress_events[-1] == (1, 1, "DSC00001.JPG", "Verified and staged image")
    with (output_dir / "tagging_report.csv").open(newline="", encoding="utf-8") as stream:
        report = next(csv.DictReader(stream))
    assert report["image"] == "DSC00001.JPG"
    assert report["source_bytes"] == str(len(jpeg_fixture()))
    assert report["source_sha256"]
    assert report["attitude_source"] == "body"
    assert report["match_method"] == "auto"
    tagger.verify_output(
        (image_dir / "DSC00001.JPG").read_bytes(),
        tagged_path.read_bytes(),
        event,
        tagger.pix4d_ypr(event.quaternion_wxyz),
    )


def test_recursive_end_to_end_preserves_relative_folders(tmp_path, monkeypatch):
    image_dir = tmp_path / "original"
    output_dir = tmp_path / "tagged"
    (image_dir / "camera_a").mkdir(parents=True)
    (image_dir / "camera_b").mkdir()
    (image_dir / "camera_a" / "IMG_1.JPG").write_bytes(jpeg_fixture())
    (image_dir / "camera_b" / "IMG_1.JPG").write_bytes(jpeg_fixture())
    log_path = tmp_path / "flight.ulg"
    log_path.write_bytes(b"placeholder")
    monkeypatch.setattr(
        tagger,
        "load_captures",
        lambda _, attitude_source="body": [capture(0), capture(1)],
    )
    args = process_args(log_path, image_dir, output_dir, match="order", recursive=True)
    assert tagger.process(args) == 0
    assert (output_dir / "camera_a" / "IMG_1.JPG").is_file()
    assert (output_dir / "camera_b" / "IMG_1.JPG").is_file()
    with (output_dir / "tagging_report.csv").open(newline="", encoding="utf-8") as stream:
        names = [row["image"] for row in csv.DictReader(stream)]
    assert names == ["camera_a/IMG_1.JPG", "camera_b/IMG_1.JPG"]


def test_cancellation_removes_staging_and_publishes_no_images(tmp_path, monkeypatch):
    image_dir = tmp_path / "original"
    output_dir = tmp_path / "tagged"
    image_dir.mkdir()
    (image_dir / "IMG_1.JPG").write_bytes(jpeg_fixture())
    log_path = tmp_path / "flight.ulg"
    log_path.write_bytes(b"placeholder")
    monkeypatch.setattr(tagger, "load_captures", lambda _, attitude_source="body": [capture()])
    cancel_event = threading.Event()

    def cancel_after_staging(_current, _total, _name, stage):
        if stage == "Verified and staged image":
            cancel_event.set()

    args = process_args(
        log_path,
        image_dir,
        output_dir,
        cancel_event=cancel_event,
        progress_callback=cancel_after_staging,
    )
    with pytest.raises(tagger.TaggerCancelled, match="No staged images were published"):
        tagger.process(args)
    assert not output_dir.exists()
    assert not list(tmp_path.glob(".tagged.staging-*"))


def test_parser_supports_recursive_image_discovery():
    args = tagger.build_parser().parse_args(["flight.ulg", "images", "output", "--recursive"])
    assert args.recursive is True


def test_process_rejects_output_inside_source_tree(tmp_path):
    image_dir = tmp_path / "original"
    image_dir.mkdir()
    log_path = tmp_path / "flight.ulg"
    log_path.write_bytes(b"placeholder")
    with pytest.raises(tagger.TaggerError, match="inside it"):
        tagger.process(process_args(log_path, image_dir, image_dir / "tagged"))


def test_process_rejects_output_path_that_is_a_file(tmp_path):
    image_dir = tmp_path / "original"
    image_dir.mkdir()
    log_path = tmp_path / "flight.ulg"
    log_path.write_bytes(b"placeholder")
    output_path = tmp_path / "tagged"
    output_path.write_text("not a folder", encoding="utf-8")
    with pytest.raises(tagger.TaggerError, match="not a folder"):
        tagger.process(process_args(log_path, image_dir, output_path))


def test_process_rejects_nonempty_output_without_overwrite(tmp_path):
    image_dir = tmp_path / "original"
    image_dir.mkdir()
    output_dir = tmp_path / "tagged"
    output_dir.mkdir()
    (output_dir / "keep.txt").write_text("existing", encoding="utf-8")
    log_path = tmp_path / "flight.ulg"
    log_path.write_bytes(b"placeholder")
    with pytest.raises(tagger.TaggerError, match="not empty"):
        tagger.process(process_args(log_path, image_dir, output_dir))


def test_overwrite_publish_preserves_unrelated_output_files(tmp_path, monkeypatch):
    image_dir = tmp_path / "original"
    image_dir.mkdir()
    (image_dir / "IMG_1.JPG").write_bytes(jpeg_fixture())
    output_dir = tmp_path / "tagged"
    output_dir.mkdir()
    (output_dir / "keep.txt").write_text("existing", encoding="utf-8")
    (output_dir / "IMG_1.JPG").write_bytes(b"old output")
    log_path = tmp_path / "flight.ulg"
    log_path.write_bytes(b"placeholder")
    monkeypatch.setattr(tagger, "load_captures", lambda _, attitude_source="body": [capture()])
    assert tagger.process(process_args(log_path, image_dir, output_dir, overwrite=True)) == 0
    assert (output_dir / "keep.txt").read_text(encoding="utf-8") == "existing"
    assert (output_dir / "IMG_1.JPG").read_bytes() != b"old output"
    assert not list(tmp_path.glob(".tagged.previous-*"))


def test_cancellation_preserves_entire_existing_output_folder(tmp_path, monkeypatch):
    image_dir = tmp_path / "original"
    image_dir.mkdir()
    (image_dir / "IMG_1.JPG").write_bytes(jpeg_fixture())
    output_dir = tmp_path / "tagged"
    output_dir.mkdir()
    (output_dir / "IMG_1.JPG").write_bytes(b"known old output")
    (output_dir / "keep.txt").write_text("existing", encoding="utf-8")
    log_path = tmp_path / "flight.ulg"
    log_path.write_bytes(b"placeholder")
    monkeypatch.setattr(tagger, "load_captures", lambda _, attitude_source="body": [capture()])
    cancel_event = threading.Event()

    def cancel_after_staging(_current, _total, _name, stage):
        if stage == "Verified and staged image":
            cancel_event.set()

    args = process_args(
        log_path,
        image_dir,
        output_dir,
        overwrite=True,
        cancel_event=cancel_event,
        progress_callback=cancel_after_staging,
    )
    with pytest.raises(tagger.TaggerCancelled):
        tagger.process(args)
    assert (output_dir / "IMG_1.JPG").read_bytes() == b"known old output"
    assert (output_dir / "keep.txt").read_text(encoding="utf-8") == "existing"
    assert not list(tmp_path.glob(".tagged.staging-*"))


def test_conversion_log_contains_version_result_and_visible_lines(tmp_path):
    started = datetime(2026, 9, 2, 12, 34, 56, tzinfo=timezone.utc)
    path = tagger.save_conversion_log(
        tmp_path,
        ["12:34:56  Flight log: flight.ulg", "12:35:01  FINAL RESULT: SUCCESS"],
        "Verification build 0.4.1",
        "SUCCESS — all output images verified",
        created_at=started,
    )
    assert path.name == "conversion_log_20260902_123456.txt"
    text = path.read_text(encoding="utf-8")
    assert "Software version: Verification build 0.4.1" in text
    assert "Final result: SUCCESS — all output images verified" in text
    assert "Flight log: flight.ulg" in text
    assert "FINAL RESULT: SUCCESS" in text


def test_conversion_log_never_overwrites_an_existing_log(tmp_path):
    started = datetime(2026, 9, 2, 12, 34, 56, tzinfo=timezone.utc)
    first = tagger.save_conversion_log(tmp_path, ["first"], "0.4.1", "SUCCESS", started)
    second = tagger.save_conversion_log(tmp_path, ["second"], "0.4.1", "FAILED", started)
    assert first.name == "conversion_log_20260902_123456.txt"
    assert second.name == "conversion_log_20260902_123456_2.txt"
    assert "first" in first.read_text(encoding="utf-8")
    assert "second" in second.read_text(encoding="utf-8")


def test_conversion_log_leaves_no_temporary_file(tmp_path):
    tagger.save_conversion_log(tmp_path, ["complete"], "0.4.1", "SUCCESS")
    assert not [path for path in tmp_path.iterdir() if path.name.startswith(".conversion_log_")]
