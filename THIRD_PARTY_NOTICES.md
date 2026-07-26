# Third-party software notices

Generated from the FirePanel Commissioning Python environment.
Versions are the versions used to build and verify version 0.1.0.

Each component remains the property of its respective copyright
holders and is provided under its own terms. Full upstream notices
copied from installed distributions are in `LICENSES/third-party`.

## Distributed runtime components

| Component | Version | Licence | Purpose |
|---|---:|---|---|
| [CPython](https://www.python.org/) | 3.13.3 | PSF-2.0 | runtime |
| [charset-normalizer](https://charset-normalizer.readthedocs.io/) | 3.4.9 | MIT | runtime |
| [et_xmlfile](https://openpyxl.pages.heptapod.net/et_xmlfile/) | 2.0.0 | MIT | runtime |
| [ezdxf](https://github.com/mozman/ezdxf) | 1.4.4 | MIT | runtime |
| [fonttools](http://github.com/fonttools/fonttools) | 4.63.0 | MIT | runtime |
| numpy | 2.5.1 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | runtime |
| [openpyxl](https://openpyxl.readthedocs.io/en/stable/) | 3.1.5 | MIT | runtime |
| [packaging](https://packaging.pypa.io/) | 26.2 | Apache-2.0 OR BSD-2-Clause | build, runtime, test |
| [pillow](https://pillow.readthedocs.io) | 12.3.0 | MIT-CMU | runtime |
| [pyparsing](https://pyparsing-docs.readthedocs.io/en/latest/) | 3.3.2 | MIT | runtime |
| [PySide6](https://pyside.org) | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | runtime |
| [PySide6_Addons](https://pyside.org) | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | runtime |
| [PySide6_Essentials](https://pyside.org) | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | runtime |
| [QtAwesome](https://github.com/spyder-ide/qtawesome) | 1.4.2 | MIT | runtime |
| [QtPy](https://github.com/spyder-ide/qtpy) | 2.4.3 | MIT | runtime |
| [reportlab](https://www.reportlab.com/) | 4.5.1 | BSD-3-Clause | runtime |
| [shapely](https://shapely.readthedocs.io/) | 2.1.2 | BSD-3-Clause | runtime |
| [shiboken6](https://pyside.org) | 6.11.1 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | runtime |
| [typing_extensions](https://typing-extensions.readthedocs.io/) | 4.16.0 | PSF-2.0 | runtime |

## Build and test components

These packages are used to build or verify the project and are not
intended to be imported by the application at runtime.

| Component | Version | Licence | Purpose |
|---|---:|---|---|
| [altgraph](https://altgraph.readthedocs.io/en/latest/) | 0.17.5 | MIT | build |
| [colorama](https://github.com/tartley/colorama) | 0.4.6 | BSD-3-Clause | test |
| [iniconfig](https://github.com/pytest-dev/iniconfig) | 2.3.0 | MIT | test |
| [pefile](https://github.com/erocarrera/pefile) | 2024.8.26 | MIT | build |
| pluggy | 1.6.0 | MIT | test |
| [Pygments](https://pygments.org) | 2.20.0 | BSD-2-Clause | test |
| [pyinstaller](https://pyinstaller.org) | 6.21.0 | GPLv2-or-later with a special exception which allows to use PyInstaller to build and distribute non-free programs (including commercial ones) | build |
| [pyinstaller-hooks-contrib](https://github.com/pyinstaller/pyinstaller-hooks-contrib) | 2026.6 | GPL-2.0-or-later WITH PyInstaller-exception-2.0 AND Apache-2.0 | build |
| [pytest](https://docs.pytest.org/en/latest/) | 8.4.2 | MIT | test |
| [pywin32-ctypes](https://github.com/enthought/pywin32-ctypes) | 0.2.3 | BSD-3-Clause | build |
| [setuptools](https://github.com/pypa/setuptools) | 83.0.0 | MIT | build |

## Bundled icon fonts

QtAwesome includes Font Awesome 5.15.4 and 6.7.2, Elusive Icons 2.0,
Material Design Icons 5.9.55 and 6.9.96, Phosphor 1.3.0,
Remix Icon 2.5.0, and Microsoft Codicons 0.0.36. Their separate
licences and attribution notices are in
`LICENSES/third-party/icon-fonts`.

## Qt framework

The PySide6 wheels contain Qt libraries. Qt and Qt for Python are
available under LGPL/GPL terms or under a separately purchased Qt
commercial licence. Some Qt modules have module-specific terms.
The applicable GNU licence texts and Qt notice are in
`LICENSES/third-party/qt-6.11.1`.

## Updating this record

Run the following command after changing dependencies:

```powershell
.\.venv\Scripts\python.exe tools\generate_third_party_notices.py
```

Review native libraries and bundled assets whenever the executable
packaging configuration changes.
