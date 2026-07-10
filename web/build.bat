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

set CORE=gameboy\core\gb.cpp gameboy\core\bus\bus.cpp gameboy\core\cpu\cpu.cpp gameboy\core\ppu\ppu.cpp ^
gameboy\core\timer\timer.cpp gameboy\core\apu\apu.cpp gameboy\core\cart\cart.cpp gameboy\adapter\gb_api.cpp
set CORE=%CORE% gameboy\core\mod\mod.cpp

set EXPORTS=_gb_create,_gb_destroy,_gb_load_rom,_gb_reset,_gb_run_frame,^
_gb_framebuffer,_gb_framebuffer_argb,_gb_set_input,_gb_read_audio,^
_gb_has_battery,_gb_save_ram,_gb_load_save_ram,_gb_rom_title,_gb_read_mem,^
_gb_write_mem,_gb_set_audio_mask,_gb_mod_load_symbols,_gb_mod_load,^
_gb_mod_unload,_gb_mod_count,_gb_mod_id,_gb_mod_name,_gb_mod_import_count,^
_gb_mod_import_name,_gb_mod_metadata,_gb_mod_last_error,_gb_mod_set_host_callback,^
_malloc,_free

call emcc %CORE% -O2 -std=c++17 -I gameboy\core -I gameboy\adapter ^
    -sMODULARIZE=1 -sEXPORT_NAME=createGBCore -sENVIRONMENT=web ^
    -sALLOW_MEMORY_GROWTH=1 ^
    -sALLOW_TABLE_GROWTH=1 ^
    -sEXPORTED_FUNCTIONS=%EXPORTS% ^
    -sEXPORTED_RUNTIME_METHODS=cwrap,ccall,addFunction,removeFunction,HEAPU8,HEAPF32,HEAPU32 ^
    -o web\gbcore.js
if errorlevel 1 exit /b 1

if not exist web\roms mkdir web\roms
if exist "Pokemon - Blue Version (USA, Europe) (SGB Enhanced).gb" (
    copy /y "Pokemon - Blue Version (USA, Europe) (SGB Enhanced).gb" web\roms\pokemon-blue.gb >nul
    echo Copied ROM to web\roms\pokemon-blue.gb
)

echo.
echo Built web\gbcore.js and web\gbcore.wasm
