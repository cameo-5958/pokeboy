#pragma once
// C ABI for tooling and the Python training stack (parity with the device).
// The structs are the C++ ones (pkai::Observation, pkai::Features); the
// layout query lets a foreign mirror verify offsets before trusting them.
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

size_t pkai_observation_size(void);
size_t pkai_features_size(void);
// Writes "name offset size\n" lines for pkai::Observation and pkai::Features fields.
// Returns the number of bytes needed (excluding NUL); truncates to len.
size_t pkai_layout(char* buf, size_t len);

// Legal mask + features for an observation, using only the ROM's data tables.
// request_kind: 0 turn, 1 faint replacement. Returns the 16-bit legal mask,
// or -1 on bad arguments.
int pkai_build_features(const uint8_t* rom, size_t rom_len, const void* observation, uint8_t request_kind, void* out_features);
uint32_t pkai_features_hash(const void* features);

#ifdef __cplusplus
}
#endif
