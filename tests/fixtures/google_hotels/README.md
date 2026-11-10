# Recorded Google Hotels answers

Real answers from Google Travel's data endpoints, recorded on 2026-11-10 from a cloud session with the honest
`hotelrate-mcp` user agent (`hl=en`, `gl=gb`, GBP, 2 adults). Each file is gzipped JSON with the call's `rpc_id`,
the `request` sent, the HTTP `status`, the `url` and the raw `body`.

| File | Call | What it holds |
|---|---|---|
| `search_the_savoy_london.json.gz` | `AtySUc` search | Search for "The Savoy London", check-in 2026-12-10 |
| `calendar_the_savoy_365d.json.gz` | `yY52ce` calendar | The Savoy, 365 check-in days from 2026-12-10, 1 night: 300 priced, horizon 2027-10-14 |
| `prices_strand_palace.json.gz` | `lvkpBc` prices | Strand Palace, 2026-12-10 for 1 night: 21 channel offers, no room-level rates |

To record new ones, run `hotelrate-mcp shop <hotel> --raw answers.jsonl` and copy the lines you need; each line is
one call in this format.
