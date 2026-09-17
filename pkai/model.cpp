#include "model.h"
#include <algorithm>
#include <cstdio>
#include <cstring>
namespace pkai {
using namespace fx;
using namespace kernels;

namespace {
enum Fn : uint8_t { FnEmbed, FnAttnIn, FnGemmQ, FnGemmK, FnGemmV, FnAttnHead, FnGemmO, FnFfnIn, FnGemmF1, FnGemmF2,
                    FnPoolIn, FnGemmPoolK, FnGemmPoolV, FnPoolHead, FnPoolO, FnGruIh, FnGruHh, FnGruGate, FnCtx,
                    FnPtrQ, FnPtrIn, FnGemmPtrK, FnPtrHead, FnValue };
struct EmbedSpec { const char* name; unsigned first, count; const char* tables[4]; unsigned cols[4]; unsigned n; };
const EmbedSpec EMBED_SPECS[N_TOKEN_TYPES] = {
    {"field", 0, 1, {"emb.trainer", "emb.request"}, {0, 1}, 2},
    {"own_mon", 1, 6, {"emb.species", "emb.type", "emb.type", "emb.matchup"}, {0, 1, 2, 3}, 4},
    {"player_mon", 7, 6, {"emb.species", "emb.type", "emb.type", "emb.matchup"}, {0, 1, 2, 3}, 4},
    {"own_move", 13, 4, {"emb.move", "emb.effect", "emb.type"}, {0, 1, 2}, 3},
    {"player_move", 17, 4, {"emb.move", "emb.effect", "emb.type"}, {0, 1, 2}, 3},
    {"item", 21, 6, {"emb.item"}, {0}, 1},
};
constexpr unsigned T = MAX_TOKENS;
}

const char* model_op_kind_name(ModelOpKind k) {
    static const char* names[OpKindCount] = {"embed", "gemm8", "attn_head", "gru", "pass"};
    return k < OpKindCount ? names[k] : "?";
}


bool PepModel::resolve_requant(const Weights& w, const char* name, unsigned n, Requant& rq) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "%s.mult", name); Tensor m = w.find(buf);
    std::snprintf(buf, sizeof buf, "%s.shift", name); Tensor s = w.find(buf);
    if (!m.i32() || !s.i8() || m.count() != s.count() || (m.count() != 1 && m.count() != n)) { error_ = std::string("bad requant ") + name; return false; }
    rq.mult = m.i32(); rq.shift = s.i8(); rq.n = unsigned(m.count());
    return true;
}

bool PepModel::resolve_linear(const Weights& w, const char* name, unsigned C, unsigned K, bool bias, Linear& lin) {
    char buf[64];
    std::snprintf(buf, sizeof buf, "%s.w", name); Tensor wt = w.find(buf);
    if (!wt.i8() || wt.ndim != 2 || wt.shape[0] != C || wt.shape[1] != K) { error_ = std::string("bad weight ") + name; return false; }
    lin.w = wt.i8(); lin.C = C; lin.K = K; lin.b = nullptr;
    if (bias) {
        std::snprintf(buf, sizeof buf, "%s.b", name); Tensor b = w.find(buf);
        if (!b.i32() || b.count() != C) { error_ = std::string("bad bias ") + name; return false; }
        lin.b = b.i32();
    }
    return resolve_requant(w, name, C, lin.rq);
}

bool PepModel::resolve_table(const Weights& w, const char* name, unsigned width, const int8_t*& p, unsigned& rows) {
    Tensor t = w.find(name);
    if (!t.i8() || t.ndim != 2 || t.shape[1] != width || !t.shape[0]) { error_ = std::string("bad embedding table ") + name; return false; }
    p = t.i8(); rows = t.shape[0];
    return true;
}

