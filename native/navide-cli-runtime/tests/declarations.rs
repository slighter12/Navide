//! Every declaration is consistent with the core that runs it: the
//! combinations the core treats as unreachable cannot be declared, so a new
//! CLI that compiles and passes this test runs on the shared engine as is.
use navide_cli_runtime::decl::*;
use navide_cli_runtime::vendors::ALL;

fn activity_rows(format: &Format) -> Option<&'static Rows> {
    match format {
        Format::Sqlite(db) => db.activity.as_ref(),
        _ => None,
    }
}

fn usage_rows(format: &Format) -> Option<&'static Rows> {
    match format {
        Format::Sqlite(db) => db.usage.as_ref(),
        _ => None,
    }
}

#[test]
fn keys_are_unique() {
    let mut keys: Vec<&str> = ALL.iter().map(|c| c.key).collect();
    keys.sort_unstable();
    let before = keys.len();
    keys.dedup();
    assert_eq!(keys.len(), before);
}

#[test]
fn each_cursor_has_its_backend_and_its_bag_key() {
    for cli in ALL {
        let Some(observe) = &cli.observe else { continue };
        let activity = &observe.activity;
        let key = cli.key;
        match activity.cursor {
            Cursor::Lines => assert!(matches!(observe.format, Format::Jsonl), "{key}: a line cursor walks JSONL"),
            Cursor::Replay => assert!(matches!(observe.format, Format::Text { .. }), "{key}: a replay walks text"),
            Cursor::Bytes => {
                assert!(matches!(observe.format, Format::Jsonl), "{key}: a byte cursor tails JSONL");
                assert!(activity.bag.cursor.is_some(), "{key}: a byte cursor is kept in the bag");
            }
            Cursor::Rows => {
                let rows = activity_rows(&observe.format).unwrap_or_else(|| panic!("{key}: a row cursor needs an activity query"));
                assert!(activity.bag.cursor.is_some(), "{key}: a row cursor is kept in the bag");
                assert_eq!(rows.partition.is_some(), activity.bag.anchor.is_some(), "{key}: partitions and the anchor key go together");
                if rows.revisit.is_some() {
                    assert!(rows.sql.contains("?3"), "{key}: settling rows are bound at ?3");
                }
            }
        }
        if activity_rows(&observe.format).is_some() {
            assert_eq!(activity.cursor, Cursor::Rows, "{key}: an activity query is walked by rows");
        }
    }
}

#[test]
fn kept_slots_are_stored_somewhere() {
    for cli in ALL {
        let Some(observe) = &cli.observe else { continue };
        let activity = &observe.activity;
        for name in activity.persist {
            let in_bag = activity.bag.slots.iter().any(|slot| match slot {
                BagSlot::Text { slot, .. } | BagSlot::TextOnChange { slot, .. } | BagSlot::JsonText { slot, .. } => slot == name,
            });
            assert!(in_bag, "{}: kept slot {name} has no bag key", cli.key);
        }
        if let Some(usage) = &observe.usage {
            for flag in usage.flags {
                assert!(usage.persist.contains(flag), "{}: flag {flag} is not persisted", cli.key);
            }
        }
    }
}

#[test]
fn usage_modes_match_their_backend() {
    for cli in ALL {
        let Some(observe) = &cli.observe else { continue };
        let Some(usage) = &observe.usage else { continue };
        let key = cli.key;
        let sqlite = matches!(observe.format, Format::Sqlite(_));
        match usage.mode {
            UsageMode::Counters { .. } => assert!(sqlite && usage_rows(&observe.format).is_some(), "{key}: counters are SQLite rows"),
            UsageMode::Watermarks { .. } => {
                let rows = usage_rows(&observe.format).unwrap_or_else(|| panic!("{key}: watermarks need a usage query"));
                assert!(rows.partition.is_some(), "{key}: watermarks are kept per partition");
            }
            UsageMode::Cumulative | UsageMode::Snapshot { .. } => assert!(!sqlite, "{key}: a file mode"),
            UsageMode::PerRecord { .. } => assert!(!sqlite || usage_rows(&observe.format).is_some(), "{key}: SQLite records need a query"),
            UsageMode::Legacy => {}
        }
    }
}

#[test]
fn exact_turns_name_their_rules() {
    for cli in ALL {
        let Some(observe) = &cli.observe else { continue };
        if observe.turns.method == TurnMethod::Exact {
            assert!(observe.turns.call.is_some(), "{}: exact turns need a call rule", cli.key);
        }
        if observe.turns.method == TurnMethod::Inferred {
            assert!(observe.usage.is_some(), "{}: inferred turns cut usage", cli.key);
        }
    }
}
