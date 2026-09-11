#include "api.h"
#include "features.h"
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

uint32_t pkai_features_hash(const void* features) {
    Features F; std::memcpy(&F, features, sizeof F);
    return features_hash(F);
}

}
