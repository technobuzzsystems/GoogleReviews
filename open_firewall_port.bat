@echo off
setlocal enabledelayedexpansion
echo ========================================
echo  TechnoBuzz - Opening Firewall Port 5000
echo ========================================
echo.

set "LOCAL_IP="
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
    if not defined LOCAL_IP (
        set "LOCAL_IP=%%A"
        set "LOCAL_IP=!LOCAL_IP: =!"
    )
)

netsh advfirewall firewall delete rule name="TechnoBuzz Flask Port 5000" >nul 2>&1

netsh advfirewall firewall add rule ^
  name="TechnoBuzz Flask Port 5000" ^
  dir=in ^
  action=allow ^
  protocol=TCP ^
  localport=5000 ^
  profile=private,public

echo.
if %errorlevel% == 0 (
    echo [SUCCESS] Firewall rule added! Port 5000 is now open.
    echo.
    if defined LOCAL_IP (
        echo Your phone can now reach: http://!LOCAL_IP!:5000/feedback
    ) else (
        echo Your phone can now reach this PC's WiFi IPv4 address on port 5000.
    )
) else (
    echo [ERROR] Failed to add rule. Make sure you ran this as Administrator.
)
echo.
pause
