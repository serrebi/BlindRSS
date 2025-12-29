@echo off
setlocal enabledelayedexpansion

set "SCRIPT_DIR=%~dp0"
pushd "%SCRIPT_DIR%"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=build"

if /I "%MODE%"=="build" (
    rem ok
) else if /I "%MODE%"=="release" (
    rem ok
) else if /I "%MODE%"=="dry-run" (
    rem ok
) else (
    echo Usage: build.bat ^<build^|release^|dry-run^>
    exit /b 1
)

set "DEFAULT_SIGNTOOL=C:\Program Files (x86)\Windows Kits\10\bin\10.0.26100.0\x64\signtool.exe"
if defined SIGNTOOL_PATH (
    set "SIGNTOOL_EXE=%SIGNTOOL_PATH%"
) else (
    set "SIGNTOOL_EXE=%DEFAULT_SIGNTOOL%"
)

if /I "%MODE%"=="dry-run" (
    set "TOOL_PY=python"
    call :compute_next_version
    echo [Dry Run] Latest tag: !LATEST_TAG!
    echo [Dry Run] Next version: v!NEXT_VERSION! [!BUMP! bump]
    echo [Dry Run] Would bump core/version.py, build, sign with "%SIGNTOOL_EXE%", zip, generate manifest, tag, push, and create a GitHub release.
    goto :done
)

call :setup_venv
if errorlevel 1 exit /b 1
set "TOOL_PY=%VENV_PYTHON%"

if /I "%MODE%"=="release" (
    call :compute_next_version
    set "VERSION_NO_V=!NEXT_VERSION!"
    set "VERSION_TAG=!NEXT_TAG!"
    echo [BlindRSS Build] Bumping version to !VERSION_TAG!...
    "%TOOL_PY%" tools\release.py bump-version --version !VERSION_NO_V!
    if errorlevel 1 exit /b 1
) else (
    call :compute_current_version
    set "VERSION_NO_V=!CURRENT_VERSION!"
    set "VERSION_TAG=v!CURRENT_VERSION!"
    call :compute_next_version
)

call :build_app
call :sign_exe
call :zip_release
call :hash_zip
call :write_notes
call :write_manifest

if /I "%MODE%"=="release" (
    call :git_release
)

goto :done

:setup_venv
set "VENV_DIR=%SCRIPT_DIR%.venv"
echo [BlindRSS Build] Preparing Python environment...
if not exist "%VENV_DIR%" (
    python -m venv "%VENV_DIR%"
)

set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"
set "VENV_PYINSTALLER=%VENV_DIR%\Scripts\pyinstaller.exe"

echo [BlindRSS Build] Updating build tools...
"%VENV_PYTHON%" -m pip install --upgrade pip >nul
"%VENV_PYTHON%" -m pip install --upgrade pyinstaller packaging >nul

echo [BlindRSS Build] Installing dependencies from requirements.txt...
if exist "requirements.txt" (
    "%VENV_PYTHON%" -m pip install -r requirements.txt >nul
) else (
    echo [!] requirements.txt not found. Installing defaults...
    "%VENV_PYTHON%" -m pip install wxPython feedparser requests beautifulsoup4 yt-dlp python-dateutil mutagen python-vlc pychromecast async-upnp-client pyatv trafilatura webrtcvad brotli html5lib lxml setuptools^<81 >nul
)

echo [BlindRSS Build] Ensuring yt-dlp binary is present...
"%VENV_PYTHON%" -c "from core.dependency_check import _ensure_yt_dlp_cli; _ensure_yt_dlp_cli()"
if not exist "%SCRIPT_DIR%bin\\yt-dlp.exe" (
    echo [BlindRSS Build] Downloading yt-dlp.exe...
    "%VENV_PYTHON%" -c "import pathlib, urllib.request; p=pathlib.Path(r'%SCRIPT_DIR%bin\\yt-dlp.exe'); p.parent.mkdir(parents=True, exist_ok=True); urllib.request.urlretrieve('https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe', p.as_posix())"
)
if not exist "%SCRIPT_DIR%bin\\yt-dlp.exe" (
    echo [X] yt-dlp.exe not found in "%SCRIPT_DIR%bin". Build cannot continue.
    exit /b 1
)
exit /b 0

:compute_next_version
for /f "usebackq tokens=1* delims==" %%A in (`"%TOOL_PY%" tools\release.py next-version`) do (
    set "%%A=%%B"
)
if not defined NEXT_VERSION (
    echo [X] Failed to compute next version.
    exit /b 1
)
exit /b 0

