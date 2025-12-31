@echo off
setlocal enabledelayedexpansion

rem Always log updater output so failures aren't silent when running hidden.
for /f %%T in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyyMMddHHmmss\")"') do set "RUNSTAMP=%%T"
set "LOG_FILE=%TEMP%\BlindRSS_update_!RUNSTAMP!_!RANDOM!.log"
call :main %* >> "%LOG_FILE%" 2>&1
exit /b %ERRORLEVEL%

:main
echo [BlindRSS Update] Log: "%LOG_FILE%"

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
powershell -NoProfile -Command "Wait-Process -Id %PID% -ErrorAction SilentlyContinue"

for /f %%T in ('powershell -NoProfile -Command "(Get-Date).ToString(\"yyyyMMddHHmmss\")"') do set STAMP=%%T
set BACKUP_DIR=%INSTALL_DIR%_backup_%STAMP%

if exist "%BACKUP_DIR%" rd /s /q "%BACKUP_DIR%"

echo [BlindRSS Update] Backing up current install...
call :move_with_retry "%INSTALL_DIR%" "%BACKUP_DIR%" 20
if errorlevel 1 (
    echo [X] Failed to backup install directory.
    goto :rollback
)

echo [BlindRSS Update] Applying update...
call :move_with_retry "%STAGING_DIR%" "%INSTALL_DIR%" 20
if errorlevel 1 (
    echo [X] Failed to move staging directory to install directory.
    goto :rollback
)

echo [BlindRSS Update] Restoring user data...
call :restore_user_data "%BACKUP_DIR%" "%INSTALL_DIR%"

echo [BlindRSS Update] Launching app...
start "" "%INSTALL_DIR%\%EXE_NAME%"
exit /b 0

:rollback
echo [BlindRSS Update] Update failed. Restoring backup...
if exist "%BACKUP_DIR%" (
    if not exist "%INSTALL_DIR%" (
        call :move_with_retry "%BACKUP_DIR%" "%INSTALL_DIR%" 20
    )
)
start "" "%INSTALL_DIR%\%EXE_NAME%"
powershell -NoProfile -Command "param([string]$log) try { Add-Type -AssemblyName PresentationFramework | Out-Null; $msg = 'BlindRSS update failed.' + \"`n`n\" + 'Log file:' + \"`n\" + $log; [System.Windows.MessageBox]::Show($msg, 'BlindRSS Update', 'OK', 'Error') | Out-Null } catch { }" "%LOG_FILE%" >nul 2>nul
exit /b 1

:move_with_retry
setlocal
set "SRC=%~1"
set "DST=%~2"
set "RETRIES=%~3"
if "%RETRIES%"=="" set "RETRIES=20"
for /l %%I in (1,1,%RETRIES%) do (
    powershell -NoProfile -Command "param([string]$src,[string]$dst) $ErrorActionPreference='Stop'; Move-Item -LiteralPath $src -Destination $dst -Force" "%SRC%" "%DST%" >nul 2>nul
    if not errorlevel 1 (
        endlocal
        exit /b 0
    )
    rem Exponential-ish backoff to reduce CPU spinning
    if %%I lss 5 ( timeout /t 1 /nobreak >nul ) else ( timeout /t 2 /nobreak >nul )
)
powershell -NoProfile -Command "param([string]$src,[string]$dst) $ErrorActionPreference='Stop'; Move-Item -LiteralPath $src -Destination $dst -Force" "%SRC%" "%DST%" >nul 2>nul
if errorlevel 1 (
    endlocal
    exit /b 1
)
endlocal
exit /b 0

:restore_user_data
setlocal
set "OLD_DIR=%~1"
set "NEW_DIR=%~2"

if "%OLD_DIR%"=="" goto :restore_done
if "%NEW_DIR%"=="" goto :restore_done
if not exist "%OLD_DIR%" goto :restore_done
if not exist "%NEW_DIR%" goto :restore_done

rem Preserve config + database across updates. These live alongside the EXE in portable mode.
if exist "%OLD_DIR%\config.json" (
    call :copy_with_retry "%OLD_DIR%\config.json" "%NEW_DIR%\config.json" 15
)

for %%F in (rss.db rss.db-wal rss.db-shm rss.db-journal) do (
    if exist "%OLD_DIR%\%%F" (
        call :copy_with_retry "%OLD_DIR%\%%F" "%NEW_DIR%\%%F" 15
    )
)

rem Preserve default downloads folder when using portable defaults.
if exist "%OLD_DIR%\podcasts" (
    if not exist "%NEW_DIR%\podcasts" (
        move /Y "%OLD_DIR%\podcasts" "%NEW_DIR%\podcasts" >nul 2>nul
        if errorlevel 1 (
            xcopy "%OLD_DIR%\podcasts" "%NEW_DIR%\podcasts\" /E /I /Y >nul 2>nul
        )
    )
)

:restore_done
endlocal
exit /b 0

:copy_with_retry
setlocal
set "SRC=%~1"
set "DST=%~2"
set "RETRIES=%~3"
if "%RETRIES%"=="" set "RETRIES=15"
for /l %%I in (1,1,%RETRIES%) do (
    copy /Y "%SRC%" "%DST%" >nul 2>nul
    if not errorlevel 1 (
        endlocal
        exit /b 0
    )
    timeout /t 1 /nobreak >nul
)
copy /Y "%SRC%" "%DST%" >nul 2>nul
if errorlevel 1 (
    endlocal
    exit /b 1
)
endlocal
exit /b 0

:usage
echo Usage: update_helper.bat ^<pid^> ^<install_dir^> ^<staging_dir^> ^<exe_name^>
exit /b 1
