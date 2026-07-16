@echo off
rem Builds the WebAssembly core bundled by the native app. The generated JS is
rem stored as .bin so Metro treats it as an asset instead of executing it in
rem the React Native JavaScript context.
rem Emscripten. Uses emcc from PATH, or an emsdk checkout supplied in %EMSDK%.
setlocal
cd /d "%~dp0..\.."

where emcc >nul 2>nul
if not errorlevel 1 goto emcc_ready
if "%EMSDK%"=="" (
    echo error: emcc not found. Add Emscripten to PATH or set EMSDK to an emsdk checkout.
    exit /b 1
)
if not exist "%EMSDK%\emsdk_env.bat" (
    echo error: emsdk_env.bat not found under EMSDK=%EMSDK%.
    exit /b 1
)
call "%EMSDK%\emsdk_env.bat" >nul 2>nul
where emcc >nul 2>nul
if errorlevel 1 (
    echo error: emsdk_env.bat did not add emcc to PATH.
    exit /b 1
)
:emcc_ready

set CORE=gameboy\core\gb.cpp gameboy\core\bus\bus.cpp gameboy\core\cpu\cpu.cpp gameboy\core\ppu\ppu.cpp ^
gameboy\core\timer\timer.cpp gameboy\core\apu\apu.cpp gameboy\core\cart\cart.cpp gameboy\adapter\gb_api.cpp
set CORE=%CORE% gameboy\core\mod\mod.cpp

set EXPORTS=_gb_create,_gb_destroy,_gb_load_rom,_gb_reset,_gb_reset_post_boot,_gb_run_frame,^
_gb_framebuffer,_gb_framebuffer_argb,_gb_set_input,_gb_read_audio,^
_gb_has_battery,_gb_save_ram,_gb_load_save_ram,_gb_save_state,_gb_load_state,^
_gb_rom_title,_gb_read_mem,^
_gb_write_mem,_gb_set_audio_mask,_gb_mod_load_symbols,_gb_mod_load,^
_gb_mod_unload,_gb_mod_count,_gb_mod_id,_gb_mod_name,_gb_mod_import_count,^
_gb_mod_import_name,_gb_mod_metadata,_gb_mod_last_error,_gb_mod_set_host_callback,^
_malloc,_free

if not exist app\assets\emulator mkdir app\assets\emulator
call emcc %CORE% -O2 -std=c++17 -I gameboy\core -I gameboy\adapter ^
    -sMODULARIZE=1 -sEXPORT_NAME=createGBCore -sENVIRONMENT=web ^
    -sALLOW_MEMORY_GROWTH=1 ^
    -sALLOW_TABLE_GROWTH=1 ^
    -sEXPORTED_FUNCTIONS=%EXPORTS% ^
    -sEXPORTED_RUNTIME_METHODS=cwrap,ccall,addFunction,removeFunction,HEAPU8,HEAPF32,HEAPU32 ^
    -o app\assets\emulator\gbcore.js
if errorlevel 1 exit /b 1
move /y app\assets\emulator\gbcore.js app\assets\emulator\gbcore.bin >nul

echo.
echo Built app\assets\emulator\gbcore.bin and app\assets\emulator\gbcore.wasm
