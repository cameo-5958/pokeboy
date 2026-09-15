// pkai_dump_trainers <out.json>
// Executes the ROM's ReadTrainer for every trainer class/party and writes the
// resulting wEnemyMons as JSON. See trainer_dump.h for what the ROM reads and
// how party counts are derived.
#include "trainer_dump.h"
#include <cstdio>
#include <fstream>
#include <iostream>
using namespace pkai::trainers;

int main(int argc, char** argv) {
    if (argc != 2) { std::cerr << "usage: pkai_dump_trainers <out.json>\n"; return 2; }
    try {
        Loader loader(AI_ROM, AI_SYM);
        const Dump d = loader.dump_all();
        if (d.rom_crc32 != pkai::tables::rom_crc32) {
            std::cerr << "ROM crc " << std::hex << d.rom_crc32 << " differs from the generated headers' " << pkai::tables::rom_crc32 << "; rerun pred-patch/build_ai.sh\n";
            return 1;
        }
        std::ofstream out(argv[1]);
        if (!out) { std::cerr << "cannot write " << argv[1] << "\n"; return 1; }
        auto list = [&](const uint8_t* v, int n) { out << '['; for (int i = 0; i < n; ++i) out << (i ? "," : "") << unsigned(v[i]); out << ']'; };
        out << "{\n  \"rom_crc32\": " << d.rom_crc32 << ",\n  \"classes\": [\n";
        for (size_t ci = 0; ci < d.classes.size(); ++ci) {
            const auto& c = d.classes[ci];
            out << "    {\"class\": " << c.id << ", \"name\": \"" << c.name << "\", \"parties\": [\n";
            for (size_t pi = 0; pi < c.parties.size(); ++pi) {
                const auto& p = c.parties[pi];
                out << "      {\"index\": " << p.index << ", \"lone_attack_no\": " << unsigned(p.lone_attack_no) << ", \"rival_starter\": " << unsigned(p.rival_starter) << ", \"mons\": [\n";
                for (size_t mi = 0; mi < p.mons.size(); ++mi) {
                    const auto& m = p.mons[mi];
                    out << "        {\"species\": " << unsigned(m.species) << ", \"dex\": " << unsigned(m.dex) << ", \"level\": " << unsigned(m.level)
                        << ", \"status\": " << unsigned(m.status) << ", \"types\": [" << unsigned(m.type1) << "," << unsigned(m.type2) << "], \"moves\": ";
                    list(m.moves, 4); out << ", \"pp\": "; list(m.pp, 4); out << ", \"dvs\": "; list(m.dvs, 2);
                    out << ", \"hp\": " << m.hp << ", \"max_hp\": " << m.max_hp << ", \"attack\": " << m.attack << ", \"defense\": " << m.defense
                        << ", \"speed\": " << m.speed << ", \"special\": " << m.special << "}" << (mi + 1 < p.mons.size() ? "," : "") << "\n";
                }
                out << "      ]}" << (pi + 1 < c.parties.size() ? "," : "") << "\n";
            }
            out << "    ]}" << (ci + 1 < d.classes.size() ? "," : "") << "\n";
        }
        out << "  ]\n}\n";
        std::cout << "dumped " << d.party_count() << " parties across " << d.classes.size() << " classes to " << argv[1] << "\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "error: " << e.what() << "\n";
        return 1;
    }
}
