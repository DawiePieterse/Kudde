@echo off
setlocal EnableDelayedExpansion
:: Kudde - double-click to be told when a new release is out.
::
:: Registers a Scheduled Task that runs "update_server.bat --check" once a
:: day. That looks for a newer signed release and writes what it found to
:: data\update_available.json, which the Admin app reads and shows at the top
:: of the Dashboard. It does NOT install anything.
::
:: Installing stays a deliberate double-click of update_server.bat by a
:: person. update_server.bat runs the database migration in the foreground
:: precisely so a schema change happens in front of whoever chose to update,
:: instead of inside a Scheduled Task nobody is watching - and an update that
:: applies itself at 03:00 needs a rollback story to match, which this does
:: not have. The cost of getting that wrong is a farm that cannot record a
:: weighing on the morning of a sale, with nobody who knows why.
::
:: NOTE: this task runs as YOU, not as SYSTEM - unlike the server. It has to.
:: Fetching from GitHub uses whatever credentials this Windows account has
:: (an SSH deploy key in %USERPROFILE%\.ssh, or a stored HTTPS credential),
:: and SYSTEM has a different profile with neither in it, so a check running
:: as SYSTEM would fail "Permission denied (publickey)" every day, silently,
:: forever. The consequence is that the check only runs while you are logged
:: on, which is also the only time anyone could act on what it finds.

cd /d "%~dp0"

echo.
echo ==^> Checking this PC can reach GitHub as you...
git ls-remote --tags origin >nul 2>&1
if errorlevel 1 (
    echo.
    echo Could not read the repository from this account.
    echo.
    echo The daily check runs as the logged-on user, so GitHub has to be
    echo reachable from THIS Windows account. See UPDATING.md, "Getting the
    echo server onto a clone it can update". Test it by hand with:
    echo     git ls-remote origin
    echo.
    echo Nothing has been registered.
    echo.
    pause
    exit /b 1
)
echo     GitHub is reachable.

schtasks /query /tn "Kudde Update Check" >nul 2>&1
if %errorLevel% equ 0 (
    schtasks /delete /tn "Kudde Update Check" /f >nul 2>&1
)
schtasks /create /tn "Kudde Update Check" /tr "\"%~dp0update_server.bat\" --check" /sc daily /st 07:30 /f >nul 2>&1
if %errorLevel% neq 0 (
    echo.
    echo Failed to register the scheduled task - see the error above.
    echo.
    echo If Windows asked for a password, register it to run only when you
    echo are logged on instead:
    echo     schtasks /create /tn "Kudde Update Check" /tr "\"%~dp0update_server.bat\" --check" /sc onlogon /f
    echo.
    pause
    exit /b 1
)

echo.
echo ==^> Running the check once now, so you can see what it does...
call "%~dp0update_server.bat" --check

echo.
echo Done - "Kudde Update Check" will run every day at 07:30 while you are
echo logged on. When a new release is out, the Admin app's Dashboard will say
echo so. Installing it is still up to you: double-click update_server.bat
echo when it suits the farm.
echo.
pause