bool PepModel::bind(const Weights& w) {
    bound_ = false; active_ = false; error_.clear();
    if (!w.loaded()) { error_ = "weights not loaded"; return false; }
    cfg_ = w.config();
    d_ = cfg_.d; heads_ = cfg_.heads; ffn_ = cfg_.ffn; g_ = cfg_.gru; layers_ = cfg_.layers;
    if (!d_ || !heads_ || d_ % heads_ || !ffn_ || !g_ || !layers_ || layers_ > 8 || d_ > 1024 || ffn_ > 4096 || g_ > 1024) { error_ = "unsupported config"; return false; }
    dh_ = d_ / heads_;
    blocks_ = (T + GEMM_BLOCK_ROWS - 1) / GEMM_BLOCK_ROWS;
    // The op program must fit its fixed table: T embeds + per layer (2 passes,
    // 6 GEMMs by block, heads) + pool/GRU/context/pointer/value tail.
    if (T + layers_ * (2 + 6 * blocks_ + heads_) + 3 * blocks_ + heads_ + 12 > MAX_OPS) { error_ = "op program too long"; return false; }
    if (g_ > 128) { error_ = "gru size exceeds the tracker hidden state"; return false; }

    // LUTs.
    Tensor e = w.find("lut.exp_q15"), r = w.find("lut.recip_q15"), s = w.find("lut.sigmoid_q14"), th = w.find("lut.tanh_q14");
    if (!e.i16() || e.count() != 257 || !r.i16() || r.count() != 256 || !s.i16() || s.count() != 257 || !th.i16() || th.count() != 257) { error_ = "bad LUTs"; return false; }
    luts_ = {e.i16(), r.i16(), s.i16(), th.i16()};

    // Embeddings: per token type, Linear(48 + sum(table widths) -> d).
    const unsigned small = cfg_.emb_small ? cfg_.emb_small : 8, esp = cfg_.emb_species ? cfg_.emb_species : 32,
                   emv = cfg_.emb_move ? cfg_.emb_move : 32, emm = cfg_.emb_matchup ? cfg_.emb_matchup : 8;
    auto width_of = [&](const char* table) -> unsigned {
        if (!std::strcmp(table, "emb.species")) return esp;
        if (!std::strcmp(table, "emb.move")) return emv;
        if (!std::strcmp(table, "emb.matchup")) return emm;
        return small;
    };
    unsigned in8_total = 0;
    for (unsigned i = 0; i < N_TOKEN_TYPES; ++i) {
        const EmbedSpec& sp = EMBED_SPECS[i]; Embed& em = embeds_[i];
        em.name = sp.name; em.first = sp.first; em.count = sp.count; em.n_tables = sp.n;
        unsigned K = FEAT;
        for (unsigned k = 0; k < sp.n; ++k) {
            em.widths[k] = width_of(sp.tables[k]); em.cols[k] = sp.cols[k];
            if (!resolve_table(w, sp.tables[k], em.widths[k], em.tables[k], em.rows[k])) return false;
            K += em.widths[k];
        }
        char name[64]; std::snprintf(name, sizeof name, "embed.%s", sp.name);
        if (!resolve_linear(w, name, d_, K, true, em.lin)) return false;
        in8_total += sp.count * K;
    }

    // Encoder layers.
    layer_.assign(layers_, Layer{});
    for (unsigned l = 0; l < layers_; ++l) {
        Layer& L = layer_[l]; char p[64];
        std::snprintf(p, sizeof p, "layers.%u.attn.in", l); if (!resolve_requant(w, p, 1, L.attn_in)) return false;
        std::snprintf(p, sizeof p, "layers.%u.attn.q", l); if (!resolve_linear(w, p, d_, d_, false, L.q)) return false;
        std::snprintf(p, sizeof p, "layers.%u.attn.k", l); if (!resolve_linear(w, p, d_, d_, false, L.k)) return false;
        std::snprintf(p, sizeof p, "layers.%u.attn.v", l); if (!resolve_linear(w, p, d_, d_, false, L.v)) return false;
        std::snprintf(p, sizeof p, "layers.%u.attn.pv", l); if (!resolve_requant(w, p, 1, L.pv)) return false;
        std::snprintf(p, sizeof p, "layers.%u.attn.o", l); if (!resolve_linear(w, p, d_, d_, false, L.o)) return false;
        std::snprintf(p, sizeof p, "layers.%u.ffn.in", l); if (!resolve_requant(w, p, 1, L.ffn_in)) return false;
        std::snprintf(p, sizeof p, "layers.%u.ffn.f1", l); if (!resolve_linear(w, p, ffn_, d_, true, L.f1)) return false;
        std::snprintf(p, sizeof p, "layers.%u.ffn.f2", l); if (!resolve_linear(w, p, d_, ffn_, true, L.f2)) return false;
        std::snprintf(p, sizeof p, "layers.%u.attn.logit_shift", l); Tensor ls = w.find(p);
        std::snprintf(p, sizeof p, "layers.%u.alpha_attn_q14", l); Tensor aa = w.find(p);
        std::snprintf(p, sizeof p, "layers.%u.alpha_ffn_q14", l); Tensor af = w.find(p);
        if (!ls.i8() || ls.count() != 1 || !aa.i16() || aa.count() != 1 || !af.i16() || af.count() != 1) { error_ = "bad layer scalars"; return false; }
        L.logit_shift = ls.i8()[0]; L.alpha_attn = aa.i16()[0]; L.alpha_ffn = af.i16()[0];
    }

    // Pool, GRU, context, pointer head, value.
    if (!resolve_requant(w, "pool.in", 1, pool_in_) || !resolve_linear(w, "pool.k", d_, d_, false, pool_k_) ||
        !resolve_linear(w, "pool.v", d_, d_, false, pool_v_) || !resolve_requant(w, "pool.pv", 1, pool_pv_) ||
        !resolve_linear(w, "pool.o", d_, d_, false, pool_o_)) return false;
    { Tensor q = w.find("pool.q8"), ls = w.find("pool.logit_shift");
      if (!q.i8() || q.count() != d_ || !ls.i8() || ls.count() != 1) { error_ = "bad pool scalars"; return false; }
      pool_q8_ = q.i8(); pool_logit_shift_ = ls.i8()[0]; }
    if (!resolve_requant(w, "gru.h8", 1, gru_h8_) || !resolve_linear(w, "gru.ih", 3 * g_, EV_DIM + d_, true, gru_ih_) ||
        !resolve_linear(w, "gru.hh", 3 * g_, g_, true, gru_hh_) || !resolve_linear(w, "ctx", d_, d_ + g_, true, ctx_) ||
        !resolve_linear(w, "ptr.q", d_, d_, false, ptr_q_) || !resolve_requant(w, "ptr.in", 1, ptr_in_) ||
        !resolve_linear(w, "ptr.k", d_, d_, false, ptr_k_)) return false;
    { Tensor ls = w.find("ptr.logit_shift"), bt = w.find("ptr.b_type_q8"), vw = w.find("value.w"), vb = w.find("value.b");
      if (!ls.i8() || ls.count() != 1 || !bt.i16() || bt.count() != N_TOKEN_TYPES || !vw.i8() || vw.count() != d_ || !vb.i32() || vb.count() != 1) { error_ = "bad head scalars"; return false; }
      ptr_logit_shift_ = ls.i8()[0]; ptr_b_type_ = bt.i16(); value_w_ = vw.i8(); value_b_ = vb.i32()[0]; }

    // Scratch.
    const size_t td = size_t(T) * d_;
    x16_.assign(td, 0); o16_.assign(td, 0); in8_.assign(in8_total, 0);
    a8_.assign(td, 0); q8_.assign(td, 0); k8_.assign(td, 0); v8_.assign(td, 0); pv8_.assign(td, 0); f8_.assign(td, 0);
    hid8_.assign(size_t(T) * ffn_, 0); tk8_.assign(td, 0); pk8_.assign(td, 0);
    acc_.assign(std::max<size_t>({size_t(GEMM_BLOCK_ROWS) * ffn_, size_t(3) * g_, d_, size_t(GEMM_BLOCK_ROWS) * d_}), 0);
    scores_.assign(size_t(heads_) * T * T, 0); d16_.assign(size_t(heads_) * T * T, 0); p_q15_.assign(size_t(heads_) * T * T, 0);
    sum_.assign(size_t(heads_) * T, 0); recip_.assign(size_t(heads_) * T, 0); probs_attn_.assign(size_t(heads_) * T * T, 0);
    pv_acc_.assign(td, 0);
    cs8_.assign(d_, 0); gx8_.assign(EV_DIM + d_, 0); h8_.assign(g_, 0); hc8_.assign(g_, 0); cx8_.assign(d_ + g_, 0); c8_.assign(d_, 0); pq8_.assign(d_, 0);
    gi_.assign(3 * g_, 0); gh_.assign(3 * g_, 0); r_.assign(g_, 0); z_.assign(g_, 0); n_.assign(g_, 0); h_new_.assign(g_, 0); h_in_.assign(g_, 0);

    build_program();
    bound_ = true;
    return true;
}

