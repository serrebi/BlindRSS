@echo off
setlocal enabledelayedexpansion

rem Always log updater output so failures aren't silent when running hidden.
for /f %%T in ('powershell -NoProfile -InputFormat None -Command "(Get-Date).ToString(\"yyyyMMddHHmmss\")"') do set "RUNSTAMP=%%T"
set "LOG_FILE=%TEMP%\BlindRSS_update_!RUNSTAMP!_!RANDOM!.log"
set "SENTINEL=__BLINDRSS_UPDATE_DONE__"

set "PID=%~1"
set "INSTALL_DIR=%~2"
set "STAGING_DIR=%~3"
set "EXE_NAME=%~4"
set "TEMP_ROOT=%~5"
set "SHOW_LOG=%~6"

call :main %* >> "%LOG_FILE%" 2>&1
set "RC=%ERRORLEVEL%"

if %RC% equ 0 (
    rem Success - always delete log file
    del /f /q "%LOG_FILE%" >nul 2>nul
) else (
    rem Failure - write sentinel so any running log window will close
    echo %SENTINEL%>>"%LOG_FILE%"
)
exit /b %RC%

:main
echo [BlindRSS Update] Log: "%LOG_FILE%"

if "%PID%"=="" goto :usage
if "%INSTALL_DIR%"=="" goto :usage
if "%STAGING_DIR%"=="" goto :usage
if "%EXE_NAME%"=="" goto :usage

rem Ensure we are not running from within the install directory
if not defined BLINDRSS_UPDATE_HELPER_RELOCATED (
    set "SCRIPT_PATH=%~f0"
    powershell -NoProfile -InputFormat None -Command "$sp=[string]$env:SCRIPT_PATH; $inst=[string]$env:INSTALL_DIR; if ($sp -and $inst -and $sp.ToLower().StartsWith($inst.ToLower())) { exit 0 } else { exit 1 }" >nul 2>nul
    if not errorlevel 1 (
        set "BLINDRSS_UPDATE_HELPER_RELOCATED=1"
        for /f %%T in ('powershell -NoProfile -InputFormat None -Command "(Get-Date).ToString(\"yyyyMMddHHmmss\")"') do set "HSTAMP=%%T"
        set "TMP_HELPER=%TEMP%\BlindRSS_update_helper_!HSTAMP!_!RANDOM!.bat"
        copy /Y "%~f0" "!TMP_HELPER!" >nul 2>nul
        start "" /b "!TMP_HELPER!" "%PID%" "%INSTALL_DIR%" "%STAGING_DIR%" "%EXE_NAME%" "%TEMP_ROOT%" "%SHOW_LOG%"
        exit /b 0
    )
)

rem Never keep the working directory inside the install folder
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
powershell -NoProfile -InputFormat None -Command "Wait-Process -Id %PID% -ErrorAction SilentlyContinue"

rem OneDrive Fix: Don't move the root folder. Move CONTENTS.
rem We back up the current contents to a backup folder, then move new contents in.
rem Robocopy is more robust for this than 'move'.

for /f %%T in ('powershell -NoProfile -InputFormat None -Command "(Get-Date).ToString(\"yyyyMMddHHmmss\")"') do set STAMP=%%T
set "BACKUP_DIR=%INSTALL_DIR%_backup_%STAMP%"

echo [BlindRSS Update] Backing up current install to "%BACKUP_DIR%"...
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
rem /MOVE moves files and dirs, effectively emptying source. /E for recursive. /NFL /NDL to reduce noise.
rem We exclude user data (rss.db, config.json) from the MOVE so they stay in place, 
rem preventing potential data loss if restore fails, and reducing IO.
rem Wait, if we leave them there, robocopy moving new files in won't touch them unless they exist in new files (they shouldn't).
rem BUT if we want to "clean install", we should move everything except user data.
rem Let's move everything. We will copy user data back if needed, or if we excluded them, they are just there.
rem To be safe and identical to previous logic: Move EVERYTHING out, then move EVERYTHING in, then Restore user data.
rem Logic:
rem 1. Robocopy /MOVE * from INSTALL to BACKUP.
rem 2. Robocopy /MOVE * from STAGING to INSTALL.
rem 3. Copy user data from BACKUP to INSTALL (if missing).

rem Robocopy exit codes: 0=No Change, 1=Copy Successful, >1=Warning/Error.
rem We accept <= 3 usually (1=copy, 2=extra, 3=both). 
rem However, for /MOVE, we want to ensure it worked.

