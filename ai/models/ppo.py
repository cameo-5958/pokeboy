"""Phase 3 - PPO league self-play (SPECS §5.3).

Fine-tunes a distilled checkpoint (policy + two-hot value head) by playing
league battles on the in-process sim and applying legality-masked PPO.

Design (v1, single GPU):
- Episodic, terminal-only reward on the win-prob scale: win 1.0 / draw or
  turn-cap 0.5 / loss 0.0 (matches the value head's [0,1] bins).
- Mirror games (both seats = current policy) contribute BOTH seats'
  transitions; league games (frozen ckpts / SearchTeacher / scripted bots)
  contribute the learner seat only.
- GAE(gamma=1, lam) over per-decision steps; returns = advantages + values;
  value loss = CE to two-hot(returns); policy loss = clipped surrogate;
  entropy bonus over the *legal* action distribution only.
- Teams: mixed sampler (the benchmark distribution) when available.

  uv run python -m models.ppo --ckpt checkpoints/banquet/latest/model.pt \
      --iters 200 --battles-per-iter 128 --eval-every 10
"""

from __future__ import annotations

import argparse
import json
import os
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from models.encoder import FieldValueEncoder
from models.tiers import TIERS
from models.tokenizer import Tokenizer
from models.train_imitation import two_hot, value_estimate

ROOT = Path(__file__).resolve().parents[1]

WIN, DRAW, LOSS = 1.0, 0.5, 0.0
LEAGUE_SCRIPTED = ("maxdamage", "umaxdamage", "lemaxdamage", "random")


# -- math helpers --------------------------------------------------------


def gae(values: list[float], final_reward: float, gamma: float = 1.0, lam: float = 0.95):
    """Advantages/returns for one episode with a single terminal reward."""
    n = len(values)
    adv = [0.0] * n
    last = 0.0
    for t in reversed(range(n)):
        next_v = final_reward if t == n - 1 else values[t + 1]
        delta = (final_reward if t == n - 1 else 0.0) + (gamma * next_v if t < n - 1 else 0.0) - values[t]
        # terminal step bootstraps the reward itself; inner steps bootstrap V
        last = delta + gamma * lam * last
        adv[t] = last
    returns = [a + v for a, v in zip(adv, values)]
    return adv, returns


def masked_dist(logits: torch.Tensor, legal: torch.Tensor) -> torch.distributions.Categorical:
    # -1e9 rather than -inf: Categorical.entropy() yields NaN from -inf * 0
    masked = logits.masked_fill(~legal, -1e9)
    return torch.distributions.Categorical(logits=masked)


# -- league ---------------------------------------------------------------


class Seat:
    """One league opponent: kind + a per-battle action function."""

    def __init__(self, kind: str, choose=None, full_info: bool = False):
        self.kind = kind
        self.choose = choose
        self.full_info = full_info


class League:
    """Opponent pool: mirror (current weights), frozen ckpts, teacher, bots."""

    def __init__(self, model, tok, frozen_agents, teacher_cfg, mix):
        self.model, self.tok = model, tok
        self.frozen = frozen_agents
        self.teacher_cfg = teacher_cfg
        self.mix = mix  # (mirror, frozen, teacher, scripted) cumulative weights

    def pick(self, rng: random.Random) -> Seat:
        from sim.agents import (
            LessEffectiveMaxDamageBot,
            MaxDamageBot,
            RandomBot,
            UniversalMaxDamageBot,
        )

        r = rng.random()
        if r < self.mix[0] or (not self.frozen and r < self.mix[1]):
            return Seat("mirror")
        if r < self.mix[1]:
            return Seat("frozen", rng.choice(self.frozen).choose)
        if r < self.mix[2]:
            from sim.search import SearchTeacher

            teacher = SearchTeacher(seed=rng.randrange(2**31), **self.teacher_cfg)
            return Seat("teacher", teacher.choose_full, full_info=True)
        name = rng.choice(LEAGUE_SCRIPTED)
        bot = {
            "maxdamage": MaxDamageBot,
            "umaxdamage": UniversalMaxDamageBot,
            "lemaxdamage": LessEffectiveMaxDamageBot,
            "random": lambda: RandomBot(rng.randrange(2**31)),
        }[name]()
        return Seat(name, bot.choose)


