
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
