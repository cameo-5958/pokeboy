"""Built-in RBY OU sample teams for CLI/bench use (standard Smogon sets)."""

from __future__ import annotations

import random

from sim.pack import PokemonSpec

STANDARD_SETS = [
    PokemonSpec("Tauros", ["Body Slam", "Hyper Beam", "Blizzard", "Earthquake"]),
    PokemonSpec("Snorlax", ["Body Slam", "Reflect", "Rest", "Ice Beam"]),
    PokemonSpec("Chansey", ["Ice Beam", "Thunderbolt", "Soft-Boiled", "Thunder Wave"]),
    PokemonSpec("Alakazam", ["Psychic", "Seismic Toss", "Recover", "Thunder Wave"]),
    PokemonSpec("Starmie", ["Surf", "Blizzard", "Thunder Wave", "Recover"]),
    PokemonSpec("Exeggutor", ["Sleep Powder", "Psychic", "Explosion", "Stun Spore"]),
    PokemonSpec("Rhydon", ["Earthquake", "Rock Slide", "Body Slam", "Substitute"]),
    PokemonSpec("Zapdos", ["Thunderbolt", "Drill Peck", "Thunder Wave", "Agility"]),
    PokemonSpec("Jynx", ["Lovely Kiss", "Blizzard", "Psychic", "Rest"]),
    PokemonSpec("Gengar", ["Hypnosis", "Night Shade", "Explosion", "Thunderbolt"]),
]


def sample_team(rng: random.Random, size: int = 6) -> list[PokemonSpec]:
    return rng.sample(STANDARD_SETS, size)
