"""Recurrent PPO self-play / league fine-tune of the PEP trainer-seat policy.

Phase 2 of the PEP training plan: start from an imitation checkpoint and improve
it with on-policy reinforcement learning against a pool of player-seat opponents.

Rollouts (CPU, multiprocessing workers, each holding a CPU copy of the current
policy): every iteration plays `--battles-per-iter` full battles on random ROM
trainer matchups (both parties from `sim.trainers.load().parties`).  The trainer
seat samples from the policy at temperature 1 over legal actions, carrying the
GRU state from zero across the whole battle.  Per decision we keep the raw
feature bytes, legal mask, event vector, action, old log-prob and value; reward
is +1 win / -1 loss / 0 tie at the terminal decision, gamma = 1, GAE lambda 0.95.

Update (GPU): PPO clipped surrogate + value BCE (the value head keeps its
imitation semantics: a win-probability logit, V = 2*sigmoid - 1) + entropy
bonus + optional KL(init || policy) regulariser against the frozen initial
policy.  Whole battles are unrolled with `PEP.forward_seq` so the hidden state
is exact; padded rows are masked.

Usage:
    cd ai && uv run python -m models.train_ppo --init-ckpt checkpoints/pep/v3/model.pt \
        --run ppo-v1 --iters 200 --battles-per-iter 256 --opponents greedy,random --device cuda
"""
from __future__ import annotations

import argparse
import copy
import json
import multiprocessing as mp
import os
import random
import sys
import time
from dataclasses import asdict, dataclass

import numpy as np
import torch
import torch.nn.functional as F

from models.pep import EV_DIM, N_ACTIONS, PEP, PEPConfig, features_to_tensors, load_checkpoint, save_checkpoint
from models.pep_data import FEAT, FEATURES_BYTES, MAX_TOKENS, TOKEN_TYPES, decode_features, decode_features_batch
from models.train_pep import DEFAULT_CKPT_DIR
from sim.matchups import parse_mix, sample_matchup
from sim.player_seat import NetPlayer
from sim.trainer_env import _u16

OPPONENTS = ("greedy", "random", "self", "past")


# --------------------------------------------------------------------------- opponents


