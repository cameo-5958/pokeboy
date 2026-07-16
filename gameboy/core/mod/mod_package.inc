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
