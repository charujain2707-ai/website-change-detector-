@echo off
rem Same as start.bat, but allows localhost so you can test with the /demo page
set ALLOW_PRIVATE=1
call "%~dp0start.bat"
