# Avoid PyInstaller contrib hook-numba recursion/stack overflow.
hiddenimports = ["numba.core", "numba.misc"]
datas = []
binaries = []
