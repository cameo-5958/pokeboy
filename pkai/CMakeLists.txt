option(PKAI_BUILD_ROM "Rebuild the AI ROM and its generated headers with rgbds at build time" ON)
option(PKAI_NEON "Use the NEON kernels on __ARM_NEON targets (bit-identical to the scalar ones)" ON)
if(PKAI_BUILD_ROM)
    add_custom_target(pkai_rom
        COMMAND ${CMAKE_CURRENT_SOURCE_DIR}/../pred-patch/build_ai.sh
        WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../pred-patch
        COMMENT "Build the AI ROM and derive its symbols/CRC")
else()
    add_custom_target(pkai_rom)
endif()

set(PKAI_SOURCES hook.cpp tracker.cpp event.cpp observe.cpp mask.cpp scheduler.cpp calc.cpp features.cpp
    weights.cpp model.cpp kernels/gemm.cpp kernels/attention.cpp kernels/gru.cpp)

# NEON kernels only where the compiler targets NEON; elsewhere kernels/gemm.cpp
# provides the scalar dispatch. Both produce identical integers (pkai_model_tests
# checks NEON against scalar on device).
include(CheckCXXSourceCompiles)
check_cxx_source_compiles("#if !defined(__ARM_NEON)\n#error no neon\n#endif\nint main() { return 0; }" PKAI_HAVE_NEON)
set(PKAI_KERNEL_DEFS "")
if(PKAI_NEON AND PKAI_HAVE_NEON)
    list(APPEND PKAI_SOURCES kernels/neon/gemm_neon.cpp)
    set(PKAI_KERNEL_DEFS PKAI_NEON_KERNELS=1)
    message(STATUS "pkai: NEON kernels enabled")
endif()

add_library(pkai STATIC ${PKAI_SOURCES})
target_include_directories(pkai PUBLIC ${CMAKE_CURRENT_SOURCE_DIR}/..)
target_compile_features(pkai PUBLIC cxx_std_17)
if(PKAI_KERNEL_DEFS)
    target_compile_definitions(pkai PRIVATE ${PKAI_KERNEL_DEFS})
endif()
set_target_properties(pkai PROPERTIES POSITION_INDEPENDENT_CODE ON)
if(NOT EMSCRIPTEN)
    add_library(pkai_c SHARED api.cpp)
    target_link_libraries(pkai_c PRIVATE pkai)
endif()

# Reference vectors for pkai_model_tests (gitignored; regenerate after retraining):
#   cd ai && uv run python -m tools.dump_vectors checkpoints/pep/stone-v1/vectors checkpoints/pep/stone-v1/vectors/bin
add_custom_target(pkai_vectors
    COMMAND uv run python -m tools.dump_vectors checkpoints/pep/stone-v1/vectors checkpoints/pep/stone-v1/vectors/bin
    WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/../ai
    COMMENT "Flatten the PEP reference vectors for pkai_model_tests")

add_dependencies(pkai pkai_rom)
