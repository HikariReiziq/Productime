"""GTK 4 / libadwaita user interface.

Importing this package pins the GI versions, so every UI module can import
gi.repository names directly afterwards.
"""

import os

import gi

# The Cairo renderer keeps RSS tens of MB lower than the GPU renderers, and this
# low-motion UI gains nothing from GPU compositing. Users can still override it.
os.environ.setdefault("GSK_RENDERER", "cairo")

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