:compute_current_version
for /f "usebackq tokens=1* delims==" %%A in (`"%TOOL_PY%" tools\release.py current-version`) do (
    set "%%A=%%B"
)
if not defined CURRENT_VERSION (
    echo [X] Failed to read current version.
    exit /b 1
)
exit /b 0

:write_notes
set "DIST_DIR=%SCRIPT_DIR%dist"
if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
set "RELEASE_NOTES=%DIST_DIR%\release-notes-%VERSION_TAG%.md"
set "SUMMARY_FILE=%DIST_DIR%\release-notes-summary.txt"
echo [BlindRSS Build] Generating release notes...
"%TOOL_PY%" tools\release.py write-notes --from-tag "%LATEST_TAG%" --to-tag "%VERSION_TAG%" --output "%RELEASE_NOTES%" --summary-output "%SUMMARY_FILE%"
if errorlevel 1 exit /b 1
exit /b 0

:build_app
echo [BlindRSS Build] Ensuring config.json exists...
if not exist "%SCRIPT_DIR%config.json" (
    echo { "active_provider": "local" } > "%SCRIPT_DIR%config.json"
)

echo [BlindRSS Build] Cleaning previous build...
if exist "%SCRIPT_DIR%build" rd /s /q "%SCRIPT_DIR%build"
if exist "%SCRIPT_DIR%dist" rd /s /q "%SCRIPT_DIR%dist"

echo [BlindRSS Build] Running PyInstaller (main.spec)...
if exist "main.spec" (
    "%VENV_PYINSTALLER%" --clean --noconfirm main.spec
) else (
    echo [!] main.spec not found. Running basic one-file build...
    "%VENV_PYINSTALLER%" --onefile --noconfirm --name BlindRSS main.py
)
if errorlevel 1 exit /b 1

echo [BlindRSS Build] Refreshing VLC plugins cache...
set "VLC_DIR=C:\Program Files\VideoLAN\VLC"
if not exist "%VLC_DIR%\vlc-cache-gen.exe" set "VLC_DIR=C:\Program Files (x86)\VideoLAN\VLC"
set "VLC_CACHE_GEN=%VLC_DIR%\vlc-cache-gen.exe"

set "DIST_PLUGINS=%SCRIPT_DIR%dist\BlindRSS\_internal\plugins"
if not exist "%DIST_PLUGINS%" set "DIST_PLUGINS=%SCRIPT_DIR%dist\BlindRSS\plugins"

if exist "%DIST_PLUGINS%" (
    if exist "%DIST_PLUGINS%\plugins.dat" del /f /q "%DIST_PLUGINS%\plugins.dat"
    if exist "%VLC_CACHE_GEN%" (
        "%VLC_CACHE_GEN%" "%DIST_PLUGINS%" >nul 2>nul
    ) else (
        echo [!] vlc-cache-gen.exe not found. Plugins cache will be rebuilt at runtime.
    )
) else (
    echo [!] VLC plugins directory not found in dist. Skipping cache refresh.
)

echo [BlindRSS Build] Staging companion files into dist...
if exist "%SCRIPT_DIR%README.md" copy /Y "%SCRIPT_DIR%README.md" "%SCRIPT_DIR%dist\README.md" >nul
if exist "%SCRIPT_DIR%update_helper.bat" copy /Y "%SCRIPT_DIR%update_helper.bat" "%SCRIPT_DIR%dist\BlindRSS\update_helper.bat" >nul

echo [BlindRSS Build] Copying exe to repo root...
if exist "%SCRIPT_DIR%dist\BlindRSS.exe" copy /Y "%SCRIPT_DIR%dist\BlindRSS.exe" "%SCRIPT_DIR%BlindRSS.exe" >nul
exit /b 0

