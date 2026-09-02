from dataclasses import replace
from io import BytesIO
from pathlib import Path
from argparse import Namespace

import numpy as np
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


def test_end_to_end_writes_copy_and_report(tmp_path, monkeypatch):
    image_dir = tmp_path / "original"
    output_dir = tmp_path / "tagged"
    image_dir.mkdir()
    (image_dir / "DSC00001.JPG").write_bytes(jpeg_fixture())
    log_path = tmp_path / "flight.ulg"
    log_path.write_bytes(b"test placeholder")
    event = capture(time_s=100.0, yaw=75, pitch=2, roll=-1)
    monkeypatch.setattr(tagger, "load_captures", lambda _: [event])
    progress_events = []
    args = Namespace(
        log=log_path,
        images=image_dir,
        output=output_dir,
        match="auto",
        tolerance=2.0,
        mount_roll=0.0,
        mount_pitch=0.0,
        mount_yaw=0.0,
        overwrite=False,
        progress_callback=lambda *event: progress_events.append(event),
    )
    assert tagger.process(args) == 0
    tagged_path = output_dir / "DSC00001.JPG"
    assert tagged_path.exists()
    assert (output_dir / "tagging_report.csv").exists()
    assert progress_events[-1] == (1, 1, "DSC00001.JPG", "Verified and saved image")
    tagger.verify_output(
        (image_dir / "DSC00001.JPG").read_bytes(),
        tagged_path.read_bytes(),
        event,
        tagger.pix4d_ypr(event.quaternion_wxyz),
    )
