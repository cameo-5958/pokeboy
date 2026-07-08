# Music pack: pokemon-rb

HeartGold/SoulSilver soundtrack, Kanto area themes (plus the shared
Pokémon Center / Gym / Mart themes and National Park), mapped onto the
Red/Blue/Yellow map ids in `index.html` (`RB_AREAS`).

The mp3s in `hgss/` are copyrighted game audio and are **not committed**
(see `web/.gitignore`) — they live only on this machine. To recreate the
pack, drop the following files into `hgss/`:

| File | HGSS OST track | Plays in |
|---|---|---|
| pallet-town.mp3 | 139. Pallet Town | Pallet Town |
| pewter-city.mp3 | 134. Pewter City | Viridian, Pewter, Saffron |
| cerulean-city.mp3 | 123. Cerulean City | Cerulean, Fuchsia |
| lavender-town.mp3 | 119. Lavender Town | Lavender, Pokémon Tower |
| vermilion-city.mp3 | 117. Vermilion City | Vermilion |
| celadon-city.mp3 | 128. Celadon City | Celadon |
| cinnabar-island.mp3 | 144. Cinnabar Island | Cinnabar, Routes 19-21, Mansion |
| pokemon-league.mp3 | 151. The Pokémon League | Indigo Plateau + lobby |
| route-1.mp3 | 138. Route 1 | Routes 1-2 |
| route-3.mp3 | 135. Route 3 | Routes 3-10, 16-18, 22 |
| route-11.mp3 | 130. Route 11 | Routes 11-15 |
| route-24.mp3 | 124. Route 24 | Routes 24-25 |
| route-26.mp3 | 115. Route 26 | Route 23 (league approach) |
| viridian-forest.mp3 | 132. Viridian Forest | Viridian Forest |
| mt-moon.mp3 | 137. Mt. Moon | Mt. Moon |
| rock-tunnel.mp3 | 120. Rock Tunnel | Rock Tunnel, Diglett's Cave, Seafoam, Cerulean Cave |
| victory-road.mp3 | 150. Victory Road | Victory Road |
| national-park.mp3 | 67. National Park | Safari Zone (gen 1's closest thing to a park) |
| ss-anne.mp3 | 116. S.S. Aqua | S.S. Anne |
| pokemon-center.mp3 | 15. Pokémon Center | all Pokémon Centers |
| pokemon-gym.mp3 | 42. Pokémon Gym | all Gyms |
| poke-mart.mp3 | 25. Poké Mart | all Marts |

Unmapped maps (houses, gates, labs, Silph Co...) keep the previous track
playing rather than switching.

The Pixabay lofi covers (pallet/town/route/cave/building.mp3, Pixabay
Content License, artists feora, vgm yume, lucas cooper) are kept in this
directory as an alternate pack, currently unused by the resolver.