robocopy "%INSTALL_DIR%" "%BACKUP_DIR%" /E /MOVE /R:3 /W:1 /NFL /NDL /XD .git .venv __pycache__
set RC=%ERRORLEVEL%
if %RC% gtr 8 (
    echo [X] Backup failed with robocopy code %RC%.
    goto :rollback
)

echo [BlindRSS Update] Applying update...
robocopy "%STAGING_DIR%" "%INSTALL_DIR%" /E /MOVE /R:3 /W:1 /NFL /NDL
set RC=%ERRORLEVEL%
if %RC% gtr 8 (
    echo [X] Update application failed with robocopy code %RC%.
    goto :rollback
)

echo [BlindRSS Update] Restoring user data...
call :restore_user_data "%BACKUP_DIR%" "%INSTALL_DIR%"

echo [BlindRSS Update] Launching app...
start "" /b "%INSTALL_DIR%\%EXE_NAME%"
call :cleanup_success "%BACKUP_DIR%" "%STAGING_DIR%" "%TEMP_ROOT%"
exit /b 0

:rollback
echo [BlindRSS Update] Update failed. Restoring backup...
if not "%SHOW_LOG%"=="" if /I not "%SHOW_LOG%"=="0" (
    call :start_log_window "%LOG_FILE%" "%SENTINEL%"
)
if exist "%BACKUP_DIR%" (
    robocopy "%BACKUP_DIR%" "%INSTALL_DIR%" /E /MOVE /R:3 /W:1 /NFL /NDL
)
start "" /b "%INSTALL_DIR%\%EXE_NAME%"
powershell -NoProfile -InputFormat None -Command "param([string]$log) try { Add-Type -AssemblyName PresentationFramework | Out-Null; $msg = 'BlindRSS update failed.' + \"`n`n\" + 'Log file:' + \"`n\" + $log; [System.Windows.MessageBox]::Show($msg, 'BlindRSS Update', 'OK', 'Error') | Out-Null } catch { }" "%LOG_FILE%" >nul 2>nul
exit /b 1

:restore_user_data
setlocal
set "OLD_DIR=%~1"
set "NEW_DIR=%~2"

if "%OLD_DIR%"=="" goto :restore_done
if "%NEW_DIR%"=="" goto :restore_done
if not exist "%OLD_DIR%" goto :restore_done
if not exist "%NEW_DIR%" goto :restore_done

rem Copy back config and database if they were moved to backup
if exist "%OLD_DIR%\config.json" (
    copy /Y "%OLD_DIR%\config.json" "%NEW_DIR%\config.json" >nul 2>nul
)

for %%F in (rss.db rss.db-wal rss.db-shm rss.db-journal) do (
    if exist "%OLD_DIR%\%%F" (
        copy /Y "%OLD_DIR%\%%F" "%NEW_DIR%\%%F" >nul 2>nul
    )
)

rem Restore podcasts folder if exists
if exist "%OLD_DIR%\podcasts" (
    if not exist "%NEW_DIR%\podcasts" (
        robocopy "%OLD_DIR%\podcasts" "%NEW_DIR%\podcasts" /E /MOVE /R:3 /W:1 /NFL /NDL >nul 2>nul
    )
)

rem Restore sounds folder if exists (preserves user customizations)
if exist "%OLD_DIR%\sounds" (
    if not exist "%NEW_DIR%\sounds" (
        robocopy "%OLD_DIR%\sounds" "%NEW_DIR%\sounds" /E /MOVE /R:3 /W:1 /NFL /NDL >nul 2>nul
    )
)

:restore_done
endlocal
exit /b 0

:cleanup_success
setlocal
set "BACKUP_DIR=%~1"
set "STAGING_DIR=%~2"
set "TEMP_ROOT=%~3"
set "KEEP_BACKUP=0"

call :should_keep_backup "%BACKUP_DIR%" "%INSTALL_DIR%"

if "%KEEP_BACKUP%"=="1" (
    echo [BlindRSS Update] Keeping backup for safety: "%BACKUP_DIR%"
) else (
    call :safe_rmdir "%BACKUP_DIR%" "backup" "%INSTALL_DIR%_backup_"
)

call :safe_rmdir "%STAGING_DIR%" "staging" "BlindRSS_update_"

if "%TEMP_ROOT%"=="" (
    call :derive_temp_root "%STAGING_DIR%"
)

if not "%TEMP_ROOT%"=="" (
    call :schedule_temp_cleanup "%TEMP_ROOT%"
)

endlocal
exit /b 0

:should_keep_backup
setlocal
set "BACKUP_DIR=%~1"
set "INSTALL_DIR=%~2"
set "KEEP=0"

