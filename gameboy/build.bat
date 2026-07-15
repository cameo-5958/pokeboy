@echo off
rem Builds the Win32 emulator (build\gbemu.exe) and the headless test harness
rem (build\gbemu_headless.exe) with MSVC. Run from a VS developer prompt, or
rem let the script locate vcvars64 itself.
setlocal
cd /d "%~dp0"

where cl >nul 2>nul
if errorlevel 1 (
    for /f "usebackq tokens=*" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do (
        call "%%i\VC\Auxiliary\Build\vcvars64.bat" >nul
    )
)
where cl >nul 2>nul
if errorlevel 1 (
    echo error: cl.exe not found. Install VS Build Tools or run from a developer prompt.
    exit /b 1
)

if not exist build mkdir build

set CORE=core\gb.cpp core\bus\bus.cpp core\cpu\cpu.cpp core\ppu\ppu.cpp ^
core\timer\timer.cpp core\apu\apu.cpp core\cart\cart.cpp adapter\gb_api.cpp
set CORE=%CORE% core\mod\mod.cpp
set CFLAGS=/nologo /std:c++17 /O2 /EHsc /W3 /I core /I adapter /Fo:build\

cl %CFLAGS% %CORE% ..\emulator\win32\main_win32.cpp /Fe:build\gbemu.exe ^
   /link /SUBSYSTEM:WINDOWS /ENTRY:mainCRTStartup user32.lib gdi32.lib winmm.lib comdlg32.lib
if errorlevel 1 exit /b 1

cl %CFLAGS% %CORE% ..\emulator\headless\headless.cpp /Fe:build\gbemu_headless.exe
if errorlevel 1 exit /b 1

cl %CFLAGS% %CORE% tests\mod_tests.cpp /Fe:build\gbcore_mod_tests.exe
if errorlevel 1 exit /b 1

cl %CFLAGS% %CORE% tests\mod_package_tests.cpp /Fe:build\gbcore_mod_package_tests.exe
if errorlevel 1 exit /b 1

cl %CFLAGS% %CORE% tests\boot_tests.cpp /Fe:build\gbemu_boot_tests.exe
if errorlevel 1 exit /b 1

build\gbemu_boot_tests.exe
if errorlevel 1 exit /b 1

echo.
echo Built build\gbemu.exe, build\gbemu_headless.exe, build\gbcore_mod_tests.exe, build\gbcore_mod_package_tests.exe, and build\gbemu_boot_tests.exe
