"""Load project-private libsodium without changing the system/Python runtime."""
import ctypes
import os
from pathlib import Path
import sys

VENDOR = Path(__file__).resolve().parent / 'vendor'
sys.path.insert(0, str(VENDOR))
_dll_directory = None
if os.name == 'nt':
    _dll_directory = os.add_dll_directory(str(VENDOR / 'libnacl'))
    _library = ctypes.CDLL(str(VENDOR / 'libnacl' / 'libsodium.dll'))
else:
    _library = ctypes.CDLL(str(VENDOR / 'libnacl' / 'libsodium.so'))

import libnacl