def parse_opponents(spec: str) -> list[tuple[str, float]]:
    """"greedy,random" or "self:0.4,past:0.3,greedy:0.3" -> [(name, weight)] normalised."""
    out: list[tuple[str, float]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        name, _, w = part.partition(":")
        if name not in OPPONENTS:
            raise ValueError(f"unknown opponent {name!r}; choose from {OPPONENTS}")
        out.append((name, float(w) if w else 1.0))
    total = sum(w for _, w in out)
    if not out or total <= 0:
        raise ValueError(f"empty opponent pool {spec!r}")
    return [(n, w / total) for n, w in out]


def make_opponent(name: str, seed: int, models: dict | None = None, temperature: float = 1.0):
    """Player-seat policy `f(env) -> engine choice`.

    `self` plays the current policy (models["self"]); `past:<ckpt>` a frozen snapshot loaded
    through models["load"](path) (cached by the caller). Both use the trainer-seat network on
    the player's side of the battle (sim.player_seat)."""
    if name == "self" or name.startswith("past:"):
        if models is None:
            raise ValueError(f"opponent {name!r} needs the model table")
        model = models["self"] if name == "self" else models["load"](name[5:])
        return NetPlayer(model, seed=seed, temperature=temperature).choose
    if name == "greedy":
        from sim.trainer_search import TrainerTeacher

        teacher = TrainerTeacher(depth=1, rolls=1, seed=seed)
        return lambda env: teacher._greedy_player(env)
    if name == "random":
        rng = random.Random(seed)
        return lambda env: rng.choice(env.player_choices())
    raise ValueError(f"unknown opponent {name!r}; choose from {OPPONENTS}")


# --------------------------------------------------------------------------- rollouts


@dataclass
class Matchup:
    trainer_idx: int
    player_idx: int
    battle_seed: int
    rng_seed: int
    opponent: str
    sample_seed: int
    mode: str = "random"


@dataclass
class Episode:
    """One battle's trainer-seat decisions (leading axis T) plus the per-decision rewards."""

    feats: np.ndarray  # (T, 1626) u8 raw pkai::Features
    legal: np.ndarray  # (T,) u16
    event: np.ndarray  # (T, 64) f32
    action: np.ndarray  # (T,) i64
    logp: np.ndarray  # (T,) f32 log-prob of `action` under the rollout policy
    value: np.ndarray  # (T,) f32 rollout value estimate in [-1, 1]
    reward: float  # +1 win / -1 loss / 0 tie (terminal outcome)
    opponent: str
    trainer_class: int
    rewards: np.ndarray | None = None  # (T,) f32 per-decision reward: shaping deltas + terminal outcome at T-1
    mode: str = "random"

    def __len__(self) -> int:
        return int(self.legal.shape[0])


def hp_potential(env) -> float:
    """Shaping potential: mean roster HP fraction, trainer minus player (full information, reward only)."""
    buf = env.buf

    def side(s: int, n: int) -> float:
        tot = 0.0
        for ix in range(n):
            o = env._slot(s, ix)
            tot += _u16(buf, o + 18) / max(1, _u16(buf, o))
        return tot / max(1, n)

    return side(env.me, len(env.trainer_specs)) - side(env.opp, len(env.player_specs))


@torch.no_grad()
def play_episode(model: PEP, env, opponent, gen: torch.Generator, *, temperature: float = 1.0,
                 max_decisions: int = 300, opponent_name: str = "", shaping: float = 0.0, mode: str = "random") -> Episode:
    """Play one battle with the trainer seat sampled from `model` (GRU state carried from zero).

    Per-decision reward = shaping * (potential after the decision's consequences - potential before)
    (potential-based shaping, Ng et al. 1999; telescopes to a per-battle constant so the optimal
    policy is unchanged) plus the +1/-1/0 outcome at the last decision."""
    feats_l: list[bytes] = []; legal_l: list[int] = []; event_l: list[np.ndarray] = []
    action_l: list[int] = []; logp_l: list[float] = []; value_l: list[float] = []; phi_l: list[float] = []
    h = None
    while not env.done() and len(action_l) < max_decisions:
        if env.request_kind() is None:
            env.auto_step(opponent(env))
            continue
        phi_l.append(hp_potential(env) if shaping else 0.0)
        feats, mask, _kind = env.features()
        raw = bytes(feats)
        arrays = decode_features(raw)
        tensors = features_to_tensors({k: np.expand_dims(v, 0) for k, v in arrays.items()})
        tensors["legal"] = torch.tensor([mask], dtype=torch.int64)
        ev = np.asarray(env.last_event, dtype=np.float32)
        logits, z, h = model(tensors, torch.from_numpy(ev)[None], h)
        logits = logits[0].float()
        legal = torch.isfinite(logits)
        if not bool(legal.any()):
            # No candidate token carries a legal action (should not happen); fall back to the mask.
            fallback = [a for a in range(N_ACTIONS) if mask >> a & 1] or [0]
            logits = torch.full((N_ACTIONS,), float("-inf")); logits[fallback] = 0.0
        logp_all = torch.log_softmax(logits.masked_fill(~torch.isfinite(logits), -1e9) / temperature, dim=-1)
        probs = torch.softmax(logits / temperature, dim=-1)
        probs = torch.nan_to_num(probs, nan=0.0)
        a = int(torch.multinomial(probs, 1, generator=gen))
        feats_l.append(raw); legal_l.append(mask); event_l.append(ev)
        action_l.append(a); logp_l.append(float(logp_all[a]))
        value_l.append(float(2.0 * torch.sigmoid(z[0].float()) - 1.0))
        env.step(a, opponent(env))
    w = env.winner_is_trainer()
    reward = 1.0 if w is True else -1.0 if w is False else 0.0
    T = len(action_l)
    rewards = np.zeros(T, np.float32)
    if T:
        phi_l.append(hp_potential(env) if shaping else 0.0)
        for t in range(T):
            rewards[t] = shaping * (phi_l[t + 1] - phi_l[t])
        rewards[T - 1] += reward
    return Episode(
        feats=np.frombuffer(b"".join(feats_l), dtype=np.uint8).reshape(T, FEATURES_BYTES) if T else np.zeros((0, FEATURES_BYTES), np.uint8),
        legal=np.asarray(legal_l, dtype=np.uint16),
        event=np.stack(event_l) if T else np.zeros((0, EV_DIM), np.float32),
        action=np.asarray(action_l, dtype=np.int64),
        logp=np.asarray(logp_l, dtype=np.float32),
        value=np.asarray(value_l, dtype=np.float32),
        reward=reward,
        opponent=opponent_name,
        trainer_class=int(env.trainer_class),
        rewards=rewards,
        mode=mode,
    )


def rollout_matchups(model: PEP, parties, matchups: list[Matchup], *, temperature: float = 1.0,
                     max_decisions: int = 300, models: dict | None = None, shaping: float = 0.0) -> list[Episode]:
    from sim.trainer_env import TrainerEnv

    if models is None:
        models = {"self": model, "load": _PastCache().load}
    out: list[Episode] = []
    for m in matchups:
        t, o = parties[m.trainer_idx], parties[m.player_idx]
        env = TrainerEnv(t.to_specs(), t.class_id, o.to_specs(), seed=m.battle_seed, rng_seed=m.rng_seed)
        opponent = make_opponent(m.opponent, m.sample_seed ^ 0x5EED, models, temperature=temperature)
        gen = torch.Generator(device="cpu").manual_seed(m.sample_seed)
        out.append(play_episode(model, env, opponent, gen, temperature=temperature, max_decisions=max_decisions,
                                opponent_name=m.opponent, shaping=shaping, mode=m.mode))
    return out


class _PastCache:
    """Frozen league snapshots by checkpoint path (small LRU, CPU, eval mode)."""

    def __init__(self, limit: int = 6):
        self.limit = limit
        self.models: dict[str, PEP] = {}

    def load(self, path: str) -> PEP:
        m = self.models.pop(path, None)
        if m is None:
            m, _ = load_checkpoint(path, map_location="cpu")
            m = m.eval()
            for p in m.parameters():
                p.requires_grad_(False)
            while len(self.models) >= self.limit:
                self.models.pop(next(iter(self.models)))
        self.models[path] = m
        return m


# -- multiprocessing workers (spawn context; each holds a CPU policy copy) --------------

_W: dict = {}


def _worker_init(cfg: dict, threads: int) -> None:
    torch.set_num_threads(threads)
    from sim import trainers

    _W["model"] = PEP(PEPConfig(**cfg)).eval()
    _W["version"] = -1
    _W["parties"] = trainers.load().parties
    _W["models"] = {"self": _W["model"], "load": _PastCache().load}


def _worker_task(job: tuple) -> list[Episode]:
    version, state, matchups, temperature, max_decisions, shaping = job
    model: PEP = _W["model"]
    if version != _W["version"]:
        model.load_state_dict(state)
        _W["version"] = version
    return rollout_matchups(model, _W["parties"], matchups, temperature=temperature, max_decisions=max_decisions,
                            models=_W["models"], shaping=shaping)


class RolloutPool:
    """Plays batches of matchups with the current policy, on `workers` CPU processes (0 = in-process)."""

    def __init__(self, cfg: PEPConfig, workers: int, parties, threads: int = 1):
        self.workers = workers
        self.parties = parties
        self.version = 0
        self._local: PEP | None = None
        if workers > 0:
            ctx = mp.get_context("spawn")
            self.pool = ctx.Pool(workers, initializer=_worker_init, initargs=(asdict(cfg), threads))
        else:
            self.pool = None
            self._local = PEP(cfg).eval()

    def close(self) -> None:
        if self.pool is not None:
            self.pool.close(); self.pool.join(); self.pool = None

    def play(self, model: PEP, matchups: list[Matchup], *, temperature: float, max_decisions: int,
             shaping: float = 0.0) -> list[Episode]:
        state = {k: v.detach().cpu() for k, v in model.state_dict().items()}
        self.version += 1
        if self.pool is None:
            self._local.load_state_dict(state)
            return rollout_matchups(self._local, self.parties, matchups, temperature=temperature,
                                    max_decisions=max_decisions, shaping=shaping)
        n = max(1, min(len(matchups), self.workers * 2))
        chunks = [matchups[i::n] for i in range(n)]
        jobs = [(self.version, state, c, temperature, max_decisions, shaping) for c in chunks if c]
        out: list[Episode] = []
        for eps in self.pool.map(_worker_task, jobs, chunksize=1):
            out.extend(eps)
        return out


def sample_matchups(rng: random.Random, parties, n: int, opponents: list[tuple[str, float]],
                    mix: list[tuple[str, float]], past: list[str]) -> list[Matchup]:
    """`n` battles: matchup mode from `mix`, opponent from the weighted pool (`past` picks a snapshot path)."""
    names = [o for o, _ in opponents]; weights = [w for _, w in opponents]
    out: list[Matchup] = []
    for _ in range(n):
        t, o, mode = sample_matchup(rng, parties, mix)
        opp = rng.choices(names, weights)[0]
        if opp == "past":
            opp = f"past:{rng.choice(past)}" if past else "self"
        out.append(Matchup(t, o, rng.getrandbits(62), rng.getrandbits(30), opp, rng.getrandbits(31), mode))
    return out


def opponent_kind(name: str) -> str:
    return "past" if name.startswith("past:") else name


# --------------------------------------------------------------------------- batching / GAE


def gae(values: np.ndarray, rewards: np.ndarray | float, gamma: float, lam: float) -> tuple[np.ndarray, np.ndarray]:
    """Advantages and returns for one episode (V_T = 0); a scalar `rewards` is the terminal-only reward."""
    T = values.shape[0]
    if np.isscalar(rewards):
        r_arr = np.zeros(T, np.float32)
        if T:
            r_arr[T - 1] = float(rewards)
        rewards = r_arr
    adv = np.zeros(T, np.float32)
    last = 0.0
    for t in range(T - 1, -1, -1):
        r = float(rewards[t])
        v_next = 0.0 if t == T - 1 else float(values[t + 1])
        delta = r + gamma * v_next - float(values[t])
        last = delta + gamma * lam * last
        adv[t] = last
    return adv, adv + values


def collate_episodes(episodes: list[Episode], gamma: float, lam: float) -> dict[str, np.ndarray]:
    """Pad episodes to (B,T,...) arrays with mask/old_logp/adv/ret."""
    episodes = [e for e in episodes if len(e) > 0]
    B = len(episodes)
    T = max(len(e) for e in episodes)
    out = {
        "type": np.tile(TOKEN_TYPES, (B, T, 1)),
        "present": np.zeros((B, T, MAX_TOKENS), np.uint8),
        "candidate": np.zeros((B, T, MAX_TOKENS), np.uint8),
        "cat": np.zeros((B, T, MAX_TOKENS, 4), np.uint16),
        "f": np.zeros((B, T, MAX_TOKENS, FEAT), np.int8),
        "legal": np.zeros((B, T), np.uint16),
        "event": np.zeros((B, T, EV_DIM), np.float32),
        "action": np.full((B, T), -1, np.int64),
        "old_logp": np.zeros((B, T), np.float32),
        "old_value": np.zeros((B, T), np.float32),
        "adv": np.zeros((B, T), np.float32),
        "ret": np.zeros((B, T), np.float32),
        "mask": np.zeros((B, T), np.bool_),
    }
    for i, e in enumerate(episodes):
        t = len(e)
        d = decode_features_batch(e.feats)
        out["present"][i, :t] = d["present"]
        out["candidate"][i, :t] = d["candidate"]
        out["cat"][i, :t] = d["cat"]
        out["f"][i, :t] = d["f"]
        out["legal"][i, :t] = e.legal
        out["event"][i, :t] = e.event
        out["action"][i, :t] = e.action
        out["old_logp"][i, :t] = e.logp
        out["old_value"][i, :t] = e.value
        adv, ret = gae(e.value, e.rewards if e.rewards is not None else e.reward, gamma, lam)
        out["adv"][i, :t] = adv
        out["ret"][i, :t] = ret
        out["mask"][i, :t] = True
    return out


def batch_to_device(arrays: dict[str, np.ndarray], device) -> dict[str, torch.Tensor]:
    out = features_to_tensors(arrays, device)
    for k in ("event", "action", "old_logp", "old_value", "adv", "ret", "mask"):
        out[k] = torch.as_tensor(arrays[k], device=device)
    return out


def _index(batch: dict[str, torch.Tensor], idx: torch.Tensor) -> dict[str, torch.Tensor]:
    return {k: v[idx] for k, v in batch.items()}


# --------------------------------------------------------------------------- PPO update


def _legal_logp(logits: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
    """(log-probs over legal actions (…,16) with -1e9 elsewhere, legal bool mask)."""
    legal = torch.isfinite(logits)
    return torch.log_softmax(logits.masked_fill(~legal, -1e9), dim=-1), legal


def ppo_losses(
    logits: torch.Tensor,
    value_logit: torch.Tensor,
    init_logits: torch.Tensor | None,
    batch: dict[str, torch.Tensor],
    *,
    clip: float,
    vf_coef: float,
    ent_coef: float,
    kl_init: float,
    adv_norm: bool = True,
) -> tuple[torch.Tensor, dict[str, float]]:
    """PPO clipped-surrogate objective over a (B,T) minibatch; padded / non-decision rows masked."""
    logits = logits.float()
    logp_all, legal = _legal_logp(logits)
    valid = batch["mask"] & legal.any(dim=-1) & (batch["action"] >= 0)
    n = valid.float().sum().clamp_min(1.0)
    act = batch["action"].clamp(0, N_ACTIONS - 1)
    new_logp = logp_all.gather(-1, act[..., None]).squeeze(-1)
    old_logp = batch["old_logp"]
    adv = batch["adv"]
    if adv_norm:
        m = (adv * valid).sum() / n
        s = torch.sqrt(((adv - m) ** 2 * valid).sum() / n).clamp_min(1e-6)
        adv = (adv - m) / s
    log_ratio = new_logp - old_logp
    ratio = torch.exp(log_ratio)
    surr = torch.min(ratio * adv, ratio.clamp(1.0 - clip, 1.0 + clip) * adv)
    pg_loss = -(surr * valid).sum() / n

    target = ((batch["ret"] + 1.0) * 0.5).clamp(0.0, 1.0)  # return in [-1,1] -> win probability
    bce = F.binary_cross_entropy_with_logits(value_logit.float(), target, reduction="none")
    v_loss = (bce * valid).sum() / n

    p = logp_all.exp().masked_fill(~legal, 0.0)
    entropy = -(p * logp_all.masked_fill(~legal, 0.0)).sum(-1)
    ent = (entropy * valid).sum() / n

    loss = pg_loss + vf_coef * v_loss - ent_coef * ent
    kl_i = torch.zeros((), device=logits.device)
    if init_logits is not None and kl_init > 0:
        ilogp, ilegal = _legal_logp(init_logits.float())
        ip = ilogp.exp().masked_fill(~ilegal, 0.0)
        kl_row = (ip * (ilogp.masked_fill(~ilegal, 0.0) - logp_all.masked_fill(~ilegal, 0.0))).sum(-1)
        kl_i = (kl_row * valid).sum() / n
        loss = loss + kl_init * kl_i

    with torch.no_grad():
        approx_kl = (((ratio - 1.0) - log_ratio) * valid).sum() / n  # k3 estimator
        clipfrac = (((ratio - 1.0).abs() > clip).float() * valid).sum() / n
        v = 2.0 * torch.sigmoid(value_logit.float()) - 1.0
        ev_num = (((batch["ret"] - v) ** 2) * valid).sum() / n
        ret_m = (batch["ret"] * valid).sum() / n
        ev_den = (((batch["ret"] - ret_m) ** 2) * valid).sum() / n
        explained = 1.0 - ev_num / ev_den.clamp_min(1e-6)
    return loss, {
        "loss": float(loss.detach()),
        "policy": float(pg_loss.detach()),
        "value": float(v_loss.detach()),
        "entropy": float(ent.detach()),
        "approx_kl": float(approx_kl),
        "clipfrac": float(clipfrac),
        "kl_init": float(kl_i.detach()),
        "explained_var": float(explained),
        "n": int(n),
    }


def ppo_update(
    model: PEP,
    init_model: PEP | None,
    opt: torch.optim.Optimizer,
    batch: dict[str, torch.Tensor],
    *,
    epochs: int,
    minibatch: int,
    clip: float,
    vf_coef: float,
    ent_coef: float,
    kl_init: float,
    grad_clip: float,
    adv_norm: bool,
    gen: torch.Generator,
) -> dict[str, float]:
    """`epochs` passes over the iteration's battles in minibatches of `minibatch` whole battles."""
    model.train()
    B = batch["mask"].shape[0]
    feats_keys = ("type", "present", "candidate", "cat", "f", "legal")
    init_cache: dict[int, torch.Tensor] = {}
    hist: list[dict[str, float]] = []
    n_updates = 0
    for _ in range(epochs):
        perm = torch.randperm(B, generator=gen).to(batch["mask"].device)
        for s in range(0, B, minibatch):
            idx = perm[s : s + minibatch]
            sub = _index(batch, idx)
            feats = {k: sub[k] for k in feats_keys}
            init_logits = None
            if init_model is not None and kl_init > 0:
                key = tuple(idx.tolist())
                init_logits = init_cache.get(key)
                if init_logits is None:
                    with torch.no_grad():
                        init_logits, _, _ = init_model.forward_seq(feats, sub["event"], None, sub["mask"])
                    init_cache[key] = init_logits
            logits, value, _ = model.forward_seq(feats, sub["event"], None, sub["mask"])
            loss, m = ppo_losses(logits, value, init_logits, sub, clip=clip, vf_coef=vf_coef, ent_coef=ent_coef,
                                 kl_init=kl_init, adv_norm=adv_norm)
            if m["n"] == 0:
                continue
            opt.zero_grad(set_to_none=True)
            loss.backward()
            gn = torch.nn.utils.clip_grad_norm_(model.parameters(), grad_clip)
            opt.step()
            m["grad_norm"] = float(gn)
            hist.append(m)
            n_updates += 1
    model.eval()
    if not hist:
        return {"updates": 0}
    keys = ("loss", "policy", "value", "entropy", "approx_kl", "clipfrac", "kl_init", "explained_var", "grad_norm")
    out = {k: float(np.mean([h[k] for h in hist])) for k in keys}
    out["updates"] = n_updates
    return out


# --------------------------------------------------------------------------- evaluation


def evaluate(model: PEP, parties, battles: int, seed: int, temperature: float = 0.5,
             opponent_kind: str = "greedy", matchups_mode: str = "random") -> dict:
    """Win-rate of the current policy (fp32, CPU) over a fixed matchup sequence via tools.eval_trainer.run."""
    from serve.pep_agent import PEPAgent
    from tools.eval_trainer import run

    cpu = copy.deepcopy(model).to("cpu").eval()
    agent = PEPAgent(model=cpu, temperature=temperature, seed=seed)
    return run({"pep": agent}, parties, battles, seed, opponent_kind, matchups_mode)["pep"]


def parse_feat_cols(spec: str) -> dict[str, list[int]]:
    """"own:38-41,player:34-37" -> {"own": [38,39,40,41], "player": [34,35,36,37]}."""
    out: dict[str, list[int]] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        tok, _, rng_s = part.partition(":")
        cols: list[int] = []
        for r in rng_s.split("+"):
            a, _, b = r.partition("-")
            cols.extend(range(int(a), int(b or a) + 1))
        out.setdefault(tok, []).extend(cols)
    return out


def reset_feature_columns(model: PEP, spec: str) -> None:
    """Zero the input-projection columns of newly populated feature slots so a checkpoint trained
    with those slots always at zero starts unchanged (the token's feature vector is the first FEAT
    columns of each token-type projection)."""
    lin = {"own": model.embed.in_own_mon, "player": model.embed.in_player_mon,
           "own_move": model.embed.in_own_move, "player_move": model.embed.in_player_move,
           "field": model.embed.in_field, "item": model.embed.in_item}
    with torch.no_grad():
        for tok, cols in parse_feat_cols(spec).items():
            for c in cols:
                if not 0 <= c < FEAT:
                    raise ValueError(f"feature column {c} out of range")
                lin[tok].weight[:, c].zero_()


# --------------------------------------------------------------------------- driver


def train(args: argparse.Namespace) -> dict:
    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    gen = torch.Generator().manual_seed(args.seed)
    device = torch.device(args.device or ("cuda" if torch.cuda.is_available() else "cpu"))
    torch.set_num_threads(max(1, args.threads))
    opponents = parse_opponents(args.opponents)
    mix = parse_mix(args.matchups)

    from sim import trainers

    parties = trainers.load().parties
    model, blob = load_checkpoint(args.init_ckpt, map_location="cpu")
    init_steps = int(blob.get("steps", 0))
    if args.reset_feat_cols:
        reset_feature_columns(model, args.reset_feat_cols)
    model = model.to(device).eval()
    init_model = None
    if args.kl_init > 0:
        init_model = copy.deepcopy(model).eval()
        for p in init_model.parameters():
            p.requires_grad_(False)
    print(
        f"[init] {args.init_ckpt} cfg={asdict(model.cfg)} params={model.num_params():,} steps={init_steps} "
        f"device={device} opponents={opponents}",
        file=sys.stderr, flush=True,
    )
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.wd, betas=(0.9, 0.95))

    run_dir = os.path.join(args.ckpt_dir, args.run)
    os.makedirs(run_dir, exist_ok=True)
    ckpt_path = os.path.join(run_dir, "model.pt")
    log = open(os.path.join(run_dir, "log.jsonl"), "a")
    pool = RolloutPool(model.cfg, args.workers, parties, threads=args.worker_threads)
    league_dir = os.path.join(run_dir, "league")
    past: list[str] = []
    if any(o == "past" for o, _ in opponents):
        os.makedirs(league_dir, exist_ok=True)
        past = sorted(os.path.join(league_dir, f) for f in os.listdir(league_dir) if f.endswith(".pt"))
        if not past or args.past_init:
            past.insert(0, os.path.abspath(args.init_ckpt))

    def do_eval(it: int) -> dict:
        t0 = time.time()
        ev = evaluate(model, parties, args.eval_battles, args.eval_seed, temperature=args.eval_temperature,
                      matchups_mode=args.eval_matchups)
        ev = {"iter": it, "win_rate": ev["win_rate"], "wins": ev["wins"], "losses": ev["losses"], "ties": ev["ties"],
              "seconds": time.time() - t0}
        print(
            f"[eval] iter={it} win={ev['win_rate']:.3f} ({ev['wins']}/{ev['losses']}/{ev['ties']} w/l/t) "
            f"battles={args.eval_battles} {ev['seconds']:.1f}s",
            file=sys.stderr, flush=True,
        )
        log.write(json.dumps({"eval": ev}) + "\n"); log.flush()
        return ev

    def save(it: int, ev: dict | None, copy_iter: bool) -> None:
        extra = {"ppo": {"iter": it, "init_ckpt": os.path.abspath(args.init_ckpt), "eval": ev, "updates": total_updates}}
        save_checkpoint(model, ckpt_path, init_steps + total_updates, extra)
        if copy_iter:
            save_checkpoint(model, os.path.join(run_dir, f"iter-{it:05d}.pt"), init_steps + total_updates, extra)

    evals: list[dict] = []
    history: list[dict] = []
    total_updates = 0
    last_eval: dict | None = None
    if args.eval_at_start and args.eval_battles > 0:
        last_eval = do_eval(0); evals.append(last_eval)
    try:
        for it in range(1, args.iters + 1):
            t_it = time.time()
            matchups = sample_matchups(rng, parties, args.battles_per_iter, opponents, mix, past)
            episodes = pool.play(model, matchups, temperature=args.temperature, max_decisions=args.max_decisions,
                                 shaping=args.shaping)
            t_roll = time.time() - t_it
            n_dec = sum(len(e) for e in episodes)
            wins = sum(e.reward > 0 for e in episodes); losses = sum(e.reward < 0 for e in episodes)
            per_opp = {}
            for o in sorted({opponent_kind(e.opponent) for e in episodes}):
                es = [e for e in episodes if opponent_kind(e.opponent) == o]
                per_opp[o] = sum(e.reward > 0 for e in es) / len(es)
            for md in sorted({e.mode for e in episodes}):
                es = [e for e in episodes if e.mode == md]
                per_opp[f"m:{md}"] = sum(e.reward > 0 for e in es) / len(es)
            print(
                f"[rollout] iter={it} battles={len(episodes)} decisions={n_dec} win={wins / max(1, len(episodes)):.3f} "
                f"(w/l/t {wins}/{losses}/{len(episodes) - wins - losses}) "
                + " ".join(f"{o}={v:.3f}" for o, v in per_opp.items())
                + f" {t_roll:.1f}s",
                file=sys.stderr, flush=True,
            )
            t_up = time.time()
            batch = batch_to_device(collate_episodes(episodes, args.gamma, args.gae_lambda), device)
            m = ppo_update(
                model, init_model, opt, batch,
                epochs=args.epochs, minibatch=args.minibatch, clip=args.clip, vf_coef=args.vf_coef,
                ent_coef=args.ent_coef, kl_init=args.kl_init, grad_clip=args.grad_clip, adv_norm=not args.no_adv_norm,
                gen=gen,
            )
            total_updates += m.get("updates", 0)
            t_up = time.time() - t_up
            rec = {
                "iter": it, "battles": len(episodes), "decisions": n_dec,
                "win_rate": wins / max(1, len(episodes)), "per_opponent": per_opp,
                "rollout_s": t_roll, "update_s": t_up, "iter_s": time.time() - t_it, "lr": opt.param_groups[0]["lr"], **m,
            }
            history.append(rec)
            log.write(json.dumps(rec) + "\n"); log.flush()
            if m.get("updates", 0):
                print(
                    f"[update] iter={it} policy={m['policy']:.4f} value={m['value']:.4f} entropy={m['entropy']:.3f} "
                    f"kl={m['approx_kl']:.5f} clipfrac={m['clipfrac']:.3f} kl_init={m['kl_init']:.4f} "
                    f"ev={m['explained_var']:.3f} gn={m['grad_norm']:.3f} updates={m['updates']} "
                    f"{t_up:.1f}s iter={rec['iter_s']:.1f}s",
                    file=sys.stderr, flush=True,
                )
            if past and args.past_every and it % args.past_every == 0:
                path = os.path.join(league_dir, f"iter-{it:05d}.pt")
                save_checkpoint(model, path, init_steps + total_updates, {"ppo": {"iter": it, "league": True}})
                past.append(path)
                if len(past) > args.past_keep:
                    past.pop(1 if args.past_init else 0)
            if args.eval_every and (it % args.eval_every == 0 or it == args.iters):
                if args.eval_battles > 0:
                    last_eval = do_eval(it); evals.append(last_eval)
                save(it, last_eval, copy_iter=True)
            elif args.save_every and it % args.save_every == 0:
                save(it, last_eval, copy_iter=False)
        save(args.iters, last_eval, copy_iter=False)
    finally:
        pool.close()
        log.close()
    print(f"[ckpt] {ckpt_path} (iters={args.iters} updates={total_updates})", file=sys.stderr, flush=True)
    return {"checkpoint": ckpt_path, "history": history, "evals": evals, "model": model, "updates": total_updates}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Recurrent PPO fine-tune of a PEP checkpoint against a player-seat opponent pool.")
    p.add_argument("--init-ckpt", required=True, help="PEP checkpoint to start from (imitation model)")
    p.add_argument("--run", required=True, help="run name -> <ckpt-dir>/<run>/model.pt")
    p.add_argument("--iters", type=int, default=100)
    p.add_argument("--battles-per-iter", type=int, default=256)
    p.add_argument("--opponents", default="greedy,random",
                   help=f"weighted pool from {OPPONENTS}, e.g. self:0.4,past:0.3,greedy:0.3")
    p.add_argument("--matchups", default="random", help="random | balanced | mirror | mix, e.g. mirror:0.5,balanced:0.5")
    p.add_argument("--shaping", type=float, default=0.0, help="potential-based HP-differential shaping coefficient")
    p.add_argument("--past-every", type=int, default=25, help="iterations between frozen league snapshots")
    p.add_argument("--past-keep", type=int, default=8, help="league snapshots kept in the pool")
    p.add_argument("--past-init", action="store_true", help="always keep the init checkpoint in the league pool")
    p.add_argument("--reset-feat-cols", default="", help="zero input columns for new feature slots, e.g. own:38-41,player:34-37")
    p.add_argument("--eval-matchups", default="mirror", help="matchup mode for the periodic eval")
    p.add_argument("--workers", type=int, default=8, help="rollout processes (0 = in-process)")
    p.add_argument("--worker-threads", type=int, default=1, help="torch threads per rollout worker")
    p.add_argument("--threads", type=int, default=4, help="torch CPU threads in the main process")
    p.add_argument("--temperature", type=float, default=1.0, help="rollout sampling temperature")
    p.add_argument("--max-decisions", type=int, default=300)
    p.add_argument("--gamma", type=float, default=1.0)
    p.add_argument("--gae-lambda", type=float, default=0.95)
    p.add_argument("--epochs", type=int, default=3)
    p.add_argument("--minibatch", type=int, default=16, help="battles per minibatch")
    p.add_argument("--clip", type=float, default=0.2)
    p.add_argument("--vf-coef", type=float, default=0.5)
    p.add_argument("--ent-coef", type=float, default=0.005)
    p.add_argument("--kl-init", type=float, default=0.01, help="KL(init || policy) coefficient (0 disables)")
    p.add_argument("--grad-clip", type=float, default=1.0)
    p.add_argument("--no-adv-norm", action="store_true", help="do not standardise advantages per iteration")
    p.add_argument("--lr", type=float, default=1e-5)
    p.add_argument("--wd", type=float, default=0.0)
    p.add_argument("--eval-every", type=int, default=5)
    p.add_argument("--eval-battles", type=int, default=200)
    p.add_argument("--eval-seed", type=int, default=1234)
    p.add_argument("--eval-temperature", type=float, default=0.5)
    p.add_argument("--no-eval-at-start", dest="eval_at_start", action="store_false")
    p.add_argument("--save-every", type=int, default=0, help="extra model.pt saves between evals (0 = off)")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--device", default=None, help="cuda|cpu (default: auto)")
    p.add_argument("--ckpt-dir", default=DEFAULT_CKPT_DIR)
    return p


def main(argv: list[str] | None = None) -> None:
    train(build_parser().parse_args(argv))


if __name__ == "__main__":
    main()