def make_league(model, tok, frozen, rng, teacher_cfg=None,
                mix=(0.5, 0.7, 0.8, 1.0)) -> League:
    del rng  # sampling rng is passed per pick(); kept in signature for clarity
    return League(model, tok, frozen, teacher_cfg or {"depth": 2, "rolls": 2, "alpha": 0.3}, mix)


# -- rollout collection ---------------------------------------------------


def _team_picker():
    try:
        from sim.teamsets import mixed_sample_team as pick

        pick(random.Random(0))
        return pick
    except (FileNotFoundError, ValueError):
        from sim.teams import sample_team

        return sample_team


def _encode_batch(tok, states, device):
    encs = [tok.encode(s.to_json()) for s in states]
    return dict(
        field_ids=torch.tensor(np.stack([e["field_ids"] for e in encs]), dtype=torch.long, device=device),
        value_ids=torch.tensor(np.stack([e["value_ids"] for e in encs]), dtype=torch.long, device=device),
        slot_ids=torch.tensor(np.stack([e["slot_ids"] for e in encs]), dtype=torch.long, device=device),
        cont=torch.tensor(np.stack([e["cont"] for e in encs]), dtype=torch.float32, device=device),
        lengths=torch.tensor([e["length"] for e in encs], dtype=torch.long, device=device),
    )


