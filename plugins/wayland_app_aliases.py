"""Extend Community application aliases for raw Wayland app IDs."""

from talon import Module

mod = Module()

mod.apps.chrome = """
os: linux
and app.name: chromium
os: linux
and app.name: chromium-browser
"""

mod.apps.nautilus = """
os: linux
and app.name: org.gnome.Nautilus
"""

mod.apps.evince = """
os: linux
and app.name: org.gnome.Evince
"""

mod.apps.wayland_terminal = """
os: linux
and app.name: foot
os: linux
and app.name: org.omarchy.terminal
"""