if not exist "%BACKUP_DIR%" goto :keep_done
if not exist "%INSTALL_DIR%" goto :keep_done

if exist "%BACKUP_DIR%\config.json" if not exist "%INSTALL_DIR%\config.json" set "KEEP=1"

for %%F in (rss.db rss.db-wal rss.db-shm rss.db-journal) do (
    if exist "%BACKUP_DIR%\%%F" if not exist "%INSTALL_DIR%\%%F" set "KEEP=1"
)

if exist "%BACKUP_DIR%\podcasts" if not exist "%INSTALL_DIR%\podcasts" set "KEEP=1"

if exist "%BACKUP_DIR%\sounds" if not exist "%INSTALL_DIR%\sounds" set "KEEP=1"

:keep_done
endlocal & set "KEEP_BACKUP=%KEEP%"
exit /b 0

:derive_temp_root
setlocal
set "STAGING_DIR=%~1"
if "%STAGING_DIR%"=="" goto :derive_done

for %%I in ("%STAGING_DIR%") do (
    set "STAGING_DIR=%%~fI"
    set "STAGING_NAME=%%~nxI"
    set "STAGING_PARENT=%%~dpI"
)

if /I "%STAGING_NAME%"=="extract" (
    endlocal & set "TEMP_ROOT=%STAGING_PARENT%" & exit /b 0
)

for %%I in ("%STAGING_PARENT%.") do (
    set "STAGING_PARENT=%%~fI"
    set "PARENT_NAME=%%~nxI"
    set "PARENT_PARENT=%%~dpI"
)

if /I "%PARENT_NAME%"=="extract" (
    endlocal & set "TEMP_ROOT=%PARENT_PARENT%" & exit /b 0
)

:derive_done
endlocal & set "TEMP_ROOT="
exit /b 0

:schedule_temp_cleanup
setlocal
set "TEMP_ROOT=%~1"
if "%TEMP_ROOT%"=="" goto :schedule_done

start "" /b powershell -NoProfile -WindowStyle Hidden -Command "param([string]$path,[string]$install) Start-Sleep -Seconds 2; try { if (-not $path) { return }; $full=[IO.Path]::GetFullPath($path); $inst=[IO.Path]::GetFullPath($install); if ($full -ieq $inst) { return }; if ($full -notmatch 'BlindRSS_update_') { return }; if (Test-Path -LiteralPath $full -PathType Container) { Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction SilentlyContinue }; $parent = Split-Path -Parent $full; if ((Split-Path -Leaf $parent) -ieq '_BlindRSS_update_tmp') { if (-not (Get-ChildItem -LiteralPath $parent -Force | Select-Object -First 1)) { Remove-Item -LiteralPath $parent -Recurse -Force -ErrorAction SilentlyContinue } } } catch { }" "%TEMP_ROOT%" "%INSTALL_DIR%" >nul 2>nul

:schedule_done
endlocal
exit /b 0

:start_log_window
setlocal
set "LOG_FILE=%~1"
set "SENTINEL=%~2"
if "%LOG_FILE%"=="" goto :start_done

start "" cmd /c powershell -NoProfile -Command "param([string]$log,[string]$sent) Write-Host 'BlindRSS update in progress...'; if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Wait | ForEach-Object { $_; if ($_ -eq $sent) { break } } } else { Write-Host 'Update log not found.'; Start-Sleep -Seconds 3 }" "%LOG_FILE%" "%SENTINEL%"

:start_done
endlocal
exit /b 0

:safe_rmdir
setlocal
set "TARGET=%~1"
set "LABEL=%~2"
set "REQUIRED_SUBSTR=%~3"
if "%TARGET%"=="" goto :safe_done

for %%I in ("%TARGET%") do set "TARGET=%%~fI"
if not exist "%TARGET%" goto :safe_done
if /I "%TARGET%"=="%INSTALL_DIR%" goto :safe_done
if /I "%TARGET%"=="%SystemRoot%" goto :safe_done
if /I "%TARGET%"=="%SystemDrive%\" goto :safe_done

if not "%REQUIRED_SUBSTR%"=="" (
    echo(%TARGET%| find /I "%REQUIRED_SUBSTR%" >nul
    if errorlevel 1 goto :safe_done
)

rmdir /s /q "%TARGET%" >nul 2>nul
echo [BlindRSS Update] Cleaned %LABEL% "%TARGET%"

:safe_done
endlocal
exit /b 0

:usage
echo Usage: update_helper.bat ^<pid^> ^<install_dir^> ^<staging_dir^> ^<exe_name^> [temp_root]
exit /b 1
