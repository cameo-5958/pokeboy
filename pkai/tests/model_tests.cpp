// PepModel vs the executable specification (ai/models/pep_int.py): every traced
// intermediate and every output of the reference vectors must match bit for bit,
// the resumable begin/step path must agree with run(), the GRU hidden must carry
// across the multi-step sequence, and the dispatched kernels (NEON on device)
// must equal the scalar reference kernels on random data.
//
//   pkai_model_tests <pkai.weights> <vectors dir>
//
// The vectors dir holds vectors.bin + vectors.idx from
//   cd ai && uv run python -m tools.dump_vectors checkpoints/pep/stone-v1/vectors <dir>
// (or `cmake --build build-ai --target pkai_vectors`). Missing files skip (exit 77).
#include "pkai/model.h"
#include "pkai/weights.h"
#include "pkai/kernels/kernels.h"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <random>
#include <sstream>
#include <string>
#include <vector>
using namespace pkai;

#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "%s:%d: CHECK(%s) failed\n", __FILE__, __LINE__, #x); std::exit(1); } } while (0)

namespace {

struct Arr { std::string dtype; unsigned ndim{}; unsigned shape[4]{}; size_t offset{}, nbytes{}; };

struct Vectors {
    std::vector<uint8_t> bin;
    std::map<std::string, Arr> idx;
    bool load(const std::string& dir) {
        std::ifstream f(dir + "/vectors.idx");
        if (!f) return false;
        std::string line;
        while (std::getline(f, line)) {
            if (line.empty()) continue;
            std::istringstream ss(line); std::string name; Arr a;
            ss >> name >> a.dtype >> a.ndim >> a.shape[0] >> a.shape[1] >> a.shape[2] >> a.shape[3] >> a.offset >> a.nbytes;
            idx[name] = a;
        }
        std::ifstream b(dir + "/vectors.bin", std::ios::binary);
        if (!b) return false;
        bin.assign(std::istreambuf_iterator<char>(b), {});
        return !idx.empty() && !bin.empty();
    }
    const Arr& at(const std::string& name) const {
        auto it = idx.find(name);
        if (it == idx.end()) { std::fprintf(stderr, "missing array %s\n", name.c_str()); std::exit(1); }
        return it->second;
    }
    // Byte slice of sample `s` (first axis) of `name`; `stride` receives the per-sample size.
    const uint8_t* sample(const std::string& name, size_t s, size_t* stride = nullptr) const {
        const Arr& a = at(name);
        const size_t st = a.nbytes / a.shape[0];
        if (stride) *stride = st;
        return bin.data() + a.offset + s * st;
    }
    template <class T> T scalar(const std::string& name, size_t s) const { T v; std::memcpy(&v, sample(name, s), sizeof v); return v; }
};

Features features_from(const Vectors& v, const std::string& prefix, size_t s) {
    Features F{};
    F.count = MAX_TOKENS;
    F.legal = v.scalar<uint16_t>(prefix + "legal", s);
    if (v.idx.count(prefix + "request_kind")) F.request_kind = v.scalar<uint8_t>(prefix + "request_kind", s);
    const uint8_t* type = v.sample(prefix + "type", s);
    const uint8_t* present = v.sample(prefix + "present", s);
    const uint8_t* cand = v.sample(prefix + "candidate", s);
    const uint8_t* cat = v.sample(prefix + "cat", s);
    const uint8_t* f = v.sample(prefix + "f", s);
    for (unsigned t = 0; t < MAX_TOKENS; ++t) {
        Token& tk = F.tokens[t];
        tk.type = type[t]; tk.present = present[t]; tk.candidate = cand[t]; tk.index = uint8_t(t);
        std::memcpy(tk.cat, cat + t * 8, 8);
        std::memcpy(tk.f, f + t * FEAT, FEAT);
    }
    return F;
}

// Trace sink: compares every emitted intermediate with the reference slice.
struct Checker {
    const Vectors* v{}; size_t sample{}; unsigned mismatches{}, emitted{};
    std::map<std::string, unsigned> seen;
    static void sink(void* ctx, const char* name, const void* data, size_t nbytes) { static_cast<Checker*>(ctx)->on(name, data, nbytes); }
    void on(const char* name, const void* data, size_t nbytes) {
        ++emitted; ++seen[name];
        const std::string key = std::string("intermediates.") + name;
        auto it = v->idx.find(key);
        if (it == v->idx.end()) { std::fprintf(stderr, "sample %zu: emitted %s has no reference\n", sample, name); ++mismatches; return; }
        size_t stride = 0; const uint8_t* ref = v->sample(key, sample, &stride);
        if (stride != nbytes) { std::fprintf(stderr, "sample %zu: %s size %zu != reference %zu\n", sample, name, nbytes, stride); ++mismatches; return; }
        if (std::memcmp(ref, data, nbytes) != 0) {
            const uint8_t* got = static_cast<const uint8_t*>(data);
            size_t i = 0; while (i < nbytes && got[i] == ref[i]) ++i;
            std::fprintf(stderr, "sample %zu: %s differs (first byte %zu of %zu: got %d ref %d)\n", sample, name, i, nbytes, got[i], ref[i]);
            ++mismatches;
        }
    }
};

bool same(const void* a, const void* b, size_t n) { return std::memcmp(a, b, n) == 0; }

void kernel_equivalence() {
    using namespace kernels;
    std::mt19937 rng(20260911);
    auto rnd8 = [&](std::vector<int8_t>& x) { std::uniform_int_distribution<int> d(-127, 127); for (auto& e : x) e = int8_t(d(rng)); };
    const unsigned dims[][3] = {{1, 48, 128}, {8, 64, 128}, {3, 104, 128}, {8, 96, 128}, {8, 56, 128}, {8, 128, 512}, {8, 512, 128}, {1, 192, 384}, {5, 13, 7}, {2, 1, 3}, {27, 27, 128}};
    for (const auto& dm : dims) {
        const unsigned rows = dm[0], K = dm[1], C = dm[2];
        std::vector<int8_t> x(size_t(rows) * K), w(size_t(C) * K); rnd8(x); rnd8(w);
        std::vector<int32_t> a(size_t(rows) * C), b(size_t(rows) * C);
        gemm_s8_scalar(x.data(), rows, K, w.data(), C, a.data());
        gemm_s8(x.data(), rows, K, w.data(), C, b.data());
        CHECK(same(a.data(), b.data(), a.size() * 4));
        // P·V with uint8 probabilities and a strided value matrix.
        std::vector<uint8_t> p(size_t(rows) * K); for (auto& e : p) e = uint8_t(rng() & 255);
        std::vector<int8_t> v(size_t(K) * (C + 5)); rnd8(v);
        gemm_u8s8_scalar(p.data(), rows, K, v.data(), C + 5, C, a.data());
        gemm_u8s8(p.data(), rows, K, v.data(), C + 5, C, b.data());
        CHECK(same(a.data(), b.data(), a.size() * 4));
        // Requantisation: per-channel and broadcast, shifts from -3 to 12, both output widths.
        std::vector<int32_t> acc(size_t(rows) * C), bias(C), mult(C); std::vector<int8_t> shift(C);
        std::uniform_int_distribution<int32_t> dacc(-(1 << 24), 1 << 24), dm31(1 << 30, INT32_MAX), dsh(-3, 12), db(-(1 << 20), 1 << 20);
        for (auto& e : acc) e = dacc(rng); for (auto& e : bias) e = db(rng); for (auto& e : mult) e = dm31(rng); for (auto& e : shift) e = int8_t(dsh(rng));
        for (int per_channel = 0; per_channel < 2; ++per_channel) for (int with_bias = 0; with_bias < 2; ++with_bias) {
            Requant rq{mult.data(), shift.data(), per_channel ? C : 1u};
            std::vector<int8_t> o8a(acc.size()), o8b(acc.size()); std::vector<int16_t> o16a(acc.size()), o16b(acc.size());
            requant_s32_scalar(acc.data(), rows, C, with_bias ? bias.data() : nullptr, rq, -127, 127, o8a.data());
            requant_s32(acc.data(), rows, C, with_bias ? bias.data() : nullptr, rq, -127, 127, o8b.data());
            CHECK(same(o8a.data(), o8b.data(), o8a.size()));
            requant_s32_scalar(acc.data(), rows, C, with_bias ? bias.data() : nullptr, rq, -32768, 32767, o16a.data());
            requant_s32(acc.data(), rows, C, with_bias ? bias.data() : nullptr, rq, -32768, 32767, o16b.data());
            CHECK(same(o16a.data(), o16b.data(), o16a.size() * 2));
        }
        std::vector<int16_t> x16(size_t(rows) * C); for (auto& e : x16) e = int16_t(rng());
        std::vector<int8_t> r8a(x16.size()), r8b(x16.size());
        Requant brq{mult.data(), shift.data(), 1};
        requant_s16_scalar(x16.data(), unsigned(x16.size()), brq, r8a.data());
        requant_s16(x16.data(), unsigned(x16.size()), brq, r8b.data());
        CHECK(same(r8a.data(), r8b.data(), r8a.size()));
    }
    // Exact ties on every shift: half-to-even must hold for the dispatched path too.
    for (int sh = 1; sh <= 12; ++sh) {
        std::vector<int32_t> acc; for (int32_t k = -40; k <= 40; ++k) acc.push_back((k << sh) + (1 << (sh - 1)));
        const int32_t m = INT32_MAX; const int8_t s = int8_t(sh);   // mult ~1.0 keeps ties exact through srdmh
        Requant rq{&m, &s, 1};
        std::vector<int16_t> a(acc.size()), b(acc.size());
        requant_s32_scalar(acc.data(), 1, unsigned(acc.size()), nullptr, rq, -32768, 32767, a.data());
        requant_s32(acc.data(), 1, unsigned(acc.size()), nullptr, rq, -32768, 32767, b.data());
        CHECK(same(a.data(), b.data(), a.size() * 2));
    }
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 3) { std::fprintf(stderr, "usage: %s pkai.weights vectors_dir\n", argv[0]); return 2; }
    kernel_equivalence();
    std::printf("kernels: %s backend equals scalar reference\n", kernels::backend_name());

