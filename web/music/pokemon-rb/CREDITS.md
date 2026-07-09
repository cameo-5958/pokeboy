# Music pack: pokemon-rb

HeartGold/SoulSilver soundtrack mapped onto Pokémon Blue's *songs* (not
maps): the resolver in `index.html` follows the gen 1 sound engine's
current music id (`wChannelSoundIDs`/`wAudioROMBank`), so the title
screen, battles, surfing, the bicycle etc. each get their own track, and
nothing plays before the game itself starts music. A few songs the game
reuses across places (dungeons, marts, the Elite Four rooms) are refined
by `wCurMap`. Songs with no entry (e.g. the GB Printer jingle) fall back
to the game's own audio.

The mp3s in `hgss/` are copyrighted game audio and are **not committed**
(see `web/.gitignore`) — they live only on this machine. To recreate the
pack, extract the following tracks from the HGSS gamerip into `hgss/`:

| File | HGSS OST track | Plays for |
|---|---|---|
| opening-movie.mp3 | 01. Opening Movie | intro fight (no loop) |
| title-screen.mp3 | 02. Title Screen | title screen |
| pallet-town.mp3 | 139. Pallet Town | Pallet Town |
| pewter-city.mp3 | 134. Pewter City | Viridian/Pewter/Saffron song |
| cerulean-city.mp3 | 123. Cerulean City | Cerulean/Fuchsia song |
| lavender-town.mp3 | 119. Lavender Town | Lavender Town |
| vermilion-city.mp3 | 117. Vermilion City | Vermilion |
| celadon-city.mp3 | 128. Celadon City | Celadon |
| cinnabar-island.mp3 | 144. Cinnabar Island | Cinnabar (song also covers Routes 19-21) |
| pokemon-league.mp3 | 151. The Pokémon League | Indigo Plateau, Route 23, E4 rooms |
| route-1.mp3 | 138. Route 1 | Routes 1-2 song |
| route-3.mp3 | 135. Route 3 | Routes 3-10/16-18/22 song |
| route-11.mp3 | 130. Route 11 | Routes 11-15 song |
| route-24.mp3 | 124. Route 24 | Routes 24-25 song |
| viridian-forest.mp3 | 132. Viridian Forest | Dungeon2 song (default) |
| ice-path.mp3 | 103. Ice Path | Seafoam Islands |
| union-cave.mp3 | 28. Union Cave | Diglett's Cave, Power Plant, Cerulean Cave |
| mt-moon.mp3 | 137. Mt. Moon | Dungeon3 song (default) |
| rock-tunnel.mp3 | 120. Rock Tunnel | Rock Tunnel |
| victory-road.mp3 | 150. Victory Road | Victory Road |
| team-rocket-hq.mp3 | 99. Team Rocket HQ | Rocket Hideout (Dungeon1 default) |
| burned-tower.mp3 | 85. Burned Tower | Pokémon Mansion |
| sprout-tower.mp3 | 23. Sprout Tower | Pokémon Tower |
| radio-tower-occupied.mp3 | 102. Radio Tower Occupied! | Silph Co. |
| safari-zone.mp3 | 147. Safari Zone | Safari Zone |
| game-corner.mp3 | 49. Goldenrod Game Corner | Game Corner |
| ss-anne.mp3 | 116. S.S. Aqua | S.S. Anne |
| pokemon-center.mp3 | 15. Pokémon Center | Pokémon Centers |
| poke-mart.mp3 | 25. Poké Mart | Marts (same song as Centers, split by map) |
| pokemon-gym.mp3 | 42. Pokémon Gym | Gyms |
| elm-lab.mp3 | 07. Elm Pokémon Lab | Oak's Lab |
| professor-oak.mp3 | 140. Professor Oak | meet Prof. Oak |
| rival-appears.mp3 | 37. A Rival Appears! | meet rival |
| hurry-along.mp3 | 05. Hurry Along | "follow me" guide / museum guy |
| eyes-meet-boy.mp3 | 17. Trainers' Eyes Meet (Boy 1) | male trainer encounter |
| eyes-meet-girl.mp3 | 65. Trainers' Eyes Meet (Girl 1) | female trainer encounter |
| eyes-meet-rocket.mp3 | 34. Trainers' Eyes Meet (Team Rocket) | evil trainer encounter |
| battle-wild.mp3 | 121. Battle! (Wild Pokémon—Kanto) | wild battle |
| battle-trainer.mp3 | 143. Battle! (Trainer Battle—Kanto) | trainer battle |
| battle-gym-leader.mp3 | 118. Battle! (Gym Leader—Kanto) | gym leader / E4 battle |
| battle-champion.mp3 | 152. Battle! (Champion) | champion battle |
| victory-wild.mp3 | 11. Victory! (Wild Pokémon) | won wild battle |
| victory-trainer.mp3 | 19. Victory! (Trainer Battle) | won trainer battle |
| victory-gym-leader.mp3 | 44. Victory! (Gym Leader) | won gym leader battle |
| pokemon-healed.mp3 | 16. Pokémon Healed | heal jingle (no loop) |
| pokemon-lullaby.mp3 | 126. Pokégear Radio - Pokémon Lullaby | Jigglypuff's song (no loop) |
| bicycle.mp3 | 64. Bicycle | riding the bike |
| surf.mp3 | 94. Surf | surfing |
| hall-of-fame.mp3 | 153. The Hall of Fame | hall of fame |
| ending-theme.mp3 | 154. Ending Theme | credits |

While a custom track plays, the core's APU channel mask keeps only the
channels the game's sound engine currently assigns to sound effects, so
SFX, cries and jingles stay audible over the replacement music.

Leftovers on disk not referenced by the resolver: national-park.mp3 and
route-26.mp3 (from the older map-keyed pack), and the Pixabay lofi covers
(pallet/town/route/cave/building.mp3, Pixabay Content License, artists
feora, vgm yume, lucas cooper), kept as an alternate pack.
