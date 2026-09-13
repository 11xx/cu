#!/usr/bin/env python3
"""Render the disposable target used by the local publication demo."""

import json
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402, I001


RESULT = Path(os.environ["CU_DEMO_RESULT"])


class Demo(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.cu.PublicationDemo")

    def do_activate(self):
        window = Gtk.ApplicationWindow(application=self, title="CU demo")
        window.set_default_size(640, 360)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for margin in ("top", "bottom", "start", "end"):
            getattr(box, f"set_margin_{margin}")(48)

        heading = Gtk.Label(label="Disposable CU target")
        heading.add_css_class("title-1")
        state = Gtk.Label(label="State: ready")
        button = Gtk.Button(label="Verify click")
        button.add_css_class("suggested-action")

        def clicked(_button):
            state.set_label("State: verified")
            RESULT.write_text(json.dumps({"clicked": True}) + "\n")

        button.connect("clicked", clicked)
        box.append(heading)
        box.append(state)
        box.append(button)
        window.set_child(box)
        window.present()


Demo().run(None)