    Weights w;
    if (!w.load(argv[1])) { std::fprintf(stderr, "SKIP: cannot load %s (%s)\n", argv[1], w.error().c_str()); return 77; }
    Vectors v;
    if (!v.load(argv[2])) {
        std::fprintf(stderr, "SKIP: no vectors in %s; generate with\n  cd ai && uv run python -m tools.dump_vectors checkpoints/pep/stone-v1/vectors %s\n", argv[2], argv[2]);
        return 77;
    }
    PepModel model;
    if (!model.bind(w)) { std::fprintf(stderr, "bind: %s\n", model.error().c_str()); return 1; }
    const unsigned g = model.gru_size();
    CHECK(g == v.at("inputs.h0").shape[1]);
    const size_t N = v.at("inputs.f").shape[0];

    // 1. Every traced intermediate and every output, per sample.
    unsigned total_mismatch = 0; std::map<std::string, unsigned> seen_all;
    for (size_t s = 0; s < N; ++s) {
        Checker ck; ck.v = &v; ck.sample = s;
        model.set_trace(&Checker::sink, &ck);
        const Features F = features_from(v, "inputs.", s);
        model.run(F, reinterpret_cast<const int8_t*>(v.sample("inputs.ev8", s)), reinterpret_cast<const int16_t*>(v.sample("inputs.h0", s)));
        model.set_trace(nullptr, nullptr);
        for (auto& kv : ck.seen) seen_all[kv.first] += kv.second;
        total_mismatch += ck.mismatches;
        auto out = [&](const char* name, const void* got, size_t n) {
            size_t st = 0; const uint8_t* ref = v.sample(std::string("outputs.") + name, s, &st);
            if (st != n || !same(ref, got, n)) { std::fprintf(stderr, "sample %zu: output %s differs\n", s, name); ++total_mismatch; }
        };
        out("logits_q8", model.logits_q8(), N_ACTIONS * 2); out("logits_t", model.logits_t(), N_ACTIONS * 2);
        out("probs", model.probs(), N_ACTIONS); const int32_t va = model.value_acc(); out("value_acc", &va, 4); out("h", model.hidden(), g * 2);
    }
    // Every reference intermediate must have been emitted for every sample.
    unsigned n_inter = 0;
    for (auto& kv : v.idx) {
        if (kv.first.rfind("intermediates.", 0) != 0) continue;
        ++n_inter;
        const std::string name = kv.first.substr(14);
        if (seen_all[name] != N) { std::fprintf(stderr, "intermediate %s emitted %u times, expected %zu\n", name.c_str(), seen_all[name], N); ++total_mismatch; }
    }
    CHECK(total_mismatch == 0);
    std::printf("intermediates: %u arrays x %zu samples bit-exact; outputs bit-exact\n", n_inter, N);

