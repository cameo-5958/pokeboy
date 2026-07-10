#include "gb_api.h"

#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace {

#define CHECK(condition) do { \
    if (!(condition)) { \
        std::fprintf(stderr, "CHECK failed at %s:%d: %s\n", __FILE__, __LINE__, #condition); \
        return false; \
    } \
} while (0)

void put_u16(std::vector<uint8_t>& data, size_t at, uint16_t value) {
    data[at] = static_cast<uint8_t>(value);
    data[at + 1] = static_cast<uint8_t>(value >> 8);
}

void put_u32(std::vector<uint8_t>& data, size_t at, uint32_t value) {
    data[at] = static_cast<uint8_t>(value);
    data[at + 1] = static_cast<uint8_t>(value >> 8);
    data[at + 2] = static_cast<uint8_t>(value >> 16);
    data[at + 3] = static_cast<uint8_t>(value >> 24);
}

uint32_t crc32(const std::vector<uint8_t>& data) {
    uint32_t crc = 0xFFFFFFFFu;
    for (uint8_t byte : data) {
        crc ^= byte;
        for (int bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

void update_checksums(std::vector<uint8_t>& rom) {
    uint8_t header = 0;
    for (size_t i = 0x134; i <= 0x14C; ++i)
        header = static_cast<uint8_t>(header - rom[i] - 1);
    rom[0x14D] = header;
    uint32_t global = 0;
    for (size_t i = 0; i < rom.size(); ++i)
        if (i != 0x14E && i != 0x14F) global += rom[i];
    rom[0x14E] = static_cast<uint8_t>(global >> 8);
    rom[0x14F] = static_cast<uint8_t>(global);
}

uint32_t add_string(std::vector<uint8_t>& strings, const char* value) {
    const uint32_t offset = static_cast<uint32_t>(strings.size());
    strings.insert(strings.end(), value, value + std::strlen(value) + 1);
    return offset;
}

size_t append(std::vector<uint8_t>& target, const std::vector<uint8_t>& value) {
    const size_t offset = target.size();
    target.insert(target.end(), value.begin(), value.end());
    return offset;
}

std::vector<uint8_t> make_rom() {
    std::vector<uint8_t> rom(4 * 0x4000, 0xFF);
    // Unmodded loop: NOP, NOP, NOP, JR $0100. The patch replaces the NOPs
    // with CALL ModuleEntry while preserving the loop.
    rom[0x100] = 0x00; rom[0x101] = 0x00; rom[0x102] = 0x00;
    rom[0x103] = 0x18; rom[0x104] = 0xFB;
    rom[0x147] = 0x01; // MBC1
    rom[0x148] = 0x01; // four banks
    rom[0x149] = 0x00;
    std::memcpy(rom.data() + 0x134, "MOD TEST", 8);
    update_checksums(rom);
    return rom;
}

std::vector<uint8_t> make_linked_package(const std::vector<uint8_t>& rom,
                                         const char* id,
                                         bool wrong_crc = false,
                                         bool wrong_expected = false) {
    constexpr size_t header = 96;
    constexpr size_t imports = header;
    constexpr size_t sections = imports + 8;
    constexpr size_t patches = sections + 32;
    constexpr size_t symbols = patches + 32;
    constexpr size_t relocations = symbols + 16;
    constexpr size_t tables_end = relocations + 3 * 24;

    std::vector<uint8_t> strings;
    const uint32_t id_name = add_string(strings, id);
    const uint32_t display_name = add_string(strings, "Fixture Mod");
    const uint32_t import_name = add_string(strings, "typescript.tick");
    const uint32_t section_name = add_string(strings, "module.text");
    const uint32_t export_name = add_string(strings, "ModuleEntry");
    const uint32_t hook_name = add_string(strings, "Hook");

    std::vector<uint8_t> package(tables_end, 0);
    const std::vector<uint8_t> metadata{'{', '}'};
    const size_t metadata_at = append(package, metadata);
    const size_t strings_at = append(package, strings);
    const std::vector<uint8_t> module{0xD3, 0x00, 0x00, 0xC9, 0x00, 0x00};
    const size_t module_at = append(package, module);
    const std::vector<uint8_t> patch{0xCD, 0x00, 0x00};
    const size_t patch_at = append(package, patch);
    const std::vector<uint8_t> expected{
        static_cast<uint8_t>(wrong_expected ? 0xFF : 0x00), 0x00, 0x00};
    const size_t expected_at = append(package, expected);

    const uint8_t magic[8] = {'G', 'B', 'M', 'O', 'D', '1', '\r', '\n'};
    std::memcpy(package.data(), magic, sizeof(magic));
    put_u16(package, 8, 1); put_u16(package, 10, 1);
    put_u32(package, 12, header);
    put_u32(package, 16, static_cast<uint32_t>(package.size()));
    put_u32(package, 24, crc32(rom) ^ (wrong_crc ? 1u : 0u));
    put_u32(package, 28, static_cast<uint32_t>(rom.size()));
    put_u32(package, 32, id_name); put_u32(package, 36, display_name);
    put_u32(package, 40, static_cast<uint32_t>(metadata_at));
    put_u32(package, 44, static_cast<uint32_t>(metadata.size()));
    put_u32(package, 48, static_cast<uint32_t>(strings_at));
    put_u32(package, 52, static_cast<uint32_t>(strings.size()));
    put_u32(package, 56, imports); put_u32(package, 60, 1);
    put_u32(package, 64, sections); put_u32(package, 68, 1);
    put_u32(package, 72, patches); put_u32(package, 76, 1);
    put_u32(package, 80, symbols); put_u32(package, 84, 1);
    put_u32(package, 88, relocations); put_u32(package, 92, 3);

    // One TypeScript import.
    put_u32(package, imports, import_name);

    // Fixed module section in ROM0 at $0200, requiring untouched $FF padding.
    put_u32(package, sections, section_name);
    put_u32(package, sections + 4, static_cast<uint32_t>(module_at));
    put_u32(package, sections + 8, static_cast<uint32_t>(module.size()));
    put_u32(package, sections + 12, 0xFFFFFFFFu);
    put_u16(package, sections + 20, 0x0200);
    put_u16(package, sections + 22, 0);
    put_u16(package, sections + 24, 1);
    package[sections + 26] = 0; // fixed
    package[sections + 27] = 1; // verify fill
    package[sections + 28] = 0xFF;

    // Guarded patch at the pret symbol Hook.
    put_u32(package, patches, static_cast<uint32_t>(patch_at));
    put_u32(package, patches + 4, static_cast<uint32_t>(patch.size()));
    put_u32(package, patches + 8, static_cast<uint32_t>(expected_at));
    put_u32(package, patches + 12, static_cast<uint32_t>(expected.size()));
    put_u32(package, patches + 16, hook_name);
    package[patches + 28] = 1; // symbol target

    put_u32(package, symbols, export_name);
    put_u32(package, symbols + 4, 0); // section
    put_u32(package, symbols + 8, 0); // offset

    // HOST16: D3 <slot16> in the module section.
    package[relocations] = 0; package[relocations + 1] = 5;
    package[relocations + 2] = 2;
    put_u32(package, relocations + 8, 1);
    // CALL16: CALL <ModuleEntry> in the ROM patch.
    const size_t call = relocations + 24;
    package[call] = 1; package[call + 1] = 4; package[call + 2] = 1;
    put_u32(package, call + 8, 1);
    // ABS16: embed the original ROM symbol address after the module RET.
    const size_t absolute = relocations + 48;
    package[absolute] = 0; package[absolute + 1] = 1; package[absolute + 2] = 0;
    put_u32(package, absolute + 8, 4);
    put_u32(package, absolute + 12, hook_name);
    return package;
}

std::vector<uint8_t> make_append_package(const std::vector<uint8_t>& rom) {
    constexpr size_t header = 96;
    constexpr size_t sections = header;
    constexpr size_t patches = sections + 32;
    constexpr size_t symbols = patches + 32;
    constexpr size_t relocations = symbols + 16;
    constexpr size_t tables_end = relocations + 2 * 24;

    std::vector<uint8_t> strings;
    const uint32_t id = add_string(strings, "append-fixture");
    const uint32_t name = add_string(strings, "Append Fixture");
    const uint32_t section_name = add_string(strings, "appended.text");
    const uint32_t export_name = add_string(strings, "AppendedEntry");
    const uint32_t pointer_name = add_string(strings, "ModulePointer");
    std::vector<uint8_t> package(tables_end, 0);
    const size_t strings_at = append(package, strings);
    const std::vector<uint8_t> module{0xC9};
    const size_t module_at = append(package, module);
    const std::vector<uint8_t> pointer{0x00, 0x00, 0x00};
    const size_t pointer_at = append(package, pointer);
    const std::vector<uint8_t> expected{0xFF, 0xFF, 0xFF};
    const size_t expected_at = append(package, expected);

    const uint8_t magic[8] = {'G', 'B', 'M', 'O', 'D', '1', '\r', '\n'};
    std::memcpy(package.data(), magic, sizeof(magic));
    put_u16(package, 8, 1); put_u16(package, 10, 1);
    put_u32(package, 12, header); put_u32(package, 16, static_cast<uint32_t>(package.size()));
    put_u32(package, 24, crc32(rom)); put_u32(package, 28, static_cast<uint32_t>(rom.size()));
    put_u32(package, 32, id); put_u32(package, 36, name);
    put_u32(package, 40, static_cast<uint32_t>(strings_at)); // empty metadata
    put_u32(package, 48, static_cast<uint32_t>(strings_at));
    put_u32(package, 52, static_cast<uint32_t>(strings.size()));
    put_u32(package, 56, header); put_u32(package, 60, 0);
    put_u32(package, 64, sections); put_u32(package, 68, 1);
    put_u32(package, 72, patches); put_u32(package, 76, 1);
    put_u32(package, 80, symbols); put_u32(package, 84, 1);
    put_u32(package, 88, relocations); put_u32(package, 92, 2);

    put_u32(package, sections, section_name);
    put_u32(package, sections + 4, static_cast<uint32_t>(module_at));
    put_u32(package, sections + 8, static_cast<uint32_t>(module.size()));
    put_u32(package, sections + 12, 0xFFFFFFFFu);
    put_u16(package, sections + 24, 16);
    package[sections + 26] = 2; // append

    put_u32(package, patches, static_cast<uint32_t>(pointer_at));
    put_u32(package, patches + 4, static_cast<uint32_t>(pointer.size()));
    put_u32(package, patches + 8, static_cast<uint32_t>(expected_at));
    put_u32(package, patches + 12, static_cast<uint32_t>(expected.size()));
    put_u32(package, patches + 16, pointer_name);
    package[patches + 28] = 1;

    put_u32(package, symbols, export_name);

    // BANK8 and ABS16 form an explicit far pointer to the appended section.
    package[relocations] = 1; package[relocations + 1] = 2;
    package[relocations + 2] = 1;
    const size_t absolute = relocations + 24;
    package[absolute] = 1; package[absolute + 1] = 1; package[absolute + 2] = 1;
    put_u32(package, absolute + 8, 1);
    return package;
}

struct HostState {
    uint32_t handle = 0;
    int calls = 0;
    bool valid = true;
};

void host_callback(gb_handle* gb, uint32_t handle, uint32_t import_index,
                   gb_mod_cpu_context* context, void* user) {
    HostState& state = *static_cast<HostState*>(user);
    state.valid = state.valid && handle == state.handle && import_index == 0 &&
                  context && context->struct_size == sizeof(*context) &&
                  context->pc == 0x0203;
    ++state.calls;
    context->af = static_cast<uint16_t>((context->af + 0x0100) & 0xFFF0);
    gb_write_mem(gb, 0xC123, 0x5A);
}

bool test_link_host_call_and_unload() {
    const std::vector<uint8_t> rom = make_rom();
    gb_handle* gb = gb_create();
    CHECK(gb != nullptr);
    CHECK(gb_load_rom(gb, rom.data(), rom.size()) == 1);
    const char symbols[] = "00:0100 Hook\n00:0200 ModulePointer\n";
    CHECK(gb_mod_load_symbols(gb, symbols, sizeof(symbols) - 1) == GB_MOD_OK);
    CHECK(gb_read_mem(gb, 0x100) == rom[0x100]);

    const std::vector<uint8_t> package = make_linked_package(rom, "fixture-one");
    uint32_t handle = 0;
    const gb_mod_status load_status = gb_mod_load(gb, package.data(), package.size(), &handle);
    if (load_status != GB_MOD_OK)
        std::fprintf(stderr, "initial mod load failed: %s\n", gb_mod_last_error(gb));
    CHECK(load_status == GB_MOD_OK);
    CHECK(handle != 0 && gb_mod_count(gb) == 1);
    CHECK(std::strcmp(gb_mod_id(gb, handle), "fixture-one") == 0);
    CHECK(std::strcmp(gb_mod_name(gb, handle), "Fixture Mod") == 0);
    CHECK(gb_mod_import_count(gb, handle) == 1);
    CHECK(std::strcmp(gb_mod_import_name(gb, handle, 0), "typescript.tick") == 0);
    size_t metadata_size = 0;
    const uint8_t* metadata = gb_mod_metadata(gb, handle, &metadata_size);
    CHECK(metadata_size == 2 && metadata[0] == '{' && metadata[1] == '}');

    CHECK(gb_read_mem(gb, 0x100) == 0xCD);
    CHECK(gb_read_mem(gb, 0x101) == 0x00 && gb_read_mem(gb, 0x102) == 0x02);
    CHECK(gb_read_mem(gb, 0x0200) == 0xD3 && gb_read_mem(gb, 0x0203) == 0xC9);
    CHECK(gb_read_mem(gb, 0x0204) == 0x00 && gb_read_mem(gb, 0x0205) == 0x01);

    HostState host{handle};
    gb_mod_set_host_callback(gb, host_callback, &host);
    gb_reset(gb);
    gb_run_frame(gb);
    CHECK(host.calls > 0 && host.valid);
    CHECK(gb_read_mem(gb, 0xC123) == 0x5A);

    // A second mod cannot claim the first mod's section/patch bytes.
    const std::vector<uint8_t> conflict = make_linked_package(rom, "fixture-two");
    uint32_t conflict_handle = 123;
    CHECK(gb_mod_load(gb, conflict.data(), conflict.size(), &conflict_handle) == GB_MOD_CONFLICT);
    CHECK(conflict_handle == 0 && gb_mod_count(gb) == 1);
    CHECK(gb_read_mem(gb, 0x100) == 0xCD);

    // Failed symbol replacement is also transactional.
    const char incomplete_symbols[] = "00:0200 ModulePointer\n";
    CHECK(gb_mod_load_symbols(gb, incomplete_symbols, sizeof(incomplete_symbols) - 1) ==
          GB_MOD_MISSING_SYMBOL);
    CHECK(gb_read_mem(gb, 0x100) == 0xCD);
    const char invalid_symbols[] = "not a symbol line\n";
    CHECK(gb_mod_load_symbols(gb, invalid_symbols, sizeof(invalid_symbols) - 1) ==
          GB_MOD_BAD_SYMBOLS);
    CHECK(gb_read_mem(gb, 0x100) == 0xCD);

    CHECK(gb_mod_unload(gb, handle) == GB_MOD_OK);
    CHECK(gb_mod_count(gb) == 0);
    CHECK(gb_read_mem(gb, 0x100) == 0x00);
    const int calls_before = host.calls;
    gb_reset(gb);
    gb_run_frame(gb);
    CHECK(host.calls == calls_before); // the unlinked D3 trap cannot fire
    CHECK(gb_mod_unload(gb, handle) == GB_MOD_NOT_FOUND);

    const std::vector<uint8_t> wrong_crc = make_linked_package(rom, "wrong-crc", true);
    uint32_t rejected = 0;
    CHECK(gb_mod_load(gb, wrong_crc.data(), wrong_crc.size(), &rejected) == GB_MOD_ROM_MISMATCH);
    const std::vector<uint8_t> wrong_expected =
        make_linked_package(rom, "wrong-expected", false, true);
    CHECK(gb_mod_load(gb, wrong_expected.data(), wrong_expected.size(), &rejected) ==
          GB_MOD_ROM_MISMATCH);
    CHECK(std::strlen(gb_mod_last_error(gb)) != 0);

    gb_destroy(gb);
    return true;
}

bool test_appended_section_and_far_pointer() {
    const std::vector<uint8_t> rom = make_rom();
    gb_handle* gb = gb_create();
    CHECK(gb_load_rom(gb, rom.data(), rom.size()) == 1);
    const char symbols[] = "00:0100 Hook\n00:0200 ModulePointer\n";
    CHECK(gb_mod_load_symbols(gb, symbols, sizeof(symbols) - 1) == GB_MOD_OK);
    const std::vector<uint8_t> package = make_append_package(rom);
    uint32_t handle = 0;
    const gb_mod_status load_status = gb_mod_load(gb, package.data(), package.size(), &handle);
    if (load_status != GB_MOD_OK)
        std::fprintf(stderr, "append mod load failed: %s\n", gb_mod_last_error(gb));
    CHECK(load_status == GB_MOD_OK);
    CHECK(gb_read_mem(gb, 0x200) == 4); // first bank after the four-bank base ROM
    CHECK(gb_read_mem(gb, 0x201) == 0x00 && gb_read_mem(gb, 0x202) == 0x40);
    CHECK(gb_read_mem(gb, 0x148) == 2); // padded to eight banks
    gb_write_mem(gb, 0x2000, 4);
    CHECK(gb_read_mem(gb, 0x4000) == 0xC9);
    CHECK(gb_mod_unload(gb, handle) == GB_MOD_OK);
    CHECK(gb_read_mem(gb, 0x148) == 1);
    CHECK(gb_read_mem(gb, 0x200) == 0xFF);
    gb_destroy(gb);
    return true;
}

} // namespace

int main() {
    if (!test_link_host_call_and_unload()) return 1;
    if (!test_appended_section_and_far_pointer()) return 1;
    std::puts("mod linker tests passed");
    return 0;
}
