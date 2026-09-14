#!/usr/bin/env bash
# Kill Showdown evaluation clients (PEP acceptor/baseline runs, Metamon challengers, watchers).
# Run as a script, never inline: pgrep -f would otherwise match the caller's own command line.
PAT="tools.showdown_eval|serve/metamon_eval|metamon_h2h.sh|showdown_watch.sh|metamon.rl"
for p in $(pgrep -f "$PAT"); do [ "$p" != "$$" ] && kill -9 "$p" 2>/dev/null; done
sleep 1; echo "remaining: $(pgrep -fa "$PAT" | grep -vc kill_showdown)"