    // 2. Resumable path: a second instance stepped one op at a time, with a
    //    foreign decision run on the first instance in between, must agree.
    PepModel stepper; CHECK(stepper.bind(w));
    for (size_t s = 0; s < N; ++s) {
        const Features F = features_from(v, "inputs.", s);
        const int8_t* ev = reinterpret_cast<const int8_t*>(v.sample("inputs.ev8", s));
        const int16_t* h0 = reinterpret_cast<const int16_t*>(v.sample("inputs.h0", s));
        stepper.begin(F, ev, h0);
        CHECK(stepper.active() && !stepper.done() && stepper.op_index() == 0);
        unsigned ops = 0;
        while (!stepper.done()) {
            CHECK(stepper.next_kind() < OpKindCount);
            const unsigned before = stepper.op_index();
            const bool fin = stepper.step(); ++ops;
            CHECK(stepper.op_index() == before + 1 && fin == stepper.done());
            if (ops == 7) model.run(features_from(v, "inputs.", (s + 1) % N), ev, h0);   // unrelated decision elsewhere
        }
        CHECK(ops == stepper.op_count() && !stepper.active());
        model.run(F, ev, h0);
        CHECK(same(model.logits_q8(), stepper.logits_q8(), N_ACTIONS * 2) && same(model.probs(), stepper.probs(), N_ACTIONS));
        CHECK(model.value_acc() == stepper.value_acc() && same(model.hidden(), stepper.hidden(), g * 2));
        CHECK(same(stepper.probs(), v.sample("outputs.probs", s), N_ACTIONS));
    }
    std::printf("resumable: %zu decisions x %u ops agree with run()\n", N, stepper.op_count());

