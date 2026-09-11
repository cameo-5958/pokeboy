#include "api.h"
#include "features.h"
#include "model.h"
#include "kernels/kernels.h"
#include <string>
#include <cstddef>
#include <cstdio>
#include <cstring>
using namespace pkai;

extern "C" {

size_t pkai_observation_size(void) { return sizeof(Observation); }
size_t pkai_features_size(void) { return sizeof(Features); }

size_t pkai_layout(char* buf, size_t len) {
    char tmp[4096]; size_t n = 0;
    auto line = [&](const char* name, size_t off, size_t size) {
        n += size_t(std::snprintf(tmp + n, sizeof tmp - n, "%s %zu %zu\n", name, off, size));
    };
#define L(S, F) line(#S "." #F, offsetof(S, F), sizeof(S{}.F))
    line("OwnMon", 0, sizeof(OwnMon)); L(OwnMon, species); L(OwnMon, level); L(OwnMon, status); L(OwnMon, moves); L(OwnMon, types); L(OwnMon, dvs);
    L(OwnMon, hp); L(OwnMon, max_hp); L(OwnMon, stats);
    line("PublicMon", 0, sizeof(PublicMon)); L(PublicMon, known); L(PublicMon, species); L(PublicMon, level); L(PublicMon, status); L(PublicMon, types);
    L(PublicMon, moves); L(PublicMon, revealed_moves); L(PublicMon, hp); L(PublicMon, max_hp); L(PublicMon, observed_round);
    line("Observation", 0, sizeof(Observation)); L(Observation, own); L(Observation, player); L(Observation, active); L(Observation, own_count);
    L(Observation, player_count); L(Observation, own_slot); L(Observation, player_slot); L(Observation, trainer_class); L(Observation, count);
    L(Observation, disabled); L(Observation, stages); L(Observation, battle_status); L(Observation, substitute); L(Observation, player_stages);
    L(Observation, player_visible_status); L(Observation, confusion_counter); L(Observation, toxic_counter); L(Observation, round);
    line("Token", 0, sizeof(Token)); L(Token, type); L(Token, index); L(Token, present); L(Token, candidate); L(Token, cat); L(Token, f);
    line("Features", 0, sizeof(Features)); L(Features, tokens); L(Features, count); L(Features, legal); L(Features, request_kind);
#undef L
    if (buf && len) { const size_t c = n < len - 1 ? n : len - 1; std::memcpy(buf, tmp, c); buf[c] = 0; }
    return n;
}

int pkai_build_features(const uint8_t* rom, size_t rom_len, const void* observation, uint8_t request_kind, void* out) {
    if (!rom || !observation || !out || request_kind > 1) return -1;
    Memory m{nullptr, [](void*, uint16_t) -> uint8_t { return 0; }, [](void*, uint16_t, uint8_t) {}, rom, rom_len};
    Observation o; std::memcpy(&o, observation, sizeof o);
    const Mask mask = legal_mask(m, o, request_kind);
    const Features F = build_features(m, o, mask, request_kind);
    std::memcpy(out, &F, sizeof F);
    return mask.bits;
}

uint8_t pkai_species_from_dex(const uint8_t* rom, size_t rom_len, uint8_t dex) {
    if (!rom || dex == 0 || dex > 151) return 0;
    if (dex == 151) return calc::MEW;
    Memory m{nullptr, [](void*, uint16_t) -> uint8_t { return 0; }, [](void*, uint16_t, uint8_t) {}, rom, rom_len};
    for (unsigned i = 0; i < 190; ++i) if (m.table(tables::PokedexOrder, i) == dex) return uint8_t(i + 1);
    return 0;
}
int pkai_class_item(const uint8_t* rom, size_t rom_len, uint8_t cls, uint8_t* item, uint8_t* divisor, uint8_t* status_required) {
    if (!rom || cls < 1 || cls > tables::trainer_classes) return -1;
    Memory m{nullptr, [](void*, uint16_t) -> uint8_t { return 0; }, [](void*, uint16_t, uint8_t) {}, rom, rom_len};
    const unsigned row = 3u * (cls - 1u);
    if (item) *item = m.table(tables::AIItemTable, row);
    if (divisor) *divisor = m.table(tables::AIItemTable, row + 1);
    if (status_required) *status_required = m.table(tables::AIItemTable, row + 2);
    return 0;
}
int pkai_class_item_count(const uint8_t* rom, size_t rom_len, uint8_t cls) {
    if (!rom || cls < 1 || cls > tables::trainer_classes) return -1;
    Memory m{nullptr, [](void*, uint16_t) -> uint8_t { return 0; }, [](void*, uint16_t, uint8_t) {}, rom, rom_len};
    return m.table(tables::TrainerAIPointers, 3u * (cls - 1u));
}

uint32_t pkai_features_hash(const void* features) {
    Features F; std::memcpy(&F, features, sizeof F);
    return features_hash(F);
}

namespace { struct ModelHandle { Weights weights; PepModel model; }; std::string g_model_error; }

void* pkai_model_open(const char* path) {
    if (!path) { g_model_error = "no path"; return nullptr; }
    auto* h = new ModelHandle;
    if (!h->weights.load(path)) { g_model_error = h->weights.error(); delete h; return nullptr; }
    if (!h->model.bind(h->weights)) { g_model_error = h->model.error(); delete h; return nullptr; }
    g_model_error.clear();
    return h;
}
void pkai_model_close(void* m) { delete static_cast<ModelHandle*>(m); }
const char* pkai_model_error(void) { return g_model_error.c_str(); }
int pkai_model_gru_size(const void* m) { return m ? int(static_cast<const ModelHandle*>(m)->model.gru_size()) : -1; }
int pkai_model_run(void* mp, const void* features, const int8_t* ev8, int16_t* hidden, int16_t* logits_q8, uint8_t* probs, int32_t* value_acc) {
    if (!mp || !features) return -1;
    PepModel& m = static_cast<ModelHandle*>(mp)->model;
    Features F; std::memcpy(&F, features, sizeof F);
    m.run(F, ev8, hidden);
    const unsigned g = m.gru_size();
    if (hidden) std::memcpy(hidden, m.hidden(), g * sizeof(int16_t));
    if (logits_q8) std::memcpy(logits_q8, m.logits_q8(), N_ACTIONS * sizeof(int16_t));
    if (probs) std::memcpy(probs, m.probs(), N_ACTIONS);
    if (value_acc) *value_acc = m.value_acc();
    return int(g);
}
const char* pkai_backend_name(void) { return kernels::backend_name(); }

}
