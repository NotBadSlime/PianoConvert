"""Torchvision 0.29 loads its stable native extensions indirectly."""
from PyInstaller.utils.hooks import collect_dynamic_libs

# The bundled hooks-contrib hook only knows the older torchvision._C name.
hiddenimports = ["torchvision._C_stable", "torchvision.image_stable"]
binaries = collect_dynamic_libs("torchvision")
module_collection_mode = "pyz+py"
