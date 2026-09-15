// Dump every enemy trainer party exactly as the ROM builds it: for each
// (class, party index) the ROM's own ReadTrainer is executed in the emulator
// and wEnemyMons is read back. No .asm is parsed for moves or stats; the ROM
// is the oracle.
//
// Party counts per class are derived from the ROM too: TrainerDataPointers
// holds one pointer per class, each class's data is a sequence of
// zero-terminated party records (first byte: shared level, or $FF for
// per-mon levels followed by (level, species) pairs). A class's data ends
// where the next-higher pointer in the table begins; the class with the
// highest pointer (Lance) is bounded by the next symbol in the same bank from
// the .sym file (TrainerAI in the current build). A class whose pointer is
// shared with a later class (UNUSED_JUGGLER -> FISHER, CHIEF -> SCIENTIST:
// empty labels in parties.asm) owns no records; the ROM would load the later
// class's party for it.
//
// What ReadTrainer reads (verified in engine/battle/read_trainer_party.asm):
//   wLinkState (must be 0), wCurOpponent (OPP_ID_OFFSET + class), wTrainerNo,
//   wLoneAttackNo (gym leader number, written by the gym map scripts, aliases
//   wGymLeaderNo), wRivalStarter (champion rival only), wTrainerBaseMoney.
// What it clobbers: wEnemyPartyCount, wEnemyPartySpecies, wEnemyMons (via
// AddPartyMon with wMonDataLocation = ENEMY_PARTY_DATA), wCurEnemyLevel,
// wCurPartySpecies, wMonDataLocation, wAmountMoneyWon.
#pragma once
#include "core.h"
#include "pkai/memory.h"
#include <algorithm>
#include <cstdint>
#include <fstream>
#include <iterator>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace pkai::trainers {

struct Mon {
    uint8_t species{}, dex{}, level{}, status{}, type1{}, type2{};
    uint8_t moves[4]{}, pp[4]{}, dvs[2]{};
    uint16_t hp{}, max_hp{}, attack{}, defense{}, speed{}, special{};
    unsigned move_count() const { unsigned n = 0; for (auto m : moves) n += m != 0; return n; }
};
struct Party {
    unsigned index{};              // 1-based wTrainerNo
    uint8_t lone_attack_no{};      // wLoneAttackNo used for this load
    uint8_t rival_starter{};       // wRivalStarter used for this load (0 = irrelevant)
    std::vector<Mon> mons;
};
struct Class {
    unsigned id{};
    std::string name;
    std::vector<Party> parties;
};
struct Dump {
    uint32_t rom_crc32{};
    std::vector<Class> classes;
    unsigned party_count() const { unsigned n = 0; for (auto& c : classes) n += unsigned(c.parties.size()); return n; }
};

inline uint32_t crc32(const uint8_t* p, size_t n) {
    uint32_t c = 0xffffffffu;
    for (size_t i = 0; i < n; ++i) {
        c ^= p[i];
        for (int k = 0; k < 8; ++k) c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1u)));
    }
    return ~c;
}

// Internal species ids of the starters (constants/pokemon_constants.asm).
inline constexpr uint8_t STARTER1 = 0xb0;   // CHARMANDER
inline constexpr uint8_t STARTER2 = 0xb1;   // SQUIRTLE
inline constexpr uint8_t STARTER3 = 0x99;   // BULBASAUR
inline constexpr uint8_t OPP_ID_OFFSET = 200;

struct Loader {
    GameBoy g;
    std::vector<uint8_t> rom;
    std::map<std::string, std::pair<unsigned, unsigned>> sym;

    Loader(const char* rom_path, const char* sym_path) {
        std::ifstream f(rom_path, std::ios::binary);
        rom.assign(std::istreambuf_iterator<char>(f), {});
        if (rom.empty() || !g.load_rom(rom.data(), rom.size())) throw std::runtime_error(std::string("cannot load ROM ") + rom_path);
        g.reset_post_boot();
        for (int i = 0; i < 4; ++i) g.run_frame();
        std::ifstream sf(sym_path); std::string line;
        while (std::getline(sf, line)) { unsigned bank, addr; char name[256]; if (sscanf(line.c_str(), "%x:%x %255s", &bank, &addr, name) == 3) sym[name] = {bank, addr}; }
        if (sym.empty()) throw std::runtime_error(std::string("cannot read symbols ") + sym_path);
    }
    uint8_t rom_byte(uint8_t bank, uint16_t addr) const {
        const unsigned off = tables::Address{bank, addr}.offset();
        return off < rom.size() ? rom[off] : 0;
    }
    void w8(uint16_t a, uint8_t v) { g.bus.write8(a, v); }
    uint8_t r8(uint16_t a) { return g.bus.read8(a); }
    uint16_t r16(uint16_t a) { return uint16_t(r8(a) << 8) | r8(a + 1); }