@torch.no_grad()
def collect_rollouts(model, tok, league: League, n_battles: int, device: str,
                     rng: random.Random, max_turns: int = 300,
                     concurrent: int = 16) -> dict:
    """Play n_battles league games; return flat tensors of learner transitions.

    Battles run in a refilling pool of `concurrent` games so every learner
    seat across the pool shares one batched forward per tick - the GPU call,
    not the engine, is the rollout bottleneck.
    """
    from sim.battle import Battle

    was_training = model.training
    model.eval()
    pick_team = _team_picker()

    steps: list[dict] = []  # per-transition storage (cpu tensors)
    episodes: list[tuple[list[int], float]] = []  # (step indexes, final reward)
    remaining = n_battles

    def spawn() -> dict | None:
        nonlocal remaining
        if remaining <= 0:
            return None
        remaining -= 1
        opp = league.pick(rng)
        return {
            "b": Battle(pick_team(rng), pick_team(rng), seed=rng.randrange(2**63)),
            "opp": opp,
            "learners": (1, 2) if opp.kind == "mirror" else (1,),
            "my_steps": {1: [], 2: []},
            "turns": 0,
        }

    live: list[dict] = []
    while len(live) < max(1, concurrent):
        lv = spawn()
        if lv is None:
            break
        live.append(lv)

    while live:
        # retire finished battles, refill the pool
        for lv in live[:]:
            if lv["b"].winner or lv["turns"] >= max_turns:
                outcome = lv["b"].winner
                for p in lv["learners"]:
                    if not lv["my_steps"][p]:
                        continue
                    r = (DRAW if outcome in (None, "tie")
                         else (WIN if outcome == f"p{p}" else LOSS))
                    episodes.append((lv["my_steps"][p], r))
                live.remove(lv)
                nxt = spawn()
                if nxt is not None:
                    live.append(nxt)
        if not live:
            break

        # one batched forward for every learner seat in the pool;
        # seat 2's state only exists if something will read it
        pend: list[tuple[dict, int]] = []
        for lv in live:
            need = set(lv["learners"])
            if 2 not in need and not lv["opp"].full_info:
                need.add(2)
            lv["states"] = {p: lv["b"].state(p) for p in need}
            for p in lv["learners"]:
                pend.append((lv, p))
        batch = _encode_batch(tok, [lv["states"][p] for lv, p in pend], device)
        with torch.inference_mode():
            logits, vlogits = model(**batch, return_value=True)
        logits, vlogits = logits.clone(), vlogits.clone()
        legal = torch.zeros(len(pend), 10, dtype=torch.bool)
        for i, (lv, p) in enumerate(pend):
            legal[i, lv["states"][p].legal_actions] = True
        dist = masked_dist(logits.float().cpu(), legal)
        acts = torch.multinomial(dist.probs, 1).squeeze(1)
        logps = dist.log_prob(acts)
        vals = value_estimate(vlogits.float().cpu())

        actions: dict[int, dict[int, int]] = {id(lv): {} for lv in live}
        for i, (lv, p) in enumerate(pend):
            actions[id(lv)][p] = int(acts[i])
            lv["my_steps"][p].append(len(steps))
            steps.append(
                dict(
                    field_ids=batch["field_ids"][i].cpu(),
                    value_ids=batch["value_ids"][i].cpu(),
                    slot_ids=batch["slot_ids"][i].cpu(),
                    cont=batch["cont"][i].cpu(),
                    length=int(batch["lengths"][i]),
                    action=int(acts[i]),
                    old_logp=float(logps[i]),
                    value=float(vals[i]),
                    legal=legal[i],
                )
            )

        # league seats + engine steps
        for lv in live:
            acts_lv = actions[id(lv)]
            if 2 not in acts_lv:
                if lv["opp"].full_info:
                    acts_lv[2] = lv["opp"].choose(lv["b"], 2)
                else:
                    acts_lv[2] = lv["opp"].choose(lv["states"][2])
            lv["b"].step(acts_lv[1], acts_lv[2])
            lv["turns"] += 1

    if was_training:
        model.train()

    adv = torch.zeros(len(steps))
    ret = torch.zeros(len(steps))
    for ixs, r in episodes:
        a, _ = gae([steps[i]["value"] for i in ixs], final_reward=r)
        for j, i in enumerate(ixs):
            # value targets are Monte-Carlo outcomes: with terminal-only
            # reward and gamma=1 the true return from every state IS r
            adv[i], ret[i] = a[j], r
    return dict(
        field_ids=torch.stack([s["field_ids"] for s in steps]),
        value_ids=torch.stack([s["value_ids"] for s in steps]),
        slot_ids=torch.stack([s["slot_ids"] for s in steps]),
        cont=torch.stack([s["cont"] for s in steps]),
        lengths=torch.tensor([s["length"] for s in steps], dtype=torch.long),
        action=torch.tensor([s["action"] for s in steps], dtype=torch.long),
        old_logp=torch.tensor([s["old_logp"] for s in steps]),
        legal=torch.stack([s["legal"] for s in steps]),
        advantage=adv,
        reward_return=ret,
    )


# -- PPO update ------------------------------------------------------------


