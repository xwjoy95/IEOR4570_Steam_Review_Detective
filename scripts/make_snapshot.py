"""Save every data source for the demo games to data/snapshots/<appid>/, so the demo and the README's
sample questions still work when Steam rate-limits us or an unofficial endpoint breaks.

Owner: C.  Usage: uv run scripts/make_snapshot.py 1086940 [appid ...]
"""

import sys


def main(appids: list[int]) -> None:
    # TODO(C): for each appid, call the same fetchers the tools use (reviews histogram + sampled reviews,
    # news, appdetails, SteamSpy, SteamCharts) and tools.http.save_snapshot(appid, name, data),
    # recording "as_of" (today's date) in every file.
    raise NotImplementedError


if __name__ == "__main__":
    main([int(a) for a in sys.argv[1:]])