void PepModel::build_program() {
    n_ops_ = 0;
    auto add = [&](ModelOpKind kind, Fn fn, unsigned layer, unsigned index) { ops_[n_ops_++] = Op{uint8_t(kind), uint8_t(fn), uint8_t(layer), uint8_t(index)}; };
    for (unsigned t = 0; t < T; ++t) add(OpEmbed, FnEmbed, 0, t);
    for (unsigned l = 0; l < layers_; ++l) {
        add(OpPass, FnAttnIn, l, 0);
        for (unsigned b = 0; b < blocks_; ++b) add(OpGemm, FnGemmQ, l, b);
        for (unsigned b = 0; b < blocks_; ++b) add(OpGemm, FnGemmK, l, b);
        for (unsigned b = 0; b < blocks_; ++b) add(OpGemm, FnGemmV, l, b);
        for (unsigned h = 0; h < heads_; ++h) add(OpAttnHead, FnAttnHead, l, h);
        for (unsigned b = 0; b < blocks_; ++b) add(OpGemm, FnGemmO, l, b);
        add(OpPass, FnFfnIn, l, 0);
        for (unsigned b = 0; b < blocks_; ++b) add(OpGemm, FnGemmF1, l, b);
        for (unsigned b = 0; b < blocks_; ++b) add(OpGemm, FnGemmF2, l, b);
    }
    add(OpPass, FnPoolIn, 0, 0);
    for (unsigned b = 0; b < blocks_; ++b) add(OpGemm, FnGemmPoolK, 0, b);
    for (unsigned b = 0; b < blocks_; ++b) add(OpGemm, FnGemmPoolV, 0, b);
    for (unsigned h = 0; h < heads_; ++h) add(OpAttnHead, FnPoolHead, 0, h);
    add(OpGemm, FnPoolO, 0, 0);
    add(OpGemm, FnGruIh, 0, 0); add(OpGemm, FnGruHh, 0, 0); add(OpGru, FnGruGate, 0, 0);
    add(OpGemm, FnCtx, 0, 0);
    add(OpGemm, FnPtrQ, 0, 0);
    add(OpPass, FnPtrIn, 0, 0);
    for (unsigned b = 0; b < blocks_; ++b) add(OpGemm, FnGemmPtrK, 0, b);
    add(OpPass, FnPtrHead, 0, 0);
    add(OpGemm, FnValue, 0, 0);
    pc_ = n_ops_;
}


