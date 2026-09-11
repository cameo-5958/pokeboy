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

// PEP integer model (pkai/model.h) for parity checks against ai/models/pep_int.py.
// pkai_model_open loads a pkai.weights file; NULL on failure (pkai_model_error
// then describes why). pkai_model_run performs one decision: `features` is a
// pkai::Features block, `ev8` an int8[64] event vector (NULL = zeros), `hidden`
// the int16[gru] GRU state read before and written after (NULL = zeros, not
// written). Outputs (each optional): logits_q8 int16[16], probs uint8[16],
// value_acc int32. Returns the GRU size, or -1 on bad arguments.
void*       pkai_model_open(const char* weights_path);
void        pkai_model_close(void* model);
const char* pkai_model_error(void);
int         pkai_model_gru_size(const void* model);
int         pkai_model_run(void* model, const void* features, const int8_t* ev8, int16_t* hidden,
                           int16_t* logits_q8, uint8_t* probs, int32_t* value_acc);
const char* pkai_backend_name(void);

#ifdef __cplusplus
}
#endif
