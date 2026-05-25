# Multi-gateway deployment

Hermes supports multiple gateway processes running concurrently — one per profile
(default, writer, admin, coder, researcher). Each gateway opens its own connection
to platform APIs and delivers messages for its profile's subscribers.

## Single-dispatcher posture

Only one gateway owns the kanban dispatcher. The owning gateway has
`kanban.dispatch_in_gateway: true` (the default). All other gateways set
`kanban.dispatch_in_gateway: false`.

**Why this matters:** gateways with `dispatch_in_gateway: true` open per-board
SQLite connections for the notifier watcher. Multiple gateways doing this concurrently
amplifies WAL reader counts on the `-shm` shared-memory page table. Under memory
pressure, this increases the risk of `-shm` state inconsistency.

## Configuration

On the dispatch-owning gateway (typically the `default` profile), no change needed —
`dispatch_in_gateway` defaults to `true`.

On every other profile gateway, add to `~/.hermes/config.yaml`:

```yaml
kanban:
  dispatch_in_gateway: false
```

Or set the env var: `HERMES_KANBAN_DISPATCH_IN_GATEWAY=false`

## What each gateway does

| Gateway role | dispatch_in_gateway | Opens per-board DBs? | Runs dispatcher? |
|---|---|---|---|
| default (dispatch owner) | true (default) | yes | yes |
| writer, admin, coder, etc. | false | no | no |

Non-dispatch gateways still deliver messages for their own platform adapters
(Telegram, Discord, etc.) — they just don't poll kanban boards.

## ASCII diagram

```
  [default gateway]          [writer gateway]       [coder gateway]
  dispatch_in_gateway=true   d_i_g=false            d_i_g=false
         |                        |                       |
         v                        v                       v
  kanban_db.connect()        (skips notifier)       (skips notifier)
  per-board polling          platform adapters      platform adapters
  dispatcher tick
```

Only the dispatch-owning gateway holds open file descriptors on `kanban.db` files.
