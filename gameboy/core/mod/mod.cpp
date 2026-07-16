#include "mod.h"

#include "cart/cart.h"
#include "cpu/cpu.h"

#include <algorithm>
#include <cctype>
#include <cstring>
#include <limits>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace gbmod {
namespace {

constexpr uint8_t package_magic[8] = {'G', 'B', 'M', 'O', 'D', '1', '\r', '\n'};
constexpr uint16_t package_version = 1;
constexpr uint16_t host_abi_version = 1;
constexpr uint32_t header_size = 96;
constexpr uint32_t import_record_size = 8;
constexpr uint32_t section_record_size = 32;
constexpr uint32_t patch_record_size = 32;
constexpr uint32_t symbol_record_size = 16;
constexpr uint32_t relocation_record_size = 24;
constexpr uint32_t no_string = 0xFFFFFFFFu;

enum : uint8_t {
    placement_fixed = 0,
    placement_symbol = 1,
    placement_append = 2,
};

enum : uint8_t {
    section_verify_fill = 0x01,
};

enum : uint8_t {
    patch_fixed = 0,
    patch_symbol = 1,
};

enum : uint8_t {
    relocation_section = 0,
    relocation_patch = 1,
};

enum : uint8_t {
    reloc_abs8 = 0,
    reloc_abs16 = 1,
    reloc_bank8 = 2,
    reloc_relative8 = 3,
    reloc_call16 = 4,
    reloc_host16 = 5,
    reloc_bank16 = 6,
};

enum : uint8_t {
    reference_rom = 0,
    reference_module = 1,
    reference_host = 2,
};

struct Address {
    uint16_t bank = 0;
    uint16_t address = 0;
    size_t offset = 0;
};

struct Import {
    std::string name;
    uint32_t flags = 0;
};

struct Section {
    std::string name;
    std::vector<uint8_t> data;
    std::string anchor;
    int32_t anchor_addend = 0;
    uint16_t address = 0;
    uint16_t bank = 0;
    uint16_t alignment = 1;
    uint8_t placement = placement_fixed;
    uint8_t flags = 0;
    uint8_t fill = 0xFF;
};

struct Patch {
    std::vector<uint8_t> data;
    std::vector<uint8_t> expected;
    std::string target;
    int32_t target_addend = 0;
    uint16_t address = 0;
    uint16_t bank = 0;
    uint8_t target_kind = patch_fixed;
};

struct ModuleSymbol {
    std::string name;
    uint32_t section = 0;
    uint32_t offset = 0;
    uint32_t flags = 0;
};

struct Relocation {
    uint8_t target_kind = relocation_section;
    uint8_t type = reloc_abs16;
    uint8_t reference_kind = reference_rom;
    uint8_t flags = 0;
    uint32_t target_index = 0;
    uint32_t offset = 0;
    uint32_t reference = 0;
    int32_t addend = 0;
    std::string rom_symbol;
};

struct Package {
    uint32_t handle = 0;
    std::string id;
    std::string name;
    uint32_t target_crc32 = 0;
    uint32_t target_size = 0;
    uint32_t flags = 0;
    std::vector<uint8_t> metadata;
    std::vector<Import> imports;
    std::vector<Section> sections;
    std::vector<Patch> patches;
    std::vector<ModuleSymbol> symbols;
    std::vector<Relocation> relocations;
};

struct Binding {
    uint32_t handle = 0;
    uint32_t import_index = 0;
};

struct LinkImage {
    std::vector<uint8_t> rom;
    std::unordered_map<size_t, Binding> traps;
};

uint16_t read_u16(const uint8_t* p) {
    return static_cast<uint16_t>(p[0] | (static_cast<uint16_t>(p[1]) << 8));
}

uint32_t read_u32(const uint8_t* p) {
    return static_cast<uint32_t>(p[0]) |
           (static_cast<uint32_t>(p[1]) << 8) |
           (static_cast<uint32_t>(p[2]) << 16) |
           (static_cast<uint32_t>(p[3]) << 24);
}

int32_t read_i32(const uint8_t* p) {
    return static_cast<int32_t>(read_u32(p));
}

void write_u16(std::vector<uint8_t>& data, size_t at, uint16_t value) {
    data[at] = static_cast<uint8_t>(value);
    data[at + 1] = static_cast<uint8_t>(value >> 8);
}

bool checked_range(size_t offset, size_t count, size_t item_size, size_t limit) {
    if (offset > limit || item_size != 0 && count > (limit - offset) / item_size) return false;
    return offset + count * item_size <= limit;
}

uint32_t crc32(const uint8_t* data, size_t len) {
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; ++i) {
        crc ^= data[i];
        for (int bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

bool is_power_of_two(uint32_t value) {
    return value != 0 && (value & (value - 1)) == 0;
}

bool make_address(uint16_t bank, uint16_t address, Address& out) {
    if (bank == 0) {
        if (address >= 0x4000) return false;
        out = {bank, address, address};
        return true;
    }
    if (address < 0x4000 || address >= 0x8000) return false;
    out = {bank, address,
           static_cast<size_t>(bank) * 0x4000 + (address - 0x4000)};
    return true;
}

bool advance_address(const Address& base, int64_t amount, Address& out) {
    const int64_t address = static_cast<int64_t>(base.address) + amount;
    if (address < 0 || address > 0xFFFF) return false;
    return make_address(base.bank, static_cast<uint16_t>(address), out);
}

uint32_t max_rom_banks(uint8_t cartridge_type) {
    switch (cartridge_type) {
        case 0x00: return 2;
        case 0x01: case 0x02: case 0x03: return 128;
        case 0x0F: case 0x10: case 0x11: case 0x12: case 0x13: return 128;
        case 0x19: case 0x1A: case 0x1B:
        case 0x1C: case 0x1D: case 0x1E: return 512;
        default: return 0;
    }
}

void update_checksums(std::vector<uint8_t>& rom) {
    if (rom.size() < 0x150) return;
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

std::string trim(std::string value) {
    const auto first = std::find_if_not(value.begin(), value.end(),
                                        [](unsigned char c) { return std::isspace(c) != 0; });
    const auto last = std::find_if_not(value.rbegin(), value.rend(),
                                       [](unsigned char c) { return std::isspace(c) != 0; }).base();
    if (first >= last) return {};
    return std::string(first, last);
}

bool parse_hex(const std::string& input, uint32_t& value) {
    size_t start = !input.empty() && input[0] == '$' ? 1 : 0;
    if (start == input.size()) return false;
    uint32_t result = 0;
    for (size_t i = start; i < input.size(); ++i) {
        const unsigned char c = static_cast<unsigned char>(input[i]);
        uint32_t digit;
        if (c >= '0' && c <= '9') digit = c - '0';
        else if (c >= 'a' && c <= 'f') digit = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') digit = c - 'A' + 10;
        else return false;
        if (result > (std::numeric_limits<uint32_t>::max() - digit) / 16) return false;
        result = result * 16 + digit;
    }
    value = result;
    return true;
}

Status parse_symbol_document(const char* text, size_t len,
                             std::unordered_map<std::string, Address>& symbols,
                             std::string& error) {
    if (!text && len) {
        error = "symbol document pointer is null";
        return Status::invalid_argument;
    }
    std::istringstream input(std::string(text ? text : "", len));
    std::string line;
    size_t line_number = 0;
    while (std::getline(input, line)) {
        ++line_number;
        const size_t comment = line.find(';');
        if (comment != std::string::npos) line.erase(comment);
        line = trim(std::move(line));
        if (line.empty()) continue;

        std::istringstream fields(line);
        std::string location, name, extra;
        fields >> location >> name;
        if (location.empty() || name.empty() || fields >> extra) {
            error = "invalid RGBDS symbol line " + std::to_string(line_number);
            return Status::bad_symbols;
        }
        const size_t colon = location.find(':');
        if (colon == std::string::npos) {
            // RGBDS also emits numeric constant records as `VALUE Name`.
            // They are not addresses and cannot participate in ROM linking.
            uint32_t constant;
            if (parse_hex(location, constant)) continue;
            error = "invalid RGBDS address on symbol line " + std::to_string(line_number);
            return Status::bad_symbols;
        }
        if (location.find(':', colon + 1) != std::string::npos) {
            error = "invalid RGBDS address on symbol line " + std::to_string(line_number);
            return Status::bad_symbols;
        }
        uint32_t bank, address;
        Address resolved;
        if (!parse_hex(location.substr(0, colon), bank) || bank > 0x1FF ||
            !parse_hex(location.substr(colon + 1), address) || address > 0xFFFF) {
            error = "out-of-range RGBDS address on symbol line " + std::to_string(line_number);
            return Status::bad_symbols;
        }
        // A normal RGBDS .sym file also contains VRAM, SRAM, WRAM, OAM, and
        // HRAM labels. They are valid records but cannot anchor ROM patches or
        // sections, so accept and ignore them instead of rejecting the entire
        // document.
        if (address >= 0x8000) continue;
        if (!make_address(static_cast<uint16_t>(bank), static_cast<uint16_t>(address), resolved)) {
            error = "out-of-range RGBDS address on symbol line " + std::to_string(line_number);
            return Status::bad_symbols;
        }
        const auto found = symbols.find(name);
        if (found != symbols.end() &&
            (found->second.bank != resolved.bank || found->second.address != resolved.address)) {
            error = "symbol '" + name + "' has conflicting definitions";
            return Status::bad_symbols;
        }
        symbols[name] = resolved;
    }
    return Status::ok;
}

Status parse_package(const uint8_t* data, size_t len, Package& package,
                     std::string& error) {
    if (!data || len < header_size) {
        error = "mod package is shorter than the v1 header";
        return data ? Status::bad_package : Status::invalid_argument;
    }
    if (std::memcmp(data, package_magic, sizeof(package_magic)) != 0) {
        error = "mod package magic is not GBMOD1";
        return Status::bad_package;
    }
    const uint16_t version = read_u16(data + 8);
    const uint16_t abi = read_u16(data + 10);
    if (version != package_version || abi != host_abi_version) {
        error = "unsupported mod package or host ABI version";
        return Status::unsupported_version;
    }
    const uint32_t declared_header = read_u32(data + 12);
    const uint32_t declared_size = read_u32(data + 16);
    if (declared_header < header_size || declared_header > len || declared_size != len) {
        error = "mod package header/total size is inconsistent";
        return Status::bad_package;
    }

    package.flags = read_u32(data + 20);
    package.target_crc32 = read_u32(data + 24);
    package.target_size = read_u32(data + 28);
    const uint32_t id_offset = read_u32(data + 32);
    const uint32_t name_offset = read_u32(data + 36);
    const uint32_t metadata_offset = read_u32(data + 40);
    const uint32_t metadata_size = read_u32(data + 44);
    const uint32_t strings_offset = read_u32(data + 48);
    const uint32_t strings_size = read_u32(data + 52);
    const uint32_t imports_offset = read_u32(data + 56);
    const uint32_t imports_count = read_u32(data + 60);
    const uint32_t sections_offset = read_u32(data + 64);
    const uint32_t sections_count = read_u32(data + 68);
    const uint32_t patches_offset = read_u32(data + 72);
    const uint32_t patches_count = read_u32(data + 76);
    const uint32_t symbols_offset = read_u32(data + 80);
    const uint32_t symbols_count = read_u32(data + 84);
    const uint32_t relocations_offset = read_u32(data + 88);
    const uint32_t relocations_count = read_u32(data + 92);

    if (!checked_range(metadata_offset, metadata_size, 1, len) ||
        !checked_range(strings_offset, strings_size, 1, len) ||
        !checked_range(imports_offset, imports_count, import_record_size, len) ||
        !checked_range(sections_offset, sections_count, section_record_size, len) ||
        !checked_range(patches_offset, patches_count, patch_record_size, len) ||
        !checked_range(symbols_offset, symbols_count, symbol_record_size, len) ||
        !checked_range(relocations_offset, relocations_count, relocation_record_size, len)) {
        error = "mod package table points outside the payload";
        return Status::bad_package;
    }

    auto get_string = [&](uint32_t offset, bool optional, std::string& out) -> bool {
        if (optional && offset == no_string) { out.clear(); return true; }
        if (offset >= strings_size) return false;
        const char* first = reinterpret_cast<const char*>(data + strings_offset + offset);
        const void* zero = std::memchr(first, 0, strings_size - offset);
        if (!zero) return false;
        out.assign(first, static_cast<const char*>(zero));
        return optional || !out.empty();
    };

    if (!get_string(id_offset, false, package.id) ||
        !get_string(name_offset, true, package.name)) {
        error = "mod id/name is not a valid string-table entry";
        return Status::bad_package;
    }
    if (package.name.empty()) package.name = package.id;
    package.metadata.assign(data + metadata_offset, data + metadata_offset + metadata_size);

    package.imports.reserve(imports_count);
    for (uint32_t i = 0; i < imports_count; ++i) {
        const uint8_t* record = data + imports_offset + i * import_record_size;
        Import import;
        if (!get_string(read_u32(record), false, import.name)) {
            error = "host import has an invalid name";
            return Status::bad_package;
        }
        import.flags = read_u32(record + 4);
        package.imports.push_back(std::move(import));
    }

    package.sections.reserve(sections_count);
    for (uint32_t i = 0; i < sections_count; ++i) {
        const uint8_t* record = data + sections_offset + i * section_record_size;
        Section section;
        const uint32_t blob_offset = read_u32(record + 4);
        const uint32_t blob_size = read_u32(record + 8);
        if (!get_string(read_u32(record), false, section.name) ||
            !get_string(read_u32(record + 12), true, section.anchor) ||
            !checked_range(blob_offset, blob_size, 1, len) || blob_size == 0) {
            error = "module section has invalid name, anchor, or data";
            return Status::bad_package;
        }
        section.data.assign(data + blob_offset, data + blob_offset + blob_size);
        section.anchor_addend = read_i32(record + 16);
        section.address = read_u16(record + 20);
        section.bank = read_u16(record + 22);
        section.alignment = read_u16(record + 24);
        section.placement = record[26];
        section.flags = record[27];
        section.fill = record[28];
        if (section.alignment == 0) section.alignment = 1;
        if (!is_power_of_two(section.alignment) || section.alignment > 0x4000 ||
            section.placement > placement_append ||
            (section.placement == placement_symbol && section.anchor.empty())) {
            error = "module section has invalid placement or alignment";
            return Status::bad_package;
        }
        package.sections.push_back(std::move(section));
    }

    package.patches.reserve(patches_count);
    for (uint32_t i = 0; i < patches_count; ++i) {
        const uint8_t* record = data + patches_offset + i * patch_record_size;
        Patch patch;
        const uint32_t blob_offset = read_u32(record);
        const uint32_t blob_size = read_u32(record + 4);
        const uint32_t expected_offset = read_u32(record + 8);
        const uint32_t expected_size = read_u32(record + 12);
        if (!checked_range(blob_offset, blob_size, 1, len) || blob_size == 0 ||
            !checked_range(expected_offset, expected_size, 1, len) ||
            expected_size != 0 && expected_size != blob_size ||
            !get_string(read_u32(record + 16), true, patch.target)) {
            error = "ROM patch has invalid target, data, or expected bytes";
            return Status::bad_package;
        }
        patch.data.assign(data + blob_offset, data + blob_offset + blob_size);
        patch.expected.assign(data + expected_offset, data + expected_offset + expected_size);
        patch.target_addend = read_i32(record + 20);
        patch.address = read_u16(record + 24);
        patch.bank = read_u16(record + 26);
        patch.target_kind = record[28];
        if (patch.target_kind > patch_symbol ||
            patch.target_kind == patch_symbol && patch.target.empty()) {
            error = "ROM patch has invalid target kind";
            return Status::bad_package;
        }
        package.patches.push_back(std::move(patch));
    }

    package.symbols.reserve(symbols_count);
    for (uint32_t i = 0; i < symbols_count; ++i) {
        const uint8_t* record = data + symbols_offset + i * symbol_record_size;
        ModuleSymbol symbol;
        if (!get_string(read_u32(record), false, symbol.name)) {
            error = "module export has an invalid name";
            return Status::bad_package;
        }
        symbol.section = read_u32(record + 4);
        symbol.offset = read_u32(record + 8);
        symbol.flags = read_u32(record + 12);
        if (symbol.section >= package.sections.size() ||
            symbol.offset >= package.sections[symbol.section].data.size()) {
            error = "module export points outside its section";
            return Status::bad_package;
        }
        package.symbols.push_back(std::move(symbol));
    }

    package.relocations.reserve(relocations_count);
    for (uint32_t i = 0; i < relocations_count; ++i) {
        const uint8_t* record = data + relocations_offset + i * relocation_record_size;
        Relocation relocation;
        relocation.target_kind = record[0];
        relocation.type = record[1];
        relocation.reference_kind = record[2];
        relocation.flags = record[3];
        relocation.target_index = read_u32(record + 4);
        relocation.offset = read_u32(record + 8);
        relocation.reference = read_u32(record + 12);
        relocation.addend = read_i32(record + 16);
        if (relocation.target_kind > relocation_patch || relocation.type > reloc_bank16 ||
            relocation.reference_kind > reference_host ||
            relocation.target_kind == relocation_section && relocation.target_index >= package.sections.size() ||
            relocation.target_kind == relocation_patch && relocation.target_index >= package.patches.size() ||
            relocation.reference_kind == reference_module && relocation.reference >= package.symbols.size() ||
            relocation.reference_kind == reference_host && relocation.reference >= package.imports.size() ||
            relocation.reference_kind == reference_rom &&
                !get_string(relocation.reference, false, relocation.rom_symbol)) {
            error = "relocation has an invalid type, target, or reference";
            return Status::bad_package;
        }
        const size_t target_size = relocation.target_kind == relocation_section
            ? package.sections[relocation.target_index].data.size()
            : package.patches[relocation.target_index].data.size();
        const size_t width = relocation.type == reloc_abs8 || relocation.type == reloc_bank8 ||
                             relocation.type == reloc_relative8 ? 1 : 2;
        if (relocation.offset > target_size || width > target_size - relocation.offset) {
            error = "relocation writes outside its target";
            return Status::bad_package;
        }
        if (relocation.type == reloc_host16 && relocation.reference_kind != reference_host) {
            error = "HOST16 relocation does not reference a host import";
            return Status::bad_package;
        }
        package.relocations.push_back(std::move(relocation));
    }
    return Status::ok;
}

bool is_call_opcode(uint8_t opcode) {
    return opcode == 0xCD || opcode == 0xC4 || opcode == 0xCC ||
           opcode == 0xD4 || opcode == 0xDC;
}

} // namespace

struct Runtime::Impl {
    Cartridge* cartridge = nullptr;
    std::vector<uint8_t> base_rom;
    std::unordered_map<std::string, Address> rom_symbols;
    std::vector<Package> packages;
    std::unordered_map<size_t, Binding> traps;
    uint32_t next_handle = 1;
    HostCallback callback = nullptr;
    void* callback_user = nullptr;
    bool invoking_host = false;
    std::string error;

    Status fail(Status status, std::string message) {
        error = std::move(message);
        return status;
    }

    const Package* find_package(uint32_t handle) const {
        const auto found = std::find_if(packages.begin(), packages.end(),
            [handle](const Package& package) { return package.handle == handle; });
        return found == packages.end() ? nullptr : &*found;
    }

    Status resolve_named(const std::string& name,
                         const std::unordered_map<std::string, Address>& symbols,
                         Address& address) {
        const auto found = symbols.find(name);
        if (found == symbols.end())
            return fail(Status::missing_symbol, "ROM symbol '" + name + "' was not provided");
        address = found->second;
        if (address.offset >= base_rom.size())
            return fail(Status::missing_symbol, "ROM symbol '" + name + "' lies outside the ROM");
        return Status::ok;
    }

    Status reserve_range(std::vector<int32_t>& owners, size_t offset, size_t size,
                         int32_t owner, const Package& package, const char* kind) {
        if (offset > owners.size() || size > owners.size() - offset)
            return fail(Status::link_error, std::string(kind) + " in mod '" + package.id +
                        "' lies outside the linked ROM");
        for (size_t i = offset; i < offset + size; ++i)
            if (owners[i] != -1)
                return fail(Status::conflict, std::string(kind) + " in mod '" + package.id +
                            "' overlaps another active mod");
        std::fill(owners.begin() + offset, owners.begin() + offset + size, owner);
        return Status::ok;
    }

    Status link(const std::vector<Package>& candidates,
                const std::unordered_map<std::string, Address>& symbols,
                LinkImage& image) {
        if (!cartridge || base_rom.empty()) return fail(Status::no_rom, "no ROM is loaded");
        if (candidates.empty()) {
            image.rom = base_rom;
            image.traps.clear();
            return Status::ok;
        }
        const uint32_t base_crc = crc32(base_rom.data(), base_rom.size());
        for (const Package& package : candidates) {
            if (package.target_size && package.target_size != base_rom.size())
                return fail(Status::rom_mismatch, "mod '" + package.id + "' targets a different ROM size");
            if (package.target_crc32 && package.target_crc32 != base_crc)
                return fail(Status::rom_mismatch, "mod '" + package.id + "' targets a different ROM CRC32");
        }

        image.rom = base_rom;
        image.traps.clear();
        std::vector<int32_t> owners(image.rom.size(), -1);
        std::vector<std::vector<Address>> section_addresses(candidates.size());
        std::vector<std::vector<Address>> patch_addresses(candidates.size());
        std::vector<std::vector<Address>> export_addresses(candidates.size());
        std::vector<std::vector<uint16_t>> import_slots(candidates.size());
        std::vector<Binding> bindings;

        for (size_t p = 0; p < candidates.size(); ++p) {
            import_slots[p].reserve(candidates[p].imports.size());
            for (size_t i = 0; i < candidates[p].imports.size(); ++i) {
                if (bindings.size() >= 0x10000)
                    return fail(Status::link_error, "active mods exceed the 65536 host-import limit");
                import_slots[p].push_back(static_cast<uint16_t>(bindings.size()));
                bindings.push_back({candidates[p].handle, static_cast<uint32_t>(i)});
            }
        }

        size_t append_bank = (base_rom.size() + 0x3FFF) / 0x4000;
        size_t append_cursor = 0;
        bool appended = false;
        int32_t owner = 0;
        for (size_t p = 0; p < candidates.size(); ++p) {
            const Package& package = candidates[p];
            section_addresses[p].reserve(package.sections.size());
            for (const Section& section : package.sections) {
                Address address;
                if (section.data.size() > 0x4000)
                    return fail(Status::link_error, "section '" + section.name + "' is larger than one ROM bank");
                if (section.placement == placement_fixed) {
                    if (!make_address(section.bank, section.address, address))
                        return fail(Status::link_error, "section '" + section.name + "' has an invalid fixed address");
                } else if (section.placement == placement_symbol) {
                    Status status = resolve_named(section.anchor, symbols, address);
                    if (status != Status::ok) return status;
                    Address advanced;
                    if (!advance_address(address, section.anchor_addend, advanced))
                        return fail(Status::link_error, "section '" + section.name + "' crosses a bank boundary");
                    address = advanced;
                } else {
                    appended = true;
                    append_cursor = (append_cursor + section.alignment - 1) & ~(section.alignment - 1);
                    if (append_cursor + section.data.size() > 0x4000) {
                        ++append_bank;
                        append_cursor = 0;
                    }
                    if (append_bank == 0 || append_bank > 0x1FF ||
                        !make_address(static_cast<uint16_t>(append_bank),
                                      static_cast<uint16_t>(0x4000 + append_cursor), address))
                        return fail(Status::link_error, "appended section cannot be addressed by the cartridge");
                    append_cursor += section.data.size();
                }

                Address last;
                if (!advance_address(address, static_cast<int64_t>(section.data.size()) - 1, last))
                    return fail(Status::link_error, "section '" + section.name + "' crosses a bank boundary");
                const size_t needed = last.offset + 1;
                if (section.placement != placement_append && needed > base_rom.size())
                    return fail(Status::link_error, "fixed section '" + section.name + "' lies outside the base ROM");
                if (needed > image.rom.size()) {
                    image.rom.resize(needed, 0xFF);
                    owners.resize(needed, -1);
                }
                Status status = reserve_range(owners, address.offset, section.data.size(),
                                              owner++, package, "module section");
                if (status != Status::ok) return status;
                if (section.flags & section_verify_fill)
                    for (size_t i = 0; i < section.data.size(); ++i)
                        if (image.rom[address.offset + i] != section.fill)
                            return fail(Status::conflict, "section '" + section.name +
                                        "' did not find its declared fill bytes");
                std::copy(section.data.begin(), section.data.end(), image.rom.begin() + address.offset);
                section_addresses[p].push_back(address);
            }
        }

        for (size_t p = 0; p < candidates.size(); ++p) {
            const Package& package = candidates[p];
            patch_addresses[p].reserve(package.patches.size());
            for (const Patch& patch : package.patches) {
                Address address;
                if (patch.target_kind == patch_fixed) {
                    if (!make_address(patch.bank, patch.address, address))
                        return fail(Status::link_error, "ROM patch in mod '" + package.id + "' has an invalid address");
                } else {
                    Status status = resolve_named(patch.target, symbols, address);
                    if (status != Status::ok) return status;
                    Address advanced;
                    if (!advance_address(address, patch.target_addend, advanced))
                        return fail(Status::link_error, "ROM patch in mod '" + package.id + "' crosses a bank boundary");
                    address = advanced;
                }
                Address last;
                if (!advance_address(address, static_cast<int64_t>(patch.data.size()) - 1, last) ||
                    last.offset >= base_rom.size())
                    return fail(Status::link_error, "ROM patch in mod '" + package.id + "' lies outside the base ROM");
                if (!patch.expected.empty() &&
                    !std::equal(patch.expected.begin(), patch.expected.end(), base_rom.begin() + address.offset))
                    return fail(Status::rom_mismatch, "ROM patch in mod '" + package.id + "' failed its expected-byte check");
                Status status = reserve_range(owners, address.offset, patch.data.size(), owner++, package, "ROM patch");
                if (status != Status::ok) return status;
                std::copy(patch.data.begin(), patch.data.end(), image.rom.begin() + address.offset);
                patch_addresses[p].push_back(address);
            }
        }

        for (size_t p = 0; p < candidates.size(); ++p) {
            const Package& package = candidates[p];
            export_addresses[p].reserve(package.symbols.size());
            for (const ModuleSymbol& symbol : package.symbols) {
                Address address;
                if (!advance_address(section_addresses[p][symbol.section], symbol.offset, address))
                    return fail(Status::link_error, "module export '" + symbol.name + "' crosses a bank boundary");
                export_addresses[p].push_back(address);
            }
        }

        for (size_t p = 0; p < candidates.size(); ++p) {
            const Package& package = candidates[p];
            for (const Relocation& relocation : package.relocations) {
                const Address& target_base = relocation.target_kind == relocation_section
                    ? section_addresses[p][relocation.target_index]
                    : patch_addresses[p][relocation.target_index];
                Address target;
                if (!advance_address(target_base, relocation.offset, target))
                    return fail(Status::link_error, "relocation target crosses a bank boundary in mod '" + package.id + "'");

                Address reference;
                uint16_t slot = 0;
                if (relocation.reference_kind == reference_rom) {
                    Status status = resolve_named(relocation.rom_symbol, symbols, reference);
                    if (status != Status::ok) return status;
                } else if (relocation.reference_kind == reference_module) {
                    reference = export_addresses[p][relocation.reference];
                } else {
                    slot = import_slots[p][relocation.reference];
                }

                Address adjusted;
                if (relocation.reference_kind != reference_host &&
                    !advance_address(reference, relocation.addend, adjusted))
                    return fail(Status::link_error, "relocation reference crosses a bank boundary in mod '" + package.id + "'");

                switch (relocation.type) {
                    case reloc_abs8:
                        if (relocation.reference_kind == reference_host || adjusted.address > 0xFF)
                            return fail(Status::link_error, "ABS8 relocation is out of range in mod '" + package.id + "'");
                        image.rom[target.offset] = static_cast<uint8_t>(adjusted.address);
                        break;
                    case reloc_abs16:
                        if (relocation.reference_kind == reference_host)
                            return fail(Status::link_error, "ABS16 relocation references a host import");
                        write_u16(image.rom, target.offset, adjusted.address);
                        break;
                    case reloc_bank8: {
                        const int64_t bank = static_cast<int64_t>(reference.bank) + relocation.addend;
                        if (relocation.reference_kind == reference_host || bank < 0 || bank > 0xFF)
                            return fail(Status::link_error, "BANK8 relocation is out of range in mod '" + package.id + "'");
                        image.rom[target.offset] = static_cast<uint8_t>(bank);
                        break;
                    }
                    case reloc_bank16: {
                        const int64_t bank = static_cast<int64_t>(reference.bank) + relocation.addend;
                        if (relocation.reference_kind == reference_host || bank < 0 || bank > 0x1FF)
                            return fail(Status::link_error, "BANK16 relocation is out of range in mod '" + package.id + "'");
                        write_u16(image.rom, target.offset, static_cast<uint16_t>(bank));
                        break;
                    }
                    case reloc_relative8: {
                        if (relocation.reference_kind == reference_host || adjusted.bank != target.bank)
                            return fail(Status::link_error, "REL8 relocation crosses a bank in mod '" + package.id + "'");
                        const int64_t delta = static_cast<int64_t>(adjusted.address) - target.address - 1;
                        if (delta < -128 || delta > 127)
                            return fail(Status::link_error, "REL8 relocation is out of range in mod '" + package.id + "'");
                        image.rom[target.offset] = static_cast<uint8_t>(static_cast<int8_t>(delta));
                        break;
                    }
                    case reloc_call16:
                        if (relocation.reference_kind == reference_host || target.offset == 0 ||
                            !is_call_opcode(image.rom[target.offset - 1]))
                            return fail(Status::link_error, "CALL16 relocation is not attached to a CALL instruction");
                        if (adjusted.bank != 0 && adjusted.bank != target.bank)
                            return fail(Status::link_error, "CALL16 relocation crosses a switched ROM bank in mod '" + package.id + "'");
                        write_u16(image.rom, target.offset, adjusted.address);
                        break;
                    case reloc_host16:
                        if (relocation.reference_kind != reference_host || relocation.addend != 0 ||
                            target.offset == 0 || image.rom[target.offset - 1] != 0xD3)
                            return fail(Status::link_error, "HOST16 relocation is not attached to a D3 host-call trap");
                        write_u16(image.rom, target.offset, slot);
                        image.traps.emplace(target.offset - 1, bindings[slot]);
                        break;
                    default:
                        return fail(Status::bad_package, "unknown relocation type");
                }
            }
        }

        if (appended) {
            uint32_t used_banks = static_cast<uint32_t>((image.rom.size() + 0x3FFF) / 0x4000);
            uint32_t padded_banks = 2;
            while (padded_banks < used_banks && padded_banks < 512) padded_banks <<= 1;
            const uint32_t maximum = max_rom_banks(base_rom[0x147]);
            if (maximum == 0 || padded_banks > maximum)
                return fail(Status::link_error, "appended sections exceed the cartridge mapper's ROM-bank limit");
            image.rom.resize(static_cast<size_t>(padded_banks) * 0x4000, 0xFF);
            uint8_t size_code = 0;
            for (uint32_t banks = 2; banks < padded_banks; banks <<= 1) ++size_code;
            image.rom[0x148] = size_code;
        }
        if (image.rom[0x147] != base_rom[0x147] || image.rom[0x149] != base_rom[0x149] ||
            !appended && image.rom[0x148] != base_rom[0x148])
            return fail(Status::link_error, "mods may not change the cartridge mapper/RAM/ROM-size header fields");
        update_checksums(image.rom);
        return Status::ok;
    }

    void commit(LinkImage&& image) {
        cartridge->rom = std::move(image.rom);
        traps = std::move(image.traps);
        error.clear();
    }
};

Runtime::Runtime() : impl_(new Impl) {}
Runtime::~Runtime() = default;

void Runtime::on_rom_loaded(Cartridge* cartridge) {
    impl_->cartridge = cartridge;
    impl_->base_rom = cartridge ? cartridge->rom : std::vector<uint8_t>{};
    impl_->rom_symbols.clear();
    impl_->packages.clear();
    impl_->traps.clear();
    impl_->next_handle = 1;
    impl_->error.clear();
}

Status Runtime::load_symbols(const char* text, size_t len) {
    if (impl_->invoking_host)
        return impl_->fail(Status::link_error, "mods cannot be relinked from inside a host callback");
    std::unordered_map<std::string, Address> symbols;
    std::string parse_error;
    Status status = parse_symbol_document(text, len, symbols, parse_error);
    if (status != Status::ok) return impl_->fail(status, std::move(parse_error));
    if (!impl_->cartridge) return impl_->fail(Status::no_rom, "no ROM is loaded");
    LinkImage image;
    status = impl_->link(impl_->packages, symbols, image);
    if (status != Status::ok) return status;
    impl_->rom_symbols = std::move(symbols);
    impl_->commit(std::move(image));
    return Status::ok;
}

Status Runtime::load_package(const uint8_t* data, size_t len, uint32_t* handle) {
    if (!handle) return impl_->fail(Status::invalid_argument, "mod handle output pointer is null");
    *handle = 0;
    if (impl_->invoking_host)
        return impl_->fail(Status::link_error, "mods cannot be relinked from inside a host callback");
    if (!impl_->cartridge) return impl_->fail(Status::no_rom, "no ROM is loaded");
    Package package;
    std::string parse_error;
    Status status = parse_package(data, len, package, parse_error);
    if (status != Status::ok) return impl_->fail(status, std::move(parse_error));
    if (std::any_of(impl_->packages.begin(), impl_->packages.end(),
                    [&](const Package& active) { return active.id == package.id; }))
        return impl_->fail(Status::conflict, "a mod with id '" + package.id + "' is already active");
    package.handle = impl_->next_handle;
    std::vector<Package> candidates = impl_->packages;
    candidates.push_back(std::move(package));
    LinkImage image;
    status = impl_->link(candidates, impl_->rom_symbols, image);
    if (status != Status::ok) return status;
    *handle = impl_->next_handle++;
    impl_->packages = std::move(candidates);
    impl_->commit(std::move(image));
    return Status::ok;
}

Status Runtime::unload_package(uint32_t handle) {
    if (impl_->invoking_host)
        return impl_->fail(Status::link_error, "mods cannot be relinked from inside a host callback");
    const auto found = std::find_if(impl_->packages.begin(), impl_->packages.end(),
        [handle](const Package& package) { return package.handle == handle; });
    if (found == impl_->packages.end())
        return impl_->fail(Status::not_found, "mod handle is not active");
    std::vector<Package> candidates = impl_->packages;
    candidates.erase(candidates.begin() + (found - impl_->packages.begin()));
    if (candidates.empty()) {
        impl_->cartridge->rom = impl_->base_rom;
        impl_->traps.clear();
        impl_->packages.clear();
        impl_->error.clear();
        return Status::ok;
    }
    LinkImage image;
    Status status = impl_->link(candidates, impl_->rom_symbols, image);
    if (status != Status::ok) return status;
    impl_->packages = std::move(candidates);
    impl_->commit(std::move(image));
    return Status::ok;
}

size_t Runtime::package_count() const { return impl_->packages.size(); }

const char* Runtime::package_id(uint32_t handle) const {
    const Package* package = impl_->find_package(handle);
    return package ? package->id.c_str() : nullptr;
}

const char* Runtime::package_name(uint32_t handle) const {
    const Package* package = impl_->find_package(handle);
    return package ? package->name.c_str() : nullptr;
}

size_t Runtime::import_count(uint32_t handle) const {
    const Package* package = impl_->find_package(handle);
    return package ? package->imports.size() : 0;
}

const char* Runtime::import_name(uint32_t handle, size_t index) const {
    const Package* package = impl_->find_package(handle);
    return package && index < package->imports.size() ? package->imports[index].name.c_str() : nullptr;
}

const uint8_t* Runtime::metadata(uint32_t handle, size_t* len) const {
    if (len) *len = 0;
    const Package* package = impl_->find_package(handle);
    if (!package || !len || package->metadata.empty()) return nullptr;
    *len = package->metadata.size();
    return package->metadata.data();
}

void Runtime::set_host_callback(HostCallback callback, void* user) {
    impl_->callback = callback;
    impl_->callback_user = user;
}

bool Runtime::invoke_host_call(CPU& cpu, uint16_t opcode_address) {
    if (!impl_->cartridge) return false;
    const size_t offset = impl_->cartridge->mapped_rom_offset(opcode_address);
    const auto found = impl_->traps.find(offset);
    if (found == impl_->traps.end()) return false;
    const Binding binding = found->second;
    cpu.pc = static_cast<uint16_t>(cpu.pc + 2); // consume the linked 16-bit trap slot
    CpuContext context{cpu.af, cpu.bc, cpu.de, cpu.hl, cpu.sp, cpu.pc,
                       static_cast<uint8_t>(cpu.ime), static_cast<uint8_t>(cpu.halted)};
    struct HostInvocation {
        explicit HostInvocation(bool& active) : active_(active) { active_ = true; }
        ~HostInvocation() { active_ = false; }
        bool& active_;
    } invocation(impl_->invoking_host);
    if (impl_->callback)
        impl_->callback(impl_->callback_user, binding.handle, binding.import_index, context);
    cpu.af = context.af & 0xFFF0;
    cpu.bc = context.bc;
    cpu.de = context.de;
    cpu.hl = context.hl;
    cpu.sp = context.sp;
    cpu.pc = context.pc;
    cpu.ime = context.ime != 0;
    cpu.halted = context.halted != 0;
    return true;
}

const char* Runtime::last_error() const { return impl_->error.c_str(); }

} // namespace gbmod