def ppo_update(model, opt, buf, device, clip=0.2, vf_coef=0.5, ent_coef=0.005,
               epochs=2, minibatch=128, max_kl=0.02, bf16=False) -> dict:
    n = len(buf["action"])
    adv = buf["advantage"]
    adv = (adv - adv.mean()) / adv.std().clamp_min(1e-6)
    value_bins = model.value_bins
    stats = {"policy_loss": 0.0, "value_loss": 0.0, "entropy": 0.0, "approx_kl": 0.0}
    batches = 0
    autocast = torch.autocast(device_type="cuda", dtype=torch.bfloat16,
                              enabled=bf16 and device == "cuda")
    for _ in range(epochs):
        perm = torch.randperm(n)
        for i in range(0, n - minibatch + 1, minibatch):
            ix = perm[i : i + minibatch]
            batch = dict(
                field_ids=buf["field_ids"][ix].to(device),
                value_ids=buf["value_ids"][ix].to(device),
                slot_ids=buf["slot_ids"][ix].to(device),
                cont=buf["cont"][ix].to(device),
                lengths=buf["lengths"][ix].to(device),
            )
            opt.zero_grad()
            with autocast:
                logits, vlogits = model(**batch, return_value=True)
            dist = masked_dist(logits.float(), buf["legal"][ix].to(device))
            logp = dist.log_prob(buf["action"][ix].to(device))
            ratio = torch.exp(logp - buf["old_logp"][ix].to(device))
            a = adv[ix].to(device)
            pl = -torch.min(ratio * a, ratio.clamp(1 - clip, 1 + clip) * a).mean()
            targets = two_hot(buf["reward_return"][ix].to(device).clamp(0, 1), value_bins)
            vl = F.cross_entropy(vlogits.float(), targets)
            ent = dist.entropy().mean()
            loss = pl + vf_coef * vl - ent_coef * ent
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            with torch.no_grad():
                kl = (buf["old_logp"][ix].to(device) - logp).mean().item()
            stats["policy_loss"] += pl.item()
            stats["value_loss"] += vl.item()
            stats["entropy"] += ent.item()
            stats["approx_kl"] += kl
            batches += 1
            if abs(kl) > max_kl:
                break  # trust-region guard: stop this epoch early
    for k in stats:
        stats[k] /= max(1, batches)
    return stats


def update_snapshot(frozen: list, model, tok, tier: str, value_bins: int,
                    device: str, seed: int) -> None:
    """Maintain ONE reusable frozen league snapshot. The snapshot model is
    allocated once and refreshed in place - appending a new GPU-resident
    copy per snapshot OOMed the first real run at the third copy."""
    from models.agent import ModelAgent

    snap_agent = next(
        (a for a in frozen if getattr(a, "_league_snapshot", False)), None)
    if snap_agent is None:
        snap = FieldValueEncoder(TIERS[tier], tok, value_bins=value_bins).to(device).eval()
        snap_agent = ModelAgent.from_model(snap, tok, seed=seed, temperature=0.25)
        snap_agent._league_snapshot = True  # preloaded seats must not be clobbered
        frozen.append(snap_agent)
    snap_agent.model.load_state_dict(model.state_dict())
    snap_agent.reseed(seed)


