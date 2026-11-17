@echo off
rem ------------------------------------------------------------------
rem EM-5406 Tester - stop the XML transfer service and remove its
rem Task Scheduler entry. Run it before replacing the program files with
rem a new version (a running EM5406_XmlTransfer.exe cannot be overwritten),
rem or to uninstall. Close the EM-5406 Tester first (it restarts the service
rem if it finds it stopped). Right-click > Run as administrator.
rem ------------------------------------------------------------------
setlocal
schtasks /End /TN "EM5406 XML Transfer" >nul 2>&1
taskkill /IM EM5406_XmlTransfer.exe /F >nul 2>&1
schtasks /Delete /TN "EM5406 XML Transfer" /F
echo.
echo The XML transfer service is stopped. XML reports still in data\results\XML\Pending
echo will be sent the next time the service runs.
pause