void PepModel::begin(const Features& f, const int8_t* ev8, const int16_t* h_in) {
    feat_ = f;
    if (ev8) std::memcpy(ev8_, ev8, EV_DIM); else std::memset(ev8_, 0, EV_DIM);
    if (h_in) std::memcpy(h_in_.data(), h_in, g_ * sizeof(int16_t)); else std::fill(h_in_.begin(), h_in_.end(), int16_t(0));
    bool any = false;
    for (unsigned t = 0; t < T; ++t) { key_mask_[t] = f.tokens[t].present ? 1 : 0; any |= key_mask_[t] != 0; }
    if (!any) key_mask_[0] = 1;
    pc_ = 0; active_ = true;
}

bool PepModel::step() {
    if (!bound_ || done()) return true;
    exec(ops_[pc_++]);
    if (done()) active_ = false;
    return done();
}

void PepModel::run(const Features& f, const int8_t* ev8, const int16_t* h_in) {
    begin(f, ev8, h_in);
    while (!step()) {}
}

void PepModel::emit_layer(unsigned layer, const char* suffix, const void* data, size_t nbytes) {
    if (!sink_) return;
    char name[64]; std::snprintf(name, sizeof name, "layers.%u.%s", layer, suffix);
    sink_(sink_ctx_, name, data, nbytes);
}

