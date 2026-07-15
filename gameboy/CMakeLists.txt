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
    core/mod/mod.cpp
    adapter/gb_api.cpp)
target_include_directories(gbcore PUBLIC core adapter)

# Per-device frontends live in the repo-root emulator/<device>/ tree; this
# project builds the core they all link against.
add_executable(gbemu_headless ../emulator/headless/headless.cpp)
target_link_libraries(gbemu_headless PRIVATE gbcore)

include(CTest)
if(BUILD_TESTING)
    add_executable(gbcore_mod_tests tests/mod_tests.cpp)
    target_link_libraries(gbcore_mod_tests PRIVATE gbcore)
    add_test(NAME gbcore_mod_tests COMMAND gbcore_mod_tests)

    add_executable(gbemu_boot_tests tests/boot_tests.cpp)
    target_link_libraries(gbemu_boot_tests PRIVATE gbcore)
    add_test(NAME custom_boot_rom COMMAND gbemu_boot_tests)

    add_executable(gbcore_state_tests tests/state_tests.cpp)
    target_link_libraries(gbcore_state_tests PRIVATE gbcore)
    add_test(NAME gbcore_state_tests COMMAND gbcore_state_tests)

    # Manual integration harness for compiled packages that require a local,
    # uncommitted ROM: mod_package_tests rom.gb symbols.sym package.gbmod
    add_executable(gbcore_mod_package_tests tests/mod_package_tests.cpp)
    target_link_libraries(gbcore_mod_package_tests PRIVATE gbcore)

    # Manual guarded-package harness for Breadwinner: Battle Link. It uses the
    # same local, uncommitted ROM and symbol inputs as gbcore_mod_package_tests.
    add_executable(gbcore_battle_link_package_tests tests/battle_link_package_tests.cpp)
    target_link_libraries(gbcore_battle_link_package_tests PRIVATE gbcore)
endif()

if(WIN32)
    add_executable(gbemu WIN32 ../emulator/win32/main_win32.cpp)
    target_link_libraries(gbemu PRIVATE gbcore user32 gdi32 winmm comdlg32)
    set_target_properties(gbemu PROPERTIES LINK_FLAGS "/ENTRY:mainCRTStartup")
endif()
