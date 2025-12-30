@echo off
setlocal enabledelayedexpansion

set "PID=%~1"
set "INSTALL_DIR=%~2"
set "STAGING_DIR=%~3"
set "EXE_NAME=%~4"

if "%PID%"=="" goto :usage
if "%INSTALL_DIR%"=="" goto :usage
if "%STAGING_DIR%"=="" goto :usage
if "%EXE_NAME%"=="" goto :usage

rem Ensure we are not running from within the install directory (moving it can fail if the
rem updater script itself is inside it). If needed, re-launch a temp copy and exit.
if not defined BLINDRSS_UPDATE_HELPER_RELOCATED (
    set "SCRIPT_PATH=%~f0"
    powershell -NoProfile -Command "$sp=[string]$env:SCRIPT_PATH; $inst=[string]$env:INSTALL_DIR; if ($sp -and $inst -and $sp.ToLower().StartsWith($inst.ToLower())) { exit 0 } else { exit 1 }" >nul 2>nul
    if not errorlevel 1 (
        set "BLINDRSS_UPDATE_HELPER_RELOCATED=1"
        for /f %%T in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyyMMddHHmmss\")"') do set "HSTAMP=%%T"
        set "TMP_HELPER=%TEMP%\BlindRSS_update_helper_!HSTAMP!_!RANDOM!.bat"
        copy /Y "%~f0" "!TMP_HELPER!" >nul 2>nul
        start "" "!TMP_HELPER!" "%PID%" "%INSTALL_DIR%" "%STAGING_DIR%" "%EXE_NAME%"
        exit /b 0
    )
)

rem Never keep the working directory inside the install folder, otherwise Windows may
rem refuse to move/rename it (current-directory handle lock).
if exist "%TEMP%" (
    pushd "%TEMP%" >nul 2>nul
) else if exist "%SystemRoot%" (
    pushd "%SystemRoot%" >nul 2>nul
)

if not exist "%STAGING_DIR%" (
    echo [BlindRSS Update] Staging folder not found: "%STAGING_DIR%"
    exit /b 1
)

echo [BlindRSS Update] Waiting for process %PID% to exit...
:wait_loop
for /f "tokens=1" %%A in ('tasklist /FI "PID eq %PID%" /NH') do (
    if /I not "%%A"=="INFO:" (
        timeout /t 1 /nobreak >nul
        goto wait_loop
    )
)
timeout /t 1 /nobreak >nul

for /f %%T in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyyMMddHHmmss\")"') do set STAMP=%%T
set BACKUP_DIR=%INSTALL_DIR%_backup_%STAMP%

if exist "%BACKUP_DIR%" rd /s /q "%BACKUP_DIR%"

echo [BlindRSS Update] Backing up current install...
call :move_with_retry "%INSTALL_DIR%" "%BACKUP_DIR%" 30
if errorlevel 1 goto :rollback

echo [BlindRSS Update] Applying update...
call :move_with_retry "%STAGING_DIR%" "%INSTALL_DIR%" 30
if errorlevel 1 goto :rollback

echo [BlindRSS Update] Launching app...
start "" "%INSTALL_DIR%\%EXE_NAME%"
exit /b 0

:rollback
echo [BlindRSS Update] Update failed. Restoring backup...
if exist "%BACKUP_DIR%" (
    if not exist "%INSTALL_DIR%" (
        move /Y "%BACKUP_DIR%" "%INSTALL_DIR%" >nul
    )
)
start "" "%INSTALL_DIR%\%EXE_NAME%"
exit /b 1

:move_with_retry
setlocal
set "SRC=%~1"
set "DST=%~2"
set "RETRIES=%~3"
if "%RETRIES%"=="" set "RETRIES=30"
for /l %%I in (1,1,%RETRIES%) do (
    move /Y "%SRC%" "%DST%" >nul 2>nul
    if not errorlevel 1 (
        endlocal
        exit /b 0
    )
    timeout /t 1 /nobreak >nul
)
move /Y "%SRC%" "%DST%" >nul 2>nul
if errorlevel 1 (
    endlocal
    exit /b 1
)
endlocal
exit /b 0

:usage
echo Usage: update_helper.bat ^<pid^> ^<install_dir^> ^<staging_dir^> ^<exe_name^>
exit /b 1
