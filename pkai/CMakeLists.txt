add_custom_target(pkai_rom
    COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/../pred-patch/build_ai.sh
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../pred-patch
    COMMENT "Build the AI ROM and derive its symbols/CRC")
add_library(pkai STATIC hook.cpp tracker.cpp observe.cpp mask.cpp scheduler.cpp)
target_include_directories(pkai PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/..)
target_compile_features(pkai PUBLIC cxx_std_17)

add_dependencies(pkai pkai_rom)
