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
add_subdirectory(../pkai pkai)
target_link_libraries(gbcore PUBLIC pkai)

# Per-device frontends live in the repo-root emulator/<device>/ tree; this
# project builds the core they all link against.
add_executable(gbemu_headless ../emulator/headless/headless.cpp)
target_link_libraries(gbemu_headless PRIVATE gbcore)

include(CTest)
if(BUILD_TESTING)
    add_executable(gbemu_boot_tests tests/boot_tests.cpp)
    target_link_libraries(gbemu_boot_tests PRIVATE gbcore)
    add_test(NAME custom_boot_rom COMMAND gbemu_boot_tests)

    add_executable(gbcore_state_tests tests/state_tests.cpp)
    target_link_libraries(gbcore_state_tests PRIVATE gbcore)
    add_test(NAME gbcore_state_tests COMMAND gbcore_state_tests)

    add_executable(gbcore_hardware_tests tests/hardware_tests.cpp)
    target_link_libraries(gbcore_hardware_tests PRIVATE gbcore)
    add_test(NAME gbcore_hardware_tests COMMAND gbcore_hardware_tests)

endif()

if(WIN32)
    add_executable(gbemu WIN32 ../emulator/win32/main_win32.cpp)
    target_link_libraries(gbemu PRIVATE gbcore user32 gdi32 winmm comdlg32)
    set_target_properties(gbemu PROPERTIES LINK_FLAGS "/ENTRY:mainCRTStartup")
endif()

if(BUILD_TESTING)
    add_executable(pkai_protocol_tests ../pkai/tests/protocol_tests.cpp)
    target_link_libraries(pkai_protocol_tests PRIVATE gbcore)
    target_compile_definitions(pkai_protocol_tests PRIVATE AI_ROM="${CMAKE_CURRENT_SOURCE_DIR}/../pred-patch/pokered-ai.gbc")
    add_test(NAME pkai_protocol COMMAND pkai_protocol_tests)
endif()
if(BUILD_TESTING)
    add_executable(pkai_rom_battle_tests ../pkai/tests/rom_battle_tests.cpp)
    target_link_libraries(pkai_rom_battle_tests PRIVATE gbcore)
    target_compile_definitions(pkai_rom_battle_tests PRIVATE
        AI_ROM="${CMAKE_CURRENT_SOURCE_DIR}/../pred-patch/pokered-ai.gbc"
        AI_SYM="${CMAKE_CURRENT_SOURCE_DIR}/../pred-patch/pokered-ai.sym")
    add_test(NAME pkai_rom_battles COMMAND pkai_rom_battle_tests)
    set_tests_properties(pkai_rom_battles PROPERTIES TIMEOUT 120)
    add_executable(pkai_calc_tests ../pkai/tests/calc_tests.cpp)
    target_link_libraries(pkai_calc_tests PRIVATE gbcore)
    target_compile_definitions(pkai_calc_tests PRIVATE AI_ROM="${CMAKE_CURRENT_SOURCE_DIR}/../pred-patch/pokered-ai.gbc")
    add_test(NAME pkai_calc COMMAND pkai_calc_tests)
    find_package(Python3 COMPONENTS Interpreter)
    if(Python3_Interpreter_FOUND)
        add_test(NAME pkai_python_binding COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/../pkai/tests/test_python_binding.py
                 ${CMAKE_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR}/../pred-patch/pokered-ai.gbc)
        set_tests_properties(pkai_python_binding PROPERTIES DEPENDS pkai_rom_battles)
    endif()
endif()
