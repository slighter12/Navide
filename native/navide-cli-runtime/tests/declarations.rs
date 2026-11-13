//! What the compiler cannot check about a declaration. The combinations the
//! core cannot run (a cursor without its backend, a SQLite usage mode on a
//! file, a slot that does not exist) do not compile; what is left are a few
//! cross-references inside one declaration, checked here for every CLI.
use navide_cli_runtime::decl::*;
use navide_cli_runtime::observe::Backend;
use navide_cli_runtime::registry::{self, Visit, ALL};

#[derive(Default)]
struct Checks {
    seen: Vec<&'static str>,
    observed: usize,
}

impl Visit for Checks {
    fn vendor<V: Vendor>(&mut self, file: &'static str) {
        let key = V::CLI.key;
        assert_eq!(key, file, "src/vendors/{file}.rs declares the CLI it is named after");
        assert!(!self.seen.contains(&key), "{key} is declared twice");
        self.seen.push(key);
        let Some(observe) = V::OBSERVE else { return };
        self.observed += 1;

        let activity = &observe.activity;
        for (n, kept) in activity.kept.iter().enumerate() {
            assert!(activity.kept[..n].iter().all(|k| k.slot != kept.slot && k.prefix != kept.prefix), "{key}: a kept slot or its bag key is named twice");
        }
        if activity.turn_key.is_some() || activity.idle.is_some() {
            let text = activity.turn_text.unwrap_or_else(|| panic!("{key}: a tracked turn carries a reply slot"));
            assert!(activity.kept.iter().any(|k| k.slot == text), "{key}: the reply of an open turn is kept between walks");
        }
        match &observe.turns {
            Turns::Exact(_) => assert!(<V::Backend as Backend<V>>::EXACT_TURNS, "{key}: exact turns need a line transcript"),
            Turns::Inferred => assert!(<V::Backend as Backend<V>>::has_usage(&observe.records), "{key}: inferred turns cut usage"),
            Turns::None => {}
        }
        // The slots an event reads are real slots of this CLI (a type-level
        // fact) and its declared seeds name each slot once.
        for (n, seed) in observe.slots.iter().enumerate() {
            assert!(observe.slots[..n].iter().all(|s| s.slot != seed.slot), "{key}: slot {} is seeded twice", seed.slot.name());
        }
        // The JSON export carries the whole declaration.
        let json = export::<V>();
        assert_eq!(json["key"], key);
        assert!(json["observe"]["activity"]["rules"].is_array(), "{key}: the export holds its rules");
    }
}

#[test]
fn every_declaration_is_consistent() {
    let mut checks = Checks::default();
    registry::each(&mut checks);
    assert_eq!(checks.seen, ALL.iter().map(|cli| cli.key).collect::<Vec<_>>(), "the registry lists every vendor file once, in order");
    assert_eq!(checks.observed, registry::AnyObserver::all().len());
}

#[test]
fn settling_rows_are_bound_in_their_query() {
    struct Bound;
    impl Visit for Bound {
        fn vendor<V: Vendor>(&mut self, _file: &'static str) {
            let Some(observe) = V::OBSERVE else { return };
            // Only a SQLite declaration serialises a row walk with `revisit`.
            let records = serde_json::to_value(&observe.records).unwrap();
            let walk = &records["activity"]["Rows"];
            if !walk["revisit"].is_null() {
                assert!(walk["sql"].as_str().unwrap().contains("?3"), "{}: settling rows are bound at ?3", V::CLI.key);
            }
        }
    }
    registry::each(&mut Bound);
}

#[test]
fn launch_declarations_are_complete() {
    for cli in ALL {
        let launch = &cli.launch;
        assert!(!launch.program.is_empty(), "{}: a program", cli.key);
        assert!(launch.efforts.is_empty() || launch.effort.is_some(), "{}: an effort vocabulary needs an effort flag", cli.key);
        for template in [launch.model, launch.effort].into_iter().flatten() {
            assert!(template.iter().any(|arg| arg.contains("{}")), "{}: a model or effort template takes the value", cli.key);
        }
        if let Some(args) = cli.resume.args {
            assert!(args.iter().any(|arg| arg.contains("{id}")), "{}: resume arguments take the id", cli.key);
        }
    }
}
