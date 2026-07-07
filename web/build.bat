@echo off
rem Builds the WebAssembly core (web\gbcore.js + web\gbcore.wasm) with
rem Emscripten. Requires emsdk installed at %EMSDK% (default C:\Users\kunru\tools\emsdk).
setlocal
cd /d "%~dp0.."

if "%EMSDK%"=="" set EMSDK=C:\Users\kunru\tools\emsdk
if not exist "%EMSDK%\emsdk_env.bat" (
    echo error: emsdk not found at %EMSDK%. Set EMSDK or install it there.
    exit /b 1
)
call "%EMSDK%\emsdk_env.bat" >nul 2>nul

set CORE=core\gb.cpp core\bus\bus.cpp core\cpu\cpu.cpp core\ppu\ppu.cpp ^
core\timer\timer.cpp core\apu\apu.cpp core\cart\cart.cpp adapter\gb_api.cpp

set EXPORTS=_gb_create,_gb_destroy,_gb_load_rom,_gb_reset,_gb_run_frame,^
_gb_framebuffer,_gb_framebuffer_argb,_gb_set_input,_gb_read_audio,^
_gb_has_battery,_gb_save_ram,_gb_load_save_ram,_gb_rom_title,_gb_read_mem,_malloc,_free

call emcc %CORE% -O2 -std=c++17 -I core -I adapter ^
    -sMODULARIZE=1 -sEXPORT_NAME=createGBCore -sENVIRONMENT=web ^
    -sALLOW_MEMORY_GROWTH=1 ^
    -sEXPORTED_FUNCTIONS=%EXPORTS% ^
    -sEXPORTED_RUNTIME_METHODS=cwrap,ccall,HEAPU8,HEAPF32,HEAPU32 ^
    -o web\gbcore.js
if errorlevel 1 exit /b 1

if not exist web\roms mkdir web\roms
if exist "Pokemon - Blue Version (USA, Europe) (SGB Enhanced).gb" (
    copy /y "Pokemon - Blue Version (USA, Europe) (SGB Enhanced).gb" web\roms\pokemon-blue.gb >nul
    echo Copied ROM to web\roms\pokemon-blue.gb
)

echo.
echo Built web\gbcore.js and web\gbcore.wasm
