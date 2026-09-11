add_custom_target(pkai_rom
    COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/../pred-patch/build_ai.sh
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../pred-patch
    COMMENT "Build the AI ROM and derive its symbols/CRC")
add_library(pkai STATIC hook.cpp tracker.cpp observe.cpp mask.cpp scheduler.cpp calc.cpp features.cpp)
target_include_directories(pkai PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/..)
target_compile_features(pkai PUBLIC cxx_std_17)
set_target_properties(pkai PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(NOT EMSCRIPTEN)
    add_library(pkai_c SHARED api.cpp)
    target_link_libraries(pkai_c PRIVATE pkai)
endif()

add_dependencies(pkai pkai_rom)
