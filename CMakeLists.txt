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
    core/cart/cart.cpp)
target_include_directories(gbcore PUBLIC core)

# M1: headless runner — runs Blargg tests, output via serial (no SDL needed).
# At M2, paste PDF §11.1 into frontend/main_sdl.cpp and switch to:
#   find_package(SDL2 CONFIG REQUIRED)
#   add_executable(gbemu frontend/main_sdl.cpp)
#   target_link_libraries(gbemu PRIVATE gbcore SDL2::SDL2 SDL2::SDL2main)
add_executable(gbemu frontend/headless.cpp)
target_link_libraries(gbemu PRIVATE gbcore)
