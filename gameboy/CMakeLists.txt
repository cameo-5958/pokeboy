cmake_minimum_required(VERSION 3.20)
project(gbemu CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(gbcore
    core/gb.cpp
    core/bus/bus.cpp
    core/cpu/cpu.cpp
    core/ppu/ppu.cpp
    core/timer/timer.cpp
    core/apu/apu.cpp
    core/cart/cart.cpp
    adapter/gb_api.cpp)
target_include_directories(gbcore PUBLIC core adapter)

add_executable(gbemu_headless frontend/headless.cpp)
target_link_libraries(gbemu_headless PRIVATE gbcore)

if(WIN32)
    add_executable(gbemu WIN32 frontend/win32/main_win32.cpp)
    target_link_libraries(gbemu PRIVATE gbcore user32 gdi32 winmm comdlg32)
    set_target_properties(gbemu PROPERTIES LINK_FLAGS "/ENTRY:mainCRTStartup")
endif()
