"""
PyInstaller hook for WorkOnward Read package.

This hook ensures that the Fonts directory and other data files
are properly collected when building with PyInstaller.
"""

from PyInstaller.utils.hooks import collect_data_files

# Collect data files from the workonward_read package. collect_data_files()
# picks up every non-Python file in the package, i.e. both the fonts/
# directory and the assets/ directory (app icons) — they land in the bundle
# under 'workonward_read/fonts' and 'workonward_read/assets'.
datas = collect_data_files('workonward_read')

# Hidden imports that PyInstaller might miss
hiddenimports = ['PIL', 'tkinter']