:sign_exe
if not exist "%SIGNTOOL_EXE%" (
    echo [X] signtool.exe not found at "%SIGNTOOL_EXE%".
    exit /b 1
)
set "EXE_PATH=%SCRIPT_DIR%dist\BlindRSS\BlindRSS.exe"
if not exist "%EXE_PATH%" set "EXE_PATH=%SCRIPT_DIR%dist\BlindRSS.exe"
if not exist "%EXE_PATH%" (
    echo [X] BlindRSS.exe not found in dist output.
    exit /b 1
)
echo [BlindRSS Build] Signing "%EXE_PATH%"...
"%SIGNTOOL_EXE%" sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /a "%EXE_PATH%"
if errorlevel 1 exit /b 1
set "SIGNING_THUMBPRINT="
if defined SIGN_CERT_THUMBPRINT (
    set "SIGNING_THUMBPRINT=%SIGN_CERT_THUMBPRINT%"
) else (
    set "SIGNING_THUMBPRINT_FILE=%TEMP%\\BlindRSS_thumbprint.txt"
    "%TOOL_PY%" -c "import re, subprocess, pathlib; exe=r'%EXE_PATH%'; tool=r'%SIGNTOOL_EXE%'; result=subprocess.run([tool,'verify','/pa','/v',exe], capture_output=True, text=True); data=(result.stdout or '') + (result.stderr or ''); m=re.search(r'SHA1 hash:\\s*([0-9A-Fa-f]{40})', data); pathlib.Path(r'%SIGNING_THUMBPRINT_FILE%').write_text(m.group(1) if m else '')"
    if exist "%SIGNING_THUMBPRINT_FILE%" set /p SIGNING_THUMBPRINT=<"%SIGNING_THUMBPRINT_FILE%"
    if exist "%SIGNING_THUMBPRINT_FILE%" del /f /q "%SIGNING_THUMBPRINT_FILE%" >nul 2>&1
    if defined SIGNING_THUMBPRINT set "SIGNING_THUMBPRINT=!SIGNING_THUMBPRINT: =!"
)
exit /b 0

:zip_release
set "ZIP_NAME=BlindRSS-v%VERSION_NO_V%.zip"
set "ZIP_PATH=%SCRIPT_DIR%dist\%ZIP_NAME%"
if exist "%ZIP_PATH%" del /f /q "%ZIP_PATH%"
echo [BlindRSS Build] Creating zip "%ZIP_NAME%"...
powershell -NoProfile -Command "Compress-Archive -Path '%SCRIPT_DIR%dist\BlindRSS' -DestinationPath '%ZIP_PATH%' -Force" >nul
if errorlevel 1 exit /b 1
copy /Y "%ZIP_PATH%" "%SCRIPT_DIR%BlindRSS.zip" >nul
exit /b 0

:hash_zip
for /f "tokens=* delims=" %%A in ('certutil -hashfile "%ZIP_PATH%" SHA256 ^| findstr /v /c:"hash of file" ^| findstr /v /c:"CertUtil"') do (
    if not defined ZIP_SHA set "ZIP_SHA=%%A"
)
if not defined ZIP_SHA (
    echo [X] Failed to compute SHA-256.
    exit /b 1
)
exit /b 0

:write_manifest
set "MANIFEST_PATH=%SCRIPT_DIR%dist\BlindRSS-update.json"
echo [BlindRSS Build] Writing update manifest...
if defined SIGNING_THUMBPRINT (
    "%TOOL_PY%" tools\release.py write-manifest --version-tag "%VERSION_TAG%" --asset-name "%ZIP_NAME%" --sha256 "%ZIP_SHA%" --output "%MANIFEST_PATH%" --notes-summary-file "%SUMMARY_FILE%" --signing-thumbprint "!SIGNING_THUMBPRINT!"
) else (
    "%TOOL_PY%" tools\release.py write-manifest --version-tag "%VERSION_TAG%" --asset-name "%ZIP_NAME%" --sha256 "%ZIP_SHA%" --output "%MANIFEST_PATH%" --notes-summary-file "%SUMMARY_FILE%"
)
if errorlevel 1 exit /b 1
exit /b 0

:git_release
echo [BlindRSS Release] Committing version bump...
git add core\version.py
git commit -m "Release %VERSION_TAG%"
if errorlevel 1 exit /b 1

echo [BlindRSS Release] Tagging %VERSION_TAG%...
git tag %VERSION_TAG%
if errorlevel 1 exit /b 1

echo [BlindRSS Release] Pushing branch and tag...
git push origin HEAD
if errorlevel 1 exit /b 1
git push origin %VERSION_TAG%
if errorlevel 1 exit /b 1

echo [BlindRSS Release] Creating GitHub release...
gh --version >nul 2>&1
if errorlevel 1 (
    echo [X] gh CLI not found in PATH.
    exit /b 1
)
gh release create "%VERSION_TAG%" "%ZIP_PATH%" "%MANIFEST_PATH%" --title "%VERSION_TAG%" --notes-file "%RELEASE_NOTES%"
if errorlevel 1 exit /b 1
exit /b 0

:done
popd
endlocal