void PepModel::gemm_rows(const int8_t* x, unsigned rows, const Linear& lin, int8_t* out, int32_t lo, int32_t hi) {
    gemm_s8(x, rows, lin.K, lin.w, lin.C, acc_.data());
    requant_s32(acc_.data(), rows, lin.C, lin.b, lin.rq, lo, hi, out);
}
void PepModel::gemm_rows(const int8_t* x, unsigned rows, const Linear& lin, int16_t* out, int32_t lo, int32_t hi) {
    gemm_s8(x, rows, lin.K, lin.w, lin.C, acc_.data());
    requant_s32(acc_.data(), rows, lin.C, lin.b, lin.rq, lo, hi, out);
}
void PepModel::gemm_block(const int8_t* x, unsigned K, const Linear& lin, unsigned block, int8_t* out, int32_t lo, int32_t hi) {
    const unsigned r0 = block * GEMM_BLOCK_ROWS, n = std::min<unsigned>(GEMM_BLOCK_ROWS, T - r0);
    gemm_rows(x + size_t(r0) * K, n, lin, out + size_t(r0) * lin.C, lo, hi);
}
void PepModel::gemm_block(const int8_t* x, unsigned K, const Linear& lin, unsigned block, int16_t* out, int32_t lo, int32_t hi) {
    const unsigned r0 = block * GEMM_BLOCK_ROWS, n = std::min<unsigned>(GEMM_BLOCK_ROWS, T - r0);
    gemm_rows(x + size_t(r0) * K, n, lin, out + size_t(r0) * lin.C, lo, hi);
}

void PepModel::run_head(unsigned layer, unsigned head, bool pool) {
    const unsigned Lq = pool ? 1 : T;
    AttentionHead a{};
    a.q = pool ? pool_q8_ : q8_.data(); a.k = k8_.data(); a.v = v8_.data(); a.key_mask = key_mask_;
    a.Lq = Lq; a.L = T; a.d = d_; a.head = head; a.dh = dh_;
    a.logit_shift = pool ? pool_logit_shift_ : layer_[layer].logit_shift;
    a.pv_rq = pool ? pool_pv_ : layer_[layer].pv; a.luts = &luts_;
    const size_t hs = size_t(head) * Lq * T;
    a.scores = scores_.data() + hs; a.d16 = d16_.data() + hs; a.p_q15 = p_q15_.data() + hs; a.probs = probs_attn_.data() + hs;
    a.sum = sum_.data() + size_t(head) * Lq; a.recip = recip_.data() + size_t(head) * Lq;
    a.pv_acc = pv_acc_.data(); a.pv8 = pv8_.data();
    attention_head_scalar(a);
    if (sink_ && head + 1 == heads_) {
        const size_t n = size_t(heads_) * Lq * T;
        auto em = [&](const char* suffix, const void* data, size_t bytes) { if (pool) { char nm[64]; std::snprintf(nm, sizeof nm, "pool.%s", suffix); sink_(sink_ctx_, nm, data, bytes); } else emit_layer(layer, suffix, data, bytes); };
        em(pool ? "scores" : "attn.scores", scores_.data(), n * 4);
        em(pool ? "d16" : "attn.d16", d16_.data(), n * 2);
        em(pool ? "p_q15" : "attn.p_q15", p_q15_.data(), n * 2);
        em(pool ? "sum" : "attn.sum", sum_.data(), size_t(heads_) * Lq * 4);
        em(pool ? "recip_q15" : "attn.recip_q15", recip_.data(), size_t(heads_) * Lq * 4);
        em(pool ? "probs_u8" : "attn.probs_u8", probs_attn_.data(), n);
        em(pool ? "pv_acc" : "attn.pv_acc", pv_acc_.data(), size_t(Lq) * d_ * 4);
        em(pool ? "pv8" : "attn.pv8", pv8_.data(), size_t(Lq) * d_);
    }
}

