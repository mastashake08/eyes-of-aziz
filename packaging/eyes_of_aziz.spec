# PyInstaller spec building two standalone, one-file executables:
#
#   eyes-of-aziz-setup   -- double-click, opens the web dashboard in your
#                            browser (login -> scan -> register cameras).
#   eyes-of-aziz-bridge  -- run per camera, pointed at the config a Setup
#                            registration produced (cameras/<name>.env).
#                            When double-clicked with no arguments and
#                            exactly one cameras/*.env exists next to it,
#                            it uses that one automatically.
#
# Build with: pyinstaller packaging/eyes_of_aziz.spec
# (run from the repo root, in a venv with the project's dependencies AND
# pyinstaller installed -- see .github/workflows/build-desktop-apps.yml)

from pathlib import Path

import onvif

REPO_ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is injected by PyInstaller
ONVIF_WSDL_DIR = str(Path(onvif.__file__).parent / "wsdl")

# The setup app serves Flask templates/static files and talks ONVIF, so it
# needs these bundled as data (see discovery.py/webapp.py); the bridge
# never imports either, so it doesn't.
setup_datas = [
    (str(REPO_ROOT / "eyes_of_aziz" / "templates"), "eyes_of_aziz/templates"),
    (str(REPO_ROOT / "eyes_of_aziz" / "static"), "eyes_of_aziz/static"),
    (ONVIF_WSDL_DIR, "onvif/wsdl"),
]

setup_analysis = Analysis(  # noqa: F821 - injected by PyInstaller
    [str(REPO_ROOT / "packaging" / "gui_entry.py")],
    pathex=[str(REPO_ROOT)],
    datas=setup_datas,
)
bridge_analysis = Analysis(  # noqa: F821
    [str(REPO_ROOT / "packaging" / "bridge_entry.py")],
    pathex=[str(REPO_ROOT)],
)

setup_pyz = PYZ(setup_analysis.pure)  # noqa: F821
bridge_pyz = PYZ(bridge_analysis.pure)  # noqa: F821

setup_exe = EXE(  # noqa: F821
    setup_pyz,
    setup_analysis.scripts,
    setup_analysis.binaries,
    setup_analysis.zipfiles,
    setup_analysis.datas,
    [],
    name="eyes-of-aziz-setup",
    console=True,
    onefile=True,
)

bridge_exe = EXE(  # noqa: F821
    bridge_pyz,
    bridge_analysis.scripts,
    bridge_analysis.binaries,
    bridge_analysis.zipfiles,
    bridge_analysis.datas,
    [],
    name="eyes-of-aziz-bridge",
    console=True,
    onefile=True,
)