    // Run `call routine; jr $` from a WRAM stub with `bank` mapped, until the sentinel.
    bool call(tables::Address routine, unsigned max_steps = 50000000) {
        using namespace symbols;
        uint16_t p = 0xc100;
        w8(p++, 0xcd); w8(p++, routine.address & 0xff); w8(p++, routine.address >> 8);
        w8(p, 0x18); w8(p + 1, 0xfe);
        g.bus.write8(0x2000, routine.bank); w8(hLoadedROMBank, routine.bank);
        g.cpu.pc = 0xc100; g.cpu.sp = 0xdff0; g.cpu.halted = false; g.cpu.stopped = false; g.cpu.ime = false;
        for (unsigned i = 0; i < max_steps && g.cpu.pc != p; ++i) g.cpu.execute_next();
        return g.cpu.pc == p;
    }

    // One parsed party record from the ROM data (only used to bound the walk
    // and to cross-check the ROM's own loader).
    struct Record { uint16_t start, end; unsigned mons; };

    std::vector<std::vector<Record>> records() const {
        const auto T = tables::TrainerDataPointers;
        std::vector<uint16_t> ptrs;
        for (unsigned i = 0; i < tables::trainer_classes; ++i)
            ptrs.push_back(uint16_t(rom_byte(T.bank, T.address + 2 * i) | rom_byte(T.bank, T.address + 2 * i + 1) << 8));
        // Bound for the highest pointer: the next symbol in the same bank.
        const uint16_t top = *std::max_element(ptrs.begin(), ptrs.end());
        unsigned table_end = 0x8000;
        for (auto& [name, ba] : sym)
            if (ba.first == T.bank && ba.second > top && ba.second < table_end && name.find('.') == std::string::npos) table_end = ba.second;
        std::vector<std::vector<Record>> out(ptrs.size());
        for (unsigned i = 0; i < ptrs.size(); ++i) {
            bool shared_with_later = false;
            for (unsigned j = i + 1; j < ptrs.size(); ++j) shared_with_later |= ptrs[j] == ptrs[i];
            if (shared_with_later) continue;   // empty label: the data belongs to the later class
            unsigned end = table_end;
            for (auto q : ptrs) if (q > ptrs[i] && q < end) end = q;
            unsigned p = ptrs[i];
            while (p < end) {
                Record r{uint16_t(p), 0, 0};
                uint8_t first = rom_byte(T.bank, p++);
                if (first == 0xff) { while (rom_byte(T.bank, p) != 0) { p += 2; ++r.mons; } }
                else { while (rom_byte(T.bank, p) != 0) { ++p; ++r.mons; } }
                ++p;   // terminator
                r.end = uint16_t(p);
                if (r.mons == 0 || r.mons > 6 || p > end) throw std::runtime_error("malformed party record in class " + std::to_string(i + 1));
                out[i].push_back(r);
            }
        }
        return out;
    }

    // wLoneAttackNo as the gym map scripts set it before the leader battle
    // (scripts/*Gym.asm write wGymLeaderNo, which aliases wLoneAttackNo).
    // Giovanni's first two parties are fought in the Rocket Hideout and Silph
    // Co., where the variable stays 0.
    static uint8_t lone_attack_no(const std::string& cls, unsigned index) {
        if (cls == "BROCK") return 1;
        if (cls == "MISTY") return 2;
        if (cls == "LT_SURGE") return 3;
        if (cls == "ERIKA") return 4;
        if (cls == "KOGA") return 5;
        if (cls == "SABRINA") return 6;
        if (cls == "BLAINE") return 7;
        if (cls == "GIOVANNI" && index == 3) return 8;
        return 0;
    }
    // ChampionsRoom.asm picks the Rival3 party from wRivalStarter:
    // STARTER2 -> 1, STARTER3 -> 2, otherwise (STARTER1) -> 3.
    static uint8_t rival_starter(const std::string& cls, unsigned index) {
        if (cls != "RIVAL3") return 0;
        return index == 1 ? STARTER2 : index == 2 ? STARTER3 : STARTER1;
    }