# -- CLI -------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--ckpt", required=True, help="distilled seed checkpoint (needs value head)")
    p.add_argument("--iters", type=int, default=100)
    p.add_argument("--battles-per-iter", type=int, default=128)
    p.add_argument("--lr", type=float, default=1e-5)
    p.add_argument("--minibatch", type=int, default=128)
    p.add_argument("--epochs", type=int, default=2)
    p.add_argument("--ent-coef", type=float, default=0.005)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--max-turns", type=int, default=300)
    p.add_argument("--eval-every", type=int, default=10, help="iterations between benchmarks")
    p.add_argument("--eval-battles", type=int, default=50)
    p.add_argument("--snapshot-every", type=int, default=25, help="iterations between league snapshots")
    p.add_argument("--concurrent", type=int, default=32,
                   help="battles rolled out in one shared-forward pool")
    p.add_argument("--frozen-ckpt", action="append", default=[],
                   help="preload a checkpoint as a permanent frozen league seat "
                        "(repeatable; e.g. a TaurosV0 mimic)")
    p.add_argument("--league-mix", default="0.5,0.7,0.8,1.0",
                   help="cumulative mirror,frozen,teacher,scripted weights")
    p.add_argument("--bf16", action="store_true")
    p.add_argument("--ckpt-root", default=str(ROOT / "checkpoints"))
    args = p.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    ckpt = torch.load(args.ckpt, map_location=device, weights_only=True)
    if not ckpt.get("value_bins"):
        raise SystemExit("PPO needs a value head; retrain the seed with --value-bins 32")
    tok = Tokenizer(seq_len=ckpt.get("seq_len"), hist_k=ckpt.get("hist_k", 0),
                    dmg_feats=ckpt.get("dmg_feats", False))
    model = FieldValueEncoder(TIERS[ckpt["tier"]], tok, value_bins=ckpt["value_bins"]).to(device)
    model.load_state_dict(ckpt["model"])
    print(json.dumps({"tier": ckpt["tier"], "params": model.num_params(), "device": device}))

    from models.agent import ModelAgent
    from models.train_imitation import periodic_eval

    rng = random.Random(args.seed)
    torch.manual_seed(args.seed)
    from models.agent import ModelAgent

    frozen: list = [ModelAgent(f, seed=args.seed, device=device, temperature=0.25)
                    for f in args.frozen_ckpt]
    mix = tuple(float(x) for x in args.league_mix.split(","))
    league = make_league(model, tok, frozen, rng, mix=mix)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, betas=(0.9, 0.95), weight_decay=0.0)

    run_id = f"ppo-{ckpt['tier']}-b{args.battles_per_iter}-lr{args.lr:g}-seed{args.seed}"
    tier_dir = Path(args.ckpt_root) / ckpt["tier"]
    run_dir = tier_dir / f"{run_id}-{time.strftime('%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    published = False

    def save(step: int) -> None:
        nonlocal published
        tmp = run_dir / ".model.pt.tmp"
        torch.save(
            {"model": model.state_dict(), "tier": ckpt["tier"], "steps": step,
             "hist_k": ckpt.get("hist_k", 0), "seq_len": tok.seq_len,
             "value_bins": ckpt["value_bins"], "dmg_feats": ckpt.get("dmg_feats", False)},
            tmp,
        )
        # keep every eval-time checkpoint: self-play can drift, and the best
        # policy of the run is routinely NOT the last one (run 2 lost its
        # iter-100 peak to this exact overwrite)
        import shutil

        shutil.copy2(tmp, run_dir / f"model-{step:04d}.pt")
        os.replace(tmp, run_dir / "model.pt")
        if not published:
            link = tier_dir / f".latest-{os.getpid()}"
            os.symlink(run_dir.name, link)
            os.replace(link, tier_dir / "latest")
            published = True

    t0 = time.monotonic()
    wins = {"n": 0, "w": 0.0}
    for it in range(1, args.iters + 1):
        buf = collect_rollouts(model, tok, league, args.battles_per_iter, device, rng,
                               max_turns=args.max_turns, concurrent=args.concurrent)
        wins["n"] += 1
        wins["w"] = 0.9 * wins["w"] + 0.1 * buf["reward_return"].mean().item()
        stats = ppo_update(model, opt, buf, device, epochs=args.epochs,
                           minibatch=args.minibatch, ent_coef=args.ent_coef, bf16=args.bf16)
        line = {"iter": it, "decisions": len(buf["action"]),
                "roll_return_ema": round(wins["w"], 4),
                **{k: round(v, 5) for k, v in stats.items()},
                "s": round(time.monotonic() - t0, 1)}
        print(json.dumps(line), flush=True)
        if args.snapshot_every and it % args.snapshot_every == 0:
            update_snapshot(frozen, model, tok, ckpt["tier"], ckpt["value_bins"],
                            device, rng.randrange(2**31))
        if args.eval_every and it % args.eval_every == 0:
            m = periodic_eval(model, tok, [], device, battles=args.eval_battles, seed=args.seed)
            m.pop("holdout_top1", None)
            eline = {"run": run_id, "iter": it, **m, "s": round(time.monotonic() - t0, 1)}
            print(json.dumps(eline), flush=True)
            with open(tier_dir / "evals.jsonl", "a") as f:
                f.write(json.dumps(eline) + "\n")
            save(it)
    save(args.iters)
    print(f"saved {run_dir / 'model.pt'}")


if __name__ == "__main__":
    main()
