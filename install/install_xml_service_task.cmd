@echo off
rem ------------------------------------------------------------------
rem DFX Tester - register the XML transfer service with Task Scheduler.
rem
rem Run it once, from the DFX_Tester folder, while logged in as the
rem station user (right-click > Run as administrator). Windows will then
rem start DFX_XmlTransfer.exe every time this user logs on, so XML reports
rem keep moving to the MES folder even when DFX Tester is closed.
rem The task runs as this user, so it has this user's network access.
rem ------------------------------------------------------------------
setlocal
set "EXE=%~dp0DFX_XmlTransfer.exe"
if not exist "%EXE%" (
    echo DFX_XmlTransfer.exe was not found next to this file:
    echo   %EXE%
    pause
    exit /b 1
)

schtasks /Create /TN "DFX XML Transfer" /TR "\"%EXE%\"" /SC ONLOGON /RU "%USERDOMAIN%\%USERNAME%" /IT /RL LIMITED /F
if errorlevel 1 (
    echo.
    echo The task could not be created. Run this file as administrator.
    pause
    exit /b 1
)

rem Start it now as well, so there is no need to log off and on.
schtasks /Run /TN "DFX XML Transfer" >nul 2>&1
echo.
echo Done. The XML transfer service starts automatically when %USERNAME% logs on.
pause