    // 3. Recurrent sequence: hidden carried across decisions from h = 0.
    const Arr& seq_f = v.at("sequence.f");
    const size_t B = seq_f.shape[0], Tm = seq_f.shape[1];
    std::vector<int16_t> h(g, 0);
    unsigned decisions = 0;
    for (size_t b = 0; b < B; ++b) {
        const int32_t steps = v.scalar<int32_t>("sequence.steps", b);
        std::fill(h.begin(), h.end(), int16_t(0));
        for (int32_t i = 0; i < steps; ++i) {
            const size_t row = b * Tm + size_t(i);
            // Flatten (B, T, ...) into (B*T, ...) views by sampling the per-battle slice at row offset.
            Features F{}; F.count = MAX_TOKENS;
            F.legal = reinterpret_cast<const uint16_t*>(v.sample("sequence.legal", b))[i];
            const uint8_t* type = v.sample("sequence.type", b) + i * MAX_TOKENS;
            const uint8_t* present = v.sample("sequence.present", b) + i * MAX_TOKENS;
            const uint8_t* cand = v.sample("sequence.candidate", b) + i * MAX_TOKENS;
            const uint8_t* cat = v.sample("sequence.cat", b) + i * MAX_TOKENS * 8;
            const uint8_t* f = v.sample("sequence.f", b) + i * MAX_TOKENS * FEAT;
            for (unsigned t = 0; t < MAX_TOKENS; ++t) {
                Token& tk = F.tokens[t]; tk.type = type[t]; tk.present = present[t]; tk.candidate = cand[t]; tk.index = uint8_t(t);
                std::memcpy(tk.cat, cat + t * 8, 8); std::memcpy(tk.f, f + t * FEAT, FEAT);
            }
            const int8_t* ev = reinterpret_cast<const int8_t*>(v.sample("sequence.ev8", b)) + i * EV_DIM;
            model.run(F, ev, h.data());
            std::memcpy(h.data(), model.hidden(), g * 2);
            const uint8_t* ref_h = v.sample("sequence.h", b) + size_t(i) * g * 2;
            const uint8_t* ref_l = v.sample("sequence.logits_q8", b) + size_t(i) * N_ACTIONS * 2;
            const uint8_t* ref_p = v.sample("sequence.probs", b) + size_t(i) * N_ACTIONS;
            if (!same(ref_h, h.data(), g * 2) || !same(ref_l, model.logits_q8(), N_ACTIONS * 2) || !same(ref_p, model.probs(), N_ACTIONS)) {
                std::fprintf(stderr, "sequence battle %zu step %d (row %zu) differs\n", b, i, row); return 1;
            }
            ++decisions;
        }
    }
    std::printf("sequence: %u decisions over %zu battles bit-exact with carried hidden\n", decisions, B);
    std::printf("pkai_model_tests: OK\n");
    return 0;
}