void PepModel::finish_pointer() {
    // score_t = <pk8[t], pq8>, shifted into 1/256-nat int16 logits plus the per-type bias.
    for (unsigned t = 0; t < T; ++t) {
        int32_t s = 0;
        const int8_t* kt = pk8_.data() + size_t(t) * d_;
        for (unsigned c = 0; c < d_; ++c) s += int32_t(kt[c]) * pq8_[c];
        score32_[t] = s;
        int64_t s16 = ptr_logit_shift_ >= 0 ? rshift_round_even(s, unsigned(ptr_logit_shift_)) : int64_t(s) << unsigned(-ptr_logit_shift_);
        s16 = sat16(s16);
        const unsigned ty = std::min<unsigned>(feat_.tokens[t].type, N_TOKEN_TYPES - 1);
        token_logit16_[t] = int16_t(sat16(s16 + ptr_b_type_[ty]));
    }
    int32_t logits[N_ACTIONS];
    for (unsigned i = 0; i < N_ACTIONS; ++i) logits[i] = MASK16;
    for (unsigned t = 0; t < T; ++t) {
        const Token& tk = feat_.tokens[t];
        if (tk.candidate > 0 && tk.candidate <= N_ACTIONS && tk.present) logits[tk.candidate - 1] = token_logit16_[t];
    }
    uint8_t pmask[N_ACTIONS]; int32_t lt[N_ACTIONS];
    for (unsigned i = 0; i < N_ACTIONS; ++i) {
        if (!((feat_.legal >> i) & 1)) logits[i] = MASK16;
        logits_q8_[i] = int16_t(logits[i]);
        lt[i] = logits[i] == MASK16 ? MASK16 : sat16(int64_t(logits[i]) << 1);
        logits_t_[i] = int16_t(lt[i]);
        pmask[i] = logits[i] != MASK16;
    }
    softmax_lut_scalar(lt, pmask, N_ACTIONS, 0, luts_, probs_, ptr_d16_, ptr_p_, &ptr_sum_, &ptr_recip_);
    if (sink_) {
        emit("ptr.score32", score32_, sizeof score32_); emit("ptr.token_logit16", token_logit16_, sizeof token_logit16_);
        emit("ptr.logits_q8", logits_q8_, sizeof logits_q8_); emit("ptr.logits_t", logits_t_, sizeof logits_t_);
        emit("ptr.d16", ptr_d16_, sizeof ptr_d16_); emit("ptr.p_q15", ptr_p_, sizeof ptr_p_);
        emit("ptr.sum", &ptr_sum_, 4); emit("ptr.recip_q15", &ptr_recip_, 4); emit("ptr.probs_u8", probs_, sizeof probs_);
    }
}

