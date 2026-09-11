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
// Internal species id (1..190) for a Pokédex number (1..151) via the ROM's PokedexOrder; 0 if none.
uint8_t pkai_species_from_dex(const uint8_t* rom, size_t rom_len, uint8_t dex);
// Trainer class item table row (generated AIItemTable): item id, HP divisor, status-required; 0 if no item.
int pkai_class_item(const uint8_t* rom, size_t rom_len, uint8_t trainer_class, uint8_t* item, uint8_t* divisor, uint8_t* status_required);
// Per-send-out item use count for a trainer class (TrainerAIPointers), or -1.
int pkai_class_item_count(const uint8_t* rom, size_t rom_len, uint8_t trainer_class);

#ifdef __cplusplus
}
#endif