    Party load(unsigned cls, unsigned index, const Record& rec) {
        using namespace symbols;
        const std::string name = tables::trainer_class_names[cls];
        Party party; party.index = index;
        party.lone_attack_no = lone_attack_no(name, index);
        party.rival_starter = rival_starter(name, index);
        w8(wLinkState, 0);
        w8(wCurOpponent, uint8_t(OPP_ID_OFFSET + cls));
        w8(wTrainerClass, uint8_t(cls));
        w8(wTrainerNo, uint8_t(index));
        w8(wLoneAttackNo, party.lone_attack_no);
        w8(wRivalStarter, party.rival_starter);
        for (int i = 0; i < 3; ++i) w8(wTrainerBaseMoney + i, 0);
        // Poison the target so a short load is visible.
        for (unsigned i = 0; i < 6 * 44; ++i) w8(wEnemyMons + i, 0xee);
        if (!call(tables::ReadTrainer)) throw std::runtime_error("ReadTrainer did not return for class " + name + " party " + std::to_string(index));
        const unsigned count = r8(wEnemyPartyCount);
        if (count != rec.mons) throw std::runtime_error("ROM loaded " + std::to_string(count) + " mons but the record has " + std::to_string(rec.mons) + " for class " + name + " party " + std::to_string(index));
        if (r8(wEnemyPartySpecies + count) != 0xff) throw std::runtime_error("species list not terminated for class " + name);
        constexpr uint16_t B = wEnemyMon1, STRUCT = wEnemyMon2 - wEnemyMon1;
        static_assert(STRUCT == 44, "PARTYMON_STRUCT_LENGTH");
        for (unsigned i = 0; i < count; ++i) {
            const uint16_t m = uint16_t(wEnemyMons + i * STRUCT);
            Mon mon;
            mon.species = r8(m + (wEnemyMon1Species - B));
            mon.dex = mon.species ? uint8_t(rom_byte(tables::PokedexOrder.bank, uint16_t(tables::PokedexOrder.address + mon.species - 1))) : 0;
            mon.level = r8(m + (wEnemyMon1Level - B));
            mon.status = r8(m + (wEnemyMon1Status - B));
            mon.type1 = r8(m + (wEnemyMon1Type1 - B));
            mon.type2 = r8(m + (wEnemyMon1Type2 - B));
            for (int k = 0; k < 4; ++k) { mon.moves[k] = r8(m + (wEnemyMon1Moves - B) + k); mon.pp[k] = r8(m + (wEnemyMon1PP - B) + k); }
            mon.dvs[0] = r8(m + (wEnemyMon1DVs - B)); mon.dvs[1] = r8(m + (wEnemyMon1DVs - B) + 1);
            mon.hp = r16(m + (wEnemyMon1HP - B));
            mon.max_hp = r16(m + (wEnemyMon1MaxHP - B));
            mon.attack = r16(m + (wEnemyMon1Attack - B));
            mon.defense = r16(m + (wEnemyMon1Defense - B));
            mon.speed = r16(m + (wEnemyMon1Speed - B));
            mon.special = r16(m + (wEnemyMon1Special - B));
            if (r8(wEnemyPartySpecies + i) != mon.species) throw std::runtime_error("species list disagrees with party struct for class " + name);
            party.mons.push_back(mon);
        }
        return party;
    }

    Dump dump_all() {
        Dump d; d.rom_crc32 = crc32(rom.data(), rom.size());
        const auto recs = records();
        for (unsigned cls = 1; cls <= tables::trainer_classes; ++cls) {
            Class c; c.id = cls; c.name = tables::trainer_class_names[cls];
            for (unsigned i = 0; i < recs[cls - 1].size(); ++i) c.parties.push_back(load(cls, i + 1, recs[cls - 1][i]));
            d.classes.push_back(std::move(c));
        }
        return d;
    }
};

}  // namespace pkai::trainers