void PepModel::exec(const Op& op) {
    const unsigned l = op.layer, i = op.index;
    const size_t td = size_t(T) * d_;
    const bool last_block = i + 1 == blocks_;
    switch (Fn(op.fn)) {
    case FnEmbed: {
        // Which token type owns token i (fixed slices, pep_int.TOKEN_SLICES).
        unsigned ty = 0; size_t base = 0;
        for (; ty < N_TOKEN_TYPES; ++ty) { if (i < embeds_[ty].first + embeds_[ty].count) break; base += size_t(embeds_[ty].count) * embeds_[ty].lin.K; }
        const Embed& em = embeds_[ty];
        const Token& tk = feat_.tokens[i];
        int8_t* in = in8_.data() + base + size_t(i - em.first) * em.lin.K;
        std::memcpy(in, tk.f, FEAT);
        unsigned off = FEAT;
        for (unsigned k = 0; k < em.n_tables; ++k) { embed_gather_scalar(em.tables[k], em.rows[k], em.widths[k], tk.cat[em.cols[k]], in + off); off += em.widths[k]; }
        gemm_rows(in, 1, em.lin, x16_.data() + size_t(i) * d_, I16_MIN, I16_MAX);
        if (sink_ && i + 1 == em.first + em.count) { char nm[64]; std::snprintf(nm, sizeof nm, "embed.%s.in8", em.name); emit(nm, in8_.data() + base, size_t(em.count) * em.lin.K); }
        if (sink_ && i + 1 == T) emit("embed.out16", x16_.data(), td * 2);
        break;
    }
    case FnAttnIn: requant_s16(x16_.data(), unsigned(td), layer_[l].attn_in, a8_.data()); emit_layer(l, "attn.in8", a8_.data(), td); break;
    case FnGemmQ: gemm_block(a8_.data(), d_, layer_[l].q, i, q8_.data(), I8_MIN, I8_MAX); if (last_block) emit_layer(l, "attn.q8", q8_.data(), td); break;
    case FnGemmK: gemm_block(a8_.data(), d_, layer_[l].k, i, k8_.data(), I8_MIN, I8_MAX); if (last_block) emit_layer(l, "attn.k8", k8_.data(), td); break;
    case FnGemmV: gemm_block(a8_.data(), d_, layer_[l].v, i, v8_.data(), I8_MIN, I8_MAX); if (last_block) emit_layer(l, "attn.v8", v8_.data(), td); break;
    case FnAttnHead: run_head(l, i, false); break;
    case FnGemmO: {
        gemm_block(pv8_.data(), d_, layer_[l].o, i, o16_.data(), I16_MIN, I16_MAX);
        const unsigned r0 = i * GEMM_BLOCK_ROWS, n = std::min<unsigned>(GEMM_BLOCK_ROWS, T - r0);
        rezero_add_scalar(x16_.data() + size_t(r0) * d_, o16_.data() + size_t(r0) * d_, n * d_, layer_[l].alpha_attn);
        if (last_block) { emit_layer(l, "attn.o16", o16_.data(), td * 2); emit_layer(l, "attn.res16", x16_.data(), td * 2); }
        break;
    }
    case FnFfnIn: requant_s16(x16_.data(), unsigned(td), layer_[l].ffn_in, f8_.data()); emit_layer(l, "ffn.in8", f8_.data(), td); break;
    case FnGemmF1: gemm_block(f8_.data(), d_, layer_[l].f1, i, hid8_.data(), 0, I8_MAX); if (last_block) emit_layer(l, "ffn.hid8", hid8_.data(), size_t(T) * ffn_); break;
    case FnGemmF2: {
        gemm_block(hid8_.data(), ffn_, layer_[l].f2, i, o16_.data(), I16_MIN, I16_MAX);
        const unsigned r0 = i * GEMM_BLOCK_ROWS, n = std::min<unsigned>(GEMM_BLOCK_ROWS, T - r0);
        rezero_add_scalar(x16_.data() + size_t(r0) * d_, o16_.data() + size_t(r0) * d_, n * d_, layer_[l].alpha_ffn);
        if (last_block) { emit_layer(l, "ffn.o16", o16_.data(), td * 2); emit_layer(l, "ffn.res16", x16_.data(), td * 2); }
        break;
    }
    case FnPoolIn: requant_s16(x16_.data(), unsigned(td), pool_in_, a8_.data()); emit("pool.in8", a8_.data(), td); break;
    case FnGemmPoolK: gemm_block(a8_.data(), d_, pool_k_, i, k8_.data(), I8_MIN, I8_MAX); if (last_block) emit("pool.k8", k8_.data(), td); break;
    case FnGemmPoolV: gemm_block(a8_.data(), d_, pool_v_, i, v8_.data(), I8_MIN, I8_MAX); if (last_block) emit("pool.v8", v8_.data(), td); break;
    case FnPoolHead: run_head(0, i, true); break;
    case FnPoolO: gemm_rows(pv8_.data(), 1, pool_o_, cs8_.data(), I8_MIN, I8_MAX); emit("pool.cs8", cs8_.data(), d_); break;
    case FnGruIh:
        std::memcpy(gx8_.data(), ev8_, EV_DIM); std::memcpy(gx8_.data() + EV_DIM, cs8_.data(), d_);
        gemm_rows(gx8_.data(), 1, gru_ih_, gi_.data(), I16_MIN, I16_MAX);
        emit("gru.x8", gx8_.data(), EV_DIM + d_); emit("gru.gi_q12", gi_.data(), size_t(3) * g_ * 2);
        break;
    case FnGruHh:
        requant_s16(h_in_.data(), g_, gru_h8_, h8_.data());
        gemm_rows(h8_.data(), 1, gru_hh_, gh_.data(), I16_MIN, I16_MAX);
        emit("gru.h8", h8_.data(), g_); emit("gru.gh_q12", gh_.data(), size_t(3) * g_ * 2);
        break;
    case FnGruGate:
        gru_step_scalar(gi_.data(), gh_.data(), h_in_.data(), g_, luts_, r_.data(), z_.data(), n_.data(), h_new_.data());
        emit("gru.r_q14", r_.data(), g_ * 2); emit("gru.z_q14", z_.data(), g_ * 2); emit("gru.n_q14", n_.data(), g_ * 2); emit("gru.h_q14", h_new_.data(), g_ * 2);
        break;
    case FnCtx:
        requant_s16(h_new_.data(), g_, gru_h8_, hc8_.data());
        std::memcpy(cx8_.data(), cs8_.data(), d_); std::memcpy(cx8_.data() + d_, hc8_.data(), g_);
        gemm_rows(cx8_.data(), 1, ctx_, c8_.data(), I8_MIN, I8_MAX);
        emit("ctx.in8", cx8_.data(), d_ + g_); emit("ctx.c8", c8_.data(), d_);
        break;
    case FnPtrQ: gemm_rows(c8_.data(), 1, ptr_q_, pq8_.data(), I8_MIN, I8_MAX); emit("ptr.q8", pq8_.data(), d_); break;
    case FnPtrIn: requant_s16(x16_.data(), unsigned(td), ptr_in_, tk8_.data()); emit("ptr.tok8", tk8_.data(), td); break;
    case FnGemmPtrK: gemm_block(tk8_.data(), d_, ptr_k_, i, pk8_.data(), I8_MIN, I8_MAX); if (last_block) emit("ptr.k8", pk8_.data(), td); break;
    case FnPtrHead: finish_pointer(); break;
    case FnValue: {
        int64_t s = value_b_;
        for (unsigned c = 0; c < d_; ++c) s += int32_t(c8_[c]) * value_w_[c];
        value_acc_ = sat32(s);
        emit("value.acc32", &value_acc_, 4);
        break;
    }
    }
}

}
