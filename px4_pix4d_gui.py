#!/usr/bin/env python3
"""Student-friendly graphical interface for the PX4 Pix4D JPEG tagger."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import traceback
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, X, BooleanVar, StringVar, Tk, filedialog, messagebox, ttk
from tkinter import Button, Checkbutton, Entry, Frame, Label
from tkinter.scrolledtext import ScrolledText

import px4_pix4d_tagger as engine


APP_TITLE = "PX4 → Pix4D Image Tagger"
APP_VERSION = "Verification build 0.4.1"

# GitHub-dark-inspired palette. These are explicit rather than OS theme colors
# so the student interface is consistent on Windows, macOS, and Linux.
BG = "#0d1117"
SURFACE = "#161b22"
SURFACE_2 = "#21262d"
INPUT_BG = "#0d1117"
BORDER = "#30363d"
TEXT = "#f0f6fc"
MUTED = "#8b949e"
SUBTLE = "#6e7681"
BLUE = "#2f81f7"
BLUE_HOVER = "#58a6ff"
GREEN = "#238636"
GREEN_HOVER = "#2ea043"
RED = "#f85149"
YELLOW = "#d29922"
LOG_BG = "#010409"

FACING_CHOICES = {
    "Forward / nose (0°)": 0.0,
    "Right wing (90°)": 90.0,
    "Rear / tail (180°)": 180.0,
    "Left wing (270°)": 270.0,
    "Custom angle": None,
}
ATTITUDE_SOURCE_CHOICES = {
    "Aircraft body — fixed camera (recommended)": "body",
    "Logged camera/gimbal — use camera_capture.q": "camera_capture",
}
LAYOUT_CHOICES = {
    "Landscape — top edge toward camera facing": 0.0,
    "Portrait clockwise — top edge toward camera right": 90.0,
    "Landscape inverted — top edge opposite camera facing": 180.0,
    "Portrait counter-clockwise — top edge toward camera left": 270.0,
}


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def load_course_config() -> dict:
    defaults = {
        "mount_roll_deg": 0.0,
        "mount_pitch_deg": 0.0,
        "mount_yaw_deg": 0.0,
        "match_method": "auto",
        "timestamp_tolerance_s": 2.0,
        "camera_facing_deg": 0.0,
        "camera_down_angle_deg": 90.0,
        "image_rotation_deg": 0.0,
        "attitude_source": "body",
    }
    path = application_directory() / "course_config.json"
    if not path.exists():
        return defaults
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        defaults.update({key: loaded[key] for key in defaults if key in loaded})
    except Exception as exc:
        messagebox.showwarning(APP_TITLE, f"Could not read course_config.json:\n{exc}\n\nDefaults will be used.")
    return defaults


class QueueWriter:
    """Convert print() chunks from a worker thread into timestamped log lines."""

    def __init__(self, messages: queue.Queue, stream_kind: str):
        self.messages = messages
        self.stream_kind = stream_kind
        self.buffer = ""

    def write(self, text: str) -> int:
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            self._emit(line)
        return len(text)

    def _emit(self, line: str) -> None:
        self.messages.put(("log", datetime.now().strftime("%H:%M:%S"), line, self.stream_kind))

    def flush(self) -> None:
        if self.buffer:
            self._emit(self.buffer)
            self.buffer = ""


class TaggerApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.configure(bg=BG)
        self.root.geometry("980x760")
        self.root.minsize(820, 680)
        self.config = load_course_config()
        self.messages: queue.Queue = queue.Queue()
        self.worker: threading.Thread | None = None
        self.session_log_lines: list[str] = []
        self.session_started: datetime | None = None
        self._progress_is_determinate = False

        self.log_var = StringVar()
        self.images_var = StringVar()
        self.output_var = StringVar()
        self.overwrite_var = BooleanVar(value=False)
        source_name = next(
            (name for name, value in ATTITUDE_SOURCE_CHOICES.items() if value == self.config["attitude_source"]),
            next(iter(ATTITUDE_SOURCE_CHOICES)),
        )
        self.attitude_source_var = StringVar(value=source_name)
        self.facing_choice_var = StringVar(value="Forward / nose (0°)")
        self.facing_deg_var = StringVar(value=f'{float(self.config["camera_facing_deg"]):g}')
        self.down_angle_var = StringVar(value=f'{float(self.config["camera_down_angle_deg"]):g}')
        configured_rotation = float(self.config["image_rotation_deg"]) % 360.0
        layout = next((name for name, value in LAYOUT_CHOICES.items() if value == configured_rotation), next(iter(LAYOUT_CHOICES)))
        self.layout_var = StringVar(value=layout)
        self.status_var = StringVar(value="Ready — select one flight log and its original-image folder.")
        self.progress_detail_var = StringVar(value="Waiting for a flight dataset")
        self._build()
        self.root.after(100, self._poll_messages)

    def _build(self) -> None:
        outer = Frame(self.root, bg=BG, padx=28, pady=22)
        outer.pack(fill=BOTH, expand=True)

        header = Frame(outer, bg=BG)
        header.pack(fill=X, pady=(0, 18))
        brand = Frame(header, bg=BG)
        brand.pack(side=LEFT, anchor="n")
        Label(brand, text="PX4", bg=BG, fg=BLUE_HOVER, font=("Helvetica Neue", 12, "bold")).pack(side=LEFT)
        Label(brand, text=" / ", bg=BG, fg=SUBTLE, font=("Helvetica Neue", 12)).pack(side=LEFT)
        Label(brand, text="PIX4D", bg=BG, fg=TEXT, font=("Helvetica Neue", 12, "bold")).pack(side=LEFT)
        Label(
            header,
            text="OPEN SOURCE  •  VERIFICATION BUILD",
            bg=SURFACE_2,
            fg=MUTED,
            padx=12,
            pady=6,
            font=("Helvetica Neue", 9, "bold"),
        ).pack(side=RIGHT, anchor="n")

        Label(
            outer,
            text="Create Pix4D-ready images",
            bg=BG,
            fg=TEXT,
            font=("Helvetica Neue", 24, "bold"),
        ).pack(anchor="w")
        Label(
            outer,
            text=(
                "Match original Sony JPEGs to a PX4 flight log, then write verified GPS and "
                "rigid-camera orientation metadata into new copies. Source images remain untouched."
            ),
            bg=BG,
            fg=MUTED,
            justify=LEFT,
            wraplength=900,
            font=("Helvetica Neue", 11),
        ).pack(anchor="w", pady=(5, 18))

        notebook = ttk.Notebook(outer, style="Dark.TNotebook")
        notebook.pack(fill=X)
        flight_tab = Frame(notebook, bg=BG)
        orientation_tab = Frame(notebook, bg=BG)
        notebook.add(flight_tab, text="  FLIGHT DATA  ")
        notebook.add(orientation_tab, text="  ORIENTATION  ")

        setup_border, setup = self._card(flight_tab)
        setup_border.pack(fill=X)
        Label(setup, text="FLIGHT DATA", bg=SURFACE, fg=MUTED, font=("Helvetica Neue", 9, "bold")).pack(anchor="w")
        self._path_row(setup, "1  PX4 flight log", ".ulg flight record", self.log_var, self._browse_log)
        self._path_row(setup, "2  Original JPEG folder", "Sony camera originals", self.images_var, self._browse_images)
        self._path_row(setup, "3  Tagged output folder", "New Pix4D-ready copies", self.output_var, self._browse_output)

        options = Frame(setup, bg=SURFACE)
        options.pack(fill=X, pady=(8, 0))
        Checkbutton(
            options,
            text="Replace same-named files already in the output folder",
            variable=self.overwrite_var,
            bg=SURFACE,
            fg=MUTED,
            activebackground=SURFACE,
            activeforeground=TEXT,
            selectcolor=INPUT_BG,
            font=("Helvetica Neue", 10),
            highlightthickness=0,
        ).pack(anchor="w")

        orientation_border, orientation = self._card(orientation_tab)
        orientation_border.pack(fill=X)
        Label(
            orientation,
            text="FIXED CAMERA MOUNT",
            bg=SURFACE,
            fg=MUTED,
            font=("Helvetica Neue", 9, "bold"),
        ).pack(anchor="w")
        Label(
            orientation,
            text=(
                "Describe the camera relative to the aircraft body. The tool combines this fixed mount "
                "with the PX4 body-attitude quaternion for every exposure."
            ),
            bg=SURFACE,
            fg=MUTED,
            justify=LEFT,
            wraplength=850,
            font=("Helvetica Neue", 10),
        ).pack(anchor="w", pady=(5, 12))
        self._orientation_combo(
            orientation,
            "Attitude source",
            "Use aircraft body data for a fixed mount",
            self.attitude_source_var,
            tuple(ATTITUDE_SOURCE_CHOICES),
        )
        self._orientation_combo(
            orientation,
            "Camera faces",
            "Direction around the aircraft body",
            self.facing_choice_var,
            tuple(FACING_CHOICES),
            self._facing_selected,
        )
        self._orientation_entry(
            orientation,
            "Facing angle from nose",
            "Degrees clockwise: 0 forward, 90 right, 180 rear, 270 left",
            self.facing_deg_var,
        )
        self._orientation_entry(
            orientation,
            "Downward angle",
            "Degrees below body horizon: 0 forward-looking, 90 straight down (nadir)",
            self.down_angle_var,
        )
        self._orientation_combo(
            orientation,
            "Photo layout",
            "Physical rotation of the camera body",
            self.layout_var,
            tuple(LAYOUT_CHOICES),
        )
        Label(
            orientation,
            text=(
                "Important: DJI gimbal pitch often reports nadir as −90°. Pix4D Camera.Pitch uses 0° "
                "for nadir. This tool performs the 3-D conversion; it does not copy or simply add those angles."
            ),
            bg=SURFACE_2,
            fg=YELLOW,
            justify=LEFT,
            wraplength=830,
            padx=12,
            pady=9,
            font=("Helvetica Neue", 9),
        ).pack(fill=X, pady=(14, 0))

        action_row = Frame(outer, bg=BG)
        action_row.pack(fill=X, pady=14)
        self.run_button = self._button(action_row, "Create Pix4D Images", self._start, primary=True)
        self.run_button.pack(side=LEFT)
        self.open_button = self._button(action_row, "Open Output Folder", self._open_output)
        self.open_button.pack(side=LEFT, padx=(10, 0))
        self.open_button.configure(state="disabled")

        progress_border, progress_card = self._card(outer, padding=14)
        progress_border.pack(fill=X, pady=(0, 14))
        status_row = Frame(progress_card, bg=SURFACE)
        status_row.pack(fill=X)
        self.status_dot = Label(status_row, text="●", bg=SURFACE, fg=SUBTLE, font=("Helvetica Neue", 10))
        self.status_dot.pack(side=LEFT, padx=(0, 7))
        Label(
            status_row,
            textvariable=self.status_var,
            bg=SURFACE,
            fg=TEXT,
            font=("Helvetica Neue", 10, "bold"),
        ).pack(side=LEFT)
        Label(
            status_row,
            textvariable=self.progress_detail_var,
            bg=SURFACE,
            fg=MUTED,
            font=("Menlo", 9),
        ).pack(side=RIGHT)
        self.progress = ttk.Progressbar(progress_card, style="Dark.Horizontal.TProgressbar", mode="determinate", maximum=1)
        self.progress.pack(fill=X, pady=(11, 0))

        log_card_border = Frame(outer, bg=BORDER, padx=1, pady=1)
        log_card_border.pack(fill=BOTH, expand=True)
        log_card = Frame(log_card_border, bg=SURFACE, padx=14, pady=12)
        log_card.pack(fill=BOTH, expand=True)
        log_header = Frame(log_card, bg=SURFACE)
        log_header.pack(fill=X, pady=(0, 8))
        Label(log_header, text="LIVE PROCESS LOG", bg=SURFACE, fg=MUTED, font=("Helvetica Neue", 9, "bold")).pack(side=LEFT)
        Label(log_header, text="copy • metadata • verification", bg=SURFACE, fg=SUBTLE, font=("Menlo", 9)).pack(side=RIGHT)
        self.log_text = ScrolledText(
            log_card,
            height=13,
            wrap="word",
            state="disabled",
            bg=LOG_BG,
            fg=MUTED,
            insertbackground=TEXT,
            selectbackground=BLUE,
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=10,
            font=("Menlo", 9),
        )
        self.log_text.pack(fill=BOTH, expand=True)
        self.log_text.tag_configure("time", foreground=SUBTLE)
        self.log_text.tag_configure("normal", foreground=MUTED)
        self.log_text.tag_configure("detail", foreground="#7d8590")
        self.log_text.tag_configure("step", foreground=BLUE_HOVER)
        self.log_text.tag_configure("success", foreground="#3fb950")
        self.log_text.tag_configure("warning", foreground=YELLOW)
        self.log_text.tag_configure("error", foreground=RED)

        footer = Frame(outer, bg=BG)
        footer.pack(fill=X, pady=(10, 0))
        Label(
            footer,
            text="Orientation is calculated from PX4 body attitude + fixed camera mount",
            bg=BG,
            fg=SUBTLE,
            font=("Menlo", 9),
        ).pack(side=LEFT)
        Label(footer, text=f"{APP_VERSION}  •  Originals untouched", bg=BG, fg=SUBTLE, font=("Menlo", 9)).pack(side=RIGHT)

    def _card(self, parent, padding: int = 16) -> tuple[Frame, Frame]:
        border = Frame(parent, bg=BORDER, padx=1, pady=1)
        inner = Frame(border, bg=SURFACE, padx=padding, pady=padding)
        inner.pack(fill=BOTH, expand=True)
        return border, inner

    def _button(self, parent, text: str, command, primary: bool = False) -> Button:
        normal = GREEN if primary else SURFACE_2
        hover = GREEN_HOVER if primary else BORDER
        button = Button(
            parent,
            text=text,
            command=command,
            bg=normal,
            fg=TEXT,
            activebackground=hover,
            activeforeground=TEXT,
            disabledforeground=SUBTLE,
            relief="flat",
            borderwidth=0,
            padx=18,
            pady=9,
            font=("Helvetica Neue", 10, "bold"),
            cursor="pointinghand" if sys.platform == "darwin" else "hand2",
        )
        button.bind("<Enter>", lambda _event: button.configure(bg=hover) if str(button["state"]) != "disabled" else None)
        button.bind("<Leave>", lambda _event: button.configure(bg=normal) if str(button["state"]) != "disabled" else None)
        return button

    def _path_row(self, parent, label: str, hint: str, variable: StringVar, command) -> None:
        row = Frame(parent, bg=SURFACE)
        row.pack(fill=X, pady=(10, 0))
        title_row = Frame(row, bg=SURFACE)
        title_row.pack(fill=X, pady=(0, 5))
        Label(title_row, text=label, bg=SURFACE, fg=TEXT, font=("Helvetica Neue", 10, "bold")).pack(side=LEFT)
        Label(title_row, text=hint, bg=SURFACE, fg=SUBTLE, font=("Helvetica Neue", 9)).pack(side=RIGHT)
        input_row = Frame(row, bg=SURFACE)
        input_row.pack(fill=X)
        entry = Entry(
            input_row,
            textvariable=variable,
            bg=INPUT_BG,
            fg=TEXT,
            insertbackground=TEXT,
            disabledbackground=INPUT_BG,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=BLUE,
            font=("Menlo", 9),
        )
        entry.pack(side=LEFT, fill=X, expand=True, ipady=8)
        browse = self._button(input_row, "Browse…", command)
        browse.pack(side=RIGHT, padx=(8, 0))

    def _orientation_entry(self, parent, label: str, hint: str, variable: StringVar) -> None:
        row = Frame(parent, bg=SURFACE)
        row.pack(fill=X, pady=(9, 0))
        Label(row, text=label, bg=SURFACE, fg=TEXT, width=24, anchor="w", font=("Helvetica Neue", 10, "bold")).pack(side=LEFT)
        Entry(
            row,
            textvariable=variable,
            width=12,
            bg=INPUT_BG,
            fg=TEXT,
            insertbackground=TEXT,
            relief="flat",
            highlightthickness=1,
            highlightbackground=BORDER,
            highlightcolor=BLUE,
            font=("Menlo", 10),
        ).pack(side=LEFT, ipady=6, padx=(8, 12))
        Label(row, text=hint, bg=SURFACE, fg=SUBTLE, anchor="w", font=("Helvetica Neue", 9)).pack(side=LEFT)

    def _orientation_combo(self, parent, label: str, hint: str, variable: StringVar, values, callback=None) -> None:
        row = Frame(parent, bg=SURFACE)
        row.pack(fill=X, pady=(9, 0))
        Label(row, text=label, bg=SURFACE, fg=TEXT, width=24, anchor="w", font=("Helvetica Neue", 10, "bold")).pack(side=LEFT)
        combo = ttk.Combobox(row, textvariable=variable, values=values, state="readonly", width=47)
        combo.pack(side=LEFT, ipady=4, padx=(8, 12))
        if callback:
            combo.bind("<<ComboboxSelected>>", callback)
        Label(row, text=hint, bg=SURFACE, fg=SUBTLE, anchor="w", font=("Helvetica Neue", 9)).pack(side=LEFT)

    def _facing_selected(self, _event=None) -> None:
        value = FACING_CHOICES.get(self.facing_choice_var.get())
        if value is not None:
            self.facing_deg_var.set(f"{value:g}")

    def _orientation_values(self) -> tuple[str, float, float, float]:
        try:
            facing = float(self.facing_deg_var.get())
            down = float(self.down_angle_var.get())
        except ValueError as exc:
            raise ValueError("Facing angle and downward angle must be numbers.") from exc
        if not -90.0 <= down <= 90.0:
            raise ValueError("Downward angle must be between -90° and 90°.")
        rotation = LAYOUT_CHOICES[self.layout_var.get()]
        source = ATTITUDE_SOURCE_CHOICES[self.attitude_source_var.get()]
        return source, facing, down, rotation

    def _browse_log(self) -> None:
        selected = filedialog.askopenfilename(title="Select PX4 flight log", filetypes=[("PX4 ULog", "*.ulg"), ("All files", "*.*")])
        if selected:
            self.log_var.set(selected)
            self._append_manual(f"Selected flight log: {selected}")
            self._suggest_output()

    def _browse_images(self) -> None:
        selected = filedialog.askdirectory(title="Select folder containing original Sony JPEGs")
        if selected:
            self.images_var.set(selected)
            self._append_manual(f"Selected original-image folder: {selected}")
            self._suggest_output()

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(title="Select or create output folder", mustexist=False)
        if selected:
            self.output_var.set(selected)
            self._append_manual(f"Selected output folder: {selected}")

    def _suggest_output(self) -> None:
        if self.output_var.get().strip() or not self.images_var.get().strip():
            return
        source = Path(self.images_var.get())
        suffix = Path(self.log_var.get()).stem if self.log_var.get().strip() else "Flight"
        suggested = source.parent / f"Pix4D_Tagged_{suffix}"
        self.output_var.set(str(suggested))
        self._append_manual(f"Suggested output folder: {suggested}")

    def _classify_log(self, text: str, stream_kind: str) -> str:
        upper = text.upper()
        if stream_kind == "stderr" and "WARNING" not in upper:
            return "error"
        if "ERROR" in upper or "TRACEBACK" in upper or "FAILED" in upper:
            return "error"
        if "WARNING" in upper or "CAUTION" in upper:
            return "warning"
        if "VERIFIED" in upper or "COMPLETE" in upper or "SUCCESS" in upper:
            return "success"
        if upper.startswith("STEP ") or upper.startswith("===") or (text.startswith("[") and "/" in text):
            return "step"
        if text.startswith("  "):
            return "detail"
        return "normal"

    def _append_log(self, timestamp: str, text: str, stream_kind: str = "stdout") -> None:
        tag = self._classify_log(text, stream_kind)
        self.session_log_lines.append(f"{timestamp}  {text}")
        self.log_text.configure(state="normal")
        self.log_text.insert(END, f"{timestamp}  ", "time")
        self.log_text.insert(END, text + "\n", tag)
        self.log_text.see(END)
        self.log_text.configure(state="disabled")

    def _append_manual(self, text: str) -> None:
        self._append_log(datetime.now().strftime("%H:%M:%S"), text)

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        log = Path(self.log_var.get().strip())
        images = Path(self.images_var.get().strip())
        output_text = self.output_var.get().strip()
        if not log.is_file() or log.suffix.lower() != ".ulg":
            messagebox.showerror(APP_TITLE, "Select a valid PX4 .ulg flight log.")
            return
        if not images.is_dir():
            messagebox.showerror(APP_TITLE, "Select the folder containing the original JPEGs.")
            return
        if not output_text:
            messagebox.showerror(APP_TITLE, "Select an output folder.")
            return
        output = Path(output_text)
        try:
            attitude_source, camera_facing, camera_down_angle, image_rotation = self._orientation_values()
        except (ValueError, KeyError) as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return
        jpeg_count = sum(1 for p in images.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg"})
        if jpeg_count == 0:
            messagebox.showerror(APP_TITLE, "The original-image folder contains no JPEG files.")
            return
        if not messagebox.askokcancel(
            APP_TITLE,
            f"Process {jpeg_count} JPEGs from this flight?\n\nOriginals:\n{images}\n\nTagged copies:\n{output}",
        ):
            return

        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", END)
        self.log_text.configure(state="disabled")
        self.session_log_lines = []
        self.session_started = datetime.now().astimezone()
        self._append_manual(f"{APP_TITLE} — {APP_VERSION}")
        self._append_manual(f"Flight log: {log}")
        self._append_manual(f"Original JPEG folder: {images}")
        self._append_manual(f"Tagged output folder: {output}")
        self._append_manual(
            "Orientation: "
            f"source={attitude_source}; facing={camera_facing:g}°; "
            f"down={camera_down_angle:g}°; image rotation={image_rotation:g}°"
        )
        self.status_var.set("Starting image-tagging session…")
        self.progress_detail_var.set(f"Preparing {jpeg_count} images")
        self.status_dot.configure(fg=BLUE_HOVER)
        self.run_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self._progress_is_determinate = False
        self.progress.configure(mode="indeterminate", maximum=max(jpeg_count, 1), value=0)
        self.progress.start(12)
        args = Namespace(
            log=log,
            images=images,
            output=output,
            match=str(self.config["match_method"]),
            attitude_source=attitude_source,
            tolerance=float(self.config["timestamp_tolerance_s"]),
            mount_roll=float(self.config["mount_roll_deg"]),
            mount_pitch=float(self.config["mount_pitch_deg"]),
            mount_yaw=float(self.config["mount_yaw_deg"]),
            camera_facing=camera_facing,
            camera_down_angle=camera_down_angle,
            image_rotation=image_rotation,
            overwrite=bool(self.overwrite_var.get()),
            progress_callback=self._queue_progress,
        )
        self.worker = threading.Thread(target=self._run_worker, args=(args,), daemon=True)
        self.worker.start()

    def _queue_progress(self, current: int, total: int, image_name: str, stage: str) -> None:
        self.messages.put(("progress", current, total, image_name, stage))

    def _run_worker(self, args: Namespace) -> None:
        stdout_writer = QueueWriter(self.messages, "stdout")
        stderr_writer = QueueWriter(self.messages, "stderr")
        try:
            with redirect_stdout(stdout_writer), redirect_stderr(stderr_writer):
                result = engine.process(args)
            stdout_writer.flush()
            stderr_writer.flush()
            self.messages.put(("done", result, str(args.output)))
        except Exception as exc:
            stdout_writer.flush()
            stderr_writer.flush()
            self.messages.put(("error", str(exc), traceback.format_exc()))

    def _show_progress(self, current: int, total: int, image_name: str, stage: str) -> None:
        if total > 0:
            if not self._progress_is_determinate:
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self._progress_is_determinate = True
            self.progress.configure(maximum=total, value=current if stage.startswith("Verified") else max(current - 1, 0))
            self.status_var.set(stage)
            self.progress_detail_var.set(f"Image {current} of {total}  •  {image_name}")
        else:
            self.status_var.set(stage)
            self.progress_detail_var.set(image_name)

    def _poll_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                kind = message[0]
                if kind == "log":
                    self._append_log(message[1], message[2], message[3])
                elif kind == "progress":
                    self._show_progress(message[1], message[2], message[3], message[4])
                elif kind == "done":
                    self.progress.stop()
                    self.progress.configure(mode="determinate", value=self.progress["maximum"])
                    self.run_button.configure(state="normal")
                    self.open_button.configure(state="normal")
                    self.status_dot.configure(fg="#3fb950")
                    self.status_var.set("Complete — every tagged copy passed verification.")
                    self.progress_detail_var.set("Ready for Pix4Dmapper")
                    self._append_manual("FINAL RESULT: SUCCESS — every tagged copy passed verification.")
                    log_path = self._save_session_log(Path(message[2]), "SUCCESS — all output images verified")
                    messagebox.showinfo(
                        APP_TITLE,
                        f"Pix4D-ready copies are complete.\n\n{message[2]}\n\nConversion log:\n{log_path}",
                    )
                elif kind == "error":
                    self.progress.stop()
                    self.run_button.configure(state="normal")
                    self.status_dot.configure(fg=RED)
                    self.status_var.set("Stopped — review the red log entries below.")
                    self.progress_detail_var.set("No complete output set was produced")
                    for line in message[2].splitlines():
                        self._append_log(datetime.now().strftime("%H:%M:%S"), line, "stderr")
                    self._append_manual(f"FINAL RESULT: FAILED — {message[1]}")
                    log_path = self._save_session_log(
                        Path(self.output_var.get().strip()), f"FAILED — {message[1]}"
                    )
                    messagebox.showerror(APP_TITLE, f"{message[1]}\n\nConversion log:\n{log_path}")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_messages)

    def _save_session_log(self, requested_output: Path, result: str) -> Path:
        target = requested_output
        try:
            source = Path(self.images_var.get().strip()).resolve()
            resolved = requested_output.resolve()
            if resolved == source or source in resolved.parents:
                target = requested_output.parent
        except (OSError, RuntimeError):
            pass
        try:
            return engine.save_conversion_log(
                target,
                self.session_log_lines,
                APP_VERSION,
                result,
                created_at=self.session_started,
            )
        except OSError as exc:
            fallback = Path(self.log_var.get().strip()).parent
            self._append_log(
                datetime.now().strftime("%H:%M:%S"),
                f"WARNING: Could not save conversion log in {target}: {exc}. Using {fallback}.",
                "stderr",
            )
            return engine.save_conversion_log(
                fallback,
                self.session_log_lines,
                APP_VERSION,
                result,
                created_at=self.session_started,
            )

    def _open_output(self) -> None:
        path = Path(self.output_var.get().strip())
        if not path.exists():
            return
        self._append_manual(f"Opening output folder: {path}")
        if os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])


def main() -> None:
    root = Tk()
    try:
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(
            "Dark.Horizontal.TProgressbar",
            troughcolor=SURFACE_2,
            background=BLUE,
            bordercolor=SURFACE_2,
            lightcolor=BLUE,
            darkcolor=BLUE,
            thickness=9,
        )
        style.configure("Dark.TNotebook", background=BG, borderwidth=0)
        style.configure(
            "Dark.TNotebook.Tab",
            background=SURFACE_2,
            foreground=MUTED,
            padding=(16, 8),
            borderwidth=0,
        )
        style.map(
            "Dark.TNotebook.Tab",
            background=[("selected", SURFACE)],
            foreground=[("selected", TEXT)],
        )
    except Exception:
        pass
    TaggerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
